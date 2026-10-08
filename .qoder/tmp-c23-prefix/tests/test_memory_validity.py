"""v0.9 时间有效性单测：valid_from/valid_to 列 + 时间切片（store 层，无需 chroma）。"""

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
    tmp = tempfile.mkdtemp(prefix="ma_valid_")
    db = os.path.join(tmp, "test.db")
    s = Store(db, tz_offset_hours=0.0)
    s.init_schema()
    yield s


def test_agent_memory_validity(store):
    mid = store.add_agent_memory(
        session_id="t", text="孩子上学期22点睡", topic_key="sleep:child",
        tags_json="[]", source_refs_json="[]", ttl_days=30,
        valid_from="2026-03-01T00:00:00", observed_at="2026-03-01T00:00:00",
    )
    rec = store.get_agent_memory(mid)
    assert rec["valid_from"] == "2026-03-01T00:00:00"
    assert rec["observed_at"] == "2026-03-01T00:00:00"
    assert rec["valid_to"] == ""  # 仍有效

    # 时间切片：关闭旧记忆的有效区间（= 新事实生效时刻）
    store.close_agent_memory_validity(mid, "2026-09-01T00:00:00")
    rec = store.get_agent_memory(mid)
    assert rec["valid_to"] == "2026-09-01T00:00:00"


def test_valid_from_defaults_to_created(store):
    mid = store.add_agent_memory(
        session_id="t", text="X", topic_key="k", tags_json="[]",
        source_refs_json="[]", ttl_days=30,
    )
    rec = store.get_agent_memory(mid)
    assert rec["valid_from"]  # 缺省填 created_at
    assert rec["observed_at"] == rec["created_at"]
    assert rec["valid_to"] == ""
