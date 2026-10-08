"""external-content FTS5 索引的自愈锁（生产事故回归）。

生产实测（2026-10-03，容器权威读数）：
  * ``[AgentMemory] 周期 sweep 异常: database disk image is malformed`` 48 小时 664 次；
    旧进程最后一个小时 **59 次异常 / 0 次完成**——sweep 每次必失败。
  * 48 小时日志里 ``FTS5 rebuild 完成`` **0 次**。
  * 一致性快照副本上的判据：主表 ``PRAGMA integrity_check`` = ``ok``，而 FTS5 自己的
    ``integrity-check`` 抛 malformed；``agent_memories_fts_docsize`` **0 行**、
    ``_data`` 只剩 2 行，而 ``SELECT COUNT(*) FROM agent_memories_fts`` 返回 **247**
    （= 主表行数）。

根因不在磁盘，在启动迁移的**判据**：``init_schema`` 每次 DROP+CREATE 外部内容表，然后靠
``if _fts_count == 0 and _main_count > 0`` 决定要不要 rebuild。外部内容 FTS5 的
``COUNT(*)`` 读的是内容表，索引整片丢了也照样返回主表行数，于是这道守卫永不成立：
启动删掉可用索引后不回填，留下一具空索引 + 三个写触发器。之后任何 UPDATE/DELETE 都会在
触发器的 ``'delete'`` 分支上抛 malformed —— ``agent_memories`` 整表被冻成只读
（自动晋升 / TTL 过期 / 镜像 reconcile / 反馈步进全停），关键词检索静默返空。

既有用例抓不到它：它们只 INSERT（不走 delete 分支），且不断言 UPDATE 还能做。
"""

import os
import sqlite3
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

import inspect  # noqa: E402

import pytest  # noqa: E402

from memory_agent.store import Store  # noqa: E402

# 旧启动迁移的形状：新建外部内容表，但不 rebuild
_FTS_TABLE_SQL = """
CREATE VIRTUAL TABLE agent_memories_fts USING fts5(
    text, topic_key, tags_json,
    content='agent_memories', content_rowid='rowid',
    tokenize='trigram'
)
"""
_FTS_TRIGGERS = {
    "agent_memories_fts_ai": """
        CREATE TRIGGER agent_memories_fts_ai
        AFTER INSERT ON agent_memories BEGIN
            INSERT INTO agent_memories_fts(rowid, text, topic_key, tags_json)
            VALUES (new.rowid, new.text, new.topic_key, new.tags_json);
        END""",
    "agent_memories_fts_ad": """
        CREATE TRIGGER agent_memories_fts_ad
        AFTER DELETE ON agent_memories BEGIN
            INSERT INTO agent_memories_fts(agent_memories_fts, rowid, text, topic_key, tags_json)
            VALUES ('delete', old.rowid, old.text, old.topic_key, old.tags_json);
        END""",
    "agent_memories_fts_au": """
        CREATE TRIGGER agent_memories_fts_au
        AFTER UPDATE ON agent_memories BEGIN
            INSERT INTO agent_memories_fts(agent_memories_fts, rowid, text, topic_key, tags_json)
            VALUES ('delete', old.rowid, old.text, old.topic_key, old.tags_json);
            INSERT INTO agent_memories_fts(rowid, text, topic_key, tags_json)
            VALUES (new.rowid, new.text, new.topic_key, new.tags_json);
        END""",
}


def _store(tmp_path):
    store = Store(os.path.join(str(tmp_path), "t.db"), tz_offset_hours=0.0)
    store.init_schema()
    return store


def _seed(store, n=3):
    ids = []
    for i in range(n):
        ids.append(store.add_agent_memory(
            "s1", f"客厅水位异常记录{i}", "habit:/vision/客厅", "[]", "[]", 30))
    return ids


def _make_ghost_index(store):
    """复刻旧迁移留下的形状：索引空、内容表有行、触发器在。"""
    conn = store.connect()
    with store._lock:
        for name in _FTS_TRIGGERS:
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        conn.execute("DROP TABLE IF EXISTS agent_memories_fts")
        conn.execute(_FTS_TABLE_SQL)
        for sql in _FTS_TRIGGERS.values():
            conn.execute(sql)
        conn.commit()


def _integrity_check(conn):
    conn.execute("INSERT INTO agent_memories_fts(agent_memories_fts, rank) "
                 "VALUES('integrity-check', 1)")


# ── L0：前提自检——生产判据在测试库里同样成立 ────────────────────────────────
def test_ghost_index_counts_like_a_healthy_one(tmp_path):
    """COUNT(*) 不能当健康判据：它就是旧守卫失效的原因。"""
    store = _store(tmp_path)
    _seed(store, 3)
    _make_ghost_index(store)
    conn = store.connect()
    with store._lock:
        n_fts = conn.execute("SELECT COUNT(*) FROM agent_memories_fts").fetchone()[0]
        n_main = conn.execute("SELECT COUNT(*) FROM agent_memories").fetchone()[0]
        docsize = conn.execute("SELECT COUNT(*) FROM agent_memories_fts_docsize").fetchone()[0]
    assert n_fts == n_main, "外部内容表的 COUNT(*) 来自主表"
    assert docsize == 0, "索引其实是空的"
    with pytest.raises(sqlite3.DatabaseError):
        with store._lock:
            _integrity_check(conn)
    store.close()


# ── L1：真实症状——UPDATE 必须还能做 ─────────────────────────────────────────
def test_write_after_ghost_index_survives_restart(tmp_path):
    """重启一次之后，sweep 的写形状（UPDATE→触发器 delete 分支）不许抛 malformed。"""
    store = _store(tmp_path)
    mid = _seed(store, 3)[0]
    _make_ghost_index(store)
    store.init_schema()  # 模拟重启：必须自愈

    store.mark_mirror_dirty(mid, 1)
    store.set_agent_memory_state(mid, "live", mirror_dirty=0)
    assert store.get_agent_memory(mid)["state"] == "live"
    assert store.expire_overdue_agent_memories() >= 0
    store.close()


def test_index_stays_healthy_across_restarts(tmp_path):
    """反复重启不许把可用索引删成空索引（旧代码每次都 DROP+CREATE 的代价）。"""
    store = _store(tmp_path)
    _seed(store, 3)
    for _ in range(3):
        store.init_schema()
    conn = store.connect()
    with store._lock:
        assert conn.execute(
            "SELECT COUNT(*) FROM agent_memories_fts_docsize").fetchone()[0] == 3
        _integrity_check(conn)
    store.close()


# ── L2：关键词检索不许静默返空 ──────────────────────────────────────────────
def test_fts_search_returns_rows_after_restart(tmp_path):
    """search_agent_memories_fts 吞异常返空，所以只能用例来证明它真能命中。"""
    store = _store(tmp_path)
    mid = _seed(store, 3)[0]
    store.set_agent_memory_state(mid, "live", mirror_dirty=0)
    for _ in range(2):
        store.init_schema()
    rows = store.search_agent_memories_fts("水位异常", limit=10, state="live")
    assert [r["memory_id"] for r in rows] == [mid], f"重启后关键词检索应命中，实得 {rows}"
    store.close()


# ── L3：判据本身 ────────────────────────────────────────────────────────────
def test_health_helper_exists_and_reports_ghost(tmp_path):
    store = _store(tmp_path)
    _seed(store, 2)
    conn = store.connect()
    with store._lock:
        assert store._fts_index_healthy(conn) is True
    _make_ghost_index(store)
    with store._lock:
        assert store._fts_index_healthy(conn) is False, "空索引必须被判为不健康"
    store.close()


def test_rebuild_is_not_gated_on_row_counts():
    """旧守卫（按行数猜要不要 rebuild）不许回来。"""
    src = inspect.getsource(Store)
    assert "_fts_count" not in src, "外部内容表的 COUNT(*) 读的是主表，按行数猜要不要 rebuild 的守卫永不成立"
    assert "'integrity-check'" in src, "健康判据必须是 FTS5 自己的 integrity-check"
    assert "VALUES('rebuild')" in src or "values('rebuild')" in src.lower(), \
        "重建路径必须无条件回填索引"
