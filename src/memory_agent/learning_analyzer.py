"""学习闭环 · 反馈分析：确定性归因 + 弱项聚类（纯函数）。

归因采用规则表而非 LLM，保证可复现、可审计；LLM 只可用于生成补充说明，
且失败时回退到规则结果（分析质量 fail-open，PII 仍 fail-closed）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

from learning_models import (
    EPS,
    FeedbackKind,
    FeedbackSignal,
    ReasonCode,
    SUGGESTION,
    SubjectType,
    WeakLink,
    negative_rate,
    signal_weight,
    weighted_sums,
)

_BASE_LINK: Mapping[FeedbackKind, WeakLink] = {
    FeedbackKind.MEMORY_RATING: WeakLink.RECALL,
    FeedbackKind.IDENTITY_CORRECTION: WeakLink.PERCEIVE,
    FeedbackKind.RULE_FEEDBACK: WeakLink.RULE,
    FeedbackKind.PROFILE_CORRECTION: WeakLink.PROFILE,
    FeedbackKind.RECALL_UNUSED: WeakLink.RECALL,
    FeedbackKind.REPEAT_QUERY: WeakLink.RECALL,
    FeedbackKind.MEMORY_EDIT: WeakLink.PROMOTE,
    FeedbackKind.RULE_DISABLE: WeakLink.RULE,
    FeedbackKind.RECALL_USED: WeakLink.RECALL,
}

# 无歧义的原因码可以覆盖默认归因
_REASON_OVERRIDE: Mapping[ReasonCode, WeakLink] = {
    ReasonCode.WRONG_PERSON: WeakLink.PERCEIVE,
    ReasonCode.WRONG_PROFILE: WeakLink.PROFILE,
    ReasonCode.CONTENT_WRONG: WeakLink.PROMOTE,
    ReasonCode.DUPLICATE: WeakLink.PROMOTE,
}


def attribute(signal: FeedbackSignal) -> WeakLink:
    """把一条信号归因到系统薄弱环节（纯函数）。"""
    return _REASON_OVERRIDE.get(signal.reason, _BASE_LINK[signal.kind])


def scope_of(signal: FeedbackSignal) -> str:
    """弱项作用域：规则 > 成员 > 房间 > 具体对象。"""
    if signal.subject_type is SubjectType.RULE:
        return f"rule:{signal.subject_id}"
    if signal.member_id:
        return f"member:{signal.member_id}"
    if signal.room:
        return f"room:{signal.room}"
    return f"{signal.subject_type.value}:{signal.subject_id}"


@dataclass(frozen=True)
class WeakSpot:
    """一个薄弱环节：某环节 + 某作用域上的负反馈聚集。"""

    link: WeakLink
    scope: str
    sample_count: int
    weighted_volume: float
    negative_rate: float
    score: float
    reason_weights: Mapping[ReasonCode, float]
    suggestion: str

    @property
    def top_reasons(self) -> tuple[ReasonCode, ...]:
        return tuple(sorted(self.reason_weights, key=lambda r: -self.reason_weights[r]))


@dataclass(frozen=True)
class AnalysisResult:
    weak_spots: tuple[WeakSpot, ...]
    link_summary: Mapping[WeakLink, float]
    sample_count: int


def analyze(
    signals: Sequence[FeedbackSignal],
    *,
    now: datetime,
    min_samples: int = 3,
    half_life_days: float = 21.0,
) -> AnalysisResult:
    """②分析：聚类到 (环节, 作用域)，按 score 排序得到薄弱环节清单。"""
    groups: dict[tuple[WeakLink, str], list[FeedbackSignal]] = {}
    for s in signals:
        groups.setdefault((attribute(s), scope_of(s)), []).append(s)

    spots: list[WeakSpot] = []
    link_summary: dict[WeakLink, float] = {}
    for (link, scope), group in groups.items():
        neg, pos = weighted_sums(group, now, half_life_days)
        volume = neg + pos
        rate = neg / (volume + EPS)
        score = rate * math.log1p(volume)
        link_summary[link] = link_summary.get(link, 0.0) + score

        reason_weights: dict[ReasonCode, float] = {}
        for s in group:
            if s.is_negative():
                reason_weights[s.reason] = reason_weights.get(s.reason, 0.0) + signal_weight(
                    s, now, half_life_days
                )

        if len(group) >= min_samples and score > 0.0:
            spots.append(
                WeakSpot(
                    link=link,
                    scope=scope,
                    sample_count=len(group),
                    weighted_volume=volume,
                    negative_rate=rate,
                    score=score,
                    reason_weights=reason_weights,
                    suggestion=SUGGESTION[link],
                )
            )

    spots.sort(key=lambda x: (-x.score, x.scope))
    return AnalysisResult(
        weak_spots=tuple(spots),
        link_summary=link_summary,
        sample_count=len(signals),
    )
