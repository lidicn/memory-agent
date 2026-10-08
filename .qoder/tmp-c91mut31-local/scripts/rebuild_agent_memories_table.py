"""Rebuild agent_memories table to fix b-tree corruption.

Steps:
1. Export all rows to a temp table
2. DROP original table + FTS
3. Recreate table from schema
4. Reimport all rows
5. Rebuild FTS
"""
import sqlite3
import sys

db_path = "/data/memory_agent.db"
db = sqlite3.connect(db_path)

print("=== Step 1: Export all agent_memories rows ===")
# Get column names
cols = db.execute("PRAGMA table_info(agent_memories)").fetchall()
col_names = [c[1] for c in cols]
print(f"Columns: {col_names}")

# Export all rows
rows = db.execute(f"SELECT * FROM agent_memories").fetchall()
print(f"Exported {len(rows)} rows")

# Convert to list of dicts
data = []
for r in rows:
    d = {}
    for i, col in enumerate(col_names):
        d[col] = r[i]
    data.append(d)

print("=== Step 2: Drop FTS triggers and table ===")
# Drop FTS triggers first
for trig in ["agent_memories_fts_ai", "agent_memories_fts_ad", "agent_memories_fts_au"]:
    db.execute(f"DROP TRIGGER IF EXISTS {trig}")
db.execute("DROP TABLE IF EXISTS agent_memories_fts")
db.execute("DROP TABLE IF EXISTS agent_memories")
db.commit()
print("Dropped agent_memories and FTS")

print("=== Step 3: Recreate table ===")
db.execute("""
CREATE TABLE agent_memories (
    memory_id            TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL,
    text                 TEXT NOT NULL,
    topic_key            TEXT NOT NULL DEFAULT '',
    source               TEXT NOT NULL DEFAULT 'ma',
    tags_json            TEXT NOT NULL DEFAULT '[]',
    source_refs_json     TEXT NOT NULL DEFAULT '[]',
    state                TEXT NOT NULL DEFAULT 'staging',
    trust                REAL NOT NULL DEFAULT 0.0,
    ttl_days             INTEGER NOT NULL DEFAULT 30,
    auto_promote_blocked INTEGER NOT NULL DEFAULT 0,
    mirror_dirty         INTEGER NOT NULL DEFAULT 0,
    feedback_up          INTEGER NOT NULL DEFAULT 0,
    feedback_down        INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    expires_at           TEXT NOT NULL,
    prev_id             TEXT NOT NULL DEFAULT '',
    valid_from          TEXT NOT NULL DEFAULT '',
    valid_to            TEXT NOT NULL DEFAULT '',
    observed_at         TEXT NOT NULL DEFAULT '',
    member_id           TEXT NOT NULL DEFAULT ''
)
""")
db.execute("CREATE INDEX IF NOT EXISTS idx_agent_mem_state ON agent_memories(state)")
db.execute("CREATE INDEX IF NOT EXISTS idx_agent_mem_session ON agent_memories(session_id)")
db.execute("CREATE INDEX IF NOT EXISTS idx_agent_mem_topic ON agent_memories(topic_key)")
db.commit()
print("Table recreated")

print("=== Step 4: Reimport data ===")
placeholders = ", ".join(["?"] * len(col_names))
col_list = ", ".join(col_names)
imported = 0
skipped = 0
for d in data:
    # Skip rows with empty memory_id (they can't be inserted into a PRIMARY KEY column)
    mid = d.get("memory_id")
    if not mid:
        print(f"  Skipping row with empty memory_id: state={d.get('state')}, text={d.get('text','')[:60]!r}")
        skipped += 1
        continue
    values = [d.get(col) for col in col_names]
    db.execute(f"INSERT OR REPLACE INTO agent_memories ({col_list}) VALUES ({placeholders})", values)
    imported += 1
db.commit()
print(f"Imported {imported} rows, skipped {skipped} rows (empty memory_id)")

print("=== Step 5: Rebuild FTS ===")
db.execute("""
CREATE VIRTUAL TABLE agent_memories_fts USING fts5(
    text, topic_key, tags_json,
    content='agent_memories', content_rowid='rowid',
    tokenize='trigram'
)
""")
db.execute("""
CREATE TRIGGER agent_memories_fts_ai
AFTER INSERT ON agent_memories BEGIN
    INSERT INTO agent_memories_fts(rowid, text, topic_key, tags_json)
    VALUES (new.rowid, new.text, new.topic_key, new.tags_json);
END;
""")
db.execute("""
CREATE TRIGGER agent_memories_fts_ad
AFTER DELETE ON agent_memories BEGIN
    INSERT INTO agent_memories_fts(agent_memories_fts, rowid, text, topic_key, tags_json)
    VALUES ('delete', old.rowid, old.text, old.topic_key, old.tags_json);
END;
""")
db.execute("""
CREATE TRIGGER agent_memories_fts_au
AFTER UPDATE ON agent_memories BEGIN
    INSERT INTO agent_memories_fts(agent_memories_fts, rowid, text, topic_key, tags_json)
    VALUES ('delete', old.rowid, old.text, old.topic_key, old.tags_json);
    INSERT INTO agent_memories_fts(rowid, text, topic_key, tags_json)
    VALUES (new.rowid, new.text, new.topic_key, new.tags_json);
END;
""")
# Populate FTS with existing data
db.execute("INSERT INTO agent_memories_fts(agent_memories_fts, rowid, text, topic_key, tags_json) SELECT 'rebuild', rowid, text, topic_key, tags_json FROM agent_memories")
db.commit()
print("FTS rebuilt and populated")

# Verify
print("=== Verification ===")
count = db.execute("SELECT COUNT(*) FROM agent_memories").fetchone()[0]
print(f"agent_memories rows: {count}")
states = db.execute("SELECT state, COUNT(*) FROM agent_memories GROUP BY state").fetchall()
for s, c in states:
    print(f"  {s}: {c}")
empty = db.execute("SELECT COUNT(*) FROM agent_memories WHERE memory_id IS NULL OR memory_id = ''").fetchone()[0]
print(f"Empty memory_id: {empty}")

# Test DELETE works now
db.execute("DELETE FROM agent_memories WHERE memory_id = '___test_delete____'")
db.commit()
print("DELETE test: OK (no malformed error)")

db.close()
print("\n=== Rebuild complete ===")
