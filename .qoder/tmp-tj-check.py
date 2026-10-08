import sqlite3

c = sqlite3.connect("file:/data/memory_agent.db?mode=ro", uri=True)
cols = [r[1] for r in c.execute("PRAGMA table_info(active_rules)").fetchall()]
print("active_rules_cols", len(cols), "trigger_json" in cols)
print("count_trigger_json_nonempty",
      c.execute("SELECT COUNT(*) FROM active_rules "
                "WHERE trigger_json NOT IN ('', '{}')").fetchone()[0])
c.close()
