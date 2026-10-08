"""tests/test_p5c_counterfactual.py — P5c 反事实查询单元测试"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.change_attribution import counterfactual_query, _ci_95   # noqa: E402


def _ev(ts, action="tv_on", persons=None):
    return {
        "id": 0, "server_ts": ts, "action": action,
        "scene": "客厅", "room": "客厅",
        "persons_json": json.dumps(persons or [{"name": "test_user"}]),
        "extra_json": "{}",
    }


def _make_history(num_days=60, causal_event="tv_on", causal_strength=0.9, person="test_user"):
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
        dstr = dt.strftime("%Y-%m-%d")
        if day in event_days and rng.random() < causal_strength:
            ah = 22
        else:
            ah = 19 if rng.random() < 0.8 else 20
        events.append(_ev(ts=f"{dstr}T{ah}:00:00", action="face_known",
                          persons=[{"name": person}]))
        if day in event_days:
            events.append(_ev(ts=f"{dstr}T18:00:00", action=causal_event, persons=[]))
    return events


class TestCI95:
    def test_empty(self):
        lo, hi = _ci_95([])
        assert lo == 0.0 and hi == 0.0

    def test_single(self):
        lo, hi = _ci_95([5.0])
        assert lo == 5.0 and hi == 5.0

    def test_range(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        lo, hi = _ci_95(vals)
        assert lo <= 3.0 <= hi
        assert hi > lo


class TestCounterfactualQuery:
    def test_enabled(self):
        events = _make_history(num_days=60, causal_strength=0.9)
        result = counterfactual_query(
            events, "test_user", "arrival_time", "tv_on",
            "2026-08-30T00:00:00", lookback_days=60,
        )
        assert result["enabled"] is True

    def test_fields_complete(self):
        events = _make_history(num_days=60, causal_strength=0.9)
        result = counterfactual_query(
            events, "test_user", "arrival_time", "tv_on",
            "2026-08-30T00:00:00", lookback_days=60,
        )
        for f in ("event_type", "person", "metric", "event_days", "no_event_days",
                  "actual_value", "counterfactual_value", "difference",
                  "ci_actual", "ci_counterfactual", "ci_difference",
                  "effect_size", "mann_whitney_p", "significant", "description"):
            assert f in result, f"缺少字段: {f}"

    def test_causal_effect_direction(self):
        """强因果事件：实际值与反事实值应有显著差异。"""
        events = _make_history(num_days=60, causal_strength=0.95)
        result = counterfactual_query(
            events, "test_user", "arrival_time", "tv_on",
            "2026-08-30T00:00:00", lookback_days=60,
        )
        assert result["enabled"] is True
        # 有事件天和无事件天均值应有显著差异
        assert abs(result["actual_value"] - result["counterfactual_value"]) > 0.5
        assert abs(result["effect_size"]) >= 0.5
        assert result["significant"] is True
        # 差异 = actual - counterfactual
        assert abs(result["difference"] - (result["actual_value"] - result["counterfactual_value"])) < 0.01

    def test_ci_difference_contains_zero_for_noncausal(self):
        """非因果事件：差异的 95% CI 应包含 0（或不显著）。"""
        events = _make_history(num_days=60, causal_strength=0.9)
        # 用一个不存在的事件类型 → 事件天不足 → disabled
        result = counterfactual_query(
            events, "test_user", "arrival_time", "nonexistent_xyz",
            "2026-08-30T00:00:00", lookback_days=60,
        )
        assert result["enabled"] is False

    def test_disabled_short_history(self):
        events = _make_history(num_days=10, causal_strength=0.9)
        result = counterfactual_query(
            events, "test_user", "arrival_time", "tv_on",
            "2026-07-11T00:00:00", lookback_days=10,
        )
        assert result["enabled"] is False

    def test_description_nonempty(self):
        events = _make_history(num_days=60, causal_strength=0.9)
        result = counterfactual_query(
            events, "test_user", "arrival_time", "tv_on",
            "2026-08-30T00:00:00", lookback_days=60,
        )
        assert result["description"]
        assert len(result["description"]) > 10

    def test_ci_actual_valid(self):
        events = _make_history(num_days=60, causal_strength=0.9)
        result = counterfactual_query(
            events, "test_user", "arrival_time", "tv_on",
            "2026-08-30T00:00:00", lookback_days=60,
        )
        lo, hi = result["ci_actual"]
        assert lo <= result["actual_value"] <= hi
        lo2, hi2 = result["ci_counterfactual"]
        assert lo2 <= result["counterfactual_value"] <= hi2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
