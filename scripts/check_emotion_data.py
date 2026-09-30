"""Check emotion data in behavior_events - vMA-1.4 readiness."""
import sqlite3
import json

db = sqlite3.connect("/data/memory_agent.db")

# 1. Total behavior_events with status='ok'
total = db.execute("SELECT COUNT(*) FROM behavior_events WHERE status='ok'").fetchone()[0]
print(f"Total ok behavior_events: {total}")

# 2. Check if scene_graph_json contains emotion
# scene_graph_json is a JSON dict with 'emotion' key
rows = db.execute("SELECT scene_graph_json FROM behavior_events WHERE status='ok' AND scene_graph_json IS NOT NULL LIMIT 5").fetchall()
print(f"\nSample scene_graph_json rows: {len(rows)}")
for (sg,) in rows[:3]:
    try:
        d = json.loads(sg)
        print(f"  emotion={d.get('emotion')}, scene={d.get('scene_text','')[:40]}")
    except:
        print(f"  (parse error)")

# 3. Count by emotion
print("\n=== Emotion distribution ===")
emotions = {}
date_range = db.execute("SELECT MIN(server_ts), MAX(server_ts) FROM behavior_events WHERE status='ok' AND scene_graph_json IS NOT NULL").fetchone()
print(f"Date range: {date_range[0]} ~ {date_range[1]}")

rows = db.execute("SELECT scene_graph_json FROM behavior_events WHERE status='ok' AND scene_graph_json IS NOT NULL").fetchall()
for (sg,) in rows:
    try:
        d = json.loads(sg)
        e = d.get("emotion", "unknown")
        emotions[e] = emotions.get(e, 0) + 1
    except:
        emotions["parse_error"] = emotions.get("parse_error", 0) + 1

for e, c in sorted(emotions.items(), key=lambda x: -x[1]):
    print(f"  {e}: {c} ({c*100/len(rows):.1f}%)")

# 4. Days with data
days = db.execute("SELECT DISTINCT day FROM behavior_events WHERE status='ok' AND scene_graph_json IS NOT NULL ORDER BY day").fetchall()
print(f"\nDays with emotion data: {len(days)}")
for (d,) in days:
    count = db.execute("SELECT COUNT(*) FROM behavior_events WHERE status='ok' AND scene_graph_json IS NOT NULL AND day=?", (d,)).fetchone()[0]
    print(f"  {d}: {count} events")

db.close()
