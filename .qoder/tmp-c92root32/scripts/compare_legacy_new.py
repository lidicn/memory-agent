"""对比 legacy insights vs 新框架核心数值一致性。

在生产库上跑同样的查询，对比 total_events / day_counts / top entities 等核心数值。
"""
import sys, json
sys.path.insert(0, "/app/src")
from datetime import datetime, timedelta
from memory_agent.store import Store
from memory_agent.insights.models import InsightConfig, TimeRange
from memory_agent.insights.repository import StoreRepository
from memory_agent.insights.service import BehaviorService
from memory_agent.insights_legacy import InsightService as LegacyService

store = Store("/data/memory_agent.db")
config = InsightConfig()

# 新框架
repo = StoreRepository(store, config)
svc = BehaviorService(repo, None, config)

# Legacy
legacy = LegacyService(store, None)

end = datetime.now()
start_1d = end - timedelta(days=1)
start_7d = end - timedelta(days=7)
tr_1d = TimeRange(start=start_1d, end=end)
tr_7d = TimeRange(start=start_7d, end=end)

def compare(name, legacy_val, new_val, keys=None):
    """对比两个 dict，打印差异。"""
    print(f"\n=== {name} ===")
    if keys:
        for k in keys:
            lv = legacy_val.get(k) if isinstance(legacy_val, dict) else None
            nv = new_val.get(k) if isinstance(new_val, dict) else None
            match = "✅" if lv == nv else "❌"
            print(f"  {match} {k}: legacy={lv} new={nv}")
    else:
        print(f"  legacy: {json.dumps(legacy_val, ensure_ascii=False, default=str)[:300]}")
        print(f"  new:    {json.dumps(new_val, ensure_ascii=False, default=str)[:300]}")

# 1. count_events (1d) - 直接用 store
legacy_count_1d = store.count_events(start=tr_1d.start.isoformat(), end=tr_1d.end.isoformat())
new_count_1d = repo.count_events(tr_1d)
compare("count_events (1d)", {"count": legacy_count_1d}, {"count": new_count_1d}, ["count"])

# 2. count_events (7d)
legacy_count_7d = store.count_events(start=tr_7d.start.isoformat(), end=tr_7d.end.isoformat())
new_count_7d = repo.count_events(tr_7d)
compare("count_events (7d)", {"count": legacy_count_7d}, {"count": new_count_7d}, ["count"])

# 3. day_counts (7d)
legacy_dc = store.day_counts(tr_7d.start.strftime("%Y-%m-%d"), tr_7d.end.strftime("%Y-%m-%d"))
new_dc = repo.day_counts(tr_7d)
print(f"\n=== day_counts (7d) ===")
all_days = sorted(set(list(legacy_dc.keys()) + list(new_dc.keys())))
for day in all_days:
    lv = legacy_dc.get(day, 0)
    nv = new_dc.get(day, 0)
    match = "✅" if lv == nv else "❌"
    print(f"  {match} {day}: legacy={lv} new={nv}")

# 4. coverage (1d)
legacy_cov = legacy.data_coverage(days=1)
new_cov = svc.coverage(tr_1d)
compare("coverage (1d)", legacy_cov, new_cov, ["total_events", "day_coverage"])

# 5. usage 客厅 (1d)
legacy_usage = legacy.device_usage(days=1, room="客厅")
new_usage = svc.usage(tr_1d, room="客厅")
print(f"\n=== usage 客厅 (1d) ===")
print(f"  legacy items: {len(legacy_usage.get('items', []))}")
print(f"  new items: {len(new_usage.get('items', []))}")
if legacy_usage.get("items") and new_usage.get("items"):
    for i in range(min(3, len(legacy_usage["items"]), len(new_usage["items"]))):
        li = legacy_usage["items"][i]
        ni = new_usage["items"][i]
        le = li.get("entity_id", li.get("entity", ""))
        ne = ni.get("entity_id", ni.get("entity", ""))
        lc = li.get("count", li.get("events", 0))
        nc = ni.get("count", ni.get("events", 0))
        print(f"  #{i}: legacy={le}({lc}) new={ne}({nc})")

print("\n=== DONE ===")
