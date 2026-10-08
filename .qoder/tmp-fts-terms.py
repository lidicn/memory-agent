"""只读探针：0 命中是数据本来没有，还是第二个缺陷？

不做任何写入；对生产库以 mode=ro 打开。
"""
import os, sqlite3, sys
sys.path.insert(0, "/app/src")

DB = os.environ.get("DB_PATH", "/data/memory_agent.db")
if not os.path.exists(DB):
    for cand in ("/data/ma.db", "/data/memory-agent.db", "/data/app.db"):
        if os.path.exists(cand):
            DB = cand
            break
print("DB =", DB)

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

print("\n-- 表清单里和 FTS 相关的对象 --")
for r in conn.execute("SELECT type,name FROM sqlite_master WHERE name LIKE '%fts%' ORDER BY type,name"):
    print("  ", r)

print("\n-- 列名 --")
cols = [r[1] for r in conn.execute("PRAGMA table_info(agent_memories)")]
print("  ", cols)

print("\n-- LIKE 直接扫主表：这些词到底在不在被索引的列里 --")
for term in ("水位", "客厅", "冰箱", "成员", "规则", "diary"):
    row = conn.execute(
        "SELECT SUM(text LIKE ?), SUM(topic_key LIKE ?), SUM(tags_json LIKE ?), COUNT(*) "
        "FROM agent_memories "
        "WHERE text LIKE ? OR topic_key LIKE ? OR tags_json LIKE ?",
        (f"%{term}%", f"%{term}%", f"%{term}%", f"%{term}%", f"%{term}%", f"%{term}%"),
    ).fetchone()
    print(f"   {term!r}: text命中={row[0]} topic_key={row[1]} tags={row[2]} 总命中行={row[3]}")

print("\n-- 原始 MATCH（不走包装层）：单引号短语 vs 裸词 --")
for q in ('"水位"', '水位', '"客厅"', '客厅', '"成员"'):
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT rowid FROM agent_memories_fts WHERE agent_memories_fts MATCH ? LIMIT 5", (q,))]
        print(f"   MATCH {q!r} -> {ids}")
    except Exception as exc:
        print(f"   MATCH {q!r} -> 抛异常 {type(exc).__name__}: {exc}")

print("\n-- docsize 行数 = 真被索引的文档数 --")
try:
    print("   ", conn.execute("SELECT COUNT(*) FROM agent_memories_fts_docsize").fetchone())
except Exception as exc:
    print("   抛", exc)

print("\n-- 抽样：live/staging 各 3 行的 text 前 60 字，看内容长什么样 --")
for st in ("live", "staging"):
    for r in conn.execute(
            "SELECT rowid, substr(text,1,60), topic_key FROM agent_memories WHERE state=? LIMIT 3", (st,)):
        print(f"   [{st}] {r}")

print("\n-- 健康检查（只读不做 integrity-check 写入，改查 shadow 表形状） --")
try:
    print("   data rows:", conn.execute("SELECT COUNT(*) FROM agent_memories_fts_data").fetchone())
except Exception as exc:
    print("   抛", exc)
conn.close()
