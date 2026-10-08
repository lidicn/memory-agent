import sqlite3

c = sqlite3.connect("file:/data/memory_agent.db?mode=ro", uri=True)
for day in ("2026-10-03", "2026-09-26", "2026-10-01"):
    rows = c.execute(
        "SELECT domain, COUNT(*) FROM events WHERE day=? GROUP BY domain ORDER BY 2 DESC",
        (day,)).fetchall()
    print(day, [(d, n) for d, n in rows])
print("cols", [r[1] for r in c.execute("PRAGMA table_info(events)").fetchall()])
c.close()
