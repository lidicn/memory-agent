import sys, os
sys.path.insert(0, os.path.abspath("scripts"))
import scan_day_bounds as s
rows = []
for r in ("src/memory_agent",):
    rows.extend(s.scan_root(r))
print("NROWS", len(rows))
sample = rows[0]
print("TYPE", type(sample), sample)
for x in rows:
    d = x if isinstance(x, dict) else getattr(x, "__dict__", None)
    if d is None:
        continue
    if d.get("label") == "config":
        print("CONFIG", d.get("file"), d.get("line"), d.get("expr"), d.get("reason"))
