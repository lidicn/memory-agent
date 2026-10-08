"""Compare legacy vs new insights framework outputs.

Runs both implementations on the same data and compares core metrics.
This validates the new framework before full migration.
"""
import sys
import os
import json
from datetime import datetime, timedelta

sys.path.insert(0, "/app/src")

from memory_agent.store import Store
from memory_agent.config import Config
from memory_agent.insights_legacy import InsightService as LegacyService
from memory_agent.insights.service import InsightService as NewService
from memory_agent.insights.repository import SqliteRepository
from memory_agent.insights.parser.entity import EntityResolver
from memory_agent.insights.models import TimeRange

# Setup
store = Store("/data/memory_agent.db")
config = Config()

# Legacy service
legacy = LegacyService(config, store)

# New service
repo = SqliteRepository(store)
resolver = EntityResolver(store)
new_svc = NewService(repo, resolver, config)

# Test window: last 7 days
end = datetime.now()
start = end - timedelta(days=7)
start_iso = start.strftime("%Y-%m-%dT%H:%M:%S")
end_iso = end.strftime("%Y-%m-%dT%H:%M:%S")

print(f"=== Test Window: {start_iso} ~ {end_iso} ===\n")

# Test 1: data_coverage vs coverage
print("--- Test 1: data_coverage ---")
try:
    leg_result = legacy.data_coverage(days=7)
    print(f"Legacy: ok={leg_result.get('ok')}, total={leg_result.get('total')}, days={len(leg_result.get('days', []))}")
    print(f"  first_data={leg_result.get('first_data')}, last_data={leg_result.get('last_data')}")
    print(f"  missing_days={leg_result.get('missing_days')}")
except Exception as e:
    print(f"Legacy ERROR: {e}")

try:
    tr = TimeRange.from_iso(start_iso, end_iso)
    new_result = new_svc.coverage(tr)
    print(f"New: total_events={new_result.get('total_events')}, days={len(new_result.get('days', []))}")
    print(f"  day_coverage={new_result.get('day_coverage')}, hour_coverage={new_result.get('hour_coverage')}")
    print(f"  entities_seen={new_result.get('entities_seen')}")
except Exception as e:
    print(f"New ERROR: {e}")

# Test 2: device_health
print("\n--- Test 2: device_health ---")
try:
    leg_result = legacy.device_health(days=7)
    print(f"Legacy: total={len(leg_result.get('devices', [])) if isinstance(leg_result, dict) else 'N/A'}")
    if isinstance(leg_result, dict):
        for k in ['ok', 'summary', 'total']:
            if k in leg_result:
                print(f"  {k}={leg_result[k]}")
except Exception as e:
    print(f"Legacy ERROR: {e}")

try:
    tr = TimeRange.from_iso(start_iso, end_iso)
    new_result = new_svc.device_health(tr)
    print(f"New: keys={list(new_result.keys())[:5]}")
    if 'items' in new_result:
        print(f"  items={len(new_result['items'])}")
except Exception as e:
    print(f"New ERROR: {e}")

# Test 3: search_events (simple)
print("\n--- Test 3: search_events (客厅, 1 day) ---")
try:
    leg_result = legacy.search_events(room="客厅", days=1, limit=10)
    print(f"Legacy: ok={leg_result.get('ok')}, total={leg_result.get('total')}, returned={len(leg_result.get('events', []))}")
except Exception as e:
    print(f"Legacy ERROR: {e}")

print("\n=== Comparison Complete ===")
print("Note: New framework uses different output formats (Page wrapper, richer fields).")
print("Core metrics (total events, days with data) should match for validation.")
