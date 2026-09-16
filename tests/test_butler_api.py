"""豆包管家对接：成员档案 + 在场查询 + 作息摘要（存储层）

契约见 docs/交接单_MA对接_成员档案与在场查询.md。
用临时 sqlite 库验证，不依赖 HA / LLM / Redis。
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.store import Store  # noqa: E402

PROFILE = {
    "nickname": "爱美丽",
    "school": "黄麻布学校五年级",
    "routine": {"weekday": {"wake": "07:00", "leave_morning": "07:40"}},
    "courses": {"mon": ["语文", "体育", "英语"]},
    "interests": ["画画"],
}


@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="mw_butler_")
    db = os.path.join(tmp, "test.db")
    s = Store(db, tz_offset_hours=0.0)
    s.init_schema()
    yield s
    try:
        os.remove(db)
    except OSError:
        pass


# ── 需求 1：成员档案 profile_json ──────────────────────────────────────────

def test_profile_json_roundtrip(store):
    m = store.create_member(name="Emily")
    store.update_member(m["id"], profile_json=PROFILE)

    got = store.get_member(m["id"])
    # 原样读回：不做任何字段解释，管家写什么就读回什么
    assert json.loads(got["profile_json"]) == PROFILE
    # 同时给一个解析好的视图，省得调用方自己 loads
    assert got["profile"] == PROFILE

    listed = store.list_members()[0]
    assert listed["profile"] == PROFILE


def test_profile_json_accepts_json_string(store):
    m = store.create_member(name="Kevin")
    store.update_member(m["id"], profile_json=json.dumps(PROFILE, ensure_ascii=False))
    assert store.get_member(m["id"])["profile"] == PROFILE


def test_profile_json_rejects_invalid(store):
    m = store.create_member(name="Kevin")
    with pytest.raises(ValueError):
        store.update_member(m["id"], profile_json="这不是 JSON")
    # 拒绝写入而不是静默清空
    assert store.get_member(m["id"])["profile"] is None


def test_patch_note_does_not_clear_profile(store):
    m = store.create_member(name="Emily")
    store.update_member(m["id"], profile_json=PROFILE)
    store.update_member(m["id"], note="五年级")
    got = store.get_member(m["id"])
    assert got["note"] == "五年级"
    assert got["profile"] == PROFILE


def test_empty_profile_clears(store):
    m = store.create_member(name="Emily")
    store.update_member(m["id"], profile_json=PROFILE)
    store.update_member(m["id"], profile_json="")
    assert store.get_member(m["id"])["profile"] is None


# ── 需求 2：在场查询 ───────────────────────────────────────────────────────

def _seed_events(store):
    rows = [
        ("2026-09-03T18:00:00", "客厅", "face",
         [{"name": "Emily", "via": "face", "match_confidence": 0.9}]),
        ("2026-09-03T18:30:00", "客厅", "identity_change",
         [{"name": "Kevin", "via": "appearance_matched", "match_confidence": 0.75},
          {"name": "未识别成员", "via": "appearance"}]),
        ("2026-09-03T18:31:00", "书房", "patrol",
         [{"name": "Emily", "via": "arcface", "match_confidence": 0.82}]),
    ]
    for ts, room, trigger, persons in rows:
        store.insert_behavior_event({
            "server_ts": ts, "room": room, "trigger": trigger,
            "persons": persons, "count": len(persons), "status": "ok",
            "day": ts[:10],
        })


def test_presence_latest_per_member(store):
    _seed_events(store)
    items = store.recent_presence("2026-09-03T17:00:00")
    names = {i["name"]: i for i in items}

    # 同名取最近一次（书房 18:31 晚于客厅 18:00）
    assert names["Emily"]["last_seen"] == "2026-09-03T18:31:00"
    assert names["Emily"]["room"] == "书房"
    # TV 端 via="face" 归一化为 arcface
    assert names["Emily"]["via"] == "arcface"
    assert names["Emily"]["via_raw"] == "arcface"

    assert names["Kevin"]["last_seen"] == "2026-09-03T18:30:00"
    assert names["Kevin"]["via"] == "appearance_matched"
    assert names["Kevin"]["confidence"] == 0.75


def test_presence_skips_unknown_identity(store):
    _seed_events(store)
    items = store.recent_presence("2026-09-03T17:00:00")
    assert "未识别成员" not in {i["name"] for i in items}
    assert len(items) == 2


def test_presence_room_filter_and_window(store):
    _seed_events(store)
    # 窗口左闭（>= since）：18:30 起 → Kevin(客厅18:30) + Emily(书房18:31)
    assert [i["name"] for i in store.recent_presence("2026-09-03T18:30:00")] == [
        "Emily", "Kevin"
    ]
    # 按房间过滤：只看客厅时 Emily 停留在 18:00，故排在 Kevin 之后
    assert [i["name"] for i in store.recent_presence(
        "2026-09-03T17:00:00", room="客厅")] == ["Kevin", "Emily"]
    # 窗口外为空
    assert store.recent_presence("2026-09-03T19:00:00") == []


def test_presence_sorted_by_last_seen_desc(store):
    _seed_events(store)
    items = store.recent_presence("2026-09-03T17:00:00")
    assert [i["name"] for i in items] == ["Emily", "Kevin"]


# ── 需求 3：作息实测摘要 ───────────────────────────────────────────────────

def test_member_schedule(store):
    # store 的 tz_offset=0，与 UTC 对齐
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for ts in (f"{today}T08:05:00", f"{today}T19:30:00", f"{today}T21:00:00"):
        store.insert_behavior_event({
            "server_ts": ts, "room": "客厅", "persons": [{"name": "Kevin"}],
            "count": 1, "day": today, "status": "ok",
        })
    data = store.member_schedule("Kevin", days=14)
    assert data["name"] == "Kevin"
    assert len(data["samples"]) == 1
    sample = data["samples"][0]
    assert sample["day"] == today
    assert sample["first_seen"] == f"{today}T08:05:00"
    assert sample["last_seen"] == f"{today}T21:00:00"
    assert sample["appearances"] == 3
    assert data["summary"]["median_last_seen"] == "21:00"


def test_member_schedule_no_data(store):
    data = store.member_schedule("不存在的人", days=14)
    assert data["samples"] == []
    assert data["summary"] == {"days_with_data": 0, "days_requested": 14}


def test_member_schedule_ignores_other_members(store):
    # store 的 tz_offset=0，与 UTC 对齐
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    store.insert_behavior_event({
        "server_ts": f"{today}T19:30:00", "room": "客厅",
        "persons": [{"name": "Emily"}], "count": 1, "day": today, "status": "ok",
    })
    assert store.member_schedule("Kevin", days=14)["samples"] == []
