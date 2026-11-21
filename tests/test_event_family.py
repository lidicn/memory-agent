"""事件族发布（契约 §1.2 `ma/behavior-state` / `ma/prediction`）回归锁。

依据：DCD `20261117` §三（事件族两主题）＋ `20261120` Q3 甲（state 变更边沿／
prediction 跨阈值 ≥15min、15–30min 轮询；直接 vendor 0.3.5、⛔ 本地兜底双写）。

钉四类：
1. 信封七键与 `homesdk.events` 真源一致（库缺席时整族显式降级、不抛）；
2. 两条载荷形状对得上 AF 消费面白名单（`af_linkage_feed`），且 `retain=False`；
3. 边沿判定：状态不变不发、变了带前一状态 `duration`、首次 seed 不发；
4. 预测跨阈值：无 prev 必发、<15min 不发、≥15min 发；`next_arrival` 过点顺延。
"""

import os
import sys
import types
from datetime import datetime

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import event_family  # noqa: E402

_EV = types.SimpleNamespace(
    EVENT_TOPICS=frozenset({"ma/behavior-state", "ma/prediction"}),
    TOPIC_BEHAVIOR_STATE="ma/behavior-state",
    TOPIC_PREDICTION="ma/prediction",
    PAYLOAD_VERSION="1.0",
    ENVELOPE_KEYS=("type", "source", "payload_version", "event_id", "trace_id",
                   "ts", "emitted_at"),
)


@pytest.fixture
def ev(monkeypatch):
    monkeypatch.setattr(event_family, "_events_probe", _EV)
    yield _EV
    event_family.reset_events_probe()


class _Bridge:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def publish_raw(self, topic, payload, retain=False):
        self.calls.append((topic, payload, retain))
        return self.ok


# ── 1. 信封七键 ─────────────────────────────────────────────────────────────

def test_envelope_is_the_seven_rules_keys(ev):
    env = event_family.build_envelope("behavior_state")
    assert set(env) == set(ev.ENVELOPE_KEYS), env
    assert env["source"] == "ma"
    assert env["payload_version"] == "1.0"
    assert env["event_id"] != env["trace_id"]
    assert datetime.fromisoformat(env["ts"]).utcoffset() is not None, "ts 必须带偏移"
    assert datetime.fromisoformat(env["emitted_at"]).utcoffset() is not None


def test_absent_library_degrades_explicitly(monkeypatch):
    monkeypatch.setattr(event_family, "_events_probe", False)
    assert event_family.events_available() is False
    assert event_family.unavailable_reason()
    assert event_family.publish_behavior_state(_Bridge(), state="x") is False
    assert event_family.publish_prediction(_Bridge(), next_arrival="x") is False
    with pytest.raises(RuntimeError):
        event_family.build_envelope("behavior_state")


# ── 2. 两条载荷形状（对齐 AF 白名单）────────────────────────────────────────

def test_behavior_state_payload_shape(ev):
    bridge = _Bridge()
    ok = event_family.publish_behavior_state(
        bridge, state="watching_tv", confidence=0.91, member="m1", member_id="m1",
        room="living_room", since="2026-11-21T20:00:00", duration_s=3600,
        trigger="edge")
    assert ok is True
    topic, payload, retain = bridge.calls[0]
    assert topic == "ma/behavior-state"
    assert retain is False, "事件类 ⛔ retain（契约 §1.2 规则）"
    for key in ("type", "source", "payload_version", "event_id", "trace_id",
                "ts", "emitted_at", "as_of", "state", "confidence",
                "member", "member_id", "room", "since", "duration", "trigger"):
        assert key in payload, f"缺 {key}"
    assert payload["type"] == "behavior_state"
    assert payload["state"] == "watching_tv"
    assert payload["duration"] == 3600.0
    assert datetime.fromisoformat(payload["since"]).utcoffset() is not None


def test_prediction_payload_shape(ev):
    bridge = _Bridge()
    ok = event_family.publish_prediction(
        bridge, next_arrival="2026-11-21T21:10:00+08:00",
        upcoming_device_hints=["climate.study_ac"], confidence=0.66, room="study")
    assert ok is True
    topic, payload, retain = bridge.calls[0]
    assert topic == "ma/prediction"
    assert retain is False
    assert payload["type"] == "prediction"
    assert payload["next_arrival"].startswith("2026-11-21T21:10")
    assert payload["upcoming_device_hints"] == ["climate.study_ac"]
    assert payload["confidence"] == 0.66


# ── 3. 行为状态边沿 ─────────────────────────────────────────────────────────

def test_seed_does_not_emit_and_change_emits_with_duration(ev):
    first = [{"room": "living_room", "activity": "watching_tv", "member": "m1",
              "ts": "2026-11-21T20:00:00", "confidence": 0.9}]
    prev = event_family.seed_behavior_states(first)
    assert prev["living_room"]["activity"] == "watching_tv"

    edges, prev2 = event_family.diff_behavior_states(prev, first)
    assert edges == [], "状态没变不得发（边沿，不是周期心跳）"

    second = [{"room": "living_room", "activity": "sleeping", "member": "m1",
               "ts": "2026-11-21T21:00:00", "confidence": 0.8}]
    edges, _prev3 = event_family.diff_behavior_states(prev2, second)
    assert len(edges) == 1
    assert edges[0]["activity"] == "sleeping"
    assert edges[0]["duration"] == 3600.0, "时长类边沿带前一状态持续秒数"


def test_latest_by_scope_takes_the_newest_row(ev):
    rows = [
        {"room": "living_room", "activity": "sleeping", "ts": "2026-11-21T22:00:00"},
        {"room": "living_room", "activity": "watching_tv", "ts": "2026-11-21T20:00:00"},
    ]
    latest = event_family.latest_by_scope(rows)
    assert latest["living_room"]["activity"] == "sleeping"


# ── 4. 预测跨阈值 ＋ next_arrival 顺延 ──────────────────────────────────────

def test_prediction_threshold(ev):
    base = "2026-11-21T18:00:00+08:00"
    assert event_family.should_publish_prediction(None, base) is True
    assert event_family.should_publish_prediction(base, "2026-11-21T18:10:00+08:00") is False
    assert event_family.should_publish_prediction(base, "2026-11-21T18:20:00+08:00") is True
    assert event_family.prediction_drift_minutes(base, "not-a-time") is None


def test_next_arrival_rolls_to_tomorrow_when_passed(ev):
    now = datetime(2026, 11, 21, 22, 0, tzinfo=event_family._house_tz())
    same_day = datetime.fromisoformat(event_family.next_arrival_iso(23.5, now=now))
    assert (same_day.day, same_day.hour) == (21, 23)
    next_day = datetime.fromisoformat(event_family.next_arrival_iso(8.0, now=now))
    assert (next_day.day, next_day.hour) == (22, 8)
