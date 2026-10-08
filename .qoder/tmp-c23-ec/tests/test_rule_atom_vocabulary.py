"""规则引擎「设备侧新条件词汇表」单测 —— ADM 联动计划 第 3 步 ③ 的验收项。

计划卡原话：``_match_atom`` 加实体/状态迁移语义，**新词汇表有单测**。
此前 `state`/`domain`/`entity_id`/`tag` 已落地但零直接测试（全仓 grep
`_match_atom` 命中 0 条），`old_state`（迁移语义）本次补齐。

锁住四类历史塌法：
1. `state` 曾经是一条 TODO + ``pass``，带 state 原子的条件**恒真**——
   「门开着」的规则门关时也命中；
2. 只有 `state` 没有 `old_state` 时，规则分不出「刚被打开」和「一直开着」；
3. 事件缺 `old_state`（感知/视觉链路不带该字段）时不能当作命中；
4. `time_range` 曾经按"现在"判定，批量 feed 喂历史事件时会整窗错位。
"""

from __future__ import annotations

import pytest

from memory_agent.device_feed import DeviceEventFeed
from memory_agent.rule_engine import ActiveRuleEngine, state_matches


eng = ActiveRuleEngine(None, None)


def _match(cond: dict, event: dict) -> bool:
    return eng._match_atom(cond, event)[0]


# ── state_matches：与推断层同源的判红口径 ────────────────────────────────────
@pytest.mark.parametrize("want,state,expect", [
    ("", "off", True),            # 空 = 不限
    ("any", None, True),
    ("on", "open", True),         # cover 的 open 归 on
    ("on", "heat", True),         # climate 的非关闭态归 on
    ("on", "closed", False),      # ← 曾经恒真时这条会绿
    ("on", "off", False),
    ("off", "not_home", True),
    ("off", "on", False),
    ("heat", "heat", True),       # 非 on/off 的字面值走精确比对
    ("heat", "cool", False),
])
def test_state_matches_classifies_binary_and_exact_values(want, state, expect):
    assert state_matches(state, want) is expect


# ── state 原子：把恒真的旧行为钉死 ───────────────────────────────────────────
def test_state_atom_is_not_always_true():
    door_open = {"kind": "device", "state": "open"}
    door_closed = {"kind": "device", "state": "closed"}
    assert _match({"state": "on"}, door_open) is True
    assert _match({"state": "on"}, door_closed) is False
    assert _match({"state": "off"}, door_closed) is True


# ── old_state 原子：让 {state, old_state} 读作一次迁移 ───────────────────────
def test_old_state_gives_the_pair_a_direction():
    just_opened = {"kind": "device", "state": "on", "old_state": "off"}
    stayed_on = {"kind": "device", "state": "on", "old_state": "on"}
    cond_opened = {"state": "on", "old_state": "off"}
    assert _match(cond_opened, just_opened) is True
    # 「一直开着」不是「刚被打开」——只有 state 时这两者不可分
    assert _match(cond_opened, stayed_on) is False
    # 方向反过来也不成立
    assert _match({"state": "off", "old_state": "on"}, just_opened) is False


def test_old_state_absent_is_not_a_match():
    """感知链路的事件没有 old_state：不知道从哪来 ≠ 确认从没来。"""
    perception = {"kind": "face_known", "person": "K"}
    assert _match({"old_state": "off"}, perception) is False
    assert _match({"old_state": "any"}, perception) is True


def test_blank_old_state_is_not_smuggled_in_as_off():
    """``state_matches(None, "off")`` 本身返回 True（空值不是 on，就判 off）。

    所以 old_state 原子必须自己拦掉缺失/空串字段，否则"没有迁移信息"会伪装成
    "从 off 变来"——feed 之外的事件全都带空 old_state，规则会在它们身上乱触发。
    """
    assert state_matches(None, "off") is True
    assert _match({"old_state": "off"}, {"state": "on", "old_state": ""}) is False
    assert _match({"old_state": "off"}, {"state": "on", "old_state": None}) is False


# ── domain / entity_id / tag：实体语义半边 ──────────────────────────────────
def test_domain_and_entity_id_accept_lists():
    ev = {"kind": "device", "domain": "binary_sensor",
          "entity_id": "binary_sensor.front_door", "tags": ["door"]}
    assert _match({"domain": "binary_sensor"}, ev) is True
    assert _match({"domain": ["light", "cover"]}, ev) is False
    assert _match({"entity_id": ["binary_sensor.front_door", "light.bed"]}, ev) is True
    assert _match({"entity_id": "binary_sensor.kitchen"}, ev) is False


def test_tag_atom_intersects_rather_than_subsumes():
    ev = {"kind": "device", "tags": ["door"]}
    assert _match({"tag": "door"}, ev) is True
    assert _match({"tag": ["light", "door"]}, ev) is True
    assert _match({"tag": "light"}, ev) is False
    assert _match({"tag": "door"}, {"kind": "device"}) is False   # 无标签事件


# ── trace：失败原因要指名是哪个原子 ──────────────────────────────────────────
def test_trace_reports_the_failing_atom():
    ok, traced = eng._match_atom({"state": "on", "old_state": "off"},
                                 {"state": "on", "old_state": "on"}, True)
    assert ok is False
    fails = traced["failures"]
    # 逐条按前缀判："state:" 是 "old_state:" 的子串，整串 in 会自证通过
    assert any(f.startswith("old_state:") for f in fails), fails
    assert not any(f.startswith("state:") for f in fails), fails


# ── time_range 按事件墙钟，不按"现在" ────────────────────────────────────────
def test_time_range_uses_event_ts_not_wall_clock_now():
    cond = {"time_range": "19:00-22:00"}
    early = {"ts": "2026-10-01T18:50:00"}
    inside = {"ts": "2026-10-01T19:30:00"}
    assert _match(cond, early) is False
    assert _match(cond, inside) is True


# ── 生产者↔消费者接缝：feed 产出的事件必须能被词汇表匹配 ─────────────────────
def _feed_event(row: dict) -> dict:
    feed = DeviceEventFeed(None, None, None,
                           names={"binary_sensor.front_door": "前门门磁"})
    return feed.to_event(row, feed._name_map())


def test_feed_produced_event_matches_the_ruled_vocabulary():
    opened = _feed_event({"entity_id": "binary_sensor.front_door",
                          "old_state": "closed", "new_state": "open",
                          "ts": "2026-10-01T19:30:00", "room": "玄关"})
    cond = {"kind": "device", "domain": "binary_sensor", "tag": "door",
            "state": "on", "old_state": "off", "entity_id": "binary_sensor.front_door"}
    assert _match(cond, opened) is True, opened

    closed = _feed_event({"entity_id": "binary_sensor.front_door",
                          "old_state": "open", "new_state": "closed",
                          "ts": "2026-10-01T19:31:00", "room": "玄关"})
    assert _match(cond, closed) is False, "同一词汇表必须能区分开/关两个方向"
