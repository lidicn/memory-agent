"""MA-AF 联动契约 v1 落地批 · C2：`person` 聚合值必须显式拒绝（⛔ 假绿）。

来源：2026-11-17 AF 联调实测——`get_behavior_prediction(person="all")` 返回
`ok:true + data_days=0 + arrival/routine 全 null`。统计层把 "all" 当人名，
谁都匹配不上，于是"没数据"与"人不存在"与"参数错"三种状态长得一模一样；
AF 侧据此报「预测引擎空转」，实际引擎与数据都通（真实成员名 confidence
0.66–1.0）。这是本仓「恒空但 ok:true」假绿形状的第二例（第一例见
`behavior_predictor.person_names` 注释）。

本锁两层：
1. 纯函数守卫 `validate_prediction_person` —— 不依赖 MCP SDK，任何环境都跑；
2. MCP 工具级行为 —— 参数拒绝发生在扫 5000 行**之前**，空库/无该成员
   一律 `DATA_INSUFFICIENT`（契约 v1 §2.1 三族），⛔ 回 ok:true。
"""

import asyncio
import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.behavior_predictor import validate_prediction_person  # noqa: E402


# ── 1) 纯守卫：聚合值/空值一律拒绝 ────────────────────────────────────

@pytest.mark.parametrize("bad", ["", "   ", "all", "ALL", "All", "*", "全部", "any", " any "])
def test_guard_rejects_aggregate_and_empty(bad):
    res = validate_prediction_person(bad)
    assert res is not None, f"{bad!r} 应被拒绝"
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_PARAM", res
    # 指路信息必须给出来，否则调用方只会反复猜参数
    assert "list_members" in res["error"]["message"], res


@pytest.mark.parametrize("ok_name", ["Kevin", "Emily", "lidicn", "  Kevin  "])
def test_guard_accepts_real_member_names(ok_name):
    assert validate_prediction_person(ok_name) is None, ok_name


# ── 2) MCP 工具级：拒绝在扫库之前；空数据回 DATA_INSUFFICIENT ─────────

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
    tool = tools.get("get_behavior_prediction")
    assert tool is not None, "get_behavior_prediction 不在已注册工具表里"
    return tool, ms


def test_tool_rejects_aggregate_before_scan(monkeypatch):
    """AF 那条读数的直接回归：person="all" 必须拒绝，且⛔ 先扫 5000 行。"""
    tool, ms = _tool()
    rt = _fake_runtime([])
    monkeypatch.setattr(ms, "get_runtime", lambda: rt)

    res = asyncio.run(tool.fn(person="all", weekday=-1))
    assert res.get("ok") is False, res
    assert res["error"]["code"] == "INVALID_PARAM", res
    assert rt.store.calls == 0, "参数拒绝必须发生在读库之前"


def test_tool_empty_event_table_is_data_insufficient(monkeypatch):
    tool, ms = _tool()
    rt = _fake_runtime([])
    monkeypatch.setattr(ms, "get_runtime", lambda: rt)

    res = asyncio.run(tool.fn(person="Kevin", weekday=-1))
    assert res.get("ok") is False, res
    assert res["error"]["code"] == "DATA_INSUFFICIENT", res


def test_tool_unknown_member_is_data_insufficient_not_ok(monkeypatch):
    """有事件、但这个人一天都没出现过 ⇒ DATA_INSUFFICIENT，⛔ ok:true+全 null。"""
    tool, ms = _tool()
    rows = [
        {"server_ts": "2026-10-01 19:15:00", "persons": [{"name": "Kevin"}],
         "action": "home", "scene": ""},
    ]
    rt = _fake_runtime(rows)
    monkeypatch.setattr(ms, "get_runtime", lambda: rt)

    res = asyncio.run(tool.fn(person="Nobody", weekday=-1))
    assert res.get("ok") is False, res
    assert res["error"]["code"] == "DATA_INSUFFICIENT", res
    assert res.get("data_days") == 0, res


def test_tool_valid_member_still_serves(monkeypatch):
    """反例锁：守卫⛔ 把正常路径一起拦掉（数据够时照常 ok:true）。"""
    tool, ms = _tool()
    rows = [
        {"server_ts": f"{day} {hour}:00", "persons": [{"name": "Kevin"}],
         "action": "home", "scene": ""}
        for day, hour in [
            ("2026-10-01", "19:15"), ("2026-09-30", "18:45"),
            ("2026-09-29", "19:00"), ("2026-09-28", "18:30"),
        ]
    ]
    rt = _fake_runtime(rows)
    monkeypatch.setattr(ms, "get_runtime", lambda: rt)

    res = asyncio.run(tool.fn(person="Kevin", weekday=-1))
    assert res.get("ok") is True, res
    assert res["arrival_prediction"] is not None, res
    assert res["daily_routine"]["data_days"] == 4, res
