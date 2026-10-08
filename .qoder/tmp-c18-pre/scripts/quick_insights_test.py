"""Quick test: legacy vs new insights framework comparison."""
import sys
sys.path.insert(0, "/app/src")

# IMPORTANT: import insights package first to avoid circular import
print("Step 1: Import insights package (triggers correct load order)...")
import memory_agent.insights  # noqa: F401
print("  Package import OK")

from memory_agent.store import Store
from memory_agent.config import Config
from memory_agent.insights_legacy import InsightService as LegacyService
print("  Legacy service import OK")

from memory_agent.insights.service import InsightService as NewService
from memory_agent.insights.repository import SqliteRepository
from memory_agent.insights.parser.entity import EntityResolver
from memory_agent.insights.models import TimeRange
print("  New framework import OK")

print("\nStep 2: Setup services...")
store = Store("/data/memory_agent.db")
config = Config()
legacy = LegacyService(config, store)
print("  Legacy service OK")

repo = SqliteRepository(store)
resolver = EntityResolver(store)
new_svc = NewService(repo, resolver, config)
print("  New service OK")

print("\nStep 3: Run legacy data_coverage (7 days)...")
leg = legacy.data_coverage(days=7)
print(f"  ok={leg.get('ok')}, total={leg.get('total')}, days={len(leg.get('days', []))}")
print(f"  first_data={leg.get('first_data')}, last_data={leg.get('last_data')}")

print("\nStep 4: Run new coverage (7 days)...")
from datetime import datetime, timedelta
end = datetime.now()
start = end - timedelta(days=7)
tr = TimeRange.from_iso(start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S"))
new = new_svc.coverage(tr)
print(f"  total_events={new.get('total_events')}, days={len(new.get('days', []))}")
print(f"  day_coverage={new.get('day_coverage')}, entities_seen={new.get('entities_seen')}")

print("\n=== Core metric comparison ===")
print(f"  Legacy total events: {leg.get('total')}")
print(f"  New total events:    {new.get('total_events')}")
match = "MATCH" if leg.get('total') == new.get('total_events') else "MISMATCH"
print(f"  Result: {match}")

# Compare per-day events
print("\n=== Per-day comparison (first 5 days) ===")
leg_days = {d['day']: d['events'] for d in leg.get('days', [])}
new_days = {d['day']: d['events'] for d in new.get('days', [])}
all_days = sorted(set(list(leg_days.keys()) + list(new_days.keys())))[:5]
for day in all_days:
    lc = leg_days.get(day, 0)
    nc = new_days.get(day, 0)
    m = "OK" if lc == nc else "DIFF"
    print(f"  {day}: legacy={lc}, new={nc} [{m}]")

print("\nDone!")
