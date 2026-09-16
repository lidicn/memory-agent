"""v0.9 MCP 契约单测：幂等键（store 层）。"""

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
    tmp = tempfile.mkdtemp(prefix="ma_idem_")
    db = os.path.join(tmp, "test.db")
    s = Store(db, tz_offset_hours=0.0)
    s.init_schema()
    yield s


def test_idempotency_roundtrip(store):
    assert store.get_idempotency("k1") is None
    store.save_idempotency("k1", "trigger_collection", '{"ok": true}')
    got = store.get_idempotency("k1")
    assert got is not None
    assert got["tool"] == "trigger_collection"
    assert got["result_text"] == '{"ok": true}'
    assert got["is_error"] == 0


def test_idempotency_error_flag(store):
    store.save_idempotency("k2", "create_member", '{"ok": false}', is_error=True)
    assert store.get_idempotency("k2")["is_error"] == 1


def test_purge_keeps_unexpired(store):
    store.save_idempotency("k3", "x", "y", ttl_hours=1)
    assert store.purge_idempotency() == 0  # 未过期不应删除
    assert store.get_idempotency("k3") is not None
