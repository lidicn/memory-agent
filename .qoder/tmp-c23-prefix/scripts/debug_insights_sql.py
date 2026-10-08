"""调试新框架 SQL 参数。"""
import sys
sys.path.insert(0, "/app/src")
from datetime import datetime, timedelta
from memory_agent.store import Store
from memory_agent.insights.models import InsightConfig, TimeRange
from memory_agent.insights.repository import StoreRepository, _to_iso, _to_epoch

store = Store("/data/memory_agent.db")
config = InsightConfig()
repo = StoreRepository(store, config)

end = datetime.now()
start = end - timedelta(days=1)
tr = TimeRange(start=start, end=end)

print(f"container now: {datetime.now()}")
print(f"tr.start: {tr.start}, tr.end: {tr.end}")
print(f"tr.start_ts: {tr.start_ts}, tr.end_ts: {tr.end_ts}")
print(f"_to_iso(start_ts): {_to_iso(tr.start_ts)}")
print(f"_to_iso(end_ts): {_to_iso(tr.end_ts)}")

bounds = repo._bounds(tr)
print(f"_bounds: {bounds}")

# 直接查库看最近一条事件的时间
rows = store.db_query("SELECT ts, day, room, entity_id FROM events ORDER BY ts DESC LIMIT 3", ())
print(f"\n最近3条事件:")
for r in rows:
    print(f"  ts={r['ts']}, day={r['day']}, room={r['room']}, entity={r['entity_id']}")

# 用 _bounds 的参数直接查
start_iso, end_iso, start_day, end_day = bounds
print(f"\n直接用 bounds 查: day BETWEEN '{start_day}' AND '{end_day}', ts BETWEEN '{start_iso}' AND '{end_iso}'")
rows2 = store.db_query(
    "SELECT COUNT(*) as c FROM events WHERE day BETWEEN ? AND ? AND ts BETWEEN ? AND ?",
    (start_day, end_day, start_iso, end_iso)
)
print(f"count: {rows2[0]['c']}")

# 只按 day 查
rows3 = store.db_query("SELECT COUNT(*) as c FROM events WHERE day BETWEEN ? AND ?", (start_day, end_day))
print(f"只按 day 查 count: {rows3[0]['c']}")
