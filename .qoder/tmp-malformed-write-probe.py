"""在副本上复现 sweep 写路径的 malformed：生产库只读，所有写测试打在快照副本上。

Phase A：用 sqlite backup API 从只读连接取一致性快照（不落生产写锁）。
Phase B：在副本上跑 sweep 的真实写语句（expire UPDATE / set_state / mark_dirty /
         FTS 触发器），逐行报错误与 traceback。
"""
import os
import sqlite3
import sys
import traceback

SRC = "/data/memory_agent.db"
CANDIDATES = ["/tmp/qoder-malformed-copy.db", "/data/qoder-malformed-copy.db"]


def has_room(path, need_bytes):
    d = os.path.dirname(path) or "."
    try:
        st = os.statvfs(d)
        return st.f_bavail * st.f_frsize > need_bytes
    except Exception as exc:
        print("statvfs fail", d, exc)
        return False


need = os.path.getsize(SRC) * 2
dst = None
for c in CANDIDATES:
    if has_room(c, need):
        dst = c
        break
if not dst:
    print("NO ROOM for a copy; aborting")
    sys.exit(2)
print("copy target:", dst, "src size:", os.path.getsize(SRC))

src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True, timeout=30)
out = sqlite3.connect(dst)
try:
    src.backup(out)
    print("backup ok")
except Exception:
    print("!! backup FAILED")
    traceback.print_exc()
    sys.exit(3)
src.close()
out.close()

conn = sqlite3.connect(dst)
conn.row_factory = sqlite3.Row
c = conn.cursor()
print("sqlite version:", sqlite3.sqlite_version)


def step(label, fn):
    print(f"\n### {label}")
    try:
        for line in fn():
            print("   ", line)
    except Exception:
        print("    !! FAILED")
        traceback.print_exc()


def integrity():
    c.execute("pragma integrity_check")
    return [str(r[0])[:200] for r in c.fetchall()]


def fts_integrity():
    # FTS5 自带的结构校验：external content 影子表一致性只有这里能查出来
    try:
        c.execute("insert into agent_memories_fts(agent_memories_fts, rank) values('integrity-check', 1)")
        return ["integrity-check ok"]
    except Exception as exc:
        return [f"integrity-check raised: {type(exc).__name__}: {exc}"]


def expire_update():
    c.execute("""UPDATE agent_memories SET state='revoked', updated_at=?, mirror_dirty=1
                 WHERE state IN ('staging','live','pending_review') AND expires_at < ?""",
              ("2026-10-03T11:20:00", "2026-10-03"))
    conn.commit()
    return [f"rowcount={c.rowcount}"]


def mark_dirty_one():
    c.execute("select memory_id from agent_memories limit 20")
    ids = [r[0] for r in c.fetchall()]
    touched = 0
    for mid in ids:
        c.execute("UPDATE agent_memories SET mirror_dirty=1, updated_at=? WHERE memory_id=?",
                  ("2026-10-03T11:20:01", mid))
        touched += c.rowcount
        conn.commit()
    return [f"touched={touched}/{len(ids)}"]


def set_state_live_one():
    c.execute("select memory_id from agent_memories where state='revoked' limit 10")
    ids = [r[0] for r in c.fetchall()]
    for mid in ids:
        c.execute("UPDATE agent_memories SET state='live', mirror_dirty=0 WHERE memory_id=?", (mid,))
        conn.commit()
    return [f"ok {len(ids)} row(s)"]


def delete_one():
    c.execute("select memory_id from agent_memories where state='revoked' limit 3")
    ids = [r[0] for r in c.fetchall()]
    for mid in ids:
        c.execute("DELETE FROM agent_memories WHERE memory_id=?", (mid,))
        conn.commit()
    return [f"deleted {len(ids)} row(s)"]


def fts_rowcount():
    c.execute("select count(*) from agent_memories_fts")
    n1 = c.fetchone()[0]
    c.execute("select count(*) from agent_memories")
    n2 = c.fetchone()[0]
    return [f"fts={n1} main={n2}"]


def fts_match():
    c.execute("""select rowid from agent_memories_fts where agent_memories_fts match ? limit 5""",
              ('"测试"',))
    return [str(r[0]) for r in c.fetchall()]


step("integrity_check (full)", integrity)
step("FTS integrity-check", fts_integrity)
step("fts/main rowcount", fts_rowcount)
step("fts MATCH", fts_match)
step("expire_overdue UPDATE", expire_update)
step("mark_mirror_dirty UPDATE x20", mark_dirty_one)
step("set_agent_memory_state UPDATE x10", set_state_live_one)
step("DELETE (fires _ad trigger)", delete_one)
step("FTS integrity-check (again after writes)", fts_integrity)

for p in (dst, dst + "-wal", dst + "-shm"):
    try:
        os.remove(p)
        print("removed", p)
    except Exception as exc:
        print("remove failed", p, exc)
