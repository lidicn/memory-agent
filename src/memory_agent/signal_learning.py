"""信号学习服务：硬排除层 + 软记忆层 的统一入口（学习策略）。

两层：
- 硬排除（kind='hard'）：写入 ``signal_exclusions`` 表，检测逻辑命中即跳过
  （无歧义硬排），优先级高于软记忆层。
- 软记忆（kind='soft'）：复用 ``AgentMemoryService.add_semantic_memory``
  （topic_key='signal_trust'），自动获得溯源校验 / 信任闭环 / TTL 衰减 / sweep 晋升。

设计要点：
- 「无歧义硬排」：命中 signal_exclusions 的实体在对应 scope 直接判定，跳过软记忆层。
- 软记忆用于「通常错但有例外」的带条件判断，复用既有信任闭环越用越准。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class SignalLearningService:
    def __init__(self, store, agent_memory):
        self.store = store
        self.agent_memory = agent_memory

    # ── vMA-1.2.1: 负样本聚类→规则建议 ───────────────────────────────────
    def suggest_rules_from_negative_feedback(self, min_count: int = 3) -> dict:
        """从 feedback_down ≥ 1 的记忆里聚类重复模式，自动生成候选规则建议。

        红线：自动建议的规则 user_confirmed=0，永不进引擎，必须人工确认。

        Args:
            min_count: 同一模式出现多少次才建议（默认 3 次）

        Returns:
            {"ok": True, "suggestions": [...], "total_negative": int}
        """
        import json as _json
        conn = self.store.connect()

        # 查所有 feedback_down ≥ 1 的记忆
        rows = conn.execute(
            "SELECT memory_id, text, tags_json, feedback_down, topic_key "
            "FROM agent_memories WHERE feedback_down >= 1 AND state = 'live'"
        ).fetchall()

        if not rows:
            return {"ok": True, "suggestions": [], "total_negative": 0}

        # 简单聚类：按 topic_key + tags 前缀分组
        clusters: dict[str, list[dict]] = {}
        for r in rows:
            try:
                tags = _json.loads(r["tags_json"]) if r["tags_json"] else []
            except Exception:
                tags = []
            # 聚类 key：topic_key + 第一个 tag（如果有）
            cluster_key = r["topic_key"] or (tags[0] if tags else "unknown")
            if cluster_key not in clusters:
                clusters[cluster_key] = []
            clusters[cluster_key].append({
                "memory_id": r["memory_id"],
                "text": r["text"],
                "tags": tags,
                "feedback_down": r["feedback_down"],
            })

        # 筛选出现 ≥ min_count 次的模式
        suggestions = []
        for key, items in clusters.items():
            total_down = sum(i["feedback_down"] for i in items)
            if len(items) >= min_count:
                # 生成候选规则建议
                sample_text = items[0]["text"][:100] if items else ""
                suggestions.append({
                    "pattern_key": key,
                    "occurrence_count": len(items),
                    "total_negative_feedback": total_down,
                    "sample_text": sample_text,
                    "suggestion": f"检测到「{key}」模式被负反馈 {len(items)} 次（累计 {total_down} 次 down），建议检查是否需要排除或调整",
                    "user_confirmed": 0,  # 红线：必须人工确认
                })

        # 按出现次数排序
        suggestions.sort(key=lambda x: x["occurrence_count"], reverse=True)

        return {
            "ok": True,
            "suggestions": suggestions,
            "total_negative": len(rows),
            "cluster_count": len(clusters),
        }

    # ── 写入：teach_signal 硬/软分流 ───────────────────────────────────────
    def teach_signal(
        self,
        entity_id: str,
        scope: str = "all",
        kind: str = "hard",
        reason: str = "",
        text: str = "",
        source_refs: Optional[List[str]] = None,
        session_id: str = "mcp",
        exclusion_type: str = "exclude",
    ) -> Dict[str, Any]:
        entity_id = (entity_id or "").strip()
        if not entity_id:
            return {"ok": False, "error": "entity_id 不能为空", "code": 400}
        kind = (kind or "hard").strip().lower()
        if kind not in ("hard", "soft"):
            return {"ok": False, "error": "kind 仅支持 'hard' | 'soft'", "code": 400}

        if kind == "hard":
            exclusion_id = self.store.upsert_signal_exclusion(
                entity_id=entity_id,
                scope=scope,
                reason=reason,
                created_by=session_id,
                exclusion_type=exclusion_type,
            )
            print(
                f"[SignalLearning] teach_signal hard: entity={entity_id} scope={scope} "
                f"type={exclusion_type} by={session_id}"
            )
            return {
                "ok": True,
                "kind": "hard",
                "exclusion_id": exclusion_id,
                "entity_id": entity_id,
                "scope": scope,
                "exclusion_type": exclusion_type,
                "message": "已写入信号硬排除（无歧义硬排）",
            }

        # kind == 'soft'：走 agent_memory 软记忆（topic_key=signal_trust）
        if not text or not text.strip():
            return {"ok": False, "error": "kind='soft' 必须提供 text", "code": 400}
        refs = source_refs or [f"event:taught:{entity_id}"]
        res = self.agent_memory.add_semantic_memory(
            session_id=session_id,
            text=text,
            tags=["signal_trust"],
            source_refs=refs,
            ttl_days=None,
            topic_key="signal_trust",
            dry_run=False,
        )
        if not res.get("ok"):
            return {
                "ok": False,
                "error": res.get("error", "add_semantic_memory 失败"),
                "detail": res,
                "code": res.get("code", 500),
            }
        print(
            f"[SignalLearning] teach_signal soft: entity={entity_id} scope={scope} "
            f"memory_id={res.get('memory_id')}"
        )
        return {
            "ok": True,
            "kind": "soft",
            "memory_id": res.get("memory_id"),
            "entity_id": entity_id,
            "scope": scope,
            "message": "已写入信号软记忆（topic_key=signal_trust），参与信任闭环",
        }

    # ── 读取：混合列表（硬排除 + 软记忆）──────────────────────────────────
    def list_rules(self, include_revoked: bool = False) -> Dict[str, Any]:
        hard = self.store.list_signal_exclusions(include_revoked=include_revoked)
        all_mems = self.agent_memory.list_agent_memories().get("memories", [])
        soft = [m for m in all_mems if m.get("topic_key") == "signal_trust"]
        return {
            "ok": True,
            "hard": hard,
            "soft": soft,
            "counts": {"hard": len(hard), "soft": len(soft)},
        }

    # ── 撤销：硬排除墓碑（保留审计）───────────────────────────────────────
    def revoke_exclusion(self, exclusion_id: str) -> Dict[str, Any]:
        ok = self.store.revoke_signal_exclusion(exclusion_id)
        if not ok:
            return {"ok": False, "error": "exclusion_id 不存在或已撤销"}
        return {"ok": True, "exclusion_id": exclusion_id, "message": "已撤销硬排除（保留审计）"}

    # ── 消费点辅助：某实体在某 scope 是否被硬排除（无歧义硬排的核心判定）──
    def is_excluded(self, entity_id: str, scope: str) -> bool:
        rows = self.store.list_signal_exclusions(include_revoked=False)
        for r in rows:
            if r["entity_id"] == entity_id and (r["scope"] == "all" or r["scope"] == scope):
                return True
        return False
