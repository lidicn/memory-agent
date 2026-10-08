import urllib.request
try:
    resp = urllib.request.urlopen("http://localhost:8000/health", timeout=5)
    print("HEALTH:", resp.read().decode())
except Exception as e:
    print("HEALTH ERROR:", e)

# Test VIEW exists
import sqlite3
try:
    conn = sqlite3.connect("/data/memory_agent.db")
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='view' AND name='unified_events'").fetchone()
    print("VIEW exists:", row is not None)
    if row:
        count = conn.execute("SELECT COUNT(*) FROM unified_events").fetchone()[0]
        print("VIEW row count:", count)
        # Check source distribution
        sources = conn.execute("SELECT source, COUNT(*) FROM unified_events GROUP BY source").fetchall()
        print("Source distribution:", sources)
    conn.close()
except Exception as e:
    print("VIEW ERROR:", e)
