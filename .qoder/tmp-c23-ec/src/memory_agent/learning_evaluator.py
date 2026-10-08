"""学习闭环 · 效果评估：前后窗口对比 + 留出窗过拟合检测 + 分桶遗忘检测。

verdict:
- KEEP     调整有效且无副作用
- WATCH    数据不足 / 无明显收益 / 疑似过拟合 → 继续观察
- ROLLBACK 整体回退或出现灾难性遗忘 → 自动回滚
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Sequence

from learning_models import (
    EPS,
    FeedbackKind,
    FeedbackSignal,
    negative_rate,
    signal_weight,
    weighted_sums,
)


@dataclass(frozen=True)
class EvalConfig:
    regression_tol: float = 0.03      # 整体负反馈率恶化容忍度
    forgetting_tol: float = 0.08      # 单桶负反馈率恶化容忍度（灾难性遗忘）
    overfit_tol: float = 0.05         # 留出窗恶化容忍度（过拟合）
    min_post_volume: int = 15
    min_bucket_volume: int = 8


class Verdict(str, Enum):
    KEEP = "keep"
    WATCH = "watch"
    ROLLBACK = "rollback"


@dataclass(frozen=True)
class Metrics:
    volume: int
    weighted_volume: float
    negative_rate: float
    usage_rate: float | None          # 使用 / (使用 + 未使用)
    repeat_query_rate: float          # 以下为占全部信号加权量的比例（代理指标）
    edit_rate: float
    disable_rate: float


@dataclass(frozen=True)
class BucketDelta:
    bucket: str
    pre_rate: float
    post_rate: float
    delta: float
    volume: int


@dataclass(frozen=True)
class EvalReport:
    adjustment_id: str
    param: str
    pre: Metrics
    post: Metrics
    holdout: Metrics | None
    buckets: tuple[BucketDelta, ...]
    verdict: Verdict
    flags: tuple[str, ...]
    narrative: str


def compute_metrics(signals: Sequence[FeedbackSignal], now: datetime) -> Metrics:
    neg, pos = weighted_sums(signals, now)
    total = neg + pos
    used = sum(signal_weight(s, now) for s in signals if s.kind is FeedbackKind.RECALL_USED)
    unused = sum(signal_weight(s, now) for s in signals if s.kind is FeedbackKind.RECALL_UNUSED)
    repeat = sum(signal_weight(s, now) for s in signals if s.kind is FeedbackKind.REPEAT_QUERY)
    edit = sum(signal_weight(s, now) for s in signals if s.kind is FeedbackKind.MEMORY_EDIT)
    disable = sum(signal_weight(s, now) for s in signals if s.kind is FeedbackKind.RULE_DISABLE)
    denom = total + EPS
    return Metrics(
        volume=len(signals),
        weighted_volume=total,
        negative_rate=neg / denom,
        usage_rate=(used / (used + unused + EPS)) if (used + unused) > 0 else None,
        repeat_query_rate=repeat / denom,
        edit_rate=edit / denom,
        disable_rate=disable / denom,
    )


def _bucket_deltas(
    pre: Sequence[FeedbackSignal],
    post: Sequence[FeedbackSignal],
    key_fn: Callable[[FeedbackSignal], str | None],
    prefix: str,
    now: datetime,
    cfg: EvalConfig,
) -> list[BucketDelta]:
    def group(signals: Sequence[FeedbackSignal]) -> dict[str, list[FeedbackSignal]]:
        out: dict[str, list[FeedbackSignal]] = {}
        for s in signals:
            key = key_fn(s)
            if key:
                out.setdefault(f"{prefix}:{key}", []).append(s)
        return out

    pre_groups, post_groups = group(pre), group(post)
    deltas: list[BucketDelta] = []
    for bucket, post_signals in post_groups.items():
        pre_signals = pre_groups.get(bucket, [])
        if len(pre_signals) < cfg.min_bucket_volume or len(post_signals) < cfg.min_bucket_volume:
            continue
        pre_rate = negative_rate(pre_signals, now)
        post_rate = negative_rate(post_signals, now)
        deltas.append(
            BucketDelta(
                bucket=bucket,
                pre_rate=pre_rate,
                post_rate=post_rate,
                delta=post_rate - pre_rate,
                volume=len(post_signals),
            )
        )
    return deltas


def evaluate(
    adjustment,  # ParamAdjustment（避免循环导入，用鸭子类型）
    pre_signals: Sequence[FeedbackSignal],
    post_signals: Sequence[FeedbackSignal],
    holdout_signals: Sequence[FeedbackSignal] | None = None,
    *,
    now: datetime,
    config: EvalConfig | None = None,
) -> EvalReport:
    """④评估：给一次参数调整出 KEEP / WATCH / ROLLBACK 结论（纯函数）。"""
    cfg = config or EvalConfig()
    pre = compute_metrics(pre_signals, now)
    post = compute_metrics(post_signals, now)
    holdout = compute_metrics(holdout_signals, now) if holdout_signals else None

    buckets = tuple(
        _bucket_deltas(pre_signals, post_signals, lambda s: s.member_id, "member", now, cfg)
        + _bucket_deltas(pre_signals, post_signals, lambda s: s.room, "room", now, cfg)
        + _bucket_deltas(pre_signals, post_signals, lambda s: s.kind.value, "kind", now, cfg)
    )

    flags: list[str] = []
    improvement = pre.negative_rate - post.negative_rate

    if post.volume < cfg.min_post_volume:
        flags.append("insufficient_data")
    if improvement < -cfg.regression_tol:
        flags.append("overall_regression")
    for b in buckets:
        if b.delta > cfg.forgetting_tol:
            flags.append(f"forgetting:{b.bucket}")
    if holdout is not None and (holdout.negative_rate - pre.negative_rate) > cfg.overfit_tol:
        flags.append("overfitting")

    if "overall_regression" in flags or any(f.startswith("forgetting:") for f in flags):
        verdict = Verdict.ROLLBACK
    elif flags or improvement <= 0.0:
        verdict = Verdict.WATCH
    else:
        verdict = Verdict.KEEP

    narrative = (
        f"参数 {adjustment.param} 调整后负反馈率 {pre.negative_rate:.0%} → {post.negative_rate:.0%}"
        f"（样本 {pre.volume} → {post.volume}）；结论：{verdict.value}"
        + (f"；风险：{', '.join(flags)}" if flags else "；未见过拟合或遗忘迹象")
    )
    return EvalReport(
        adjustment_id=adjustment.adjustment_id,
        param=adjustment.param,
        pre=pre,
        post=post,
        holdout=holdout,
        buckets=buckets,
        verdict=verdict,
        flags=tuple(flags),
        narrative=narrative,
    )
