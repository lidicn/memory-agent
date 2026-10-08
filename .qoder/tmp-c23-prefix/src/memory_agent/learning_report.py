"""学习闭环 · 学习报告：服务端算好，前端只展示（与 insights.py 同一原则）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from learning_analyzer import AnalysisResult
from learning_evaluator import EvalReport
from learning_models import LINK_LABEL, REASON_LABEL
from learning_optimizer import ParamAdjustment


@dataclass(frozen=True)
class LearningReport:
    period: tuple[datetime, datetime]
    sample_count: int
    headline: str
    learned: tuple[str, ...]     # 系统学到了什么
    changes: tuple[str, ...]     # 本轮改了什么、为什么
    metrics: tuple[str, ...]     # 效果对比
    risks: tuple[str, ...]       # 风险与护栏动作
    next_steps: tuple[str, ...]  # 待改进与建议


def build_report(
    period: tuple[datetime, datetime],
    analysis: AnalysisResult,
    adjustments: Sequence[ParamAdjustment],
    evals: Sequence[EvalReport],
) -> LearningReport:
    spots = analysis.weak_spots
    top = spots[:3]
    learned = tuple(
        f"{LINK_LABEL[s.link]}（{s.scope}）仍有 {s.negative_rate:.0%} 负反馈，"
        f"主因：{'、'.join(REASON_LABEL[r] for r in s.top_reasons[:2])}"
        for s in top
    ) or ("本期未发现稳定的薄弱环节（可能是样本不足）",)

    changes = tuple(
        f"[{a.mode}] {a.param}：{a.old_value:g} → {a.new_value:g}｜原因：{a.reason}" for a in adjustments
    ) or ("本期未做参数调整",)

    metrics = tuple(
        f"{e.param}：负反馈率 {e.pre.negative_rate:.0%} → {e.post.negative_rate:.0%}，结论 {e.verdict.value}"
        for e in evals
    ) or ("本期尚无已完成评估的调整",)

    risks_list: list[str] = []
    for e in evals:
        risks_list.extend(f"{e.param}：{flag}" for flag in e.flags)
    risks = tuple(risks_list) or ("未触发过拟合/灾难性遗忘护栏",)

    next_steps: list[str] = [s.suggestion for s in top]
    if not next_steps:
        next_steps = ["扩大反馈覆盖：在召回结果页补点赞/点踩入口，提高显式反馈占比"]
    headline = (
        f"本期共 {analysis.sample_count} 条学习信号，定位 {len(spots)} 个薄弱环节，"
        f"完成 {len(evals)} 次效果评估。"
    )
    return LearningReport(
        period=period,
        sample_count=analysis.sample_count,
        headline=headline,
        learned=learned,
        changes=changes,
        metrics=metrics,
        risks=risks,
        next_steps=tuple(dict.fromkeys(next_steps)),  # 去重保序
    )


def render_markdown(report: LearningReport) -> str:
    start, end = report.period
    lines = [
        f"# MemoryAgent 学习报告（{start:%Y-%m-%d} ~ {end:%Y-%m-%d}）",
        "",
        f"> {report.headline}",
        "",
    ]
    sections = (
        ("一、系统学到了什么", report.learned),
        ("二、本轮参数调整", report.changes),
        ("三、效果对比", report.metrics),
        ("四、风险与护栏动作", report.risks),
        ("五、待改进与建议", report.next_steps),
    )
    for title, items in sections:
        lines += [f"## {title}", ""]
        lines += [f"- {item}" for item in items] or ["- （无）"]
        lines.append("")
    return "\n".join(lines)
