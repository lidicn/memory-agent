"""v0.9 任务 1 下半：member-schedule 时间维度（store 层，无需 runtime/chroma）。

覆盖：
1. start/end 时间窗查询：只返回窗口内的样本；
2. segments 按习惯记忆 valid_from/valid_to 分段：上学期 vs 这学期作息演变可被计算。
"""
import json
import os
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.store import Store  # noqa: E402


@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="ma_msched_")
    db = os.path.join(tmp, "test.db")
    s = Store(db, tz_offset_hours=0.0)
    s.init_schema()
    yield s


def _add_event(s, day, hhmm, room, name):
    ts = f"{day}T{hhmm}:00"
    s.insert_behavior_event(
        {"server_ts": ts, "day": day, "room": room,
         "persons": [{"name": name, "confidence": 0.9}]}
    )


def test_window_query_filters_by_range(store):
    _add_event(store, "2026-03-01", "22:00", "卧室", "Kevin")
    _add_event(store, "2026-03-05", "23:30", "卧室", "Kevin")
    _add_event(store, "2026-03-10", "23:00", "卧室", "Kevin")

    data = store.member_schedule("Kevin", days=90, start="2026-03-04", end="2026-03-06")
    days = [s["day"] for s in data["samples"]]
    assert days == ["2026-03-05"], days
    assert data["window"] == {"start": "2026-03-04", "end": "2026-03-06"}


def test_segments_split_by_habit_validity(store):
    # 上学期（习惯 valid 3/1~9/1）放 3 天样本，均 22:00
    for d in ("2026-03-02", "2026-03-10", "2026-08-30"):
        _add_event(store, d, "22:00", "卧室", "Kevin")
    # 这学期（习惯 valid 9/1~）放 3 天样本，均 23:30
    for d in ("2026-09-05", "2026-09-10", "2026-09-15"):
        _add_event(store, d, "23:30", "卧室", "Kevin")

    mid_old = store.add_agent_memory(
        session_id="habits", text="Kevin 上学期 22 点睡",
        topic_key="habit:Kevin:user_asleep",
        tags_json=json.dumps(["habit", "member:Kevin", "activity:user_asleep"]),
        source_refs_json="[]", ttl_days=90,
        valid_from="2026-03-01T00:00:00",
    )
    store.close_agent_memory_validity(mid_old, "2026-09-01T00:00:00")
    store.add_agent_memory(
        session_id="habits", text="Kevin 这学期 23:30 睡",
        topic_key="habit:Kevin:user_asleep",
        tags_json=json.dumps(["habit", "member:Kevin", "activity:user_asleep"]),
        source_refs_json="[]", ttl_days=90,
        valid_from="2026-09-01T00:00:00",
    )

    data = store.member_schedule("Kevin", days=365, start="2026-03-01", end="2026-12-31")
    segs = {s["window"]["valid_from"]: s for s in data["segments"]}
    old = segs.get("2026-03-01T00:00:00")
    new = segs.get("2026-09-01T00:00:00")
    assert old and old["days_with_data"] == 3, segs
    assert old["median_last_seen"] == "22:00", old
    assert new and new["days_with_data"] == 3, segs
    assert new["median_last_seen"] == "23:30", new
    # 两段已覆盖全部样本，无「未归类」
    assert all(s["activity"] != "(未归类)" for s in data["segments"])
