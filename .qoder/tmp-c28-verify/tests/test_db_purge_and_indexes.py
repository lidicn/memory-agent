"""审计 P0-7/P0-8/P2-1 的回归锁：分批清理、空间回收、重复索引。

三条都属于"跑一次全绿、上线后冻结两分钟"那一类：功能正确、代价致命。
所以断言的不是返回值，而是**执行结构**——批次数、锁释放点、索引份数。
"""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import store as store_mod  # noqa: E402
from memory_agent.store import Store  # noqa: E402


@pytest.fixture
def db_path():
    tmp = tempfile.mkdtemp(prefix="ma_purge_")
    yield os.path.join(tmp, "p.db")


def _seed(store, rows: int, *, days_back: int = 30) -> None:
    """灌入 ``rows`` 条历史事件（全部早于保留期，等待被清）。"""
    base = datetime.now() - timedelta(days=days_back)
    batch = []
    for i in range(rows):
        ts = (base + timedelta(seconds=i)).isoformat(timespec="seconds")
        batch.append({"entity_id": f"binary_sensor.pir_{i % 20}", "ts": ts,
                      "room": "书房", "domain": "binary_sensor",
                      "new_state": "on", "old_state": "off"})
        if len(batch) >= 1000:
            store.insert_events(batch)
            batch = []
    if batch:
        store.insert_events(batch)


def _recording_connect(store, log):
    """包装连接，记录每条 SQL——用结构断言代替计时断言（计时在 CI 上不可信）。"""
    real_connect = store.connect

    class _ConnProxy:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *a, **kw):
            first = str(sql).strip().split("\n")[0].upper()
            log.append(first[:60])
            return self._conn.execute(sql, *a, **kw)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    def connect():
        return _ConnProxy(real_connect())

    store.connect = connect
    return real_connect


def test_purge_old_deletes_in_batches(db_path):
    """一批最多删 batch_rows 行：DELETE 语句出现多次，而不是一条删完。"""
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    _seed(st, 2100)
    log = []
    _recording_connect(st, log)

    removed = st.purge_old(1, batch_rows=500)

    assert removed == 2100
    deletes = [s for s in log if s.startswith("DELETE FROM EVENTS")]
    assert len(deletes) >= 4, f"2100 行 / 每批 500 应至少 4 批，实测 {len(deletes)} 条 DELETE"
    assert st.connect().execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 0
    st.close()


def test_purge_old_releases_lock_between_batches(db_path):
    """每一批都要独立地「拿锁 → 提交 → 放锁」——P0-7 的实质是这把全局锁被独占 143 秒。

    用计数代理而不是并发抢锁：抢锁的结果取决于线程调度，在小库上必然偶发假绿。
    计数能证明"锁被释放了 N 次"，也就证明了别的线程有 N 个空隙可以进去。
    """
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    _seed(st, 2100)

    real_lock = st._lock

    class _CountingLock:
        """透传真实锁，只数「完整的持锁段」（``with self._lock`` 进出各一次）。"""

        def __init__(self):
            self.segments = 0

        def __enter__(self):
            real_lock.__enter__()
            return self

        def __exit__(self, *exc):
            self.segments += 1
            return real_lock.__exit__(*exc)

    counter = _CountingLock()
    st._lock = counter
    try:
        removed = st.purge_old(1, batch_rows=500)
    finally:
        st._lock = real_lock

    assert removed == 2100
    assert counter.segments >= 5, (
        f"2100 行 / 每批 500 应有至少 5 个独立的持锁段，实测 {counter.segments} 段"
        "——说明删除又回到了一次性独占全局锁")
    st.close()


def test_purge_old_vacuums_only_after_large_delete(db_path, monkeypatch):
    """P0-8：删除量达标才 VACUUM；小清理不付排他代价。"""
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    _seed(st, 300)
    log = []
    _recording_connect(st, log)
    monkeypatch.setattr(store_mod, "PURGE_VACUUM_MIN_ROWS", 50_000)
    st.purge_old(1, batch_rows=100)
    assert not [s for s in log if s.startswith("VACUUM")], "小清理不应触发 VACUUM"
    st.close()

    st2 = Store(db_path, tz_offset_hours=0.0)
    st2.init_schema()
    _seed(st2, 300)
    log2 = []
    _recording_connect(st2, log2)
    monkeypatch.setattr(store_mod, "PURGE_VACUUM_MIN_ROWS", 100)  # 把阈值调低验证会做
    st2.purge_old(1, batch_rows=100)
    assert [s for s in log2 if s.startswith("VACUUM")], "达到阈值必须回收一次磁盘"
    st2.close()


def test_perception_event_id_has_exactly_one_index(db_path):
    """P2-1：event_id 只留唯一索引；非唯一那条要么不存在，要么被迁移删掉。"""
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    idx = st.connect().execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' "
        "AND tbl_name='perception_events' AND sql LIKE '%event_id%'").fetchall()
    names = {r["name"] for r in idx}
    assert "idx_pe_event_id" not in names, f"重复的非唯一索引仍在：{names}"
    assert "idx_perception_events_event_id" in names, f"唯一索引缺失：{names}"
    st.close()


def test_legacy_db_with_duplicate_index_gets_it_dropped(db_path):
    """存量库（老 schema 已建过非唯一索引）迁移后必须把它删掉。

    用真实 ``init_schema()`` 造库再手工补回那条冗余索引，而不是手写一张迷你表：
    迷你表会让 ``init_schema`` 末尾校验 ``unified_events`` 视图时炸在
    ``no such column: pe.id`` 上——那是测试自己造的假场景，不是产品缺陷。
    """
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    assert st.connect().execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_pe_event_id'"
    ).fetchone() is None, "新库本不该有这条索引，测试前提不成立"
    st.close()

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE INDEX idx_pe_event_id ON perception_events(event_id)")
    conn.commit()
    conn.close()

    st2 = Store(db_path, tz_offset_hours=0.0)
    st2.init_schema()
    idx = st2.connect().execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='perception_events'").fetchall()
    names = {r["name"] for r in idx}
    assert "idx_pe_event_id" not in names, f"旧库冗余索引未清理：{names}"
    assert "idx_perception_events_event_id" in names, f"旧库没补上唯一索引：{names}"
    st2.close()


# ── 审计 P0-6：实体聚合覆盖索引 ────────────────────────────────────────────
#: 与 insights/repository.py:274 entity_catalog 同形的聚合句
_ENTITY_AGG_SQL = (
    "EXPLAIN QUERY PLAN SELECT entity_id, MAX(room) AS room, MAX(domain) AS domain, "
    "MAX(ts) AS last_ts, COUNT(*) AS total FROM events GROUP BY entity_id "
    "ORDER BY entity_id LIMIT ?"
)


def _indexes(store, table: str) -> dict:
    rows = store.connect().execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=?",
        (table,)).fetchall()
    return {r["name"]: (r["sql"] or "") for r in rows}


def test_entity_covering_index_exists_and_replaces_narrow_one(db_path):
    """实体聚合要覆盖索引；旧的 (entity_id, ts) 是它的前缀，只能留一条。"""
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    idx = _indexes(st, "events")
    cover = idx.get("idx_events_entity_cover")
    assert cover, f"覆盖索引缺失，实体聚合会退回逐行回表（30 万行实测 12s）：{sorted(idx)}"
    for col in ("entity_id", "ts", "room", "domain"):
        assert col in cover, f"覆盖索引少了 {col}：{cover}"
    assert "idx_events_entity_ts" not in idx, (
        "前缀重复的窄索引还在：每写一行维护两份同前缀 B 树（P2-1 同一类缺陷）")
    st.close()


def test_entity_aggregation_plan_uses_the_covering_index(db_path):
    """规划器真的走覆盖索引——只建了索引却用不上，等于白付写放大。"""
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    _seed(st, 200)
    plan = "\n".join(
        str(r["detail"]) for r in st.connect().execute(_ENTITY_AGG_SQL, (50,)).fetchall())
    assert "idx_events_entity_cover" in plan, f"聚合没走覆盖索引，plan:\n{plan}"
    assert "USE TEMP B-TREE FOR GROUP BY" not in plan, (
        f"聚合仍在建临时分组 B 树（= 没吃到索引的有序性），plan:\n{plan}")
    st.close()


def test_legacy_db_with_narrow_index_gets_it_replaced(db_path):
    """存量库：只有窄索引 → 迁移后窄的去、覆盖的来，顺序不能反。"""
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    st.close()

    conn = sqlite3.connect(db_path)
    conn.execute("DROP INDEX idx_events_entity_cover")
    conn.execute("CREATE INDEX idx_events_entity_ts ON events(entity_id, ts)")
    conn.commit()
    conn.close()

    st2 = Store(db_path, tz_offset_hours=0.0)
    st2.init_schema()
    idx = _indexes(st2, "events")
    assert "idx_events_entity_cover" in idx, f"存量库没补上覆盖索引：{sorted(idx)}"
    assert "idx_events_entity_ts" not in idx, f"存量库窄索引未清理：{sorted(idx)}"
    st2.close()


def test_purge_old_is_idempotent(db_path):
    """再跑一次不炸也不重复删（分批循环的退出条件靠这个兜住）。"""
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    _seed(st, 800)
    assert st.purge_old(1, batch_rows=300) == 800
    assert st.purge_old(1, batch_rows=300) == 0
    assert st.purge_old(0) == 0
    st.close()
