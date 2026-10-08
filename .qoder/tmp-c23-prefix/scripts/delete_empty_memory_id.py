"""Delete 2 revoked rows with empty memory_id (QA test artifacts)."""
import sqlite3

db = sqlite3.connect("/data/memory_agent.db")

# Show before
before = db.execute("SELECT COUNT(*) FROM agent_memories WHERE memory_id IS NULL OR memory_id = ''").fetchone()[0]
print(f"Rows with empty memory_id before: {before}")

# Delete them (they are revoked QA test data)
deleted = db.execute(
    "DELETE FROM agent_memories WHERE (memory_id IS NULL OR memory_id = '') AND state = 'revoked'"
).rowcount
print(f"Deleted {deleted} revoked rows with empty memory_id")

db.commit()

# Verify
after = db.execute("SELECT COUNT(*) FROM agent_memories WHERE memory_id IS NULL OR memory_id = ''").fetchone()[0]
print(f"Rows with empty memory_id after: {after}")

# Also check dirty count
dirty = db.execute("SELECT COUNT(*) FROM agent_memories WHERE mirror_dirty=1 AND memory_id IS NOT NULL AND memory_id != ''").fetchone()[0]
print(f"Dirty mirrors (valid memory_id): {dirty}")

db.close()
print("Done.")
