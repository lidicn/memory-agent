"""两问：1) 容器里 import 到的 Store 有没有自愈方法；2) 同样的数据形状上自愈到底走哪条分支。

第 2 问在副本上跑，不碰生产库。
"""
import os
import shutil
import sqlite3
import traceback

from memory_agent.config import get_config
from memory_agent.store import Store

cfg = get_config()
print("1) Store 有新方法:", hasattr(Store, "_fts_index_healthy"), hasattr(Store, "_rebuild_agent_memory_fts"))
import inspect  # noqa: E402
src = inspect.getsource(Store.init_schema)
print("   init_schema 里提到 integrity-check:", "integrity-check" in src)
print("   init_schema 里提到 已重建:", "已重建" in src)

SRC = cfg.db_path
DST = "/tmp/qoder-heal-check.db"
st = os.statvfs("/tmp")
print("2) /tmp 可用:", st.f_bavail * st.f_frsize // (1024 * 1024), "MB，需要",
      os.path.getsize(SRC) // (1024 * 1024), "MB")
src_conn = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True, timeout=30)
dst_conn = sqlite3.connect(DST)
src_conn.backup(dst_conn)
src_conn.close()
dst_conn.close()
print("   copy ok:", os.path.getsize(DST))


def shape(tag, path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True).cursor()
    def q(sql):
        try:
            c.execute(sql)
            return c.fetchone()[0]
        except Exception as exc:
            return f"ERR {exc}"
    print(f"   [{tag}] main={q('select count(*) from agent_memories')} "
          f"docsize={q('select count(*) from agent_memories_fts_docsize')} "
          f"data={q('select count(*) from agent_memories_fts_data')}")


shape("副本 复制后", DST)

store = Store(DST, tz_offset_hours=cfg.tz_offset_hours)
conn = store.connect()
print("   _fts_index_healthy(副本) =", store._fts_index_healthy(conn))
try:
    tok = store._rebuild_agent_memory_fts(conn)
    print("   _rebuild_agent_memory_fts 返回分词器:", repr(tok))
except Exception:
    print("   !! rebuild 抛出")
    traceback.print_exc()
shape("副本 自愈后", DST)
try:
    conn.execute("INSERT INTO agent_memories_fts(agent_memories_fts, rank) VALUES('integrity-check', 1)")
    conn.commit()
    print("   integrity-check: PASS")
except Exception as exc:
    print(f"   integrity-check: FAIL {type(exc).__name__}: {exc}")
store.close()
for p in (DST, DST + "-wal", DST + "-shm"):
    if os.path.exists(p):
        os.remove(p)
print("   副本已删除")
