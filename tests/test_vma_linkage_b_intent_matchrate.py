"""MA-AF 联动契约 v1 落地批 · B 档：意图规则面**离线回放匹配率**。

来源：2026-11-17 AF 联调实测六档实时窗口全零，A 档（实时调用）被方法打脸——
它只能看到"此刻"一个切面，量不出规则面厚薄。链主定 B 档：按**事件时间戳**
逐事件回放（该事件时刻当窗口末端），分母＝事件数、分子＝至少命中一条规则的事件数。

本锁：
1. 纯函数 `replay_intent_match` —— 逐类构造（不依赖 MCP SDK/store）；
2. 服务方法 `ActivityInferenceService.audit_intent_match` —— 数据源＝behavior_events，
   带 `truncated`/`window_days`/`query_start` 元信息；
3. 口径边界：⛔ 与序列规则 `near_miss` 混读（两把尺分工写死在 docstring）。
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.intent_inference import replay_intent_match  # noqa: E402


def _ev(ts, action="sensor_update", scene="humidity", names=("Kevin",), sg=None):
    ev = {"server_ts": ts, "action": action, "scene": scene,
          "persons": [{"name": n} for n in names]}
    if sg:
        ev["scene_graph_json"] = sg
    return ev


# ── 1) 纯函数：命中 / 未命中 / 分路径 ─────────────────────────────────

def test_replay_matches_watch_tv_and_reports_path():
    base = datetime(2026, 11, 16, 20, 0, 0)
    events = [
        _ev(base.isoformat(), action="tv_on"),
        _ev((base - timedelta(minutes=2)).isoformat(), action="light_on"),
    ]
    res = replay_intent_match(events, windows=(5,))
    assert res["events_total"] == 2
    win = res["windows"][0]
    assert win["window_min"] == 5
    assert win["matched_end_events"] >= 1, res
    assert win["match_rate"] > 0
    assert res["paths"].get("device_state", 0) >= 1, res
    assert res["as_of"].startswith("2026-11-16T20:00:00")


def test_replay_zero_match_keeps_top_unmatched_signature():
    base = datetime(2026, 11, 16, 20, 0, 0)
    events = [
        _ev(base.isoformat(), action="sensor_update"),
        _ev((base - timedelta(minutes=1)).isoformat(), action="battery_low"),
    ]
    res = replay_intent_match(events, windows=(5, 15))
    assert res["events_total"] == 2
    assert all(w["matched_end_events"] == 0 for w in res["windows"]), res
    assert res["top_unmatched"], "未命中模式必须可数"
    patterns = {item["pattern"] for item in res["top_unmatched"]}
    assert any("sensor_update" in p or "battery_low" in p for p in patterns), res


def test_replay_person_filter_makes_empty_result():
    events = [_ev("2026-11-16 20:00:00", action="tv_on")]
    res = replay_intent_match(events, windows=(5,), person="Nobody")
    assert res["events_total"] == 0
    assert res["windows"][0]["match_rate"] == 0.0


def test_replay_window_subset_only_returns_requested_windows():
    res = replay_intent_match([_ev("2026-11-16 20:00:00")], windows=(30,))
    assert len(res["windows"]) == 1
    assert res["windows"][0]["window_min"] == 30


def test_replay_handles_aware_and_naive_timestamps_together():
    events = [
        _ev("2026-11-16T12:00:00+00:00", action="tv_on"),   # = 本地 20:00
        _ev("2026-11-16 20:01:00", action="light_on"),
    ]
    res = replay_intent_match(events, windows=(5,))
    assert res["events_total"] == 2, "aware/naive 混排不许把任一条丢掉"


# ── 2) 服务方法：数据源＋元信息 ───────────────────────────────────────

class _Store:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def list_behavior_events(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows


def _service(rows):
    from memory_agent.activity_inference import ActivityInferenceService

    svc = ActivityInferenceService.__new__(ActivityInferenceService)
    svc.config = SimpleNamespace(tz_offset_hours=8.0)
    svc.store = _Store(rows)
    return svc


def test_service_queries_behavior_events_with_day_window_and_flags_truncation():
    base = datetime(2026, 11, 16, 20, 0, 0)
    rows = [_ev(base.isoformat(), action="tv_on"),
            _ev((base - timedelta(minutes=1)).isoformat(), action="light_on")]
    svc = _service(rows)
    res = svc.audit_intent_match(days=7, windows=(5,), max_events=2)
    assert res["ok"] is True, res
    assert res["window_days"] == 7
    assert res["query_start"], "必须回带查询起点，否则读数无法复核"
    assert res["truncated"] is True, "取数触顶必须显式标记"
    assert svc.store.calls and svc.store.calls[0].get("day_from"), svc.store.calls


def test_service_does_not_flag_truncation_when_below_cap():
    svc = _service([_ev("2026-11-16 20:00:00")])
    res = svc.audit_intent_match(days=7, windows=(5,), max_events=5000)
    assert res["ok"] is True and res["truncated"] is False, res


def test_service_empty_store_still_ok_with_zero_rates():
    svc = _service([])
    res = svc.audit_intent_match(days=7, windows=(5,))
    assert res["ok"] is True, res
    assert res["events_total"] == 0
    assert res["windows"][0]["match_rate"] == 0.0
