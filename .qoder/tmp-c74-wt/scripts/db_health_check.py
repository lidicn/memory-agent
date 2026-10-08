"""Quick DB health check + Chroma sync diagnosis."""
import sqlite3
import sys

db = sqlite3.connect("/data/memory_agent.db")

# 1. Integrity check
print("=== Integrity Check ===")
result = db.execute("PRAGMA integrity_check").fetchone()
print(f"integrity_check: {result[0]}")

# 2. Table sizes
print("\n=== Table Sizes ===")
tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
for (t,) in tables:
    try:
        count = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {count} rows")
    except Exception as e:
        print(f"  {t}: ERROR - {e}")

# 3. Check agent_memories specifically (where promote writes)
print("\n=== agent_memories state distribution ===")
states = db.execute("SELECT state, COUNT(*) FROM agent_memories GROUP BY state").fetchall()
for s, c in states:
    print(f"  {s}: {c}")

# 4. Check for memories with None/empty memory_id
print("\n=== agent_memories with empty/None memory_id ===")
bad = db.execute("SELECT COUNT(*) FROM agent_memories WHERE memory_id IS NULL OR memory_id = ''").fetchone()[0]
print(f"  empty memory_id: {bad}")

# 5. Chroma check
print("\n=== Chroma DB check ===")
import os
chroma_path = "/data/chroma"
if os.path.exists(chroma_path):
    for root, dirs, files in os.walk(chroma_path):
        for f in files:
            fp = os.path.join(root, f)
            size = os.path.getsize(fp)
            print(f"  {fp}: {size} bytes")
else:
    print(f"  Chroma path not found: {chroma_path}")

db.close()
print("\nDone.")
