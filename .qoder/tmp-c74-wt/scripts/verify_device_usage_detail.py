"""device_usage 详细对账：逐个设备对比，找出差异来源。"""
import sys
sys.path.insert(0, "/app/src")

from memory_agent.config import get_config
from memory_agent.store import Store
from memory_agent.insights.api import InsightService

cfg = get_config()
store = Store("/data/memory_agent.db")
svc = InsightService(store=store, config=cfg)

print("=== device_usage 详细对账（7天，light类）===")

legacy_result = svc.legacy.device_usage(days=7, room="", category="light", query="", include_timeline=False)
new_result = svc.device_usage(days=7, category="light", include_timeline=False)

leg_devs = {d["entity_id"]: d for d in legacy_result.get("devices", [])}
new_devs = {d["entity_id"]: d for d in new_result.get("devices", [])}

print(f"legacy devices: {len(leg_devs)}, new devices: {len(new_devs)}")
print(f"legacy total: {legacy_result.get('total_on_seconds')}, new total: {new_result.get('total_on_seconds')}")
print()

# 逐个对比
diffs = []
for eid in sorted(set(list(leg_devs.keys()) + list(new_devs.keys()))):
    ld = leg_devs.get(eid, {})
    nd = new_devs.get(eid, {})
    lt = float(ld.get("total_on_seconds") or 0)
    nt = float(nd.get("total_on_seconds") or 0)
    if abs(lt - nt) > 1.0:
        diffs.append((eid, lt, nt, nt - lt, ld.get("raw_event_count"), nd.get("raw_event_count")))

print(f"有差异的设备数: {len(diffs)}")
print(f"{'entity_id':<60} {'legacy':>12} {'new':>12} {'diff':>12} {'leg_evts':>8} {'new_evts':>8}")
print("-" * 120)
for eid, lt, nt, diff, le, ne in sorted(diffs, key=lambda x: -abs(x[3]))[:20]:
    print(f"{eid:<60} {lt:>12.0f} {nt:>12.0f} {diff:>+12.0f} {str(le):>8} {str(ne):>8}")

print()
print("=== 差异最大的前5个设备详细对比 ===")
for eid, lt, nt, diff, le, ne in sorted(diffs, key=lambda x: -abs(x[3]))[:5]:
    ld = leg_devs.get(eid, {})
    nd = new_devs.get(eid, {})
    print(f"\n{eid}:")
    print(f"  legacy: total={ld.get('total_on_seconds')}s sessions={ld.get('sessions')} on={ld.get('switch_on_count')} off={ld.get('switch_off_count')} evts={ld.get('raw_event_count')}")
    print(f"  new:    total={nd.get('total_on_seconds')}s sessions={nd.get('sessions')} on={nd.get('switch_on_count')} off={nd.get('switch_off_count')} evts={nd.get('raw_event_count')}")
    print(f"  legacy by_day: {dict(list(ld.get('by_day_seconds', {}).items())[:3])}...")
    print(f"  new by_day:    {dict(list(nd.get('by_day_seconds', {}).items())[:3])}...")
