"""只读探针：phrase 包裹在 trigram 下的召回边界（用生产真实存在的文本行）。"""
import sqlite3

conn = sqlite3.connect("file:/data/memory_agent.db?mode=ro", uri=True)
txt = conn.execute("SELECT text FROM agent_memories WHERE rowid=229").fetchone()[0]
print("rowid229 text =", repr(txt))
print("字符数 =", len(txt))


def n(q):
    try:
        return len(conn.execute(
            "SELECT rowid FROM agent_memories_fts WHERE agent_memories_fts MATCH ?",
            (q,)).fetchall())
    except Exception as exc:
        return f"抛 {type(exc).__name__}"


Q = '"'
cases = {
    "整句=原文 (phrase)": Q + txt + Q,
    "整句+结尾多一字": Q + txt + "X" + Q,
    "整句中间删一字": Q + txt[:5] + txt[6:] + Q,
    "两段不连续 OR": (Q + txt[0:4] + Q) + " OR " + (Q + txt[-4:] + Q),
    "单个 4 字片段": Q + txt[0:4] + Q,
}
for k, v in cases.items():
    print(f"   {k:18} -> {n(v)} 行")

print("\n-- 2 字常用房间词（低于 trigram 的 3 字下限）--")
for w in ("客厅", "卧室", "厨房", "卫生间"):
    main = conn.execute(
        "SELECT COUNT(*) FROM agent_memories WHERE text LIKE ?", (f"%{w}%",)).fetchone()[0]
    acc = conn.execute(
        "SELECT COUNT(*) FROM agent_memories_fts WHERE text LIKE ?", (f"%{w}%",)).fetchone()[0]
    print(f"   {w}: 主表含词 {main} 行 / phrase MATCH {n(Q + w + Q)} 行 / FTS 表 LIKE 加速 {acc} 行")
conn.close()
