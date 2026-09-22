"""
candidate_promotion —— 候选区事件晋升模块（改进版）
==================================================

职责
----
感知层产生的低置信度事件首先进入"候选区"（perception_events）。
只有当某一类事件被多条证据反复验证、且证据之间互相内聚时，才允许把它
"晋升"为正式的行为事件（behavior_events），进入长期记忆。

相对旧版的改进
--------------
1. **可解释的多维评分模型**（:class:`ScoringModel`）：
   事件重要性 / 置信可信度 / 证据频次 / 到访次数 / 重复强化 / 跨天跨度 /
   身份内聚 七个正向维度 + 冲突惩罚，每个维度输出归一化分值、权重、
   加权贡献与人话理由，可整体渲染为解释文本。
2. **灵活的晋升策略**（:class:`PromotionPolicy`）：
   阈值、最小证据数、时间窗口、饱和上限、冲突惩罚、去重策略等全部可配置，
   且支持 ``dataclasses.replace`` 派生。
3. **审计事件**（:class:`AuditRecorder`）：
   每一次晋升 / 拒绝 / 直通 / 失败都会落一条审计记录，包含分数、
   证据摘要、评分拆解、阻断原因与策略快照。
4. **缓存层**（:class:`EvidenceCache`）：
   内存 / Redis 两种后端，Redis 故障自动降级到内存，并统计命中率。
5. **接口完全向后兼容**：``should_promote_directly`` / ``collect_evidence`` /
   ``check_promotion_criteria`` / ``promote_to_behavior`` / ``process_candidates``
   的签名与返回形态保持不变，老 evidence 字典继续可用。

典型用法
--------
    promoter = get_promoter(store)                 # 兼容旧入口
    stats = promoter.process_candidates()          # 定时任务调用

    # 需要解释时：
    evidence = promoter.collect_evidence("face_known", "客厅", "爸爸")
    result = promoter.evaluate(evidence)
    print(result.breakdown.explain())
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import (Any, Dict, Iterable, List, Mapping, Optional, Protocol,
                    Sequence, Tuple)

logger = logging.getLogger(__name__)

__all__ = [
    # 模块级常量（旧代码可能直接引用）
    "HIGH_CONFIDENCE_THRESHOLD", "CANDIDATE_WINDOW_HOURS",
    "MIN_EVIDENCE_COUNT", "PROMOTION_SCORE_THRESHOLD",
    # 配置
    "ScoringWeights", "PromotionPolicy", "DEFAULT_KIND_IMPORTANCE",
    # 评分
    "EvidenceView", "DimensionScore", "ScoreBreakdown", "EvaluationResult",
    "ScoringModel",
    # 缓存
    "CacheBackend", "MemoryCacheBackend", "RedisCacheBackend",
    "EvidenceCache", "create_cache",
    # 审计
    "AuditEvent", "AuditSink", "MemoryAuditSink", "StoreAuditSink",
    "AuditRecorder",
    # 主体
    "CandidatePromoter", "get_promoter", "reset_promoter",
]

# ---------------------------------------------------------------------------
# 向后兼容的模块级常量
# ---------------------------------------------------------------------------
HIGH_CONFIDENCE_THRESHOLD = 0.85   # 低于此置信度的事件进入候选区
CANDIDATE_WINDOW_HOURS = 24        # 候选区扫描窗口
MIN_EVIDENCE_COUNT = 3             # 晋升所需的最小证据条数
PROMOTION_SCORE_THRESHOLD = 70.0   # 晋升所需综合得分（百分制）


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------
def clamp01(value: float) -> float:
    """把数值截断到 [0, 1]。"""
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return float(value)


def log_saturate(value: float, cap: float) -> float:
    """对数饱和：次数越多增益越小，``cap`` 为满分所需的次数。

    ``value <= 0`` 得 0 分；``value >= cap`` 得 1 分。
    """
    if value <= 0.0:
        return 0.0
    if cap <= 0.0:
        return 1.0
    return clamp01(math.log1p(value) / math.log1p(cap))


def _as_mapping(value: Any) -> Dict[str, Any]:
    """把可能是 JSON 文本 / Mapping / None 的列值统一成 dict。"""
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except Exception:
            return {}
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except Exception:
            return {}
        return decoded if isinstance(decoded, Mapping) else {}
    return {}


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    """兼容 Mapping 行与 sqlite3.Row 等下标访问行。"""
    if row is None:
        return default
    if isinstance(row, Mapping):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, default)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_ts(value: Any) -> Optional[datetime]:
    """尽力解析 ISO 时间字符串，失败返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _now_iso() -> str:
    return datetime.now().isoformat()


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScoringWeights:
    """评分模型各维度权重（正向维度，建议归一化后使用）。"""

    importance: float = 0.15      # 事件类型重要性
    credibility: float = 0.15     # 置信可信度
    frequency: float = 0.20       # 证据频次（证据条数）
    access: float = 0.15          # 到访次数（独立会话数）
    reinforcement: float = 0.10   # 重复强化次数
    time_span: float = 0.15       # 跨天跨度
    cohesion: float = 0.10        # 身份内聚度

    def as_dict(self) -> Dict[str, float]:
        return {
            "importance": self.importance,
            "credibility": self.credibility,
            "frequency": self.frequency,
            "access": self.access,
            "reinforcement": self.reinforcement,
            "time_span": self.time_span,
            "cohesion": self.cohesion,
        }

    def normalized(self) -> "ScoringWeights":
        """权重归一化，保证总和为 1.0（允许调用方写任意相对权重）。"""
        items = self.as_dict()
        total = sum(v for v in items.values() if v > 0)
        if total <= 0:
            return ScoringWeights()
        return ScoringWeights(**{k: max(0.0, v) / total for k, v in items.items()})


#: 各事件类型的重要性基线（0~1），可被 PromotionPolicy 覆盖
DEFAULT_KIND_IMPORTANCE: Dict[str, float] = {
    "human": 0.50,
    "face_known": 0.90,
    "face_unknown": 0.70,
    "pet": 0.60,
    "cry": 1.00,
    "baby_woke": 0.95,
}

#: 互相矛盾的事件类型对（用于冲突惩罚）
DEFAULT_CONFLICT_KINDS: Dict[str, Tuple[str, ...]] = {
    "face_known": ("face_unknown",),
    "face_unknown": ("face_known",),
}


@dataclass(frozen=True)
class PromotionPolicy:
    """晋升策略：所有阈值、窗口、饱和上限、惩罚与去重开关。"""

    # ---- 判定阈值 ----
    promotion_threshold: float = PROMOTION_SCORE_THRESHOLD   # 综合得分阈值（百分制）
    min_evidence_count: int = MIN_EVIDENCE_COUNT             # 最小证据条数
    min_distinct_days: int = 2                               # 跨天要求的最少天数
    require_cross_day: bool = True                           # 是否强制要求跨天
    legacy_gate_only: bool = True                            # 旧版 evidence 字典只走硬门槛

    # ---- 时间窗口 ----
    scan_window_hours: int = CANDIDATE_WINDOW_HOURS          # 候选区扫描窗口
    evidence_window_hours: int = 72                          # 证据回溯窗口
    session_gap_minutes: int = 30                            # 超过此间隔视为一次新到访
    scan_limit: int = 100                                    # 单轮扫描行数上限
    evidence_limit: int = 500                                # 单次证据查询行数上限

    # ---- 置信度 ----
    high_confidence_threshold: float = HIGH_CONFIDENCE_THRESHOLD  # 入候选区的置信度上限
    direct_promotion_confidence: float = 0.95               # 高置信度直通阈值
    credibility_floor: float = 0.20                          # 可信度评分下界
    credibility_ceiling: float = 0.95                        # 可信度评分上界

    # ---- 评分饱和上限（满分所需次数）----
    evidence_saturation: int = 6
    access_saturation: int = 3
    reinforcement_saturation: int = 5
    day_saturation: int = 3

    # ---- 冲突惩罚（百分点）----
    conflict_penalty_per_event: float = 10.0
    max_conflict_penalty: float = 30.0

    # ---- 映射表 ----
    kind_importance: Mapping[str, float] = field(
        default_factory=lambda: dict(DEFAULT_KIND_IMPORTANCE)
    )
    conflict_kinds: Mapping[str, Tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_CONFLICT_KINDS)
    )
    direct_promote_kinds: Tuple[str, ...] = ("cry", "baby_woke")
    default_importance: float = 0.5

    # ---- 行为开关 ----
    dedupe_within_run: bool = True       # 同一轮内同 (kind, room, person) 只晋升一次
    skip_already_promoted: bool = True   # 跳过窗口内已经晋升过的候选

    # ---- 权重与缓存 ----
    weights: ScoringWeights = field(default_factory=ScoringWeights)
    cache_ttl_seconds: int = 30

    def validate(self) -> List[str]:
        """返回配置错误列表（空表示合法）。"""
        problems: List[str] = []
        if not 0.0 <= self.promotion_threshold <= 100.0:
            problems.append("promotion_threshold 必须在 [0, 100]")
        if self.min_evidence_count < 1:
            problems.append("min_evidence_count 必须 >= 1")
        if self.min_distinct_days < 1:
            problems.append("min_distinct_days 必须 >= 1")
        if self.evidence_window_hours <= 0 or self.scan_window_hours <= 0:
            problems.append("时间窗口必须为正数")
        if self.credibility_ceiling <= self.credibility_floor:
            problems.append("credibility_ceiling 必须大于 credibility_floor")
        if sum(self.weights.as_dict().values()) <= 0:
            problems.append("评分权重之和必须为正")
        return problems

    def snapshot(self) -> Dict[str, Any]:
        """策略快照（写入审计，便于事后复盘当时用的是什么规则）。"""
        data = {
            "promotion_threshold": self.promotion_threshold,
            "min_evidence_count": self.min_evidence_count,
            "min_distinct_days": self.min_distinct_days,
            "require_cross_day": self.require_cross_day,
            "scan_window_hours": self.scan_window_hours,
            "evidence_window_hours": self.evidence_window_hours,
            "session_gap_minutes": self.session_gap_minutes,
            "conflict_penalty_per_event": self.conflict_penalty_per_event,
            "max_conflict_penalty": self.max_conflict_penalty,
            "weights": self.weights.normalized().as_dict(),
            "legacy_gate_only": self.legacy_gate_only,
        }
        return data


# ---------------------------------------------------------------------------
# 证据视图与评分结果
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EvidenceView:
    """证据的归一化视图：评分模型的唯一输入。

    ``estimated=True`` 表示由旧版精简字典（只有 count / cross_day）估算而来。
    """

    kind: str = "human"
    room: str = ""
    person_name: Optional[str] = None
    count: int = 0
    visit_count: int = 0
    reinforcement_count: int = 0
    distinct_days: int = 1
    cross_day: bool = False
    avg_confidence: float = 0.0
    max_confidence: float = 0.0
    cohesion: float = 1.0
    conflict_count: int = 0
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None
    estimated: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, evidence: Mapping[str, Any]) -> "EvidenceView":
        """从 ``collect_evidence`` 的返回值或任意旧版字典构造视图。"""
        evidence = dict(evidence or {})
        estimated = evidence.get("evidence_version") is None

        if estimated:
            # 旧版精简字典：count / cross_day 为必有字段，其余用保守估计值补齐
            count = _to_int(evidence.get("count"), 0)
            cross_day = bool(evidence.get("cross_day", False))
            distinct_days = _to_int(evidence.get("distinct_days"), 2 if cross_day else 1)
            visit_count = _to_int(evidence.get("visit_count"), max(1, count // 2) if count else 0)
            confidence = _to_float(evidence.get("confidence"), 0.5)
            return cls(
                kind=str(evidence.get("kind") or "human"),
                room=str(evidence.get("room") or ""),
                person_name=evidence.get("person_name"),
                count=count,
                visit_count=visit_count,
                reinforcement_count=max(0, count - visit_count),
                distinct_days=max(1, distinct_days),
                cross_day=cross_day,
                avg_confidence=confidence,
                max_confidence=_to_float(evidence.get("max_confidence"), confidence),
                cohesion=_to_float(evidence.get("cohesion"), 1.0),
                conflict_count=_to_int(evidence.get("conflict_count"), 0),
                first_ts=evidence.get("first_ts"),
                last_ts=evidence.get("last_ts"),
                estimated=True,
                raw=evidence,
            )

        count = _to_int(evidence.get("count"), 0)
        visit_count = _to_int(evidence.get("visit_count"), 0)
        return cls(
            kind=str(evidence.get("kind") or "human"),
            room=str(evidence.get("room") or ""),
            person_name=evidence.get("person_name"),
            count=count,
            visit_count=visit_count,
            reinforcement_count=_to_int(
                evidence.get("reinforcement_count"), max(0, count - visit_count)
            ),
            distinct_days=max(1, _to_int(evidence.get("distinct_days"), 1)),
            cross_day=bool(evidence.get("cross_day", False)),
            avg_confidence=_to_float(evidence.get("avg_confidence")),
            max_confidence=_to_float(evidence.get("max_confidence")),
            cohesion=clamp01(_to_float(evidence.get("cohesion"), 1.0)),
            conflict_count=_to_int(evidence.get("conflict_count"), 0),
            first_ts=evidence.get("first_ts"),
            last_ts=evidence.get("last_ts"),
            estimated=False,
            raw=evidence,
        )


@dataclass(frozen=True)
class DimensionScore:
    """单个评分维度的可解释结果。"""

    name: str
    label: str
    raw: float
    score: float
    weight: float
    note: str

    @property
    def weighted(self) -> float:
        """该维度对总分（百分制）的贡献。"""
        return clamp01(self.score) * self.weight * 100.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "raw": round(self.raw, 4),
            "score": round(clamp01(self.score), 4),
            "weight": round(self.weight, 4),
            "weighted": round(self.weighted, 2),
            "note": self.note,
        }


@dataclass(frozen=True)
class ScoreBreakdown:
    """评分拆解：维度贡献 + 冲突惩罚 + 总分。"""

    dimensions: Tuple[DimensionScore, ...] = ()
    conflict_count: int = 0
    penalty_points: float = 0.0

    @property
    def subtotal(self) -> float:
        return sum(d.weighted for d in self.dimensions)

    @property
    def total(self) -> float:
        return max(0.0, self.subtotal - self.penalty_points)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": round(self.total, 2),
            "subtotal": round(self.subtotal, 2),
            "penalty_points": round(self.penalty_points, 2),
            "conflict_count": self.conflict_count,
            "dimensions": [d.to_dict() for d in self.dimensions],
        }

    def explain(self, threshold: Optional[float] = None) -> str:
        """渲染成多行人话解释，便于日志 / 前端展示。"""
        lines = [f"综合得分 {self.total:.1f}" + (f" / 阈值 {threshold:.1f}" if threshold is not None else "")]
        for dim in sorted(self.dimensions, key=lambda d: -d.weighted):
            lines.append(
                f"  [+] {dim.label:<8} {dim.score:.2f} × {dim.weight:.2f} "
                f"= {dim.weighted:5.1f} 分  ({dim.note})"
            )
        if self.conflict_count > 0:
            lines.append(
                f"  [-] 冲突惩罚 {self.penalty_points:.1f} 分  "
                f"(冲突证据 {self.conflict_count} 条)"
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class EvaluationResult:
    """一次晋升判定的完整结果。"""

    promoted: bool
    score: float
    threshold: float
    breakdown: ScoreBreakdown
    reasons: Tuple[str, ...] = ()
    blocking: Tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)
    estimated: bool = False
    policy: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "promoted": self.promoted,
            "score": round(self.score, 2),
            "threshold": self.threshold,
            "reasons": list(self.reasons),
            "blocking": list(self.blocking),
            "estimated": self.estimated,
            "breakdown": self.breakdown.to_dict(),
            "policy": dict(self.policy),
        }


# ---------------------------------------------------------------------------
# 评分模型
# ---------------------------------------------------------------------------
class ScoringModel:
    """多维可解释评分模型。

    纯函数式设计：``score(evidence, policy)`` 只依赖输入证据与策略配置，
    不访问存储、不产生副作用，便于单元测试与离线复算。
    """

    def score(self, evidence: EvidenceView, policy: PromotionPolicy) -> ScoreBreakdown:
        weights = policy.weights.normalized()
        dims: List[DimensionScore] = []

        # 1) 事件重要性：来自类型基线配置
        importance = clamp01(
            policy.kind_importance.get(evidence.kind, policy.default_importance)
        )
        dims.append(DimensionScore(
            name="importance", label="事件重要性", raw=importance, score=importance,
            weight=weights.importance,
            note=f"类型 {evidence.kind} 的重要性基线 {importance:.2f}",
        ))

        # 2) 置信可信度：平均置信度在 [floor, ceiling] 上线性映射
        span = max(1e-6, policy.credibility_ceiling - policy.credibility_floor)
        credibility = clamp01((evidence.avg_confidence - policy.credibility_floor) / span)
        dims.append(DimensionScore(
            name="credibility", label="置信可信度", raw=evidence.avg_confidence,
            score=credibility, weight=weights.credibility,
            note=f"平均置信度 {evidence.avg_confidence:.2f}（峰值 {evidence.max_confidence:.2f}）",
        ))

        # 3) 证据频次：证据条数的对数饱和
        frequency = log_saturate(evidence.count, policy.evidence_saturation)
        dims.append(DimensionScore(
            name="frequency", label="证据频次", raw=float(evidence.count),
            score=frequency, weight=weights.frequency,
            note=f"证据 {evidence.count} 条，满分需 {policy.evidence_saturation} 条",
        ))

        # 4) 到访次数：独立会话数的对数饱和（避免单次密集采样刷分）
        access = log_saturate(evidence.visit_count, policy.access_saturation)
        dims.append(DimensionScore(
            name="access", label="到访次数", raw=float(evidence.visit_count),
            score=access, weight=weights.access,
            note=f"独立到访 {evidence.visit_count} 次（间隔 > {policy.session_gap_minutes} 分钟算新到访）",
        ))

        # 5) 重复强化：同一到访内的重复观测
        reinforcement = log_saturate(evidence.reinforcement_count, policy.reinforcement_saturation)
        dims.append(DimensionScore(
            name="reinforcement", label="重复强化", raw=float(evidence.reinforcement_count),
            score=reinforcement, weight=weights.reinforcement,
            note=f"重复强化 {evidence.reinforcement_count} 次，满分需 {policy.reinforcement_saturation} 次",
        ))

        # 6) 跨天跨度：1 天得 0 分，day_saturation 天得满分
        day_den = max(1, policy.day_saturation - 1)
        time_span = clamp01((evidence.distinct_days - 1) / day_den)
        dims.append(DimensionScore(
            name="time_span", label="跨天跨度", raw=float(evidence.distinct_days),
            score=time_span, weight=weights.time_span,
            note=f"覆盖 {evidence.distinct_days} 个自然日，满分需 {policy.day_saturation} 天",
        ))

        # 7) 身份内聚：证据主体一致程度（同人同房，无身份漂移）
        cohesion = clamp01(evidence.cohesion)
        dims.append(DimensionScore(
            name="cohesion", label="身份内聚", raw=evidence.cohesion,
            score=cohesion, weight=weights.cohesion,
            note=f"主体一致率 {evidence.cohesion:.2f}",
        ))

        penalty = min(
            policy.max_conflict_penalty,
            evidence.conflict_count * policy.conflict_penalty_per_event,
        )
        return ScoreBreakdown(
            dimensions=tuple(dims),
            conflict_count=evidence.conflict_count,
            penalty_points=penalty,
        )


# ---------------------------------------------------------------------------
# 缓存层
# ---------------------------------------------------------------------------
class CacheBackend(Protocol):
    """缓存后端协议：字符串 KV + TTL。"""

    def get(self, key: str) -> Optional[str]: ...
    def set(self, key: str, value: str, ttl: int) -> None: ...
    def delete(self, key: str) -> None: ...
    def clear(self) -> None: ...


class MemoryCacheBackend:
    """线程安全的内存缓存（带 TTL 与容量上限的近似 LRU）。"""

    def __init__(self, max_entries: int = 2048, time_func=None):
        self.max_entries = max(1, int(max_entries))
        self._time = time_func or time.time
        self._data: "OrderedDict[str, Tuple[float, str]]" = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[str]:
        now = self._time()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at and expires_at <= now:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: str, ttl: int) -> None:
        expires_at = self._time() + ttl if ttl and ttl > 0 else 0.0
        with self._lock:
            self._data[key] = (expires_at, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


class RedisCacheBackend:
    """Redis 缓存后端；任何异常都自动降级到内存后端，绝不影响主流程。

    ``client`` 可注入（便于测试）；不传则尝试 ``redis.from_url`` 创建。
    创建失败（未安装 redis / 无法连接）时直接进入降级模式。
    """

    def __init__(self, client: Any = None, url: Optional[str] = None,
                 fallback: Optional[MemoryCacheBackend] = None):
        self.fallback = fallback or MemoryCacheBackend()
        self._client = client
        self._degraded = client is None and not url
        self.degraded_reason: Optional[str] = None
        if self._client is None and url:
            try:
                import redis  # type: ignore
                self._client = redis.from_url(url, socket_timeout=1.0)
            except Exception as exc:  # 未安装 / 配置错误
                self._degraded = True
                self.degraded_reason = f"{type(exc).__name__}: {exc}"
                logger.warning("Redis 缓存初始化失败，降级为内存缓存: %s", exc)

    # -- 内部：故障降级 -----------------------------------------------------
    def _downgrade(self, exc: Exception) -> None:
        if not self._degraded:
            logger.warning("Redis 缓存不可用，降级为内存缓存: %s", exc)
        self._degraded = True
        self.degraded_reason = f"{type(exc).__name__}: {exc}"

    def get(self, key: str) -> Optional[str]:
        if self._degraded:
            return self.fallback.get(key)
        try:
            return self._client.get(key)
        except Exception as exc:
            self._downgrade(exc)
            return self.fallback.get(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        if self._degraded:
            self.fallback.set(key, value, ttl)
            return
        try:
            if ttl and ttl > 0:
                self._client.setex(key, ttl, value)
            else:
                self._client.set(key, value)
        except Exception as exc:
            self._downgrade(exc)
            self.fallback.set(key, value, ttl)

    def delete(self, key: str) -> None:
        if self._degraded:
            self.fallback.delete(key)
            return
        try:
            self._client.delete(key)
        except Exception as exc:
            self._downgrade(exc)
            self.fallback.delete(key)

    def clear(self) -> None:
        self.fallback.clear()
        if self._degraded:
            return
        try:
            self._client.delete(*[])  # 无操作占位：不主动 FLUSHDB
        except Exception as exc:
            self._downgrade(exc)


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    errors: int = 0

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"hits": self.hits, "misses": self.misses,
                "errors": self.errors, "hit_rate": round(self.hit_rate, 4)}


class EvidenceCache:
    """证据缓存门面：JSON 序列化、命名空间前缀、统计、异常兜底。

    任何缓存异常都只计入 ``errors``，绝不上抛——缓存只是加速器。
    """

    def __init__(self, backend: Optional[CacheBackend] = None,
                 namespace: str = "cand_promo:v2"):
        self.backend = backend or MemoryCacheBackend()
        self.namespace = namespace
        self.stats = CacheStats()
        self._lock = threading.RLock()

    def build_key(self, kind: str, room: str, person_name: Optional[str],
                  window_hours: int) -> str:
        person = person_name or "-"
        return f"{self.namespace}:evidence:{kind}:{room}:{person}:{window_hours}"

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            raw = self.backend.get(key)
        except Exception as exc:
            with self._lock:
                self.stats.errors += 1
            logger.debug("缓存读取失败（忽略）: %s", exc)
            return None
        if raw is None:
            with self._lock:
                self.stats.misses += 1
            return None
        try:
            data = json.loads(raw)
        except Exception:
            with self._lock:
                self.stats.misses += 1
            return None
        if not isinstance(data, dict):
            with self._lock:
                self.stats.misses += 1
            return None
        with self._lock:
            self.stats.hits += 1
        return data

    def set_json(self, key: str, value: Mapping[str, Any], ttl: int) -> None:
        try:
            self.backend.set(key, _json_dumps(dict(value)), ttl)
        except Exception as exc:
            with self._lock:
                self.stats.errors += 1
            logger.debug("缓存写入失败（忽略）: %s", exc)

    def invalidate(self, key: str) -> None:
        try:
            self.backend.delete(key)
        except Exception as exc:
            with self._lock:
                self.stats.errors += 1
            logger.debug("缓存删除失败（忽略）: %s", exc)

    def clear(self) -> None:
        try:
            self.backend.clear()
        except Exception as exc:
            with self._lock:
                self.stats.errors += 1
            logger.debug("缓存清空失败（忽略）: %s", exc)


def create_cache(backend: str = "memory", *, url: Optional[str] = None,
                 client: Any = None, max_entries: int = 2048,
                 namespace: str = "cand_promo:v2") -> EvidenceCache:
    """缓存工厂：``backend`` 取 ``"memory"`` 或 ``"redis"``。

    Redis 创建失败自动降级为内存后端，因此本函数保证总是返回可用实例。
    """
    if backend == "redis":
        return EvidenceCache(RedisCacheBackend(client=client, url=url), namespace=namespace)
    return EvidenceCache(MemoryCacheBackend(max_entries=max_entries), namespace=namespace)


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AuditEvent:
    """一次晋升决策的审计事件。"""

    decision: str                      # promoted / rejected / direct / error
    kind: str = ""
    room: str = ""
    person_name: Optional[str] = None
    score: float = 0.0
    threshold: float = 0.0
    behavior_id: int = 0
    breakdown: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    reasons: Sequence[str] = ()
    blocking: Sequence[str] = ()
    source: str = "perception_ingest"
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "ts": self.ts,
            "decision": self.decision,
            "kind": self.kind,
            "room": self.room,
            "person_name": self.person_name,
            "score": round(float(self.score), 2),
            "threshold": round(float(self.threshold), 2),
            "behavior_id": int(self.behavior_id or 0),
            "breakdown": dict(self.breakdown),
            "evidence": dict(self.evidence),
            "reasons": list(self.reasons),
            "blocking": list(self.blocking),
            "source": self.source,
        }


class AuditSink(Protocol):
    def record(self, event: Mapping[str, Any]) -> None: ...


class MemoryAuditSink:
    """内存审计接收器（测试 / 降级兜底）。"""

    def __init__(self, max_events: int = 500):
        self.max_events = max_events
        self.events: List[Dict[str, Any]] = []
        self._lock = threading.RLock()

    def record(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            self.events.append(dict(event))
            if len(self.events) > self.max_events:
                del self.events[: len(self.events) - self.max_events]

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.events[-limit:])


class StoreAuditSink:
    """把审计事件写入存储层的 ``promotion_audit`` 表。

    失败时自动降级到内存接收器并只告警一次，绝不影响晋升主流程。
    如果 store 提供了 ``insert_promotion_audit`` 钩子，则优先使用。
    """

    DDL = """
    CREATE TABLE IF NOT EXISTS promotion_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        ts TEXT NOT NULL,
        decision TEXT NOT NULL,
        kind TEXT,
        room TEXT,
        person_name TEXT,
        score REAL,
        threshold REAL,
        behavior_id INTEGER,
        breakdown_json TEXT,
        evidence_json TEXT,
        reasons_json TEXT,
        policy_json TEXT
    )
    """

    INSERT = """
    INSERT INTO promotion_audit
        (event_id, ts, decision, kind, room, person_name, score, threshold,
         behavior_id, breakdown_json, evidence_json, reasons_json, policy_json)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def __init__(self, store: Any, fallback: Optional[MemoryAuditSink] = None):
        self.store = store
        self.fallback = fallback or MemoryAuditSink()
        self._table_ready = False
        self._degraded = False
        self._lock = threading.RLock()

    def record(self, event: Mapping[str, Any]) -> None:
        data = dict(event)
        hook = getattr(self.store, "insert_promotion_audit", None)
        if callable(hook):
            try:
                hook(data)
                return
            except Exception as exc:
                self._downgrade(exc)
                self.fallback.record(data)
                return
        try:
            conn = self.store.connect()
            with self._lock:
                if not self._table_ready:
                    conn.execute(self.DDL)
                    self._table_ready = True
            conn.execute(self.INSERT, (
                data.get("event_id"), data.get("ts"), data.get("decision"),
                data.get("kind"), data.get("room"), data.get("person_name"),
                data.get("score"), data.get("threshold"), data.get("behavior_id"),
                _json_dumps(data.get("breakdown", {})),
                _json_dumps(data.get("evidence", {})),
                _json_dumps(data.get("reasons", [])),
                _json_dumps(data.get("policy", {})),
            ))
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()
        except Exception as exc:
            self._downgrade(exc)
            self.fallback.record(data)

    def _downgrade(self, exc: Exception) -> None:
        if not self._degraded:
            logger.warning("审计写入存储失败，降级为内存审计: %s", exc)
            self._degraded = True


class AuditRecorder:
    """审计门面：统一入口 + 本地环形缓冲，便于运行时查看最近决策。"""

    def __init__(self, sink: Optional[AuditSink] = None, buffer_size: int = 200):
        self.sink = sink or MemoryAuditSink()
        self.buffer_size = buffer_size
        self._recent: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lock = threading.RLock()

    def record(self, event: AuditEvent) -> Dict[str, Any]:
        data = event.to_dict()
        with self._lock:
            self._recent[data["event_id"]] = data
            while len(self._recent) > self.buffer_size:
                self._recent.popitem(last=False)
        try:
            self.sink.record(data)
        except Exception as exc:  # 审计失败不能影响业务
            logger.warning("审计记录失败（已忽略）: %s", exc)
        return data

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._recent.values())[-limit:]


# ---------------------------------------------------------------------------
# 主体：候选区晋升器
# ---------------------------------------------------------------------------
class CandidatePromoter:
    """候选区事件晋升器。

    公共接口（与旧版保持兼容）：
      * :meth:`should_promote_directly`  —— 高置信 / 紧急事件是否直通
      * :meth:`collect_evidence`         —— 收集证据（返回 dict，含 count/cross_day）
      * :meth:`check_promotion_criteria` —— 是否满足晋升条件（返回 bool）
      * :meth:`promote_to_behavior`      —— 写入 behavior_events（返回 id，失败 0）
      * :meth:`process_candidates`       —— 批量扫描候选区并晋升（返回统计 dict）

    新增接口：
      * :meth:`evaluate`                 —— 返回带评分拆解的完整判定结果
    """

    def __init__(self, store: Any, policy: Optional[PromotionPolicy] = None,
                 cache: Optional[EvidenceCache] = None,
                 audit: Optional[AuditRecorder] = None,
                 scoring: Optional[ScoringModel] = None):
        self.store = store
        self.policy = policy or PromotionPolicy()
        self.scoring = scoring or ScoringModel()
        problems = self.policy.validate()
        if problems:
            logger.warning("晋升策略配置存在异常（使用默认行为继续运行）: %s", problems)

        # 缓存默认开启（内存后端）；传入 None 字段表示显式禁用
        self._cache = cache
        if cache is None:
            self._cache = create_cache("memory")
        self.audit = audit or AuditRecorder(StoreAuditSink(store))
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # 直通判定
    # ------------------------------------------------------------------
    def should_promote_directly(self, kind: Any, confidence: Any = 1.0,
                                payload: Optional[Mapping[str, Any]] = None) -> bool:
        """判断是否绕过候选区直接晋升。

        兼容说明：旧调用方可能以 ``(kind, confidence)`` 位置参数调用，
        也可能把整个事件 dict 作为第一个参数传入，两种写法都支持。

        规则：
          1. 紧急事件（``policy.direct_promote_kinds``，如哭声 / 婴儿醒来）
             且置信度 >= ``high_confidence_threshold``；
          2. 任意事件置信度 >= ``direct_promotion_confidence``（默认 0.95）。
        """
        # 兼容：第一个参数是事件字典
        if isinstance(kind, Mapping):
            event = dict(kind)
            kind = event.get("kind", "human")
            if confidence is None or isinstance(confidence, Mapping):
                confidence = event.get("confidence", 1.0)
            payload = payload or event.get("payload")

        conf = _to_float(confidence, 0.0)
        kind = str(kind or "human")

        if kind in self.policy.direct_promote_kinds:
            return conf >= self.policy.high_confidence_threshold
        return conf >= self.policy.direct_promotion_confidence

    # ------------------------------------------------------------------
    # 证据收集
    # ------------------------------------------------------------------
    def collect_evidence(self, kind: str, room: str,
                         person_name: Optional[str] = None, *,
                         window_hours: Optional[int] = None,
                         use_cache: bool = True) -> Dict[str, Any]:
        """收集 ``(kind, room, person_name)`` 在窗口内的重复证据。

        返回值**保持向后兼容**：一定包含 ``count``（int）与 ``cross_day``（bool），
        同时附带富化字段（``evidence_version=2`` 标记），供评分模型使用。

        主要字段：
          count / visit_count / reinforcement_count / distinct_days / cross_day /
          avg_confidence / max_confidence / cohesion / conflict_count /
          first_ts / last_ts / span_hours / source_refs / cache_hit
        """
        policy = self.policy
        window_hours = int(window_hours or policy.evidence_window_hours)
        cache_key = self._cache.build_key(kind, room, person_name, window_hours) \
            if self._cache else None

        if use_cache and cache_key:
            cached = self._cache.get_json(cache_key)
            if cached is not None:
                cached = dict(cached)
                cached["cache_hit"] = True
                return cached

        rows = self._load_candidate_rows(kind, room, window_hours)
        bundle = self._build_evidence(kind, room, person_name, rows, window_hours)
        bundle["cache_hit"] = False

        if use_cache and cache_key:
            self._cache.set_json(cache_key, bundle, ttl=policy.cache_ttl_seconds)
        return bundle

    def _load_candidate_rows(self, kind: str, room: str,
                             window_hours: int) -> List[Dict[str, Any]]:
        """读取候选区内同房间、同类型 + 冲突类型的原始行。"""
        kinds = (kind, *tuple(self.policy.conflict_kinds.get(kind, ())))
        placeholders = ",".join("?" for _ in kinds)
        cutoff = (datetime.now() - timedelta(hours=window_hours)).isoformat()
        sql = (
            "SELECT server_ts, kind, room, confidence, payload_json "
            "FROM perception_events "
            f"WHERE room = ? AND server_ts >= ? AND kind IN ({placeholders}) "
            "ORDER BY server_ts ASC "
            "LIMIT ?"
        )
        params: Tuple[Any, ...] = (room, cutoff, *kinds, self.policy.evidence_limit)
        try:
            conn = self.store.connect()
            rows = conn.execute(sql, params).fetchall()
        except Exception as exc:
            logger.error("读取候选区证据失败: %s", exc)
            return []
        return [dict(r) if isinstance(r, Mapping) else r for r in rows]

    @staticmethod
    def _person_label(kind: str, payload: Mapping[str, Any]) -> Optional[str]:
        """统一主体标签，用于匹配与内聚度计算。"""
        payload = payload or {}
        if kind == "face_known":
            return payload.get("person_name") or "熟人"
        if kind == "face_unknown":
            return "陌生人"
        return payload.get("person_name")

    @classmethod
    def _person_matches(cls, kind: str, person_name: Optional[str],
                        label: Optional[str]) -> bool:
        if person_name is None:
            return True
        if kind == "face_unknown":      # 陌生人是统称
            return True
        if person_name == label:
            return True
        return person_name == "熟人" and kind == "face_known" and label in (None, "熟人")

    def _build_evidence(self, kind: str, room: str, person_name: Optional[str],
                        rows: Sequence[Any], window_hours: int) -> Dict[str, Any]:
        """把原始行折叠成证据包（全部为可 JSON 序列化结构）。"""
        matched_rows: List[Dict[str, Any]] = []
        scope_rows: List[Dict[str, Any]] = []      # 同类型全部行（用于内聚度）
        conflict_rows: List[Dict[str, Any]] = []   # 冲突类型行
        label_counts: Dict[Optional[str], int] = {}

        for row in rows:
            row_kind = str(_row_get(row, "kind") or "")
            payload = _as_mapping(_row_get(row, "payload_json"))
            label = self._person_label(row_kind, payload)
            item = {
                "ts": _row_get(row, "server_ts"),
                "confidence": _to_float(_row_get(row, "confidence")),
                "label": label,
            }
            if row_kind == kind:
                scope_rows.append(item)
                label_counts[label] = label_counts.get(label, 0) + 1
                if self._person_matches(kind, person_name, label):
                    matched_rows.append(item)
            elif row_kind in tuple(self.policy.conflict_kinds.get(kind, ())):
                conflict_rows.append(item)

        count = len(matched_rows)
        confidences = [r["confidence"] for r in matched_rows]
        timestamps = sorted(filter(None, (r["ts"] for r in matched_rows)))
        days = sorted({str(ts)[:10] for ts in timestamps if ts})
        distinct_days = max(1, len(days)) if days else 1

        # 到访次数：按 session_gap_minutes 切分会话
        parsed = sorted(filter(None, (_parse_ts(ts) for ts in timestamps)))
        visit_count = 0
        previous: Optional[datetime] = None
        gap = timedelta(minutes=self.policy.session_gap_minutes)
        for moment in parsed:
            if previous is None or (moment - previous) > gap:
                visit_count += 1
            previous = moment

        # 身份内聚度：主体标签的主导占比
        if scope_rows:
            dominant = max(label_counts.values()) if label_counts else 0
            cohesion = dominant / len(scope_rows)
        else:
            cohesion = 1.0

        span_hours = 0.0
        if len(parsed) >= 2:
            span_hours = (parsed[-1] - parsed[0]).total_seconds() / 3600.0

        cross_day = len(days) >= self.policy.min_distinct_days

        bundle: Dict[str, Any] = {
            "evidence_version": 2,
            "kind": kind,
            "room": room,
            "person_name": person_name,
            "window_hours": window_hours,
            # —— 旧版兼容字段 ——
            "count": count,
            "cross_day": cross_day,
            # —— 富化字段 ——
            "days": days,
            "distinct_days": len(days) if days else 1,
            "visit_count": visit_count,
            "reinforcement_count": max(0, count - visit_count),
            "confidences": [round(c, 4) for c in confidences],
            "avg_confidence": round(sum(confidences) / count, 4) if count else 0.0,
            "max_confidence": round(max(confidences), 4) if count else 0.0,
            "min_confidence": round(min(confidences), 4) if count else 0.0,
            "cohesion": round(cohesion, 4),
            "conflict_count": len(conflict_rows),
            "first_ts": timestamps[0] if timestamps else None,
            "last_ts": timestamps[-1] if timestamps else None,
            "span_hours": round(span_hours, 3),
            "source_refs": [f"perception:{ts}:{kind}" for ts in timestamps],
            "collected_at": _now_iso(),
        }
        return bundle

    # ------------------------------------------------------------------
    # 晋升判定
    # ------------------------------------------------------------------
    def evaluate(self, evidence: Mapping[str, Any]) -> EvaluationResult:
        """完整的可解释晋升判定（评分 + 硬门槛）。

        硬门槛（任一不满足即阻断）：
          1. ``count >= policy.min_evidence_count``
          2. ``require_cross_day`` 时 ``distinct_days >= policy.min_distinct_days``
          3. 综合得分 ``>= policy.promotion_threshold``

        对旧版精简字典（无 ``evidence_version``）：默认 ``legacy_gate_only=True``，
        只应用硬门槛 1、2，与旧版行为完全一致；评分数值仍然计算并写入解释，
        便于观察。设置 ``policy.legacy_gate_only=False`` 可让旧字典也走评分。
        """
        policy = self.policy
        view = EvidenceView.from_mapping(evidence)
        breakdown = self.scoring.score(view, policy)
        score = breakdown.total

        blocking: List[str] = []
        reasons: List[str] = []

        # 硬门槛 1：最小证据数
        if view.count < policy.min_evidence_count:
            blocking.append(f"证据 {view.count} 条 < 最小证据数 {policy.min_evidence_count}")
        else:
            reasons.append(f"证据 {view.count} 条 ≥ 最小证据数 {policy.min_evidence_count}")

        # 硬门槛 2：跨天要求
        if policy.require_cross_day and view.distinct_days < policy.min_distinct_days:
            blocking.append(
                f"仅覆盖 {view.distinct_days} 天 < 要求的 {policy.min_distinct_days} 天（需要跨天重复出现）"
            )
        elif policy.require_cross_day:
            reasons.append(f"覆盖 {view.distinct_days} 天 ≥ 要求的 {policy.min_distinct_days} 天")

        legacy_gate = bool(view.estimated and policy.legacy_gate_only)

        # 硬门槛 3：综合得分（兼容模式下对旧字典跳过）
        if legacy_gate:
            reasons.append("兼容模式：旧版证据字典仅应用硬门槛，评分仅供参考")
        elif score < policy.promotion_threshold:
            blocking.append(f"综合得分 {score:.1f} < 晋升阈值 {policy.promotion_threshold:.1f}")
        else:
            reasons.append(f"综合得分 {score:.1f} ≥ 晋升阈值 {policy.promotion_threshold:.1f}")

        if breakdown.conflict_count > 0:
            reasons.append(
                f"发现 {breakdown.conflict_count} 条冲突证据，扣减 {breakdown.penalty_points:.1f} 分"
            )

        return EvaluationResult(
            promoted=not blocking,
            score=score,
            threshold=policy.promotion_threshold,
            breakdown=breakdown,
            reasons=tuple(reasons),
            blocking=tuple(blocking),
            evidence=dict(evidence),
            estimated=view.estimated,
            policy=policy.snapshot(),
        )

    def check_promotion_criteria(self, evidence: Mapping[str, Any]) -> bool:
        """是否满足晋升条件（**返回 bool，签名与旧版一致**）。

        需要评分细节时请使用 :meth:`evaluate`。
        """
        try:
            return self.evaluate(evidence).promoted
        except Exception as exc:
            logger.error("晋升条件判断失败: %s", exc)
            return False

    # ------------------------------------------------------------------
    # 晋升写入
    # ------------------------------------------------------------------
    def promote_to_behavior(self, kind: str, room: str,
                            person_name: Optional[str], server_ts: str,
                            confidence: float, payload: Optional[Mapping[str, Any]],
                            *, evaluation: Optional[EvaluationResult] = None,
                            source_refs: Optional[Sequence[str]] = None,
                            audit_decision: str = "promoted") -> int:
        """把候选事件晋升为 behavior_events 记录。

        返回 behavior_events 的主键 id，失败返回 0（**与旧版一致**）。
        每次调用（成功或失败）都会写一条审计事件。
        """
        behavior_id = 0
        try:
            payload = dict(payload or {})
            source_refs = list(source_refs or [f"perception:candidate:{kind}"])
            member_id = payload.get("member_id")

            persons = [{
                "name": person_name,
                "member_id": member_id,
                "detail": payload,
            }]

            behavior_id = int(self.store.insert_behavior_event({
                "server_ts": server_ts,
                "room": room,
                "kind": kind,
                "action": self._kind_to_action(kind),
                "persons": persons,
                "source": "perception_ingest",
                "source_refs": source_refs,
                "confidence": confidence,
                "payload": payload,
            }) or 0)

            self._write_audit(
                decision=audit_decision, kind=kind, room=room, person_name=person_name,
                score=evaluation.score if evaluation else 0.0,
                threshold=evaluation.threshold if evaluation else self.policy.promotion_threshold,
                evaluation=evaluation, behavior_id=behavior_id,
            )
            return behavior_id

        except Exception as exc:
            logger.error(f"晋升候选事件到 behavior_events 失败: {exc}")
            self._write_audit(
                decision="error", kind=kind, room=room, person_name=person_name,
                score=evaluation.score if evaluation else 0.0,
                threshold=evaluation.threshold if evaluation else self.policy.promotion_threshold,
                evaluation=evaluation, behavior_id=0, error=str(exc),
            )
            return 0

    def _write_audit(self, *, decision: str, kind: str, room: str,
                     person_name: Optional[str], score: float, threshold: float,
                     evaluation: Optional[EvaluationResult],
                     behavior_id: int, error: Optional[str] = None) -> None:
        """构造并写入审计事件（内部使用，异常不上抛）。"""
        try:
            evidence = dict(evaluation.evidence) if evaluation else {}
            breakdown = evaluation.breakdown.to_dict() if evaluation else {}
            reasons = list(evaluation.reasons) if evaluation else []
            blocking = list(evaluation.blocking) if evaluation else []
            if error:
                blocking = [*blocking, f"写入失败: {error}"]
            self.audit.record(AuditEvent(
                decision=decision,
                kind=kind,
                room=room,
                person_name=person_name,
                score=score,
                threshold=threshold,
                behavior_id=behavior_id,
                breakdown=breakdown,
                evidence=evidence,
                reasons=reasons,
                blocking=blocking,
            ))
        except Exception as exc:  # 审计永远不能影响业务
            logger.warning("写入晋升审计失败（已忽略）: %s", exc)

    def _kind_to_action(self, kind: str) -> str:
        """kind 映射到 action 描述。"""
        mapping = {
            "human": "有人出现",
            "face_known": "熟人出现",
            "face_unknown": "陌生人出现",
            "pet": "宠物出现",
            "cry": "婴儿哭声",
            "baby_woke": "婴儿醒来",
        }
        return mapping.get(kind, kind)

    def _already_promoted(self, kind: str, room: str, server_ts: str,
                          person_name: Optional[str]) -> bool:
        """判断该候选是否在证据窗口内已晋升过（避免重复写入长期记忆）。

        存储层不支持该查询时静默返回 False，退化为不跳过。
        """
        moment = _parse_ts(server_ts)
        if moment is None:
            return False
        cutoff = (moment - timedelta(hours=self.policy.evidence_window_hours)).isoformat()
        try:
            conn = self.store.connect()
            rows = conn.execute(
                "SELECT server_ts, payload_json, persons FROM behavior_events "
                "WHERE kind = ? AND room = ? AND source = ? AND server_ts >= ? "
                "ORDER BY server_ts DESC LIMIT 50",
                (kind, room, "perception_ingest", cutoff),
            ).fetchall()
        except Exception:
            return False

        for row in rows:
            payload = _as_mapping(_row_get(row, "payload_json"))
            label = self._person_label(kind, payload)
            if self._person_matches(kind, person_name, label):
                return True
        return False

    # ------------------------------------------------------------------
    # 批量处理
    # ------------------------------------------------------------------
    def process_candidates(self) -> Dict[str, int]:
        """定期扫描候选区，晋升满足条件的事件。

        返回统计（前三个键与旧版完全一致，其余为新增观测指标）：

        {
            "scanned": int,        # 扫描到的候选行数
            "promoted": int,       # 成功写入 behavior_events 的数量
            "failed": int,         # 写入失败的数量
            "rejected": int,       # 未满足晋升条件的数量
            "skipped": int,        # 去重 / 已晋升而跳过的数量
            "direct": int,        # 高置信直通的数量（也计入 promoted）
            "cache_hits": int,     # 证据缓存命中次数
        }
        """
        stats = {
            "scanned": 0, "promoted": 0, "failed": 0,
            "rejected": 0, "skipped": 0, "direct": 0, "cache_hits": 0,
        }

        try:
            conn = self.store.connect()
            # 扫描最近 24 小时的低置信度候选事件
            cutoff = (datetime.now() - timedelta(hours=self.policy.scan_window_hours)).isoformat()

            rows = conn.execute(
                """
                SELECT DISTINCT kind, room, payload_json, server_ts, confidence
                FROM perception_events
                WHERE server_ts >= ?
                  AND confidence < ?
                ORDER BY server_ts DESC
                LIMIT ?
                """,
                (cutoff, self.policy.high_confidence_threshold, self.policy.scan_limit),
            ).fetchall()

            stats["scanned"] = len(rows)
            seen: set = set()

            for row in rows:
                kind = str(_row_get(row, "kind") or "human")
                room = str(_row_get(row, "room") or "")
                payload = _as_mapping(_row_get(row, "payload_json"))
                confidence = _to_float(_row_get(row, "confidence"))
                server_ts = str(_row_get(row, "server_ts") or _now_iso())

                # 提取 person_name（与旧版保持一致的默认值）
                person_name = None
                if kind == "face_known":
                    person_name = payload.get("person_name") or "熟人"
                elif kind == "face_unknown":
                    person_name = "陌生人"

                fingerprint = f"{kind}|{room}|{person_name}"
                if self.policy.dedupe_within_run and fingerprint in seen:
                    stats["skipped"] += 1
                    continue
                seen.add(fingerprint)

                if (self.policy.skip_already_promoted
                        and self._already_promoted(kind, room, server_ts, person_name)):
                    stats["skipped"] += 1
                    continue

                # 高置信 / 紧急事件直通
                if self.should_promote_directly(kind, confidence, payload):
                    bid = self.promote_to_behavior(
                        kind, room, person_name, server_ts, confidence, payload,
                        audit_decision="direct",
                    )
                    if bid > 0:
                        stats["promoted"] += 1
                        stats["direct"] += 1
                    else:
                        stats["failed"] += 1
                    continue

                # 收集证据并做可解释判定
                evidence = self.collect_evidence(kind, room, person_name)
                if evidence.get("cache_hit"):
                    stats["cache_hits"] += 1

                result = self.evaluate(evidence)
                if result.promoted:
                    bid = self.promote_to_behavior(
                        kind, room, person_name, server_ts, confidence, payload,
                        evaluation=result,
                        source_refs=evidence.get("source_refs"),
                    )
                    if bid > 0:
                        stats["promoted"] += 1
                        logger.info(
                            f"候选区晋升: {kind} in {room} by {person_name} "
                            f"(证据数={evidence['count']}, 跨天={evidence['cross_day']}, "
                            f"得分={result.score:.1f}/{result.threshold:.1f})"
                        )
                        if logger.isEnabledFor(logging.DEBUG):
                            logger.debug("晋升评分解释:\n%s", result.breakdown.explain(result.threshold))
                    else:
                        stats["failed"] += 1
                else:
                    stats["rejected"] += 1
                    self._write_audit(
                        decision="rejected", kind=kind, room=room,
                        person_name=person_name, score=result.score,
                        threshold=result.threshold, evaluation=result, behavior_id=0,
                    )
                    logger.debug(
                        "候选未晋升: %s in %s by %s —— %s",
                        kind, room, person_name, "; ".join(result.blocking),
                    )

        except Exception as exc:
            logger.error(f"扫描候选区失败: {exc}")

        return stats

    # ------------------------------------------------------------------
    # 运行时观测
    # ------------------------------------------------------------------
    def cache_stats(self) -> Dict[str, Any]:
        """缓存命中率等指标。"""
        return self._cache.stats.to_dict() if self._cache else {}

    def recent_audit(self, limit: int = 20) -> List[Dict[str, Any]]:
        """最近的审计事件（调试 / 管理接口用）。"""
        return self.audit.recent(limit)


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------
_promoter: Optional[CandidatePromoter] = None
_promoter_lock = threading.RLock()


def get_promoter(store: Any, policy: Optional[PromotionPolicy] = None,
                 cache: Optional[EvidenceCache] = None,
                 audit: Optional[AuditRecorder] = None) -> CandidatePromoter:
    """获取全局候选区晋升器单例（旧调用方只需传 store）。"""
    global _promoter
    with _promoter_lock:
        if _promoter is None:
            _promoter = CandidatePromoter(store, policy=policy, cache=cache, audit=audit)
        return _promoter


def reset_promoter() -> None:
    """重置单例（测试 / 热更新配置时使用）。"""
    global _promoter
    with _promoter_lock:
        _promoter = None
