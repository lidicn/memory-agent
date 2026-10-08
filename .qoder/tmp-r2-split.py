import sqlite3, hashlib
db = "/data/memory_agent.db"
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rows = c.execute("SELECT state, COUNT(*), SUM(LENGTH(content)) FROM agent_memories GROUP BY state").fetchall()
print("[R2-split] state / rows / content_bytes_sum")
for s, n, b in rows:
    print(f"  {s:<10} rows={n:<5} content_bytes={b}")
print("[R2-split] total_rows=", c.execute("SELECT COUNT(*) FROM agent_memories").fetchone()[0])
print("[R2-split] mirror_dirty=", c.execute("SELECT COUNT(*) FROM agent_memories WHERE mirror_dirty=1").fetchone()[0])
print("[R2-split] wal_mode=", c.execute("PRAGMA journal_mode").fetchone()[0])
c.close()
