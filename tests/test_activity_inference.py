"""v0.9.5 行为推断层单测：有序序列匹配 / 去抖 / 多传感器融合 / 低置信降级。"""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.activity_inference import (  # noqa: E402
    ActivityInferenceService,
    _in_time_window,
)
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.store import Store  # noqa: E402


class _FakeInsights:
    """复用真实 InsightService 的标签推断（classmethod，无需实例状态）。"""

    def _tags_of(self, eid, name):
        return InsightService._tags_of(eid, name)


def make_runtime(store, **cfg_kw):
    cfg = SimpleNamespace(
        tz_offset_hours=0.0,
        rooms={},
        activity_window_minutes=cfg_kw.get("activity_window_minutes", 120),
        pir_debounce_sec=cfg_kw.get("pir_debounce_sec", 30),
        activity_conf_threshold=cfg_kw.get("activity_conf_threshold", 0.6),
    )
    return SimpleNamespace(config=cfg, store=store, insights=_FakeInsights())


@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="ma_act_")
    db = os.path.join(tmp, "test.db")
    s = Store(db, tz_offset_hours=0.0)
    s.init_schema()
    yield s


def _ev(store, ts, eid, room, state):
    store.insert_events([{
        "entity_id": eid, "ts": ts, "room": room,
        "domain": eid.split(".")[0], "new_state": state, "old_state": "",
    }])


def test_sequence_match_in_order(store):
    base = datetime(2026, 9, 15, 10, 0, 0)
    t = lambda m: (base + timedelta(minutes=m)).isoformat()  # noqa: E731
    _ev(store, t(0), "binary_sensor.study_door_contact", "书房", "on")
    _ev(store, t(2), "binary_sensor.study_door_contact", "书房", "off")
    _ev(store, t(4), "light.study_ceiling", "书房", "on")
    _ev(store, t(6), "climate.study_ac", "书房", "cool")
    _ev(store, t(8), "switch.study_pc", "书房", "on")
    res = ActivityInferenceService(make_runtime(store)).run(start=t(-1), end=t(30))
    assert any(d["activity"] == "study_work" and d["kind"] == "state" for d in res["detail"])
    assert res["persisted"] >= 1
    rows = store.list_behavior_states()
    assert any(r["activity"] == "study_work" and r["room"] == "书房" for r in rows)


def test_reverse_order_not_matched(store):
    base = datetime(2026, 9, 15, 10, 0, 0)
    t = lambda m: (base + timedelta(minutes=m)).isoformat()  # noqa: E731
    # 逆序：电脑→空调→灯→门关→门开
    _ev(store, t(0), "switch.study_pc", "书房", "on")
    _ev(store, t(2), "climate.study_ac", "书房", "cool")
    _ev(store, t(4), "light.study_ceiling", "书房", "on")
    _ev(store, t(6), "binary_sensor.study_door_contact", "书房", "off")
    _ev(store, t(8), "binary_sensor.study_door_contact", "书房", "on")
    res = ActivityInferenceService(make_runtime(store)).run(start=t(-1), end=t(30))
    assert all(d["activity"] != "study_work" for d in res["detail"])


def test_low_confidence_only_candidate(store):
    base = datetime(2026, 9, 15, 14, 0, 0)
    t = lambda m: (base + timedelta(minutes=m)).isoformat()  # noqa: E731
    _ev(store, t(0), "binary_sensor.hall_door_contact", "走廊", "on")
    _ev(store, t(2), "binary_sensor.hall_door_contact", "走廊", "off")
    res = ActivityInferenceService(make_runtime(store)).run(start=t(-1), end=t(30))
    # transit 置信 0.4 < 阈值 0.6 → 只进候选，不污染权威状态
    assert res["candidates"] >= 1
    assert store.list_behavior_states() == []
    assert any(r["infer"] == "transit" for r in store.list_candidate_rules())


def test_pir_debounce(store):
    svc = ActivityInferenceService(make_runtime(store, pir_debounce_sec=30))
    base = datetime(2026, 9, 15, 10, 0, 0)
    evs = [
        {"ts": (base + timedelta(seconds=0)).isoformat(), "entity_id": "binary_sensor.pir"},
        {"ts": (base + timedelta(seconds=5)).isoformat(), "entity_id": "binary_sensor.pir"},
        {"ts": (base + timedelta(seconds=40)).isoformat(), "entity_id": "binary_sensor.pir"},
    ]
    out = svc._debounce(evs, 30)
    assert len(out) == 2  # 第 2 条落在 30s 抖动窗内被丢弃


def test_time_window_cross_midnight():
    assert _in_time_window("2026-09-15T22:30:00", "21:00-02:00")
    assert _in_time_window("2026-09-15T01:30:00", "21:00-02:00")
    assert not _in_time_window("2026-09-15T12:00:00", "21:00-02:00")
    assert _in_time_window("2026-09-15T12:00:00", "")


def test_sleep_sequence_with_time_window(store):
    base = datetime(2026, 9, 15, 22, 0, 0)
    t = lambda m: (base + timedelta(minutes=m)).isoformat()  # noqa: E731
    _ev(store, t(0), "binary_sensor.bedroom_door_contact", "主卧室", "off")
    _ev(store, t(2), "light.bedroom_ceiling", "主卧室", "on")
    _ev(store, t(5), "climate.bedroom_ac", "主卧室", "cool")
    _ev(store, t(10), "light.bedroom_ceiling", "主卧室", "off")
    res = ActivityInferenceService(make_runtime(store)).run(start=t(-1), end=t(30))
    assert any(d["activity"] == "user_asleep" for d in res["detail"])


def test_mine_sequences(store):
    """任务 C：同一有序序列重复出现 → 自动挖掘为候选规则。"""
    base = datetime(2026, 9, 15, 20, 0, 0)
    t = lambda m: (base + timedelta(minutes=m)).isoformat()  # noqa: E731
    for d in range(3):  # 连续 3 天重复 door(on)→light(on)→climate(on)
        off = d * 1440
        _ev(store, t(off + 0), "binary_sensor.study_door_contact", "书房", "on")
        _ev(store, t(off + 1), "light.study_ceiling", "书房", "on")
        _ev(store, t(off + 2), "climate.study_ac", "书房", "cool")
    res = ActivityInferenceService(make_runtime(store)).mine_sequences(
        start=t(-1), end=t(3 * 1440), days=7, min_support=2)
    assert res["ok"] and res["candidates"] >= 1
    rules = store.list_candidate_rules()
    assert any(r["source"] == "researcher" for r in rules)


def test_infer_habits(store):
    """任务 D：重复活动（3 天）→ 沉淀为成员习惯写 agent_memories。"""
    for d in range(3):
        store.add_behavior_state(
            member="lidicn", room="主卧室", activity="user_asleep",
            confidence=0.8, ts=f"2026-09-{13 + d:02d}T22:30:00")
    captured: dict = {}

    class _FakeMem:
        def merge_semantic_memory(self, **kw):
            captured.update(kw)
            return {"ok": True, "action": "added", "memory_id": "m1"}

    rt = make_runtime(store)
    rt.agent_memory = _FakeMem()
    res = ActivityInferenceService(rt).infer_habits(days=14, min_days=3)
    assert res["saved"] == 1
    assert "lidicn" in captured.get("text", "")
    assert "habit" in (captured.get("tags") or [])
