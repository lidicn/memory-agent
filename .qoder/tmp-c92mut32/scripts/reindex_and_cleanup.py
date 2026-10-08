"""REINDEX agent_memories and delete empty memory_id rows."""
import sqlite3

db = sqlite3.connect("/data/memory_agent.db")
print("Reindexing agent_memories...")
db.execute("REINDEX agent_memories")
db.commit()
print("REINDEX OK")

before = db.execute("SELECT COUNT(*) FROM agent_memories WHERE memory_id IS NULL OR memory_id = ''").fetchone()[0]
print(f"Empty memory_id rows before: {before}")

deleted = db.execute("DELETE FROM agent_memories WHERE (memory_id IS NULL OR memory_id = '') AND state = 'revoked'").rowcount
print(f"Deleted: {deleted}")
db.commit()

after = db.execute("SELECT COUNT(*) FROM agent_memories WHERE memory_id IS NULL OR memory_id = ''").fetchone()[0]
print(f"Empty memory_id rows after: {after}")

# Also reindex FTS table since it was rebuilt recently
print("Reindexing FTS...")
db.execute("REINDEX agent_memories_fts")
db.commit()
print("FTS REINDEX OK")

db.close()
print("Done.")
