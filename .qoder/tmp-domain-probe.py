"""events 表里到底有没有 light/climate 域？有历史还是从未有过？"""
import sqlite3
import sys
from collections import Counter
from datetime import timedelta

sys.path.insert(0, "/app/src")
from memory_agent.config import get_config      # noqa: E402
from memory_agent.store import now_local        # noqa: E402

cfg = get_config()
c = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
q = lambda s: c.execute(s).fetchall()  # noqa: E731

now = now_local(cfg.tz_offset_hours)
print("WALLCLOCK =", now.isoformat(timespec="seconds"))
print("total =", q("select count(*) from events")[0][0])
print("min_ts/max_ts =", q("select min(ts), max(ts) from events")[0])

print("domain 全量分布 =", q(
    "select domain, count(*), min(ts), max(ts) from events group by domain "
    "order by 2 desc limit 15"))

for dom in ("light", "climate", "switch", "cover"):
    rows = q(f"select count(*), min(ts), max(ts) from events where domain='{dom}'")[0]
    print(f"domain={dom:8} n={rows[0]:7} first={rows[1]} last={rows[2]}")

print("按 entity_id 前缀（域）Top15 =", Counter(
    (r[0].split(".")[0] if r[0] else "<empty>") for r in
    q("select distinct entity_id from events")).most_common(15))
