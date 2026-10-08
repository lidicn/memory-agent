"""Phase 4.2 建议语义去重器。

复用现有 OpenAI 兼容 embedding 端点（bge-m3 等），对建议/洞察文本做
语义相似度去重，避免重复推送语义相近的建议。

设计：
- 通用接口：dedupe(texts, threshold) → 去重后的文本 + 被抑制的映射
- 余弦相似度计算（numpy，无额外依赖）
- embedding 不可用时回退到精确匹配（fail-soft，不阻塞主流程）
- 批量 embedding 调用，减少 API 请求次数
- 可配置阈值（默认 0.85，bge-m3 余弦相似度）

用法：
    deduper = SemanticDeduplicator(config)
    result = deduper.dedupe(["建议A", "建议B", "建议A的变体"], threshold=0.85)
    unique_texts = result["unique"]  # 去重后的文本
    suppressed = result["suppressed"]  # {被抑制文本: 保留的相似文本}
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SemanticDeduplicator:
    """语义去重器：基于 embedding 余弦相似度的文本去重。

    embedding 不可用时回退到精确匹配（fail-soft）。
    """

    def __init__(self, config=None, embedding_fn=None) -> None:
        self.config = config
        self._embedding_fn = embedding_fn
        #: 构造时注入的 embedding_fn 不归热更新管，重置探测时要还原它
        self._injected_embedding_fn = embedding_fn
        self._embedding_tried = False
        self._embedding_available = False

    def reset_embedding_probe(self) -> None:
        """清除「试一次就定终身」的 embedding 探测缓存，让下次调用重新构建。

        第七轮审计 CRITICAL-2 的姊妹处：`_get_embedding_fn()` 一旦 `_embedding_tried`
        为真就永不重试。用户在 WebUI 里补对 embedding 端点后，若不重置探测，
        语义去重会一直停留在精确匹配降级态，直到重启进程。
        """
        self._embedding_tried = False
        self._embedding_available = False
        self._embedding_fn = self._injected_embedding_fn

    def _get_embedding_fn(self):
        """获取 embedding 函数（懒加载；失败后由 `reset_embedding_probe()` 解锁重试）。"""
        if self._embedding_tried:
            return self._embedding_fn if self._embedding_available else None
        self._embedding_tried = True

        # 优先使用注入的 embedding_fn
        if self._embedding_fn is not None:
            self._embedding_available = True
            return self._embedding_fn

        # 从 config 构建
        if self.config is None:
            return None

        base = (getattr(self.config, "embedding_base_url", "") or "").strip()
        model = (getattr(self.config, "embedding_model", "") or "").strip()
        if not base or not model:
            logger.debug("[SemanticDedup] 未配置 embedding 端点，回退精确匹配")
            return None

        try:
            from .history import _OpenAICompatEmbeddingFunction
            api_key = getattr(self.config, "embedding_api_key", "") or ""
            self._embedding_fn = _OpenAICompatEmbeddingFunction(base, model, api_key)
            self._embedding_available = True
            logger.info("[SemanticDedup] embedding 端点已就绪: %s (model=%s)", base, model)
            return self._embedding_fn
        except Exception as exc:
            logger.warning("[SemanticDedup] embedding 初始化失败，回退精确匹配: %s", exc)
            return None

    @staticmethod
    def _cosine_similarity(vec1, vec2) -> float:
        """计算两个向量的余弦相似度（numpy 优先，不可用时回退纯 Python）。

        A2/A7 P2-1 实测：维度不等时 numpy 路径抛 ValueError，纯 Python 的 `zip` 却
        **截到短的那条**并给出一个合法数字（3 维 vs 2 维实测 0.5976）——同一输入两条
        路径两种答案，只在没装 numpy 的环境里出错。故先显式比长度，让两条路径同判。
        """
        n1, n2 = len(vec1), len(vec2)
        if n1 != n2:
            raise ValueError(f"向量维度不一致: {n1} vs {n2}（不同模型的向量不可比）")
        try:
            import numpy as np
            dot = float(np.dot(vec1, vec2))
            norm1 = float(np.linalg.norm(vec1))
            norm2 = float(np.linalg.norm(vec2))
        except ImportError:
            # 纯 Python 回退（长度已在上面校验过，这里的 zip 不会再悄悄截短）
            dot = sum(float(a) * float(b) for a, b in zip(vec1, vec2, strict=True))
            norm1 = (sum(float(a) * float(a) for a in vec1)) ** 0.5
            norm2 = (sum(float(b) * float(b) for b in vec2)) ** 0.5
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    def dedupe(
        self,
        texts: list[str],
        threshold: float = 0.85,
    ) -> dict[str, Any]:
        """对文本列表做语义去重。

        Args:
            texts: 待去重的文本列表
            threshold: 余弦相似度阈值（0-1），超过此值视为重复

        Returns:
            {
                "unique": list[str],           # 去重后保留的文本（保持原顺序）
                "suppressed": dict[str, str],  # {被抑制文本: 保留的相似文本}
                "method": str,                  # "semantic" 或 "exact"（回退）
                "pairs": list[dict],            # 相似对详情（text1, text2, similarity）
            }
        """
        if not texts:
            return {"unique": [], "suppressed": {}, "method": "none", "pairs": []}

        # 先做精确去重（无论 embedding 是否可用）
        seen_exact: set[str] = set()
        unique_exact: list[str] = []
        suppressed_exact: dict[str, str] = {}
        for t in texts:
            if t in seen_exact:
                suppressed_exact[t] = t  # 精确重复，保留第一个
            else:
                seen_exact.add(t)
                unique_exact.append(t)

        # 只有一条或零条，直接返回
        if len(unique_exact) <= 1:
            return {
                "unique": unique_exact,
                "suppressed": suppressed_exact,
                "method": "exact",
                "pairs": [],
            }

        # 尝试语义去重
        embed_fn = self._get_embedding_fn()
        if embed_fn is None:
            # embedding 不可用，仅精确去重
            return {
                "unique": unique_exact,
                "suppressed": suppressed_exact,
                "method": "exact",
                "pairs": [],
            }

        try:
            # 批量 embedding
            embeddings = embed_fn(unique_exact)
            if embeddings is None or len(embeddings) == 0:
                raise ValueError("embedding 返回空")

            # 两两比较，贪心去重（保留先出现的）
            unique_semantic: list[str] = []
            suppressed_semantic: dict[str, str] = {}
            pairs: list[dict] = []
            kept_indices: list[int] = []

            for i, text in enumerate(unique_exact):
                is_dup = False
                for j in kept_indices:
                    sim = self._cosine_similarity(embeddings[i], embeddings[j])
                    if sim >= threshold:
                        is_dup = True
                        suppressed_semantic[text] = unique_exact[j]
                        pairs.append({
                            "text": text,
                            "duplicate_of": unique_exact[j],
                            "similarity": round(sim, 4),
                        })
                        break
                if not is_dup:
                    unique_semantic.append(text)
                    kept_indices.append(i)

            # 合并精确去重和语义去重的 suppressed
            all_suppressed = dict(suppressed_exact)
            all_suppressed.update(suppressed_semantic)

            return {
                "unique": unique_semantic,
                "suppressed": all_suppressed,
                "method": "semantic",
                "pairs": pairs,
            }

        except Exception as exc:
            logger.warning("[SemanticDedup] 语义去重失败，回退精确匹配: %s", exc)
            return {
                "unique": unique_exact,
                "suppressed": suppressed_exact,
                "method": "exact (fallback)",
                "pairs": [],
            }

    def is_duplicate(
        self,
        text: str,
        existing_texts: list[str],
        threshold: float = 0.85,
    ) -> dict[str, Any]:
        """检查单条文本是否与现有文本列表中的某条语义重复。

        适用于逐条生成建议时的实时去重。

        Returns:
            {"duplicate": bool, "duplicate_of": str|None, "similarity": float, "method": str}
        """
        if not existing_texts:
            return {"duplicate": False, "duplicate_of": None, "similarity": 0.0, "method": "none"}

        # 精确匹配
        for existing in existing_texts:
            if text == existing:
                return {"duplicate": True, "duplicate_of": existing, "similarity": 1.0, "method": "exact"}

        # 语义匹配
        embed_fn = self._get_embedding_fn()
        if embed_fn is None:
            return {"duplicate": False, "duplicate_of": None, "similarity": 0.0, "method": "exact"}

        try:
            all_texts = [text] + existing_texts
            embeddings = embed_fn(all_texts)
            if embeddings is None or len(embeddings) < 2:
                raise ValueError("embedding 返回不足")

            best_sim = 0.0
            best_match = None
            for i, existing in enumerate(existing_texts):
                sim = self._cosine_similarity(embeddings[0], embeddings[i + 1])
                if sim > best_sim:
                    best_sim = sim
                    best_match = existing

            if best_sim >= threshold:
                return {
                    "duplicate": True,
                    "duplicate_of": best_match,
                    "similarity": round(best_sim, 4),
                    "method": "semantic",
                }
            return {
                "duplicate": False,
                "duplicate_of": None,
                "similarity": round(best_sim, 4),
                "method": "semantic",
            }

        except Exception as exc:
            logger.warning("[SemanticDedup] is_duplicate 失败，回退精确匹配: %s", exc)
            return {"duplicate": False, "duplicate_of": None, "similarity": 0.0, "method": "exact (fallback)"}
