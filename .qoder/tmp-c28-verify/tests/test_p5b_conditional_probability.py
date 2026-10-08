"""tests/test_p5b_conditional_probability.py — P5b 条件概率建模（分组比较法）单元测试"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.change_attribution import (   # noqa: E402
    _fisher_exact_2x2,
    analyze_conditional_causes,
    attribute_with_conditional,
)


def _ev(ts, action="tv_on", persons=None):
    import json as _json
    return {
        "id": 0, "server_ts": ts, "action": action,
        "scene": "客厅", "room": "客厅",
        "persons_json": _json.dumps(persons or [{"name": "test_user"}]),
        "extra_json": "{}",
    }


def _make_history(num_days=60, causal_event="tv_on", causal_strength=0.9, person="test_user"):
    """构造合成历史：causal_event 发生的天，person 到家时间更晚。"""
    import random
    rng = random.Random(42)
    events = []
    base = datetime(2026, 7, 1)
    event_days = set()
    for day in range(num_days):
        if rng.random() < 0.5:
            event_days.add(day)
    for day in range(num_days):
        dt = base + timedelta(days=day)
        if day in event_days and rng.random() < causal_strength:
            arrival_hour = 22
        else:
            arrival_hour = 19 if rng.random() < 0.8 else 20
        events.append(_ev(
            ts=f"{dt.strftime('%Y-%m-%d')}T{arrival_hour}:00:00",
            action="face_known", persons=[{"name": person}],
        ))
        if day in event_days:
            events.append(_ev(
                ts=f"{dt.strftime('%Y-%m-%d')}T18:00:00",
                action=causal_event, persons=[],
            ))
    return events


class TestFisherExact:
    def test_strong(self):
        p = _fisher_exact_2x2(8, 2, 2, 8)
        assert p < 0.05

    def test_none(self):
        p = _fisher_exact_2x2(5, 5, 5, 5)
        assert p > 0.05

    def test_zero(self):
        p = _fisher_exact_2x2(0, 0, 0, 0)
        assert p == 1.0

    def test_range(self):
        for a in range(4):
            for b in range(4):
                for c in range(4):
                    for d in range(4):
                        p = _fisher_exact_2x2(a, b, c, d)
                        assert 0.0 <= p <= 1.0


class TestConditionalAnalysis:
    def test_enabled(self):
        events = _make_history(num_days=60, causal_strength=0.9)
        result = analyze_conditional_causes(
            events, "test_user", "arrival_time", "2026-08-30T00:00:00",
            lookback_days=60, candidate_event_types=["tv_on"],
        )
        assert result["enabled"] is True
        assert result["method"] == "group_comparison"
        assert len(result["causes"]) >= 1

    def test_disabled_short(self):
        events = _make_history(num_days=10, causal_strength=0.9)
        result = analyze_conditional_causes(
            events, "test_user", "arrival_time", "2026-07-11T00:00:00",
            lookback_days=10, candidate_event_types=["tv_on"],
        )
        assert result["enabled"] is False

    def test_causal_lift_gt_one(self):
        """强因果事件：有事件天与无事件天的行为指标有显著差异。"""
        events = _make_history(num_days=60, causal_strength=0.95)
        result = analyze_conditional_causes(
            events, "test_user", "arrival_time", "2026-08-30T00:00:00",
            lookback_days=60, candidate_event_types=["tv_on"],
        )
        assert result["enabled"] is True
        cause = result["causes"][0]
        assert cause["event_type"] == "tv_on"
        # 分组比较：有事件天与无事件天均值应有显著差异
        assert abs(cause["mean_event"] - cause["mean_no_event"]) > 0.5, \
            f"两组均值差异应 > 0.5，实际 {cause['mean_event']} vs {cause['mean_no_event']}"
        assert abs(cause["effect_size"]) >= 0.5, \
            f"Cohen's d={cause['effect_size']} 绝对值应 >= 0.5"
        assert cause["significant"] is True

    def test_noncausal_no_association(self):
        """不存在的事件类型 → 不会出现在 causes 中（因 event_days 不足被跳过）。"""
        events = _make_history(num_days=60, causal_strength=0.9)
        result = analyze_conditional_causes(
            events, "test_user", "arrival_time", "2026-08-30T00:00:00",
            lookback_days=60, candidate_event_types=["nonexistent_xyz"],
        )
        assert result["enabled"] is True
        # 不存在的事件 → event_days=0 < min_event_days → 被跳过
        assert len(result["causes"]) == 0

    def test_fields_complete(self):
        events = _make_history(num_days=60, causal_strength=0.9)
        result = analyze_conditional_causes(
            events, "test_user", "arrival_time", "2026-08-30T00:00:00",
            lookback_days=60, candidate_event_types=["tv_on"],
        )
        cause = result["causes"][0]
        for f in ("event_type", "cause_type", "event_days", "no_event_days",
                  "mean_event", "mean_no_event", "effect_size", "mann_whitney_p",
                  "p_change_given_event", "p_change_given_no_event",
                  "conditional_lift", "causal_strength", "fisher_p",
                  "significant", "description"):
            assert f in cause, f"缺少字段: {f}"


class TestAttributeWithConditional:
    def test_full_flow(self):
        events = _make_history(num_days=60, causal_strength=0.95)
        result = attribute_with_conditional(
            events, "test_user", "arrival_time",
            split_ratio=0.5, lookback_days=7, conditional_lookback_days=60,
        )
        assert "conditional_analysis" in result
        if result["changed"]:
            assert result["conditional_analysis"]["enabled"] in (True, False)

    def test_no_change(self):
        events = []
        base = datetime(2026, 7, 1)
        for day in range(60):
            dt = base + timedelta(days=day)
            events.append(_ev(
                ts=f"{dt.strftime('%Y-%m-%d')}T19:00:00",
                action="face_known", persons=[{"name": "test_user"}],
            ))
        result = attribute_with_conditional(
            events, "test_user", "arrival_time",
            split_ratio=0.5, lookback_days=7, conditional_lookback_days=60,
        )
        assert result["changed"] is False
        assert result["conditional_analysis"]["enabled"] is False

    def test_short_history_degrades(self):
        events = _make_history(num_days=10, causal_strength=0.9)
        result = attribute_with_conditional(
            events, "test_user", "arrival_time",
            split_ratio=0.5, lookback_days=7, conditional_lookback_days=10,
        )
        assert "conditional_analysis" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
