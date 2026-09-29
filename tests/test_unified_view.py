"""vMA-1.3 unified_events VIEW 契约测试。

验证：
1. VIEW 存在
2. VIEW 行数 = 三源之和 - 过滤行（behavior_events status!='ok'）
3. 字段语义正确（source 分布、event_type 非空）
"""
import sqlite3
import os
import pytest


DB_PATH = os.environ.get("MA_TEST_DB", "/data/memory_agent.db")


@pytest.fixture(scope="module")
def conn():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"DB not found: {DB_PATH}")
    c = sqlite3.connect(DB_PATH)
    yield c
    c.close()


def test_view_exists(conn):
    """VIEW 必须存在。"""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' AND name='unified_events'"
    ).fetchone()
    assert row is not None, "unified_events VIEW does not exist"


def test_row_count_contract(conn):
    """VIEW 行数 = events + behavior_events(status='ok') + perception_events。"""
    view_count = conn.execute("SELECT COUNT(*) FROM unified_events").fetchone()[0]
    events_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    vision_count = conn.execute(
        "SELECT COUNT(*) FROM behavior_events WHERE status='ok'"
    ).fetchone()[0]
    perception_count = conn.execute("SELECT COUNT(*) FROM perception_events").fetchone()[0]

    expected = events_count + vision_count + perception_count
    assert view_count == expected, (
        f"VIEW row count mismatch: {view_count} != {expected} "
        f"(events={events_count}, vision_ok={vision_count}, perception={perception_count})"
    )


def test_source_distribution(conn):
    """source 只能是 device/vision/perception，且与三源行数对应。"""
    rows = conn.execute(
        "SELECT source, COUNT(*) FROM unified_events GROUP BY source ORDER BY source"
    ).fetchall()
    sources = {r[0]: r[1] for r in rows}

    assert set(sources.keys()) <= {"device", "vision", "perception"}, (
        f"Unexpected source values: {set(sources.keys())}"
    )

    # 各 source 行数与源表一致
    if "device" in sources:
        events_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert sources["device"] == events_count
    if "vision" in sources:
        vision_count = conn.execute(
            "SELECT COUNT(*) FROM behavior_events WHERE status='ok'"
        ).fetchone()[0]
        assert sources["vision"] == vision_count
    if "perception" in sources:
        perception_count = conn.execute("SELECT COUNT(*) FROM perception_events").fetchone()[0]
        assert sources["perception"] == perception_count


def test_event_type_not_empty(conn):
    """event_type 不应为空字符串。"""
    null_count = conn.execute(
        "SELECT COUNT(*) FROM unified_events WHERE event_type IS NULL OR event_type = ''"
    ).fetchone()[0]
    assert null_count == 0, f"{null_count} rows have empty event_type"


def test_device_event_type_format(conn):
    """device 的 event_type 应包含 ':'（entity:action 格式）。"""
    sample = conn.execute(
        "SELECT event_type FROM unified_events WHERE source='device' LIMIT 100"
    ).fetchall()
    if sample:
        no_colon = sum(1 for (r,) in sample if ":" not in r)
        assert no_colon == 0, f"{no_colon}/100 device event_type missing ':' separator"
