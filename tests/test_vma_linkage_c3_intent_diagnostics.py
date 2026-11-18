"""MA-AF 联动契约 v1 落地批 · C3：意图空转必须可归因（⛔ 一种空转五种解释）。

来源：2026-11-17 AF 联调实测——`infer_behavior_intent(window_min=5/15/30/60/120/240)`
六档全零；调用方无法区分「库没数据」「此人没数据」「窗口内没事件」「事件太少」
「有事件但规则没命中」。AF-PM 现场判为"正确空转"，但读数形状上它和"规则面全坏"
完全一样，这正是 DCD 20261005 §三 Q2「不允许静默忽略」那一族。

本锁两层：
1. 纯函数 `diagnose_intent_windows` —— 五类归因逐个构造（不依赖 MCP SDK）；
2. MCP 工具级 —— 空库回 `DATA_INSUFFICIENT`（契约 v1 §2.1 三族），
   空结果带 `reason`＋`as_of`＋`diagnostics`。
"""

import asyncio
import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.intent_inference import diagnose_intent_windows  # noqa: E402


def _ev(ts, action="sensor_update", scene="humidity", names=("Kevin",)):
    return {"server_ts": ts, "action": action, "scene": scene,
            "persons": [{"name": n} for n in names]}


# ── 1) 纯函数：五类归因 ───────────────────────────────────────────────

def test_reason_no_events_on_empty_library():
    diag = diagnose_intent_windows([])
    assert diag["reason"] == "no_events"
    assert diag["events_total"] == 0
    assert diag["matched_any"] is False


def test_reason_no_events_when_timestamps_unparseable():
    diag = diagnose_intent_windows([{"server_ts": "", "action": "x"}])
    assert diag["reason"] == "no_events", diag


def test_reason_person_empty_when_member_absent():
    events = [_ev("2026-11-16 20:00:00"), _ev("2026-11-16 19:58:00")]
    diag = diagnose_intent_windows(events, person="Nobody")
    assert diag["reason"] == "person_empty", diag
    assert diag["person"] == "Nobody"


def test_reason_insufficient_events_when_single_event():
    diag = diagnose_intent_windows([_ev("2026-11-16 20:00:00")])
    assert diag["reason"] == "insufficient_events", diag
    assert diag["windows"][0]["events_in_window"] == 1


def test_reason_no_rule_match_when_events_but_no_rule_hits():
    events = [_ev("2026-11-16 20:00:00", action="sensor_update"),
              _ev("2026-11-16 19:59:00", action="battery_low")]
    diag = diagnose_intent_windows(events)
    assert diag["reason"] == "no_rule_match", diag
    assert diag["matched_any"] is False
    assert all(w["matched"] is None for w in diag["windows"])


def test_reason_matched_and_window_detail():
    # watch_tv 规则：主触发 tv_on（无时间窗限制）
    events = [_ev("2026-11-16 20:00:00", action="tv_on"),
              _ev("2026-11-16 19:59:00", action="light_on")]
    diag = diagnose_intent_windows(events)
    assert diag["reason"] == "matched", diag
    assert diag["matched_any"] is True
    hit = [w for w in diag["windows"] if w["matched"]]
    assert hit and hit[0]["matched"] == "watch_tv", diag
    assert diag["as_of"].startswith("2026-11-16T20:00:00"), diag


def test_window_subset_respected_when_window_min_given():
    events = [_ev("2026-11-16 20:00:00"), _ev("2026-11-16 19:40:00")]
    diag = diagnose_intent_windows(events, windows=(5,))
    assert len(diag["windows"]) == 1
    assert diag["windows"][0]["events_in_window"] == 1, diag


# ── 2) MCP 工具级：空库 DATA_INSUFFICIENT；空结果带归因 ─────────────────

def _tools_by_name():
    from memory_agent import mcp_server as ms

    server = getattr(ms, "mcp_server", None)
    if server is None:
        return None, ms
    manager = getattr(server, "_tool_manager", None)
    tools = getattr(manager, "_tools", None) if manager else None
    return (tools if isinstance(tools, dict) else None), ms


class _Store:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def list_behavior_events(self, limit=None):
        self.calls += 1
        return self.rows


def _fake_runtime(rows):
    config = type("Cfg", (), {"tz_offset_hours": 8.0})()
    return type("RT", (), {"store": _Store(rows), "config": config})()


def _tool():
    tools, ms = _tools_by_name()
    if tools is None:
        pytest.skip("MCP SDK 不可用或版本过旧，无法取到已注册工具函数")
    tool = tools.get("infer_behavior_intent")
    assert tool is not None, "infer_behavior_intent 不在已注册工具表里"
    return tool, ms


def test_tool_empty_library_is_data_insufficient(monkeypatch):
    tool, ms = _tool()
    rt = _fake_runtime([])
    monkeypatch.setattr(ms, "get_runtime", lambda: rt)
    res = asyncio.run(tool.fn(person="", window_min=None, limit=3))
    assert res.get("ok") is False, res
    assert res["error"]["code"] == "DATA_INSUFFICIENT", res


def test_tool_empty_result_carries_reason_and_diagnostics(monkeypatch):
    tool, ms = _tool()
    rows = [_ev("2026-11-16 20:00:00", action="sensor_update"),
            _ev("2026-11-16 19:59:00", action="battery_low")]
    rt = _fake_runtime(rows)
    monkeypatch.setattr(ms, "get_runtime", lambda: rt)
    res = asyncio.run(tool.fn(person="", window_min=30, limit=3))
    assert res.get("ok") is True, res
    assert res.get("count") == 0
    assert res.get("reason") == "no_rule_match", res
    assert res.get("diagnostics", {}).get("person") == ""
    assert res.get("as_of", "").startswith("2026-11-16T20:00:00"), res
