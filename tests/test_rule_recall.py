"""P1.4 规则召回审计单测：eligible/matched/near-miss + 卡点诊断 + 放宽建议。

背景：spike 实测手写规则**高精确/低召回**（sleeping P=0.988/R=0.585），
所以方向是"补召回"而非"换模型"——审计要能回答"该判没判的场景里，卡在哪一步"。
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.activity_inference import ActivityInferenceService  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.store import Store  # noqa: E402

DOOR = "binary_sensor.study_door_contact"
LIGHT = "light.study_ceiling"
CLIMATE = "climate.study_ac"
PC = "switch.study_pc"


class _FakeInsights:
    def _tags_of(self, eid, name):
        return InsightService._tags_of(eid, name)


def _runtime(store):
    cfg = SimpleNamespace(tz_offset_hours=0.0, rooms={}, pir_debounce_sec=0)
    return SimpleNamespace(config=cfg, store=store, insights=_FakeInsights())


@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="ma_recall_")
    s = Store(os.path.join(tmp, "test.db"), tz_offset_hours=0.0)
    s.init_schema()
    yield s


def _ev(store, ts: datetime, eid: str, room: str = "书房", state: str = "on"):
    store.insert_events([{
        "entity_id": eid, "ts": ts.isoformat(sep="T"), "room": room,
        "domain": eid.split(".")[0], "new_state": state, "old_state": "",
    }])


def _day(store, day: int, acts: list[tuple]):
    """在某天 09:00 起按 (分钟, 实体, 状态) 写事件。"""
    base = datetime(2026, 9, day, 9, 0, 0)
    for minute, eid, state in acts:
        _ev(store, base + timedelta(minutes=minute), eid, state=state)
    return base


NORMAL = [(0, DOOR, "on"), (2, DOOR, "off"), (4, LIGHT, "on"),
          (6, CLIMATE, "on"), (8, PC, "on")]
# 电脑/空调顺序颠倒：5 步全齐但末步对不上
SWAPPED = [(0, DOOR, "on"), (2, DOOR, "off"), (4, LIGHT, "on"),
           (6, PC, "on"), (8, CLIMATE, "on")]


def test_audit_counts_matched_and_near_miss(store):
    _day(store, 1, NORMAL)          # 应命中
    _day(store, 2, SWAPPED)         # 原材料齐备但顺序不对 → 召回缺口
    _day(store, 3, SWAPPED)

    svc = ActivityInferenceService(_runtime(store))
    res = svc.audit_rule_recall(start="2026-09-01T00:00:00", end="2026-09-04T00:00:00",
                                persist=False)
    assert res["ok"] is True
    assert res["room_days"] == 3

    study = next(a for a in res["audit"] if a["rule"] == "书房工作")
    assert study["eligible_days"] == 3
    assert study["matched_days"] == 1
    assert study["near_miss_days"] == 2
    assert study["estimated_recall"] == round(1 / 3, 3)
    # 卡点在最后一步「computer(on)」
    assert study["top_blockers"][0]["blocker"] == "computer(on)@step5"
    assert study["top_blockers"][0]["count"] == 2


def test_audit_skips_days_without_all_anchors(store):
    """当天缺标签（原材料不全）→ 不算"应命中"，不计入召回。"""
    _day(store, 1, NORMAL)
    _day(store, 2, [(0, DOOR, "on"), (2, LIGHT, "on")])   # 无 climate / computer

    svc = ActivityInferenceService(_runtime(store))
    res = svc.audit_rule_recall(start="2026-09-01T00:00:00", end="2026-09-03T00:00:00",
                                persist=False)
    study = next(a for a in res["audit"] if a["rule"] == "书房工作")
    assert study["eligible_days"] == 1          # 只有第 1 天原材料齐备
    assert study["near_miss_days"] == 0
    assert study["estimated_recall"] == 1.0


def test_audit_emits_relaxed_candidate_rule(store):
    _day(store, 1, NORMAL)
    _day(store, 2, SWAPPED)
    _day(store, 3, SWAPPED)

    svc = ActivityInferenceService(_runtime(store))
    res = svc.audit_rule_recall(start="2026-09-01T00:00:00", end="2026-09-04T00:00:00",
                                persist=True, min_near_miss=2)
    assert res["gap_count"] >= 1
    gap = next(g for g in res["gaps"] if g["blocker"] == "computer(on)@step5")
    assert gap["name"] == "召回放宽[书房工作]:去掉第5步"

    rules = [r for r in store.list_candidate_rules() if r["source"] == "recall_gap"]
    assert rules
    rule = next(r for r in rules if r["name"] == gap["name"])
    assert [s["tag"] for s in rule["steps"]] == ["door", "door", "light", "climate"]
    assert rule["status"] == "staging"          # 只进 staging，等人工审核
    assert rule["evidence"][0]["blocker"] == "computer(on)@step5"


def test_audit_no_gap_when_below_threshold(store):
    _day(store, 1, NORMAL)
    _day(store, 2, SWAPPED)                     # 仅 1 天缺口

    svc = ActivityInferenceService(_runtime(store))
    res = svc.audit_rule_recall(start="2026-09-01T00:00:00", end="2026-09-03T00:00:00",
                                persist=True, min_near_miss=2)
    assert res["gap_count"] == 0
    assert [r for r in store.list_candidate_rules() if r["source"] == "recall_gap"] == []


def test_audit_reports_room_move_rule_recall(store):
    """房间移动（门开→门关）：只开不关的日子属召回缺口，卡在第 2 步。"""
    _day(store, 1, [(0, DOOR, "on"), (3, DOOR, "off")])   # 命中
    _day(store, 2, [(0, DOOR, "on")])                     # 只开不关

    svc = ActivityInferenceService(_runtime(store))
    res = svc.audit_rule_recall(start="2026-09-01T00:00:00", end="2026-09-03T00:00:00",
                                persist=False)
    move = next(a for a in res["audit"] if a["rule"] == "房间移动")
    assert move["eligible_days"] == 2 and move["matched_days"] == 1
    assert move["top_blockers"][0]["blocker"] == "door(off)@step2"
