"""只读探针：真实 question 文本下，FTS 第二路到底召回多少行。

不改任何数据。phrase 包裹 = 生产实现；split = 假想的"按 3 字以上片段 OR"实现，
只用来看差距，不落进代码。
"""
import sqlite3, sys
sys.path.insert(0, "/app/src")
conn = sqlite3.connect("file:/data/memory_agent.db?mode=ro", uri=True)

rows = conn.execute(
    "SELECT DISTINCT feedback_question FROM agent_memories "
    "WHERE feedback_question IS NOT NULL AND feedback_question != '' LIMIT 20").fetchall()
qs = [r[0] for r in rows]
print(f"真实 feedback_question 条数 = {len(qs)}")
for q in qs:
    print("   ", repr(q))

def phrase_search(q):
    p = '"' + q.replace('"', '""') + '"'
    try:
        return len(conn.execute(
            "SELECT rowid FROM agent_memories_fts WHERE agent_memories_fts MATCH ?", (p,)).fetchall())
    except Exception as exc:
        return f"抛 {exc}"

def split_search(q):
    toks = [t for t in q.replace("，", " ").replace("。", " ").replace("？", " ").split() if len(t) >= 3]
    if not toks:
        return "无可切分片段(≥3字)"
    expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in toks)
    try:
        return len(conn.execute(
            "SELECT rowid FROM agent_memories_fts WHERE agent_memories_fts MATCH ?", (expr,)).fetchall())
    except Exception as exc:
        return f"抛 {exc}"

print("\n-- 逐条：phrase（生产实现）vs split（仅对比用）--")
for q in qs:
    print(f"   {q!r:40} phrase={phrase_search(q)}  split={split_search(q)}")

print("\n-- 主表里 LIKE 真命中数（Ground truth）--")
for q in qs[:8]:
    for seg in [s for s in q.replace("，", " ").replace("。", " ").replace("？", " ").split() if len(s) >= 2]:
        n = conn.execute("SELECT COUNT(*) FROM agent_memories WHERE text LIKE ?", (f"%{seg}%",)).fetchone()[0]
        m = conn.execute("SELECT COUNT(*) FROM agent_memories_fts WHERE text LIKE ?", (f"%{seg}%",)).fetchone()[0]
        print(f"   片段 {seg!r}: 主表 LIKE={n}  FTS表 LIKE 加速={m}")

print("\n-- 各 state 行数（live 只有 1 行时召回面天然小）--")
for r in conn.execute("SELECT state, COUNT(*) FROM agent_memories GROUP BY state"):
    print("   ", r)
conn.close()
