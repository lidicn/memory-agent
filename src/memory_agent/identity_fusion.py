"""身份融合模块（v2.0）- 借鉴 MiMo 设计。

三路身份信号融合：
1. ArcFace 人脸识别（TV 端）
2. HA 人脸事件（米家摄像头）
3. 手动标记（用户手动确认）

设计原则：
- 融合内核为纯函数，IO 全部外置
- 手动标记分 CONFIRM/VOTE 两态
- 消除法降级为伪信号源
- 置信度双阈值 + margin 判据
- 学习只吸收高置信度样本
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ── 数据模型 ────────────────────────────────────────────────────────

class Source(Enum):
    """信号源。"""
    ARCFACE = "arcface"      # TV 端 ArcFace 人脸识别
    HA_FACE = "ha_face"      # Home Assistant 人脸事件
    MANUAL = "manual"        # 手动标记
    ELIMINATION = "elimination"  # 名册消除法（伪信号源）


class ManualMode(Enum):
    """手动标记模式。"""
    CONFIRM = "confirm"      # 确认：直接覆盖并作为训练标签
    VOTE = "vote"            # 投票：参与投票，权重提高


class Level(Enum):
    """判定等级。"""
    HIGH = "high"            # 高置信度，直接放行
    MED = "med"              # 中置信度，需要进一步验证
    LOW = "low"              # 低置信度，标记为未识别
    NEEDS_REVIEW = "needs_review"  # 需要人工确认


@dataclass(frozen=True)
class SignalEvidence:
    """信号证据。"""
    signal_id: str
    source: Source
    candidate_id: Optional[str]  # None = 陌生人
    confidence: float
    event_ts: float
    room_id: str
    manual_mode: Optional[ManualMode] = None
    raw_score: Optional[float] = None


@dataclass(frozen=True)
class CandidateScore:
    """候选者得分。"""
    candidate_id: str
    score_raw: float
    prior_boost: float
    score_final: float


@dataclass(frozen=True)
class Conflict:
    """冲突记录。"""
    rule: str
    status: str
    details: str = ""


@dataclass(frozen=True)
class FusionResult:
    """融合结果。"""
    chosen_id: Optional[str]  # None = 陌生人
    confidence: float
    level: Level
    method: str
    scores: list[CandidateScore]
    conflicts: list[Conflict]
    needs_review: bool
    degraded: bool
    degraded_reason: Optional[str] = None


# ── 配置 ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FusionConfig:
    """融合配置。"""
    # 信号权重
    weights: dict[Source, float] = field(default_factory=lambda: {
        Source.ARCFACE: 0.6,
        Source.HA_FACE: 0.3,
        Source.MANUAL: 0.1,
        Source.ELIMINATION: 0.2,
    })
    # 手动标记投票模式权重
    manual_vote_weight: float = 1.0
    # 手动标记模式
    manual_mode: str = "OVERRIDE"  # OVERRIDE | VOTE
    # 阈值
    high_threshold: float = 0.80
    med_threshold: float = 0.50
    # margin 判据
    margin_threshold: float = 0.15
    # 先验最小样本数
    prior_min_samples: int = 30
    # 无信号时的评审阈值
    review_floor_without_signal: float = 0.55
    # 时序衰减时间常数（秒）
    temporal_tau: float = 300.0
    # 房间匹配权重
    room_match: float = 1.0
    # 房间不匹配权重
    room_mismatch: float = 0.5


CFG = FusionConfig()


# ── 时序衰减 ────────────────────────────────────────────────────────

def temporal_decay(age_sec: float, tau: float = 300.0) -> float:
    """指数时序衰减。"""
    if age_sec <= 0:
        return 1.0
    return math.exp(-age_sec / tau)


def room_weight(signal_room: str, slot_room: str, cfg: FusionConfig = CFG) -> float:
    """房间匹配权重。"""
    if not signal_room or not slot_room:
        return 1.0
    return cfg.room_match if signal_room == slot_room else cfg.room_mismatch


# ── 先验模型 ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PriorLookup:
    """先验查询结果。"""
    posterior: dict[str, float]  # member_id -> P(member|hour, room)
    samples: int  # 样本数


def prior_boost(posterior: Optional[dict[str, float]], member_id: str,
                k: int, samples: int, cfg: FusionConfig = CFG) -> float:
    """先验修正项（log-odds）。"""
    if not posterior or k <= 1 or samples < cfg.prior_min_samples:
        return 0.0
    p = float(posterior.get(member_id, 0.0))
    if p <= 0:
        return 0.0
    # log-odds 修正
    beta = 0.25 * math.log(p * k)
    # 裁剪
    return max(-0.6, min(0.6, beta))


# ── 冲突规则 ────────────────────────────────────────────────────────

def evaluate_conflicts(signals: list[SignalEvidence], scores: list[CandidateScore],
                       cfg: FusionConfig = CFG) -> tuple[list[Conflict], Level]:
    """评估冲突规则。"""
    conflicts: list[Conflict] = []
    cap = Level.HIGH

    # R1: 人工确认
    manual_confirms = [s for s in signals
                       if s.source == Source.MANUAL and s.manual_mode == ManualMode.CONFIRM]
    if manual_confirms:
        conflicts.append(Conflict("R1", "AUTO_RESOLVED", "人工确认"))
        return conflicts, Level.HIGH

    # R2: 两路以上人脸信号对立
    face_sources = [s for s in signals if s.source in (Source.ARCFACE, Source.HA_FACE)]
    if len(face_sources) >= 2:
        face_ids = {s.candidate_id for s in face_sources if s.candidate_id}
        if len(face_ids) >= 2:
            conflicts.append(Conflict("R2", "SOURCE_DISAGREE", "两路人脸信号对立"))
            cap = Level.MED

    # R3: margin 太小
    if len(scores) >= 2:
        sorted_scores = sorted([s.score_final for s in scores], reverse=True)
        if sorted_scores[0] - sorted_scores[1] < cfg.margin_threshold:
            conflicts.append(Conflict("R3", "CLOSE_RACE", "分差太小"))
            cap = Level.NEEDS_REVIEW

    # R4: 陌生人判定
    stranger_signals = [s for s in signals if s.candidate_id is None]
    if stranger_signals:
        stranger_score = sum(s.confidence for s in stranger_signals) / len(stranger_signals)
        if stranger_score > 0.8:
            conflicts.append(Conflict("R4", "STRANGER_DETECTED", "陌生人检测"))
            cap = Level.LOW

    # R5: 房间不匹配
    room_mismatch_signals = [s for s in signals if s.room_id and s.room_id != signals[0].room_id]
    if room_mismatch_signals:
        conflicts.append(Conflict("R5", "ROOM_MISMATCH", "房间不匹配"))
        cap = min(cap, Level.MED, key=lambda x: list(Level).index(x))

    # R6: 仅推断/先验，无人脸观测
    face_observations = [s for s in signals if s.source in (Source.ARCFACE, Source.HA_FACE)]
    if not face_observations:
        conflicts.append(Conflict("R6", "NO_FACE_OBSERVATION", "无人脸观测"))
        cap = min(cap, Level.MED, key=lambda x: list(Level).index(x))

    return conflicts, cap


# ── 融合内核（纯函数）────────────────────────────────────────────────

def fuse(slot_room: str,
         signals: list[SignalEvidence],
         prior: Optional[PriorLookup] = None,
         roster: list[str] = None,
         cfg: FusionConfig = CFG,
         now: float = 0.0) -> FusionResult:
    """融合身份信号（纯函数，无 IO）。

    Args:
        slot_room: 当前房间
        signals: 信号列表
        prior: 先验查询结果
        roster: 成员名册
        cfg: 配置
        now: 当前时间戳

    Returns:
        融合结果
    """
    if roster is None:
        roster = []

    sigs = list(signals)
    W = cfg.weights.copy()

    # ── 人工确认短路 ──────────────────────────────────────────────
    confirms = [s for s in sigs
                if s.source == Source.MANUAL and s.manual_mode == ManualMode.CONFIRM]
    if confirms:
        chosen = confirms[0].candidate_id
        conflicts = [Conflict("R1", "AUTO_RESOLVED", "人工确认")]
        return FusionResult(
            chosen_id=chosen,
            confidence=1.0,
            level=Level.HIGH,
            method="MANUAL_OVERRIDE",
            scores=[CandidateScore(chosen or "stranger", 1.0, 0.0, 1.0)],
            conflicts=conflicts,
            needs_review=False,
            degraded=False,
        )

    # ── 手动标记投票模式权重提升 ──────────────────────────────────
    if cfg.manual_mode == "VOTE":
        W[Source.MANUAL] = cfg.manual_vote_weight

    # ── 按候选者分组计算得分 ──────────────────────────────────────
    candidates: dict[str, list[tuple[float, float]]] = {}  # candidate_id -> [(weight, confidence)]
    for s in sigs:
        if s.candidate_id is None:
            continue
        # 时序衰减
        age = now - s.event_ts if now > 0 else 0
        decay = temporal_decay(age, cfg.temporal_tau)
        # 房间权重
        rw = room_weight(s.room_id, slot_room, cfg)
        # 有效权重
        effective_weight = W.get(s.source, 0.1) * decay * rw
        # 加入候选者
        if s.candidate_id not in candidates:
            candidates[s.candidate_id] = []
        candidates[s.candidate_id].append((effective_weight, s.confidence))

    # ── 计算每个候选者的得分 ────────────────────────────────────────
    scores: list[CandidateScore] = []
    total_weight = sum(W.values())
    for member_id, pairs in candidates.items():
        score_raw = sum(w * c for w, c in pairs) / total_weight
        # 先验修正
        samples = prior.samples if prior else 0
        posterior = prior.posterior if prior else None
        boost = prior_boost(posterior, member_id, len(pairs), samples, cfg)
        score_final = score_raw + boost
        scores.append(CandidateScore(member_id, score_raw, boost, score_final))

    # ── 计算陌生人得分 ──────────────────────────────────────────────
    stranger_signals = [s for s in sigs if s.candidate_id is None]
    if stranger_signals:
        stranger_score = sum(s.confidence for s in stranger_signals) / len(stranger_signals)
        scores.append(CandidateScore("stranger", stranger_score, 0.0, stranger_score))

    # ── 排序 ───────────────────────────────────────────────────────
    scores.sort(key=lambda x: x.score_final, reverse=True)

    # ── 评估冲突 ───────────────────────────────────────────────────
    conflicts, cap = evaluate_conflicts(sigs, scores, cfg)

    # ── 判定等级 ───────────────────────────────────────────────────
    if not scores:
        return FusionResult(
            chosen_id=None,
            confidence=0.0,
            level=Level.LOW,
            method="NONE",
            scores=[],
            conflicts=conflicts,
            needs_review=False,
            degraded=False,
        )

    top_score = scores[0]
    chosen_id = top_score.candidate_id if top_score.candidate_id != "stranger" else None

    # 根据得分判定等级
    if top_score.score_final >= cfg.high_threshold and cap == Level.HIGH:
        level = Level.HIGH
    elif top_score.score_final >= cfg.med_threshold:
        level = Level.MED
    else:
        level = Level.LOW

    # 冲突规则可能封顶
    level = min(level, cap, key=lambda x: list(Level).index(x))

    # 是否需要人工确认
    needs_review = level == Level.NEEDS_REVIEW

    return FusionResult(
        chosen_id=chosen_id,
        confidence=top_score.score_final,
        level=level,
        method="WEIGHTED_FUSION",
        scores=scores,
        conflicts=conflicts,
        needs_review=needs_review,
        degraded=False,
    )


# ── 消除法（伪信号源）───────────────────────────────────────────────

def elimination_signals(roster: list[dict], occupancy: list[dict],
                        now: float = 0.0) -> list[SignalEvidence]:
    """名册消除法（降级为伪信号源）。

    输出 ELIMINATION 伪信号，可被人脸信号覆盖。
    """
    # 简化实现：直接调用现有的 fuse_presence
    from .presence_fusion import fuse_presence
    result = fuse_presence(roster, occupancy)

    signals: list[SignalEvidence] = []
    for inferred in result.get("inferred", []):
        signals.append(SignalEvidence(
            signal_id=f"elim:{inferred['member']}:{inferred['room']}",
            source=Source.ELIMINATION,
            candidate_id=inferred["member"],
            confidence=min(inferred["confidence"], 0.65),  # 置信度上限 0.65
            event_ts=now,
            room_id=inferred["room"],
        ))

    return signals
