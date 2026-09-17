"""P2 概率实体解析（Fellegi-Sunter）—— 升级 identity.py 的 difflib 启发式。

为什么需要这一层
----------------
原判定是「同 room + 同 domain + 名称相似度 ≥ 0.85（或公共前缀 ≥6）」的**硬阈值**：
* **没有概率**：0.84 与 0.86 一刀两断，无法表达"像是但拿不准"
* **没有不确定性**：不知道哪些配对该交人工复核
* **没用上强信号**：entity_id 里的设备号段（同硬件被双集成接入）完全没参与判定

Fellegi-Sunter（FS）给出标准答案：对候选配对算**比较向量**，用
``m``（真匹配时出现该比较结果的概率）/ ``u``（非匹配时出现）合成匹配权重，
再经贝叶斯换算成**匹配概率**；按概率分三档：自动合并 / 待复核 / 不合并。

实现选择：**纯 Python 核心**，Splink 作为可选交叉验证（``identity_splink_eval``）。
理由与 P1.1 过程挖掘一致——Splink 依赖重（duckdb/pandas/altair…），J3455 NAS
常驻服务不适合；本场景比较字段少、候选量小（百级），FS 核心百余行即可，
且能离线用 EM 自校准。装了 splink 时可用其做对照验证。

相似度/归一化等字符串工具**定义在本模块**，identity.py 从此处导入并再导出，
保证两处判定口径完全一致（也是避免循环依赖的模块划分）。
"""
from __future__ import annotations

import difflib
import math
import re

# 名称相似度阈值：≥ 此值且同 room + 同 domain 才判定为同一物理设备
SIMILARITY_THRESHOLD = 0.85
# 加固：同 room 同 domain 的实体，若归一化名称共享公共前缀 ≥ 此字符数，也判定为同一
# 物理设备（如「lidicn的电视电视」与「lidicn的电视播放控制」实为同一台电视）。
PREFIX_MERGE_MIN = 6

_CJK = r"\u4e00-\u9fff"

# 比较结果等级
MISSING, DISAGREE, PARTIAL, AGREE = -1, 0, 1, 2

# 参与比较的字段（顺序无关）
FIELDS = ("name", "domain", "room", "stem")


# ── 归一化与相似度（与 identity 共用，口径唯一）──────────────────────────────

def normalize_name(name: str) -> str:
    """归一化设备名：小写、去掉空白与标点，仅保留字母数字与中日韩字符。"""
    s = (name or "").strip().lower()
    return re.sub(rf"[^0-9a-z{_CJK}]+", "", s)


def similarity(a: str, b: str) -> float:
    """归一化后的名称相似度（0~1），用于 A1 重匹配与 A2 冗余合并。"""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _common_prefix_len(a: str, b: str) -> int:
    """最长公共前缀长度（按字符计）。"""
    n = 0
    for ca, cb in zip(a, b):
        if ca == cb:
            n += 1
        else:
            break
    return n


# ── 比较向量 ───────────────────────────────────────────────────────────────

def _tokens(entity_id: str) -> set[str]:
    """从 entity_id 拆出可比对片段：``switch.study_pc`` → {switch, study, pc}。"""
    return {t for t in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", str(entity_id or ""))
            if len(t) >= 4}


def name_level(a: str, b: str) -> int:
    """名称比较等级。

    ``AGREE`` 沿用历史已验证口径：归一化相等 / 相似度 ≥ 0.85 / 公共前缀 ≥ 6
    （把既有的"硬规则"并入"强相似"，保证升级不回退已验证的合并）。
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return MISSING
    if na == nb or similarity(a, b) >= SIMILARITY_THRESHOLD:
        return AGREE
    if _common_prefix_len(na, nb) >= PREFIX_MERGE_MIN:
        return AGREE
    if similarity(a, b) >= 0.6:
        return PARTIAL
    return DISAGREE


def eq_level(a: str, b: str) -> int:
    """精确字段（domain / room）比较等级。"""
    if not a or not b:
        return MISSING
    return AGREE if a == b else DISAGREE


def stem_level(id_a: str, id_b: str) -> int:
    """entity_id 级强信号：同一硬件被两个集成接入时，设备号段会同时出现。

    * ``AGREE``：共享长数字号段（如 ``chuangmi_cn_1072229835_051a01`` 的 ``1072229835``）
    * ``PARTIAL``：共享其他长度 ≥4 的片段（如同一个 object_id 词根）
    """
    ta, tb = _tokens(id_a), _tokens(id_b)
    if not ta or not tb:
        return MISSING
    shared = ta & tb
    if not shared:
        return DISAGREE
    if any(t.isdigit() and len(t) >= 6 for t in shared):
        return AGREE
    return PARTIAL


# ── 先验：各字段各等级的 (m, u) ─────────────────────────────────────────────
# m = 真匹配时出现该等级的概率；u = 非匹配时出现该等级的概率。
# 取自领域经验，可由 ProbabilisticMatcher.fit() 用 EM 在无标注数据上自校准。
DEFAULT_PRIORS: dict[str, dict[int, tuple[float, float]]] = {
    "name": {AGREE: (0.90, 0.02), PARTIAL: (0.08, 0.18), DISAGREE: (0.02, 0.80)},
    "domain": {AGREE: (0.98, 0.15), DISAGREE: (0.02, 0.85)},
    "room": {AGREE: (0.95, 0.10), DISAGREE: (0.05, 0.90)},
    "stem": {AGREE: (0.60, 0.01), PARTIAL: (0.25, 0.20), DISAGREE: (0.15, 0.79)},
}
# 先验匹配率（同一 room+domain 块内的配对属于同一设备的比例）
DEFAULT_LAMBDA = 0.1


class ProbabilisticMatcher:
    """Fellegi-Sunter 概率实体解析器（纯 Python，无第三方依赖）。"""

    def __init__(
        self,
        priors: dict | None = None,
        lamb: float = DEFAULT_LAMBDA,
        match_threshold: float = 0.95,
        review_threshold: float = 0.5,
        require_domain: bool = True,
        require_room: bool = True,
        fields=FIELDS,
    ) -> None:
        self.priors = {f: dict(v) for f, v in (priors or DEFAULT_PRIORS).items()}
        self.lamb = max(1e-4, min(0.99, float(lamb)))
        self.match_threshold = float(match_threshold)
        self.review_threshold = float(review_threshold)
        self.require_domain = bool(require_domain)
        self.require_room = bool(require_room)
        self.fields = tuple(fields)

    # -- 比较向量 -----------------------------------------------------------
    def levels(self, a: dict, b: dict) -> dict[str, int]:
        """构造两条实体记录的比较向量。"""
        out: dict[str, int] = {}
        if "name" in self.fields:
            out["name"] = name_level(a.get("name") or "", b.get("name") or "")
        if "domain" in self.fields:
            out["domain"] = eq_level(a.get("domain") or "", b.get("domain") or "")
        if "room" in self.fields:
            out["room"] = eq_level(a.get("room") or "", b.get("room") or "")
        if "stem" in self.fields:
            out["stem"] = stem_level(a.get("entity_id") or "", b.get("entity_id") or "")
        return out

    def blocked(self, levels: dict[str, int]) -> bool:
        """阻塞规则：domain 或 room 明确不等 → 直接判非匹配（沿用"跨 room 不合并"）。"""
        if self.require_domain and levels.get("domain") == DISAGREE:
            return True
        if self.require_room and levels.get("room") == DISAGREE:
            return True
        return False

    # -- 打分 ---------------------------------------------------------------
    def _log_odds(self, levels: dict[str, int]) -> float:
        if self.blocked(levels):
            return float("-inf")
        total = math.log2(self.lamb / (1.0 - self.lamb))
        for f in self.fields:
            lv = levels.get(f, MISSING)
            if lv == MISSING:
                continue  # 缺失字段不贡献证据（而非当作不一致）
            m, u = self.priors.get(f, {}).get(lv, (0.5, 0.5))
            if u <= 0 or m <= 0:
                continue
            total += math.log2(m / u)
        return total

    def probability(self, levels: dict[str, int]) -> float:
        lo = self._log_odds(levels)
        if lo == float("-inf"):
            return 0.0
        # 数值稳定：odds 极大时直接判 1
        if lo > 60:
            return 1.0
        return 1.0 / (1.0 + 2.0 ** (-lo))

    def band(self, p: float) -> str:
        """按匹配概率分档——**不确定性**就体现在 ``review`` 这一档。"""
        if p >= self.match_threshold:
            return "match"
        if p >= self.review_threshold:
            return "review"
        return "non"

    def compare(self, a: dict, b: dict) -> dict:
        """完整比较：等级、各字段权重、匹配权重、概率与分档。"""
        lv = self.levels(a, b)
        weights: dict[str, float] = {}
        for f in self.fields:
            l = lv.get(f, MISSING)
            if l == MISSING:
                weights[f] = 0.0
                continue
            m, u = self.priors.get(f, {}).get(l, (0.5, 0.5))
            weights[f] = round(math.log2(m / u), 3) if (u > 0 and m > 0) else 0.0
        lo = self._log_odds(lv)
        p = self.probability(lv)
        return {
            "levels": lv,
            "weights": weights,
            "match_weight": round(lo, 3) if lo != float("-inf") else None,
            "probability": round(p, 4),
            "band": self.band(p),
            "blocked": self.blocked(lv),
        }

    def is_match(self, a: dict, b: dict) -> bool:
        return self.compare(a, b)["band"] == "match"

    # -- 无监督自校准（EM）---------------------------------------------------
    def fit(self, pairs, iterations: int = 25, min_pairs: int = 20) -> dict:
        """用 EM 在无标注配对上自校准 m/u/λ。

        :param pairs: ``[(rec_a, rec_b), ...]``——传入**阻塞内**的候选配对
            （如同一 room+domain 块），否则 u 会被大量"显然不匹配"稀释。
        """
        vecs = [self.levels(a, b) for a, b in pairs]
        if len(vecs) < max(1, min_pairs):
            return {"ok": False, "error": "样本不足，沿用先验",
                    "pairs": len(vecs), "min_pairs": min_pairs}
        for _ in range(max(1, iterations)):
            probs = [self.probability(v) for v in vecs]
            self.lamb = max(1e-4, min(0.99, sum(probs) / len(probs)))
            for f in self.fields:
                cnt_m: dict[int, float] = {}
                cnt_u: dict[int, float] = {}
                for v, p in zip(vecs, probs):
                    lv = v.get(f, MISSING)
                    if lv == MISSING:
                        continue
                    cnt_m[lv] = cnt_m.get(lv, 0.0) + p
                    cnt_u[lv] = cnt_u.get(lv, 0.0) + (1.0 - p)
                tot_m = sum(cnt_m.values()) or 1.0
                tot_u = sum(cnt_u.values()) or 1.0
                levels_priors = self.priors.setdefault(f, {})
                for lv in (DISAGREE, PARTIAL, AGREE):
                    m = max(1e-6, min(1 - 1e-6, cnt_m.get(lv, 1e-6) / tot_m))
                    u = max(1e-6, min(1 - 1e-6, cnt_u.get(lv, 1e-6) / tot_u))
                    levels_priors[lv] = (m, u)
        return {
            "ok": True, "pairs": len(vecs), "iterations": iterations,
            "lambda": round(self.lamb, 4), "priors": self.to_dict()["priors"],
        }

    # -- 序列化 -------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "lamb": self.lamb,
            "match_threshold": self.match_threshold,
            "review_threshold": self.review_threshold,
            "require_domain": self.require_domain,
            "require_room": self.require_room,
            "priors": {f: {str(k): list(v) for k, v in lv.items()}
                       for f, lv in self.priors.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProbabilisticMatcher":
        m = cls(
            lamb=float(data.get("lamb", DEFAULT_LAMBDA)),
            match_threshold=float(data.get("match_threshold", 0.95)),
            review_threshold=float(data.get("review_threshold", 0.5)),
            require_domain=bool(data.get("require_domain", True)),
            require_room=bool(data.get("require_room", True)),
        )
        for f, lv in (data.get("priors") or {}).items():
            m.priors[f] = {int(k): tuple(v) for k, v in lv.items()}
        return m
