"""路线图 3.5：MCP 查询工具面补全 —— summary_queries 三个只读汇总工具的测试。

纯 store 层测试（不需要 mcp SDK / runtime）：
1. device_usage_summary：时长积分口径（窗口前已开启 / 窗口末未闭合 / 去抖 / 无数据置 None）；
2. room_behavior_summary：活动分布（推断活动+视觉动作）+ 24 小时直方图（剔除遥测域）；
3. member_daily_pattern：成员隔离 fail-closed + 当日时间线合并；
4. mcp_scopes / tool_schema 登记完整性（新工具必须登记，否则 scope 拒绝调用）。
"""
import os
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.store import Store  # noqa: E402
from memory_agent.summary_queries import (  # noqa: E402
    device_usage_summary,
    member_daily_pattern,
    room_behavior_summary,
)

NEW_TOOLS = (
    "get_device_usage_summary",
    "get_room_behavior_summary",
    "get_member_daily_pattern",
)


@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="ma_sumq_")
    s = Store(os.path.join(tmp, "test.db"), tz_offset_hours=0.0)
    s.init_schema()
    return s


def _evt(entity, ts, new_state, room="", domain="light"):
    return {"entity_id": entity, "ts": ts, "new_state": new_state,
            "old_state": "on" if new_state == "off" else "off",
            "room": room, "domain": domain}


# ── 1. device_usage_summary ──────────────────────────────────────────────────

def test_device_basic_session(store):
    store.insert_events([
        _evt("light.desk", "2026-03-01T10:00:00", "on"),
        _evt("light.desk", "2026-03-01T11:00:00", "off"),
    ])
    out = device_usage_summary(store, "light.desk",
                               start="2026-03-01", end="2026-03-01")
    assert out["ok"] is True
    assert out["total_on_minutes"] == 60.0
    assert out["on_off_count"] == 1
    assert out["average_session_minutes"] == 60.0
    assert out["no_data"] is False


def test_device_open_before_window_counts_from_left_edge(store):
    store.insert_events([
        _evt("light.desk", "2026-03-01T07:00:00", "on"),   # 窗口开始前已开启
        _evt("light.desk", "2026-03-01T08:30:00", "off"),
    ])
    out = device_usage_summary(store, "light.desk",
                               start="2026-03-01T08:00:00",
                               end="2026-03-01T12:00:00")
    assert out["total_on_minutes"] == 30.0
    assert out["on_off_count"] == 1


def test_device_open_at_window_end_truncated(store):
    store.insert_events([_evt("light.desk", "2026-03-01T09:00:00", "on")])
    out = device_usage_summary(store, "light.desk",
                               start="2026-03-01T08:00:00",
                               end="2026-03-01T10:00:00")
    assert out["total_on_minutes"] == 60.0
    assert out["on_off_count"] == 1
    assert out["window_open_session"] is True


def test_device_debounce_drops_flaps(store):
    store.insert_events([
        _evt("light.desk", "2026-03-01T09:00:00", "on"),
        _evt("light.desk", "2026-03-01T09:00:02", "off"),  # 2s < 默认去抖 5s
    ])
    out = device_usage_summary(store, "light.desk",
                               start="2026-03-01", end="2026-03-01")
    assert out["on_off_count"] == 0
    assert out["total_on_minutes"] == 0.0
    assert out["average_session_minutes"] is None


def test_device_climate_heat_is_on(store):
    store.insert_events([
        _evt("climate.ac", "2026-03-01T10:00:00", "heat", domain="climate"),
        _evt("climate.ac", "2026-03-01T10:30:00", "off", domain="climate"),
    ])
    out = device_usage_summary(store, "climate.ac",
                               start="2026-03-01", end="2026-03-01")
    assert out["total_on_minutes"] == 30.0


def test_device_no_events_returns_none_fields(store):
    out = device_usage_summary(store, "light.ghost",
                               start="2026-03-01", end="2026-03-01")
    assert out["ok"] is True
    assert out["no_data"] is True
    assert out["total_on_minutes"] is None
    assert out["on_off_count"] is None
    assert out["average_session_minutes"] is None


def test_device_multiple_entities_aggregated(store):
    store.insert_events([
        _evt("light.a", "2026-03-01T10:00:00", "on"),
        _evt("light.a", "2026-03-01T11:00:00", "off"),
        _evt("light.b", "2026-03-01T12:00:00", "on"),
        _evt("light.b", "2026-03-01T13:00:00", "off"),
    ])
    out = device_usage_summary(store, "light.a,light.b",
                               start="2026-03-01", end="2026-03-01")
    assert out["total_on_minutes"] == 120.0
    assert out["on_off_count"] == 2
    assert out["average_session_minutes"] == 60.0
    assert len(out["per_entity"]) == 2


def test_device_requires_entity_id(store):
    out = device_usage_summary(store, "")
    assert out["ok"] is False and "INVALID_PARAM" in out["error"]


# ── 2. room_behavior_summary ─────────────────────────────────────────────────

def test_room_summary_distribution_and_hourly(store):
    store.add_behavior_state("爸爸", "客厅", "看电视", 0.8,
                             ts="2026-03-01T20:00:00")
    store.add_behavior_state("爸爸", "客厅", "看电视", 0.9,
                             ts="2026-03-01T20:30:00")
    store.add_behavior_state("", "客厅", "走动", 0.6, ts="2026-03-01T08:00:00")
    store.insert_behavior_event({
        "server_ts": "2026-03-01T21:00:00", "day": "2026-03-01", "room": "客厅",
        "action": "坐在沙发看书", "persons": [{"name": "爸爸"}], "status": "ok",
    })
    store.insert_behavior_event({
        "server_ts": "2026-03-01T21:10:00", "day": "2026-03-01", "room": "客厅",
        "action": "失败动作", "persons": [], "status": "vlm_failed",
    })
    store.insert_events([
        _evt("light.tv", "2026-03-01T20:05:00", "on", room="客厅"),
        {"entity_id": "sensor.power", "ts": "2026-03-01T20:06:00",
         "new_state": "35", "room": "客厅", "domain": "sensor"},
    ])
    out = room_behavior_summary(store, "客厅",
                                start="2026-03-01", end="2026-03-01")
    assert out["ok"] is True
    inferred = {d["activity"]: d["count"] for d in out["activity_distribution"]
                if d["source"] == "inferred_state"}
    vision = {d["activity"]: d["count"] for d in out["activity_distribution"]
              if d["source"] == "vision_action"}
    assert inferred == {"看电视": 2, "走动": 1}
    assert vision == {"坐在沙发看书": 1}  # status!=ok 的已过滤
    assert out["hourly_activity"]["20"] == 1  # 遥测域 sensor 不进直方图
    assert out["device_event_count"] == 1
    assert out["no_data"] is False


def test_room_requires_room(store):
    out = room_behavior_summary(store, "")
    assert out["ok"] is False and "INVALID_PARAM" in out["error"]


def test_room_no_data(store):
    out = room_behavior_summary(store, "书房", start="2026-03-01", end="2026-03-01")
    assert out["ok"] is True
    assert out["no_data"] is True
    assert out["activity_distribution"] == []


# ── 3. member_daily_pattern ──────────────────────────────────────────────────

def test_member_daily_timeline(store):
    member = store.create_member(name="爸爸")
    store.add_behavior_state("爸爸", "客厅", "看电视", 0.8,
                             ts="2026-03-01T20:00:00")
    store.add_behavior_state("爸爸", "书房", "工作", 0.7,
                             ts="2026-03-01T09:00:00")
    store.insert_behavior_event({
        "server_ts": "2026-03-01T12:00:00", "day": "2026-03-01", "room": "厨房",
        "action": "做饭", "persons": [{"name": "爸爸", "confidence": 0.9}],
        "status": "ok",
    })
    out = member_daily_pattern(store, member["id"], date="2026-03-01")
    assert out["ok"] is True
    assert out["member_name"] == "爸爸"
    times = [t["time"] for t in out["timeline"]]
    assert times == sorted(times)
    assert times == ["09:00", "12:00", "20:00"]
    kinds = [t["kind"] for t in out["timeline"]]
    assert kinds == ["activity_state", "vision_action", "activity_state"]
    assert out["summary"]["rooms_visited"] == ["书房", "厨房", "客厅"]
    assert out["summary"]["first_seen"] == "2026-03-01T09:00:00"
    assert out["summary"]["last_seen"] == "2026-03-01T20:00:00"
    assert out["no_data"] is False


def test_member_daily_isolates_members(store):
    dad = store.create_member(name="爸爸")
    mom = store.create_member(name="妈妈")
    store.add_behavior_state("爸爸", "书房", "工作", 0.7, ts="2026-03-01T09:00:00")
    out = member_daily_pattern(store, mom["id"], date="2026-03-01")
    assert out["ok"] is True and out["count"] == 0 and out["no_data"] is True


def test_member_daily_fail_closed(store):
    out = member_daily_pattern(store, "")
    assert out["ok"] is False and "member_id 缺失" in out["error"]
    out2 = member_daily_pattern(store, "no-such-id", date="2026-03-01")
    assert out2["ok"] is False and "NOT_FOUND" in out2["error"]
    member = store.create_member(name="爸爸")
    out3 = member_daily_pattern(store, member["id"], date="20260301")
    assert out3["ok"] is False and "INVALID_PARAM" in out3["error"]


# ── 4. 登记完整性（scope 断言 / 写工具不漏登 / help 目录同源）────────────────

def test_new_tools_registered_as_read():
    from memory_agent.mcp_scopes import REGISTERED_TOOLS, requires, scope_of

    for name in NEW_TOOLS:
        assert name in REGISTERED_TOOLS, f"{name} 未登记 REGISTERED_TOOLS"
        assert scope_of(name) == "read"
        assert requires(name, ["read"]) is True


def test_write_tools_assertion_still_passes():
    from memory_agent.mcp_scopes import assert_write_tools_complete

    assert assert_write_tools_complete() == []


def test_new_tools_in_schema_catalog():
    from memory_agent.tool_schema import SPEC_BY_NAME, TOOL_NAMES, build_catalog

    catalog_names = {c["name"] for c in build_catalog()}
    for name in NEW_TOOLS:
        assert name in TOOL_NAMES
        assert name in catalog_names
        spec = SPEC_BY_NAME[name]
        assert spec.summary and spec.description and spec.example and spec.pitfall
        assert spec.expose == ("mcp",)
