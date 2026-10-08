"""只读探针：events 表里到底有没有 light / climate 域的事件（全表聚合，非采样）。"""
import sqlite3

conn = sqlite3.connect("file:/data/memory_agent.db?mode=ro", uri=True)
print("-- events 表列 --")
cols = [r[1] for r in conn.execute("PRAGMA table_info(events)")]
print("   ", cols)

for col in ("domain", "entity_id"):
    if col not in cols:
        continue
    expr = col if col == "domain" else "substr(entity_id, 1, instr(entity_id, '.') - 1)"
    print(f"\n-- 全表按 {col} 聚合（{conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]} 行）--")
    rows = conn.execute(
        f"SELECT {expr} AS d, COUNT(*) FROM events GROUP BY d ORDER BY COUNT(*) DESC LIMIT 25").fetchall()
    for d, n in rows:
        print(f"   {d}: {n}")

print("\n-- 关键判定：light / climate 域是否存在 --")
for want in ("light", "climate", "switch", "cover"):
    if "domain" in cols:
        n = conn.execute("SELECT COUNT(*) FROM events WHERE domain=?", (want,)).fetchone()[0]
    else:
        n = conn.execute(
            "SELECT COUNT(*) FROM events WHERE entity_id LIKE ?", (f"{want}.%",)).fetchone()[0]
    print(f"   {want}: {n} 行")

print("\n-- 时间范围 --")
for c in ("ts", "server_ts", "created_at"):
    if c in cols:
        print(f"   {c}: min={conn.execute(f'SELECT MIN({c}) FROM events').fetchone()[0]}"
              f" max={conn.execute(f'SELECT MAX({c}) FROM events').fetchone()[0]}")
        break
conn.close()
