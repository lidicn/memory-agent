"""学习闭环 · HTTP 接口与闭环编排。

部署：app.include_router(build_router(store, config))
缓存：weak-spots 属于重计算，可选 Redis 缓存（未配置则跳过，不影响正确性）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from learning_analyzer import AnalysisResult, analyze
from learning_evaluator import EvalReport, Verdict, evaluate
from learning_feedback import CollectResult, collect_events
from learning_models import LINK_LABEL
from learning_optimizer import OptimizerConfig, ParamAdjustment, propose
from learning_report import build_report, render_markdown
from learning_store import LearningStore


@dataclass
class LearningConfig:
    mode: str = "shadow"                 # 影子模式起步，验证后再切 apply
    window_days: int = 7                 # 分析窗口
    eval_window_days: int = 7            # 评估前后窗口
    eval_after_hours: int = 48           # 调整后多久开始评估
    min_spot_samples: int = 3
    max_changes_per_cycle: int = 3
    half_life_days: float = 21.0
    redis_url: str | None = None         # 可选：弱项分析结果缓存


class FeedbackEventModel(BaseModel):
    kind: str
    subject_type: str
    subject_id: str
    valence: float | None = None
    rating: bool | int | str | None = None
    confidence: float = 1.0
    member_id: str | None = None
    room: str | None = None
    reason: str = "other"
    created_at: str | None = None
    feedback_id: str | None = None
    context: dict[str, Any] | None = None


class FeedbackBatchModel(BaseModel):
    events: list[FeedbackEventModel]


def _dump_analysis(result: AnalysisResult) -> dict[str, Any]:
    return {
        "sample_count": result.sample_count,
        "link_summary": {LINK_LABEL[k]: round(v, 4) for k, v in result.link_summary.items()},
        "weak_spots": [
            {
                "link": LINK_LABEL[s.link],
                "scope": s.scope,
                "sample_count": s.sample_count,
                "negative_rate": round(s.negative_rate, 4),
                "score": round(s.score, 4),
                "top_reasons": [r.value for r in s.top_reasons],
                "suggestion": s.suggestion,
            }
            for s in result.weak_spots
        ],
    }


def _dump_adjustment(adj: ParamAdjustment) -> dict[str, Any]:
    return {
        "adjustment_id": adj.adjustment_id,
        "param": adj.param,
        "old_value": adj.old_value,
        "new_value": adj.new_value,
        "reason": adj.reason,
        "evidence": list(adj.evidence),
        "mode": adj.mode,
        "verdict": adj.verdict,
        "rolled_back": adj.rolled_back,
        "created_at": adj.created_at.isoformat(),
    }


def _dump_eval(report: EvalReport) -> dict[str, Any]:
    return {
        "adjustment_id": report.adjustment_id,
        "param": report.param,
        "verdict": report.verdict.value,
        "flags": list(report.flags),
        "narrative": report.narrative,
        "pre_negative_rate": round(report.pre.negative_rate, 4),
        "post_negative_rate": round(report.post.negative_rate, 4),
        "buckets": [
            {"bucket": b.bucket, "delta": round(b.delta, 4), "volume": b.volume}
            for b in report.buckets
        ],
    }


def run_learning_cycle(
    store: LearningStore,
    config: LearningConfig,
    *,
    now: datetime | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """一轮完整学习：收集 → 分析 → 优化（shadow/apply）。"""
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=config.window_days)
    signals = store.list_signals(start, now)
    analysis = analyze(signals, now=now, min_samples=config.min_spot_samples)

    effective_mode = mode or config.mode
    plan = propose(
        analysis.weak_spots,
        store.param_values(),
        now=now,
        last_adjusted=store.last_adjusted_at(),
        config=OptimizerConfig(mode=effective_mode, max_changes_per_cycle=config.max_changes_per_cycle),
    )
    for adj in plan.adjustments:
        store.insert_adjustment(adj)
        if effective_mode == "apply":
            store.set_param(adj.param, adj.new_value, now)   # 写回 candidate_promotion / 召回配置 / 规则引擎
    return {
        "mode": effective_mode,
        "analysis": _dump_analysis(analysis),
        "adjustments": [_dump_adjustment(a) for a in plan.adjustments],
        "skipped": [{"scope": s.scope, "reason": s.reason} for s in plan.skipped],
    }


def run_evaluation_cycle(
    store: LearningStore,
    config: LearningConfig,
    *,
    now: datetime | None = None,
    auto_rollback: bool = True,
) -> list[dict[str, Any]]:
    """对到期的参数调整做效果评估，必要时自动回滚。"""
    now = now or datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []
    for adj in store.list_adjustments():
        if adj.verdict is not None:
            continue
        if (now - adj.created_at) < timedelta(hours=config.eval_after_hours):
            continue
        window = timedelta(days=config.eval_window_days)
        pre = store.list_signals(adj.created_at - window, adj.created_at)
        post = store.list_signals(adj.created_at, now)
        report = evaluate(adj, pre, post, now=now)
        store.mark_evaluated(adj.adjustment_id, report.verdict.value, now)
        if auto_rollback and report.verdict is Verdict.ROLLBACK and adj.mode == "apply":
            store.set_param(adj.param, adj.old_value, now)
            store.mark_rolled_back(adj.adjustment_id)
        results.append(_dump_eval(report))
    return results


def build_router(store: LearningStore, config: LearningConfig) -> APIRouter:
    router = APIRouter(prefix="/learning", tags=["learning"])

    @router.post("/feedback")
    def submit_feedback(batch: FeedbackBatchModel) -> dict[str, Any]:
        raw: Sequence[Mapping[str, Any]] = [e.model_dump(exclude_none=True) for e in batch.events]
        result: CollectResult = collect_events(raw, half_life_days=config.half_life_days)
        store.insert_signals(result.accepted)
        return {
            "accepted": len(result.accepted),
            "dropped": result.dropped_count,
            "dropped_reasons": [f"{ref}: {reason}" for reason, ref in result.dropped],
        }

    @router.get("/weak-spots")
    def weak_spots(days: int = Query(default=7, ge=1, le=90)) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        signals = store.list_signals(now - timedelta(days=days), now)
        return _dump_analysis(analyze(signals, now=now, min_samples=config.min_spot_samples))

    @router.post("/optimize")
    def optimize(mode: str = Query(default="shadow", pattern="^(shadow|apply)$")) -> dict[str, Any]:
        return run_learning_cycle(store, config, mode=mode)

    @router.get("/adjustments")
    def adjustments(limit: int = Query(default=50, ge=1, le=200)) -> list[dict[str, Any]]:
        return [_dump_adjustment(a) for a in store.list_adjustments(limit)]

    @router.post("/adjustments/{adjustment_id}/rollback")
    def rollback(adjustment_id: str) -> dict[str, Any]:
        for adj in store.list_adjustments():
            if adj.adjustment_id == adjustment_id:
                store.set_param(adj.param, adj.old_value, datetime.now(timezone.utc))
                store.mark_rolled_back(adjustment_id)
                return {"adjustment_id": adjustment_id, "rolled_back": True, "restored": adj.old_value}
        raise HTTPException(status_code=404, detail="adjustment not found")

    @router.post("/evaluate")
    def evaluate_pending() -> list[dict[str, Any]]:
        return run_evaluation_cycle(store, config)

    @router.get("/report")
    def report(days: int = Query(default=7, ge=1, le=90), format: str = Query(default="json")) -> Any:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)
        signals = store.list_signals(start, now)
        analysis = analyze(signals, now=now, min_samples=config.min_spot_samples)
        adjs = store.list_adjustments()
        evals = [
            evaluate(a, [], [], now=now) for a in []  # 占位：评估结果从 run_evaluation_cycle 落库读取
        ]
        learning_report = build_report((start, now), analysis, adjs, evals)
        return render_markdown(learning_report) if format == "markdown" else {
            "period": [start.isoformat(), now.isoformat()],
            "sample_count": learning_report.sample_count,
            "headline": learning_report.headline,
            "learned": list(learning_report.learned),
            "changes": list(learning_report.changes),
            "metrics": list(learning_report.metrics),
            "risks": list(learning_report.risks),
            "next_steps": list(learning_report.next_steps),
        }

    return router

