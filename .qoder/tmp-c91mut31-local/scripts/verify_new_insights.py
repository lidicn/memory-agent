"""验证新 insights 框架 StoreRepository + BehaviorService 能查到生产数据。"""
import sys, os, json
sys.path.insert(0, "/app/src")

from memory_agent.store import Store
from memory_agent.insights.models import InsightConfig, TimeRange
from memory_agent.insights.repository import StoreRepository
from memory_agent.insights.service import BehaviorService

print("=== 1. 初始化 Store ===")
store = Store("/data/memory_agent.db")
print(f"Store connected: {store is not None}")

print("\n=== 2. StoreRepository.health() ===")
config = InsightConfig()
repo = StoreRepository(store, config)
try:
    healthy = repo.health()
    print(f"health: {healthy}")
except Exception as e:
    print(f"health FAILED: {type(e).__name__}: {e}")
    sys.exit(1)

print("\n=== 3. TimeRange (1 day) ===")
from datetime import datetime, timedelta
end = datetime.now()
start = end - timedelta(days=1)
tr = TimeRange(start=start, end=end, label="1d")
print(f"start_ts={tr.start_ts}, end_ts={tr.end_ts}, days={tr.days}")

print("\n=== 4. repo.count_events() ===")
try:
    cnt = repo.count_events(tr)
    print(f"count_events: {cnt}")
except Exception as e:
    print(f"count_events FAILED: {type(e).__name__}: {e}")

print("\n=== 5. repo.day_counts() ===")
try:
    dc = repo.day_counts(tr)
    print(f"day_counts: {json.dumps(dc, ensure_ascii=False)[:200]}")
except Exception as e:
    print(f"day_counts FAILED: {type(e).__name__}: {e}")

print("\n=== 6. repo.load_events(limit=3) ===")
try:
    evts = repo.load_events(tr, limit=3)
    print(f"load_events: {len(evts)} rows")
    if evts:
        e = evts[0]
        print(f"  first: entity_id={e.entity_id}, state={e.state}, room={e.room}, ts={e.ts}")
except Exception as e:
    print(f"load_events FAILED: {type(e).__name__}: {e}")

print("\n=== 7. BehaviorService.coverage() ===")
svc = BehaviorService(repo, None, config)
try:
    cov = svc.coverage(tr)
    print(f"coverage ok={cov.get('ok')}")
    print(f"  total_events={cov.get('total_events')}")
    print(f"  days_with_data={cov.get('days_with_data')}")
    print(f"  day_coverage={cov.get('day_coverage')}")
except Exception as e:
    print(f"coverage FAILED: {type(e).__name__}: {e}")

print("\n=== 8. BehaviorService.usage(room='客厅') ===")
try:
    usage = svc.usage(tr, room="客厅")
    print(f"usage ok={usage.get('ok')}")
    if usage.get("items"):
        print(f"  top item: {json.dumps(usage['items'][0], ensure_ascii=False)[:200]}")
except Exception as e:
    print(f"usage FAILED: {type(e).__name__}: {e}")

print("\n=== DONE ===")
