"""自愈后的两条判据（只读）：FTS 关键词能命中；sweep 的 UPDATE 真落库。

注意 search_agent_memories_fts 的 state 参数没有 'all' 这个哨兵——SQL 里是
`WHERE a.state = ?`，传 'all' 就是字面匹配 state='all'，永远返空。所以这里按真实状态取。
"""
import sqlite3

from memory_agent.config import get_config
from memory_agent.store import Store

cfg = get_config()
store = Store(cfg.db_path, tz_offset_hours=cfg.tz_offset_hours)
for st in ("live", "staging", "revoked"):
    rows = store.search_agent_memories_fts("水位", limit=5, state=st)
    print(f"search('水位', state={st!r}) -> {len(rows)} 条",
          [r.get("memory_id") for r in rows])
rows = store.search_agent_memories_fts("客厅", limit=5, state="staging")
print(f"search('客厅', state='staging') -> {len(rows)} 条",
      [r.get("memory_id") for r in rows])

c = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True).cursor()
c.execute("select state, count(*) from agent_memories group by state")
print("agent_memories by state:", c.fetchall())
c.execute("select count(*) from agent_memories where state in "
          "('staging','live','pending_review') and expires_at < '2026-10-03'")
print("仍待过期的行数（sweep 每轮应清 0 批）:", c.fetchone()[0])
c.execute("select count(*) from agent_memories where mirror_dirty=1")
print("mirror_dirty=1:", c.fetchone()[0])
c.execute("select count(*) from agent_memories_fts_docsize")
print("docsize:", c.fetchone()[0])
