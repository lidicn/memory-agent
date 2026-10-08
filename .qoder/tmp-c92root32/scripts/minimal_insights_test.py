"""Minimal test: just verify both services work with 1-day window."""
import sys
import traceback
sys.path.insert(0, "/app/src")

log = []
def log(msg):
    print(msg, flush=True)

try:
    log("Import insights package...")
    import memory_agent.insights
    log("OK")

    from memory_agent.store import Store
    from memory_agent.config import Config
    from memory_agent.insights_legacy import InsightService as LegacyService
    log("Legacy import OK")

    store = Store("/data/memory_agent.db")
    config = Config()
    legacy = LegacyService(config, store)
    log("Legacy service created")

    log("Running legacy data_coverage(days=1)...")
    leg = legacy.data_coverage(days=1)
    log(f"Legacy result: total={leg.get('total')}, days={len(leg.get('days', []))}")

    log("Now testing new framework...")
    from memory_agent.insights.service import InsightService as NewService
    from memory_agent.insights.repository import SqliteRepository
    from memory_agent.insights.parser.entity import EntityResolver
    from memory_agent.insights.models import TimeRange
    log("New imports OK")

    repo = SqliteRepository(store)
    resolver = EntityResolver(store)
    new_svc = NewService(repo, resolver, config)
    log("New service created")

    from datetime import datetime, timedelta
    end = datetime.now()
    start = end - timedelta(days=1)
    tr = TimeRange.from_iso(start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S"))
    log(f"Running new coverage (1 day: {start.date()} ~ {end.date()})...")
    new = new_svc.coverage(tr)
    log(f"New result: total_events={new.get('total_events')}, days={len(new.get('days', []))}")

    log(f"\n=== COMPARISON ===")
    log(f"Legacy total: {leg.get('total')}")
    log(f"New total:    {new.get('total_events')}")
    log(f"Match: {leg.get('total') == new.get('total_events')}")

except Exception as e:
    log(f"\nERROR: {e}")
    traceback.print_exc()
