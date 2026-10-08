"""Compare legacy vs new insights: data_coverage (1 day)."""
import sys
import traceback
sys.path.insert(0, "/app/src")

def log(msg):
    print(msg, flush=True)

try:
    import memory_agent.insights
    from memory_agent.store import Store
    from memory_agent.config import Config
    from memory_agent.insights_legacy import InsightService as LegacyService
    from memory_agent.insights.api import InsightService as NewService
    log("All imports OK")

    store = Store("/data/memory_agent.db")
    config = Config()
    legacy = LegacyService(config, store)
    new_svc = NewService(store, config)
    log("Both services created")

    # Legacy: 1 day
    log("\n=== Legacy data_coverage(days=1) ===")
    leg = legacy.data_coverage(days=1)
    log(f"  total_events={leg.get('total_events')}")
    log(f"  days_with_data={leg.get('days_with_data')}/{leg.get('days_total')}")
    for d in leg.get('days', []):
        log(f"    {d['day']}: {d['events']} events")

    # New: 1 day
    log("\n=== New InsightService.data_coverage(days=1) ===")
    new = new_svc.data_coverage(days=1)
    log(f"  keys: {list(new.keys())[:10]}")
    log(f"  total_events={new.get('total_events')}")
    if 'days' in new:
        for d in new['days']:
            log(f"    {d.get('day')}: {d.get('events')} events")
    elif 'items' in new:
        for d in new['items']:
            log(f"    {d.get('day')}: {d.get('events')} events")

    # Compare
    log("\n=== COMPARISON ===")
    leg_total = leg.get('total_events')
    new_total = new.get('total_events')
    log(f"  Legacy total_events: {leg_total}")
    log(f"  New total_events:    {new_total}")
    log(f"  Match: {leg_total == new_total}")

except Exception as e:
    log(f"\nERROR: {e}")
    traceback.print_exc()
