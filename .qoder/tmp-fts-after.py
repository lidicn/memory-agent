"""部署后只读复核：FTS 索引是否真被回填、关键词检索是否恢复、agent_memories 是否不再是只读。

不改任何生产数据：全部走只读连接 + 只读 SELECT。
"""
import sqlite3
from datetime import timedelta

from memory_agent.config import get_config
from memory_agent.store import Store, now_local

cfg = get_config()
print("db_path:", cfg.db_path)
conn = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True, timeout=30)
c = conn.cursor()


def q(sql, params=()):
    try:
        c.execute(sql, params)
        return c.fetchall()
    except Exception as exc:
        return [("ERR", str(exc))]


print("agent_memories by state:", q("select state, count(*) from agent_memories group by state"))
print("mirror_dirty=1:", q("select count(*) from agent_memories where mirror_dirty=1"))
print("docsize rows (=索引文档数):", q("select count(*) from agent_memories_fts_docsize"))
print("fts data rows:", q("select count(*) from agent_memories_fts_data"))
print("main rows:", q("select count(*) from agent_memories"))
print("triggers:", q("select name from sqlite_master where type='trigger'"))

now = now_local(cfg.tz_offset_hours)
print("expire 命中数(expires_at<今天):", q(
    "select count(*) from agent_memories where state in ('staging','live','pending_review') "
    "and expires_at < ?", (now.strftime("%Y-%m-%d"),)))

# 走服务自己的检索路径：它吞异常返空，所以命中非空才算恢复
store = Store(cfg.db_path, tz_offset_hours=cfg.tz_offset_hours)
rows = store.search_agent_memories_fts("水位", limit=5, state="all")
print("search_agent_memories_fts('水位'):", len(rows), [r.get("memory_id") for r in rows])
rows2 = store.search_agent_memories_fts("客厅", limit=5, state="all")
print("search_agent_memories_fts('客厅'):", len(rows2), [r.get("memory_id") for r in rows2])

print("behavior_states rows:", q("select count(*) from behavior_states"))
print("events last 60min:", q("select count(*) from events where ts >= ?",
                              ((now - timedelta(minutes=60)).isoformat(sep="T"),)))
