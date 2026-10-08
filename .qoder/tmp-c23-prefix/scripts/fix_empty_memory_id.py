"""Fix empty memory_id rows + find Chroma path."""
import sqlite3
import os
import json

db = sqlite3.connect("/data/memory_agent.db")
db.row_factory = sqlite3.Row

# 1. Find rows with empty memory_id
print("=== Rows with empty memory_id ===")
rows = db.execute("SELECT rowid, memory_id, state, substr(text,1,80) as text_preview, created_at, session_id, topic_key FROM agent_memories WHERE memory_id IS NULL OR memory_id = ''").fetchall()
for r in rows:
    print(f"  rowid={r['rowid']}, state={r['state']}, topic={r['topic_key']}, text={r['text_preview']!r}, created_at={r['created_at']}")

# 2. Check config for chroma path
print("\n=== Config chroma/vector keys ===")
config_path = "/data/config.json"
if os.path.exists(config_path):
    with open(config_path) as f:
        cfg = json.load(f)
    for k, v in cfg.items():
        kl = k.lower()
        if "chroma" in kl or "vector" in kl or "persist" in kl:
            print(f"  {k}: {v}")

# 3. Find chroma directories under /data
print("\n=== Find chroma dirs under /data ===")
for root, dirs, files in os.walk("/data"):
    for d in dirs:
        if "chroma" in d.lower():
            full = os.path.join(root, d)
            print(f"  DIR: {full}")
            try:
                for item in os.listdir(full):
                    item_path = os.path.join(full, item)
                    if os.path.isfile(item_path):
                        print(f"    {item}: {os.path.getsize(item_path)} bytes")
                    else:
                        print(f"    {item}/")
            except Exception as e:
                print(f"    (error listing: {e})")

# 4. Check common alternate paths
print("\n=== Alternate chroma paths ===")
for p in ["/app/data/chroma", "/data/vector/chroma", "/data/chroma_db", "/data/memory_chroma", "/data/chroma_storage"]:
    print(f"  {p}: {'EXISTS' if os.path.exists(p) else 'not found'}")

db.close()
