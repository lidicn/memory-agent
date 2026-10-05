"""学习闭环 · 领域模型与纯函数基座。

约定：本模块不做任何 I/O，全部函数可直接单测；时间使用带时区 datetime（建议 UTC）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping, Sequence

EPS = 1e-9
HALF_LIFE_DAYS = 21.0  # 反馈信号半衰期：越新的信号权重越高


class FeedbackKind(str, Enum):
    """反馈类型：4 类显式 + 5 类隐式（含正向 recall_used，用于使用率分母）。"""

    MEMORY_RATING = "memory_rating"              # 显式：对召回记忆点赞/点踩
    IDENTITY_CORRECTION = "identity_correction"  # 显式：人脸识别纠正
    RULE_FEEDBACK = "rule_feedback"              # 显式：规则触发结果反馈
    PROFILE_CORRECTION = "profile_correction"    # 显式：家庭画像纠正
    RECALL_UNUSED = "recall_unused"              # 隐式：召回后未使用
    REPEAT_QUERY = "repeat_query"                # 隐式：重复查询
    MEMORY_EDIT = "memory_edit"                  # 隐式：用户手动编辑记忆
    RULE_DISABLE = "rule_disable"                # 隐式：用户禁用规则
    RECALL_USED = "recall_used"                  # 隐式正向：召回被采纳


EXPLICIT_KINDS: frozenset[FeedbackKind] = frozenset(
    {
        FeedbackKind.MEMORY_RATING,
        FeedbackKind.IDENTITY_CORRECTION,
        FeedbackKind.RULE_FEEDBACK,
        FeedbackKind.PROFILE_CORRECTION,
    }
)
IMPLICIT_KINDS: frozenset[FeedbackKind] = frozenset(set(FeedbackKind) - set(EXPLICIT_KINDS))


class SubjectType(str, Enum):
    MEMORY = "memory"
    IDENTITY = "identity"
    RULE = "rule"
    PROFILE = "profile"
    RETRIEVAL = "retrieval"


class WeakLink(str, Enum):
    """薄弱环节：学习闭环要定位的系统环节。"""

    PERCEIVE = "perceive"   # 感知/识别
    PROMOTE = "promote"     # 记忆晋升
    RECALL = "recall"       # 召回
    RULE = "rule"           # 规则
    PROFILE = "profile"     # 家庭画像
    TIMING = "timing"       # 时机/分发


class ReasonCode(str, Enum):
    """负反馈原因（结构化，避免靠自由文本归因）。"""

    CONTENT_WRONG = "content_wrong"    # 内容错了
    DUPLICATE = "duplicate"            # 重复/冗余
    MISSED = "missed"                  # 该发生没发生（漏记/漏召回/漏触发）
    IRRELEVANT = "irrelevant"          # 不相关
    LATE = "late"                      # 太晚/时机不对
    FALSE_ALARM = "false_alarm"        # 不该发生却发生了
    WRONG_PERSON = "wrong_person"      # 识别错人
    WRONG_PROFILE = "wrong_profile"    # 画像信息错
    OTHER = "other"


LINK_LABEL: Mapping[WeakLink, str] = {
    WeakLink.PERCEIVE: "感知/识别",
    WeakLink.PROMOTE: "记忆晋升",
    WeakLink.RECALL: "召回",
    WeakLink.RULE: "规则",
    WeakLink.PROFILE: "家庭画像",
    WeakLink.TIMING: "时机/分发",
}

REASON_LABEL: Mapping[ReasonCode, str] = {
    ReasonCode.CONTENT_WRONG: "内容错误",
    ReasonCode.DUPLICATE: "重复冗余",
    ReasonCode.MISSED: "该发生的没发生",
    ReasonCode.IRRELEVANT: "召回不相关",
    ReasonCode.LATE: "时机太晚",
    ReasonCode.FALSE_ALARM: "误触发",
    ReasonCode.WRONG_PERSON: "识别错人",
    ReasonCode.WRONG_PROFILE: "画像信息错",
    ReasonCode.OTHER: "其他",
}

# 各环节的人工/系统建议（人类可读第一）
SUGGESTION: Mapping[WeakLink, str] = {
    WeakLink.PERCEIVE: "走 feedback_pack 导出 bad-case 回流识别模型/人脸库，人工确认后修正",
    WeakLink.PROMOTE: "调晋升阈值或补证据；重复记忆增加去重比对",
    WeakLink.RECALL: "调召回阈值/TopK/时间衰减权重",
    WeakLink.RULE: "调规则去抖窗口与优先级，必要时人工改规则条件",
    WeakLink.PROFILE: "画像字段需人工确认后修正，禁止自动改写",
    WeakLink.TIMING: "缩短去抖延迟或提升分发优先级",
}

# 隐式信号默认效价（显式信号由用户评分决定）
DEFAULT_VALENCE: Mapping[FeedbackKind, float] = {
    FeedbackKind.RECALL_USED: 1.0,
    FeedbackKind.RECALL_UNUSED: -0.6,
    FeedbackKind.REPEAT_QUERY: -0.5,
    FeedbackKind.MEMORY_EDIT: -0.8,
    FeedbackKind.RULE_DISABLE: -1.0,
}

# 信号基础权重：显式纠正 > 显式评分 > 隐式行为
KIND_BASE_WEIGHT: Mapping[FeedbackKind, float] = {
    FeedbackKind.IDENTITY_CORRECTION: 1.3,
    FeedbackKind.PROFILE_CORRECTION: 1.2,
    FeedbackKind.MEMORY_RATING: 1.0,
    FeedbackKind.RULE_FEEDBACK: 1.0,
    FeedbackKind.RULE_DISABLE: 0.9,
    FeedbackKind.MEMORY_EDIT: 0.7,
    FeedbackKind.RECALL_UNUSED: 0.5,
    FeedbackKind.RECALL_USED: 0.5,
    FeedbackKind.REPEAT_QUERY: 0.4,
}


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


@dataclass(frozen=True)
class FeedbackSignal:
    """一条已归一化、已脱敏的学习信号。"""

    feedback_id: str
    kind: FeedbackKind
    subject_type: SubjectType
    subject_id: str
    valence: float                     # [-1, 1]，负=负反馈
    created_at: datetime
    confidence: float = 1.0            # [0, 1]，隐式推断信号可小于 1
    member_id: str | None = None
    room: str | None = None
    reason: ReasonCode = ReasonCode.OTHER
    repeat_count: int = 1              # 折叠后的重复次数（重复查询）
    context: Mapping[str, object] = field(default_factory=dict)  # 必须已脱敏
    context_dropped: bool = False      # True = PII 脱敏失败，trace 已丢弃（fail-closed）

    def is_negative(self) -> bool:
        return self.valence < 0


def decay_factor(created_at: datetime, now: datetime, half_life_days: float = HALF_LIFE_DAYS) -> float:
    """时间衰减：越旧的反馈权重越低。"""
    age_days = max(0.0, (now - created_at).total_seconds()) / 86400.0
    return math.exp(-math.log(2.0) * age_days / max(half_life_days, EPS))


def signal_weight(signal: FeedbackSignal, now: datetime, half_life_days: float = HALF_LIFE_DAYS) -> float:
    """综合权重 = 类型基础权重 × 置信度 × 时间衰减 × 重复强度。"""
    repeat_boost = 1.0 + 0.2 * max(0, signal.repeat_count - 1)
    return (
        KIND_BASE_WEIGHT[signal.kind]
        * clamp(signal.confidence, 0.0, 1.0)
        * decay_factor(signal.created_at, now, half_life_days)
        * min(repeat_boost, 2.0)
    )


def weighted_sums(
    signals: Sequence[FeedbackSignal], now: datetime, half_life_days: float = HALF_LIFE_DAYS
) -> tuple[float, float]:
    """返回 (负向加权量, 正向加权量)。"""
    neg = pos = 0.0
    for s in signals:
        w = signal_weight(s, now, half_life_days)
        if s.valence < 0.0:
            neg += w * abs(s.valence)
        else:
            pos += w * s.valence
    return neg, pos


def negative_rate(
    signals: Sequence[FeedbackSignal], now: datetime, half_life_days: float = HALF_LIFE_DAYS
) -> float:
    neg, pos = weighted_sums(signals, now, half_life_days)
    return neg / (neg + pos + EPS)
