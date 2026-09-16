"""Plan B 三层评估协议（benchmarks/openshs_eval.py）的单元测试。

这些用例只用合成输入，不依赖真实数据集，保证快速且确定性。
背景：旧口径只有裸 macro-F1，而 sleeping/watching_tv 的真实基础率达 93%，
「每天预测全部活动」的傻瓜基线就能拿 0.649 > MA 的 0.638 —— 裸 F1 无鉴别力。
本模块因此补了退化基线对照（lift / MCC）与时段级、段级口径。
"""

import os

import pytest

from benchmarks.openshs_eval import (
    BASELINE_KEYS,
    _baselines,
    _scores,
    day_level,
    segment_level,
    slot_level,
)

# 合成输入：一段睡眠（跨午夜）+ 一段做饭
GT_SEGMENTS = [
    {"activity": "sleep", "ma": "sleeping",
     "start": "2021-01-01T23:00:00", "end": "2021-01-02T06:00:00", "day": "2021-01-01"},
    {"activity": "cook", "ma": "cooking",
     "start": "2021-01-02T07:00:00", "end": "2021-01-02T07:30:00", "day": "2021-01-02"},
]
WINDOW = ("2021-01-01T22:00:00", "2021-01-02T08:00:00")  # 10 小时
EVALUATED = ["cooking", "sleeping"]

# MA 输出与真值完全对齐（理想情况）
MA_PERFECT = [
    {"activity": "sleeping", "day": "2021-01-01",
     "start_ts": "2021-01-01T23:00:00", "end_ts": "2021-01-02T06:00:00"},
    {"activity": "cooking", "day": "2021-01-02",
     "start_ts": "2021-01-02T07:00:00", "end_ts": "2021-01-02T07:30:00"},
]


def test_scores_computes_precision_recall_f1_mcc():
    # tp=4 fp=1 fn=1 tn=4 -> P=R=0.8, F1=0.8, MCC=(16-1)/sqrt(5*5*5*5)=0.6
    s = _scores(4, 1, 1, 4)
    assert s["precision"] == 0.8 and s["recall"] == 0.8
    assert s["f1"] == 0.8
    assert s["mcc"] == 0.6
    assert s["balanced_acc"] == 0.8


def test_baselines_high_base_rate_makes_always_positive_strong():
    # 基础率 13/14：傻瓜「永真」即可拿到 F1=0.963（这正是旧口径失效的原因）
    b = _baselines(13, 14)
    assert b["always_positive"]["f1"] == 0.963
    assert b["always_positive"]["recall"] == 1.0
    assert b["majority"]["f1"] == 0.963  # >=0.5 时 majority 退化为永真


def test_baselines_low_base_rate_majority_collapses_to_zero():
    # 基础率 1/14：majority 选择「永假」-> F1=0
    b = _baselines(1, 14)
    assert b["majority"]["f1"] == 0.0
    # 而永真基线依然很差：P=1/14=0.071, R=1 -> F1=0.133
    assert b["always_positive"]["f1"] == 0.133


def test_day_level_reports_no_gain_when_MA_equals_trivial():
    # 构造：MA 每天预测「全部活动」，真值也是每天都有 -> MA 与永真基线等价，lift<=0
    days = [f"2021-01-{d:02d}" for d in range(1, 11)]
    evaluated = ["sleeping"]
    gt_days = {d: ["sleeping"] for d in days}
    pred_days = {d: ["sleeping"] for d in days}
    out = day_level(gt_days, pred_days, days, evaluated)
    # 每天都有真值 -> 基础率 1.0，任何预测都「对」
    assert out["baselines"]["sleeping"]["base_rate"] == 1.0
    assert out["macro"]["lift"] <= 0.0, out["macro"]


def test_day_level_positive_lift_when_MA_beats_baseline():
    # 构造：10 天里只有 2 天真值阳性，MA 精确命中这 2 天（无 FP）
    days = [f"2021-01-{d:02d}" for d in range(1, 11)]
    evaluated = ["working"]
    gt_days = {days[0]: ["working"], days[1]: ["working"]}
    pred_days = {days[0]: ["working"], days[1]: ["working"]}
    out = day_level(gt_days, pred_days, days, evaluated)
    assert out["ma"]["working"]["f1"] == 1.0
    # 低基础率下 majority=0，永真基线也很低 -> 应有明显正增益
    assert out["macro"]["lift"] > 0.5, out["macro"]


def test_slot_level_perfect_localization_scores_one():
    out = slot_level(GT_SEGMENTS, MA_PERFECT, EVALUATED, *WINDOW, slot_seconds=3600)
    assert out["n_samples_per_activity"] == 10  # 22:00->08:00，每小时一槽
    assert out["n_MA_records_without_interval"] == 0
    assert out["ma"]["sleeping"]["f1"] == 1.0
    assert out["ma"]["cooking"]["f1"] == 1.0
    assert out["macro"]["ma"] == 1.0


def test_slot_level_has_discriminative_power_unlike_day_level():
    """时段级的关键价值：基础率骤降，傻瓜基线崩塌，从而能体现真实增益。"""
    out = slot_level(GT_SEGMENTS, MA_PERFECT, EVALUATED, *WINDOW, slot_seconds=3600)
    # cooking 基础率仅 1/10=0.1，永真基线 F1 只有 0.182
    assert out["baselines"]["cooking"]["base_rate"] == 0.1
    assert out["baselines"]["cooking"]["always_positive"]["f1"] == 0.182
    # 完美识别 -> 相对最强基线有明显正增益
    assert out["macro"]["lift"] > 0.4, out["macro"]
    assert out["macro"]["lift"] > 0


def test_slot_level_penalizes_wrong_localization():
    # MA 只覆盖了真实睡眠窗口中间一小段 -> 精确但召回低，F1 明显低于 1
    ma_partial = [
        {"activity": "sleeping", "day": "2021-01-01",
         "start_ts": "2021-01-02T02:00:00", "end_ts": "2021-01-02T04:00:00"},
        {"activity": "cooking", "day": "2021-01-02",
         "start_ts": "2021-01-02T07:00:00", "end_ts": "2021-01-02T07:30:00"},
    ]
    out = slot_level(GT_SEGMENTS, ma_partial, EVALUATED, *WINDOW, slot_seconds=3600)
    assert out["ma"]["cooking"]["f1"] == 1.0          # 仍完全对齐
    sleeping = out["ma"]["sleeping"]
    assert sleeping["recall"] < 0.5                    # 只盖到 2/7 个小时
    assert sleeping["f1"] < 1.0
    assert 0.0 < sleeping["f1"] < 1.0


def test_slot_level_and_segment_level_ignore_records_without_interval():
    # 仍可能有无区间的 MA 记录（如遥测兜底产出的日级活动），它们无法做时间定位，
    # 必须从时段级/段级评估中剔除并被如实计数，而不是被当成命中。
    ma_no_iv = [
        {"activity": "sleeping", "day": "2021-01-01", "start_ts": None, "end_ts": None},
    ]
    out = slot_level(GT_SEGMENTS, ma_no_iv, EVALUATED, *WINDOW, slot_seconds=3600)
    assert out["n_MA_records_without_interval"] == 1
    assert out["ma"]["sleeping"]["tp"] == 0
    assert out["ma"]["sleeping"]["f1"] == 0.0

    seg = segment_level(GT_SEGMENTS, ma_no_iv, EVALUATED)
    assert seg["n_MA_records_without_interval"] == 1
    assert seg["ma"]["sleeping"]["ma_records"] == 0


def test_segment_level_counts_overlap_and_false_positives():
    # 一条 MA 记录命中 GT 睡眠段，另一条同活动记录完全不重叠 -> FP=1
    ma = [
        {"activity": "sleeping", "day": "2021-01-01",
         "start_ts": "2021-01-01T23:00:00", "end_ts": "2021-01-02T06:00:00"},
        {"activity": "sleeping", "day": "2021-01-02",
         "start_ts": "2021-01-02T12:00:00", "end_ts": "2021-01-02T13:00:00"},
    ]
    out = segment_level(GT_SEGMENTS, ma, EVALUATED)
    sleeping = out["ma"]["sleeping"]
    assert sleeping["gt_segments"] == 1 and sleeping["ma_records"] == 2
    assert sleeping["tp"] == 1 and sleeping["fp"] == 1 and sleeping["fn"] == 0
    assert sleeping["precision"] == 0.5
    assert sleeping["recall"] == 1.0
    assert abs(sleeping["f1"] - 0.667) < 0.01


def test_baseline_keys_are_stable():
    # 打印/报告逻辑依赖这三个基线的键名顺序稳定性
    assert tuple(BASELINE_KEYS) == ("always_positive", "majority", "random")
