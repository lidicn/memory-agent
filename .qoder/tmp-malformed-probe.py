"""定位 malformed 来源：只读连接逐项检查，任何一步失败都打完整 traceback。

sweep 链路的只读半步：integrity/quick_check、agent_memories 计数、staging 列表、
FTS5 MATCH（search_agent_memories_fts 的 SQL 形状）。不写任何数据。
"""
import os
import sqlite3
import traceback

from memory_agent.config import get_config

cfg = get_config()
p = cfg.db_path
print("db:", p, "size:", os.path.getsize(p))
for suffix in ("-wal", "-shm"):
    q = p + suffix
    e = os.path.exists(q)
    print(suffix, "exists:", e, "size:", os.path.getsize(q) if e else "-")

conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
c = conn.cursor()


def step(label, sql, params=(), show=12):
    print(f"\n### {label}")
    try:
        c.execute(sql, params)
        rows = c.fetchall()
        for r in rows[:show]:
            print("   ", r)
        print(f"    -> {len(rows)} row(s)")
    except Exception:
        print("    !! FAILED")
        traceback.print_exc()


step("journal_mode", "pragma journal_mode")
step("quick_check", "pragma quick_check")
step("freelist_count", "pragma freelist_count")
step("page_count", "pragma page_count")
step("agent_memories count", "select count(*) from agent_memories")
step("agent_memories by state", "select state, count(*) from agent_memories group by state")
step("staging list (sweep 第一步读)",
     "select memory_id, topic_key, state, ttl_days, expires_at, auto_promote_blocked "
     "from agent_memories where state='staging' order by created_at desc limit 500")
step("expire_overdue WHERE (只读计数)",
     "select count(*) from agent_memories where state in ('staging','live','pending_review') "
     "and expires_at < '2026-10-03'")
step("fts rowcount", "select count(*) from agent_memories_fts")
step("fts MATCH + bm25 (conflict_scan 形状)",
     """select a.memory_id, a.text, f.rank from (
          select rowid as rid, bm25(agent_memories_fts) as rank
          from agent_memories_fts where agent_memories_fts match ?) f
        join agent_memories a on a.rowid = f.rid
        where a.state in ('live','pending_review') limit 20""", ("测试",))
step("sqlite_master 完整性", "select type, name from sqlite_master order by type, name", show=200)
