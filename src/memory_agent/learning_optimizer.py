"""学习闭环 · 参数优化：证据 → 参数提案（纯函数 + 强护栏）。

设计要点：
- 参数白名单：只有登记过的旋钮可调，防止「学习」变成乱改系统。
- 只响应负反馈（正反馈由 evaluator 用于验证），避免参数单向漂移。
- 单轮最多 N 个参数、单步上限、边界截断、冷却期、最小样本。
- 每次调整都是可回滚的审计记录 ParamAdjustment。
- PERCEIVE / PROFILE 类弱项不进参数自调（回流 feedback_pack / 人工修正）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

from learning_models import (
    EPS,
    LINK_LABEL,
    REASON_LABEL,
    ReasonCode,
    WeakLink,
    clamp,
)
from learning_analyzer import WeakSpot


@dataclass(frozen=True)
class ParamSpec:
    name: str
    lower: float
    upper: float
    max_step: float
    cooldown_hours: float
    min_samples: int
    integer: bool = False


@dataclass(frozen=True)
class OptimizerConfig:
    mode: str = "shadow"                # "shadow" 只记录不生效 | "apply" 生效
    max_changes_per_cycle: int = 3


@dataclass(frozen=True)
class ParamAdjustment:
    adjustment_id: str
    param: str
    old_value: float
    new_value: float
    reason: str
    evidence: tuple[str, ...]
    mode: str
    created_at: datetime
    verdict: str | None = None          # KEEP / WATCH / ROLLBACK
    rolled_back: bool = False


@dataclass(frozen=True)
class SkippedItem:
    scope: str
    reason: str


@dataclass(frozen=True)
class OptimizationPlan:
    adjustments: tuple[ParamAdjustment, ...]
    skipped: tuple[SkippedItem, ...]


# ---- 参数白名单（模板；{rule} 由作用域绑定）--------------------------------

_PARAM_TEMPLATES: tuple[ParamSpec, ...] = (
    ParamSpec("promotion.threshold", 0.30, 0.95, 0.05, 72.0, 20),
    ParamSpec("recall.score_threshold", 0.20, 0.90, 0.05, 72.0, 20),
    ParamSpec("recall.top_k", 1.0, 10.0, 1.0, 48.0, 15, integer=True),
    ParamSpec("recall.weight.recency", 0.0, 1.0, 0.10, 72.0, 15),
    ParamSpec("recall.weight.importance", 0.0, 1.0, 0.10, 72.0, 15),
    ParamSpec("rule.debounce_window_s:{rule}", 0.0, 3600.0, 60.0, 24.0, 5),
    ParamSpec("rule.priority:{rule}", 0.0, 100.0, 5.0, 24.0, 5),
)


def resolve_spec(name: str) -> ParamSpec | None:
    for spec in _PARAM_TEMPLATES:
        if spec.name == name or (spec.name.endswith(":{rule}") and name.startswith(spec.name.split("{")[0])):
            return ParamSpec(
                name=name,
                lower=spec.lower,
                upper=spec.upper,
                max_step=spec.max_step,
                cooldown_hours=spec.cooldown_hours,
                min_samples=spec.min_samples,
                integer=spec.integer,
            )
    return None


def bind_param(template: str, scope: str) -> str | None:
    if "{rule}" not in template:
        return template
    prefix, _, rule_id = scope.partition(":")
    return template.format(rule=rule_id) if prefix == "rule" and rule_id else None


# ---- 证据 → 方向 -----------------------------------------------------------

_DIRECTIONS: Mapping[tuple[WeakLink, ReasonCode], tuple[tuple[str, float], ...]] = {
    # 记忆晋升：内容错/重复/编辑 → 提门槛；漏记 → 降门槛
    (WeakLink.PROMOTE, ReasonCode.CONTENT_WRONG): (("promotion.threshold", +1.0),),
    (WeakLink.PROMOTE, ReasonCode.DUPLICATE): (("promotion.threshold", +1.0),),
    (WeakLink.PROMOTE, ReasonCode.OTHER): (("promotion.threshold", +1.0),),
    (WeakLink.PROMOTE, ReasonCode.MISSED): (("promotion.threshold", -1.0),),
    # 召回：不相关 → 提阈值、降 TopK；漏召回 → 反向；太晚 → 加大时间衰减权重
    (WeakLink.RECALL, ReasonCode.IRRELEVANT): (
        ("recall.score_threshold", +1.0),
        ("recall.top_k", -1.0),
    ),
    (WeakLink.RECALL, ReasonCode.OTHER): (("recall.score_threshold", +1.0),),
    (WeakLink.RECALL, ReasonCode.MISSED): (
        ("recall.score_threshold", -1.0),
        ("recall.top_k", +1.0),
    ),
    (WeakLink.RECALL, ReasonCode.LATE): (("recall.weight.recency", +1.0),),
    # 规则：误触发 → 加大去抖、降优先级；漏触发 → 反向
    (WeakLink.RULE, ReasonCode.FALSE_ALARM): (
        ("rule.debounce_window_s:{rule}", +1.0),
        ("rule.priority:{rule}", -1.0),
    ),
    (WeakLink.RULE, ReasonCode.DUPLICATE): (("rule.debounce_window_s:{rule}", +1.0),),
    (WeakLink.RULE, ReasonCode.OTHER): (("rule.debounce_window_s:{rule}", +1.0),),
    (WeakLink.RULE, ReasonCode.MISSED): (
        ("rule.debounce_window_s:{rule}", -1.0),
        ("rule.priority:{rule}", +1.0),
    ),
    # 时机：太晚 → 缩短去抖延迟
    (WeakLink.TIMING, ReasonCode.LATE): (("rule.debounce_window_s:{rule}", -1.0),),
    (WeakLink.TIMING, ReasonCode.OTHER): (("rule.debounce_window_s:{rule}", -1.0),),
}


def directed_params(link: WeakLink, reason: ReasonCode) -> tuple[tuple[str, float], ...]:
    return _DIRECTIONS.get((link, reason), ())


def propose(
    spots: Sequence[WeakSpot],
    values: Mapping[str, float],
    *,
    now: datetime,
    last_adjusted: Mapping[str, datetime] | None = None,
    config: OptimizerConfig | None = None,
) -> OptimizationPlan:
    """③优化：把弱项证据换算成受护栏约束的参数提案（纯函数）。"""
    cfg = config or OptimizerConfig()
    last_adjusted = last_adjusted or {}

    intent: dict[str, float] = {}
    support: dict[str, int] = {}
    evidence: dict[str, list[str]] = {}
    skipped: list[SkippedItem] = []

    for spot in spots:
        if not any(directed_params(spot.link, reason) for reason in spot.reason_weights):
            skipped.append(SkippedItem(f"{spot.link.value}:{spot.scope}", spot.suggestion))
            continue
        total = sum(spot.reason_weights.values()) + EPS
        for reason, weight in spot.reason_weights.items():
            share = weight / total
            for template, direction in directed_params(spot.link, reason):
                name = bind_param(template, spot.scope)
                if name is None:
                    skipped.append(SkippedItem(spot.scope, f"{template} 需要规则作用域，已跳过"))
                    continue
                spec = resolve_spec(name)
                if spec is None:
                    skipped.append(SkippedItem(name, "不在参数白名单内"))
                    continue
                intensity = clamp(spot.negative_rate * share * 2.0, 0.2, 1.0)
                intent[name] = intent.get(name, 0.0) + direction * spec.max_step * intensity
                support[name] = support.get(name, 0) + max(1, round(spot.sample_count * share))
                evidence.setdefault(name, []).append(
                    f"{LINK_LABEL[spot.link]}/{spot.scope}:{REASON_LABEL[reason]}"
                )

    adjustments: list[ParamAdjustment] = []
    for name in sorted(intent, key=lambda n: -abs(intent[n])):
        spec = resolve_spec(name)
        if spec is None or abs(intent[name]) < EPS:
            continue
        if len(adjustments) >= cfg.max_changes_per_cycle:
            skipped.append(SkippedItem(name, f"本轮变更数已达上限（{cfg.max_changes_per_cycle}），顺延下一轮"))
            continue
        if name not in values:
            skipped.append(SkippedItem(name, "当前配置中不存在该参数（下游未接入）"))
            continue
        if support[name] < spec.min_samples:
            skipped.append(SkippedItem(name, f"样本不足（{support[name]} < {spec.min_samples}）"))
            continue
        last = last_adjusted.get(name)
        if last is not None and (now - last).total_seconds() < spec.cooldown_hours * 3600:
            skipped.append(SkippedItem(name, f"冷却期未过（{spec.cooldown_hours:.0f}h）"))
            continue

        old_value = float(values[name])
        delta = clamp(intent[name], -spec.max_step, spec.max_step)
        new_value = clamp(old_value + delta, spec.lower, spec.upper)
        if spec.integer:
            new_value = float(round(new_value))
        if abs(new_value - old_value) < EPS:
            skipped.append(SkippedItem(name, "已在边界或无可调空间"))
            continue

        trend = "上调" if new_value > old_value else "下调"
        reason_text = (
            f"{evidence[name][0]} 等 {len(evidence[name])} 条证据 → {trend} {name} "
            f"{old_value:g}→{new_value:g}"
        )
        adjustments.append(
            ParamAdjustment(
                adjustment_id=f"adj_{uuid.uuid4().hex[:12]}",
                param=name,
                old_value=old_value,
                new_value=new_value,
                reason=reason_text,
                evidence=tuple(evidence[name]),
                mode=cfg.mode,
                created_at=now,
            )
        )

    return OptimizationPlan(adjustments=tuple(adjustments), skipped=tuple(skipped))
