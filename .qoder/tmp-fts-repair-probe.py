"""副本上验证「启动迁移为什么不自愈」并找最小修复配方。

顺序：
 1) 复现启动序列（DROP 触发器 → DROP TABLE → CREATE trigram → 计数），看新建后的索引行数；
 2) 试最小修复：INSERT … ('rebuild')，再看 integrity-check 与真实 UPDATE 是否恢复；
 3) 若 1) 说明 DROP 没清掉影子表，则验证彻底重建（DROP + CREATE + rebuild）。
生产库只读，全部写测试打在快照副本上。
"""
import os
import sqlite3
import traceback

SRC = "/data/memory_agent.db"
DST = "/tmp/qoder-fts-repair-copy.db"
need = os.path.getsize(SRC) * 2
st = os.statvfs(os.path.dirname(DST))
if st.f_bavail * st.f_frsize < need:
    print("NO ROOM in /tmp")
    raise SystemExit(2)

src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True, timeout=30)
out = sqlite3.connect(DST)
src.backup(out)
src.close()
out.close()
print("copy ok, size:", os.path.getsize(DST))

conn = sqlite3.connect(DST)
c = conn.cursor()


def show(label, sql, params=()):
    try:
        c.execute(sql, params)
        rows = c.fetchall()
        print(f"  {label}: {rows[:6]}{' …' if len(rows) > 6 else ''} ({len(rows)} row)")
        return rows
    except Exception as exc:
        print(f"  {label}: !! {type(exc).__name__}: {exc}")
        return None


def step(label, sql, params=(), commit=True):
    print(f"\n### {label}")
    try:
        c.execute(sql, params)
        if commit:
            conn.commit()
        print("    ok, rowcount =", c.rowcount)
        return True
    except Exception as exc:
        print(f"    !! {type(exc).__name__}: {exc}")
        traceback.print_exc()
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def integrity_check():
    try:
        c.execute("insert into agent_memories_fts(agent_memories_fts, rank) values('integrity-check', 1)")
        conn.commit()
        return "integrity-check PASS"
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return f"integrity-check FAIL: {type(exc).__name__}: {exc}"


def one_update():
    """sweep 的真实写形状：UPDATE 触发 _au 触发器。"""
    try:
        c.execute("UPDATE agent_memories SET mirror_dirty=mirror_dirty WHERE memory_id IN "
                  "(select memory_id from agent_memories limit 5)")
        conn.commit()
        return "UPDATE PASS"
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return f"UPDATE FAIL: {type(exc).__name__}: {exc}"


print("\n=== 0. 副本现状 ===")
show("main count", "select count(*) from agent_memories")
show("fts count", "select count(*) from agent_memories_fts")
show("docsize count", "select count(*) from agent_memories_fts_docsize")
show("data count", "select count(*) from agent_memories_fts_data")
show("config rows", "select * from agent_memories_fts_config")
print("  ", integrity_check())
print("  ", one_update())

print("\n=== 1. 复现启动序列：DROP 触发器 + DROP TABLE + CREATE(trigram) ===")
for t in ("agent_memories_fts_ai", "agent_memories_fts_ad", "agent_memories_fts_au"):
    step(f"drop trigger {t}", f"DROP TRIGGER IF EXISTS {t}")
step("drop table agent_memories_fts", "DROP TABLE IF EXISTS agent_memories_fts")
print("  shadow tables left after DROP TABLE:")
show("  sqlite_master like %fts%", "select name from sqlite_master where name like '%fts%'")
step("create virtual table (trigram)",
     """CREATE VIRTUAL TABLE agent_memories_fts USING fts5(
          text, topic_key, tags_json,
          content='agent_memories', content_rowid='rowid', tokenize='trigram')""")
# 触发器在 DROP TABLE 前已被删掉；不重建回来，UPDATE 就不经过 FTS，写测试会变成假绿。
step("recreate trigger ai", """CREATE TRIGGER agent_memories_fts_ai
     AFTER INSERT ON agent_memories BEGIN
       INSERT INTO agent_memories_fts(rowid, text, topic_key, tags_json)
       VALUES (new.rowid, new.text, new.topic_key, new.tags_json);
     END""")
step("recreate trigger ad", """CREATE TRIGGER agent_memories_fts_ad
     AFTER DELETE ON agent_memories BEGIN
       INSERT INTO agent_memories_fts(agent_memories_fts, rowid, text, topic_key, tags_json)
       VALUES ('delete', old.rowid, old.text, old.topic_key, old.tags_json);
     END""")
step("recreate trigger au", """CREATE TRIGGER agent_memories_fts_au
     AFTER UPDATE ON agent_memories BEGIN
       INSERT INTO agent_memories_fts(agent_memories_fts, rowid, text, topic_key, tags_json)
       VALUES ('delete', old.rowid, old.text, old.topic_key, old.tags_json);
       INSERT INTO agent_memories_fts(rowid, text, topic_key, tags_json)
       VALUES (new.rowid, new.text, new.topic_key, new.tags_json);
     END""")
show("fts count right after CREATE", "select count(*) from agent_memories_fts")
show("docsize count right after CREATE", "select count(*) from agent_memories_fts_docsize")
print("  ", integrity_check())
print("  ", one_update())

print("\n=== 2. rebuild 能否修好 ===")
step("INSERT … ('rebuild')", "insert into agent_memories_fts(agent_memories_fts) values('rebuild')")
show("fts count after rebuild", "select count(*) from agent_memories_fts")
print("  ", integrity_check())
print("  ", one_update())

for p in (DST, DST + "-wal", DST + "-shm"):
    try:
        os.remove(p)
        print("removed", p)
    except Exception as exc:
        print("remove failed", p, exc)
