"""device_usage 新旧实现对账脚本：用真实库数据对比关键指标。"""
import sys
sys.path.insert(0, "/app/src")

from memory_agent.config import get_config
from memory_agent.store import Store
from memory_agent.insights.api import InsightService

cfg = get_config()
store = Store("/data/memory_agent.db")
svc = InsightService(store=store, config=cfg)

print("=== device_usage 新旧实现对账 ===")

# 先用 legacy 拿一个设备列表
legacy_result = svc.legacy.device_usage(days=7, room="", category="light", query="", include_timeline=False)
if not legacy_result.get("ok"):
    print(f"legacy 查询失败: {legacy_result.get('error')}")
    sys.exit(1)

devices = legacy_result.get("devices", [])[:5]
print(f"选取 {len(devices)} 个设备对比\n")

all_match = True
for d in devices:
    eid = d["entity_id"]
    # legacy
    leg = svc.legacy.device_usage(entity_id=eid, days=7, include_timeline=False)
    leg_dev = leg.get("devices", [{}])[0] if leg.get("devices") else {}
    # new (走门面 → core)
    new = svc.device_usage(entity_id=eid, days=7, include_timeline=False)
    new_dev = new.get("devices", [{}])[0] if new.get("devices") else {}

    # 对比关键指标
    keys = ["sessions", "switch_on_count", "switch_off_count",
            "total_on_seconds", "avg_session_seconds", "duty_cycle_percent"]
    diffs = []
    for k in keys:
        lv = leg_dev.get(k)
        nv = new_dev.get(k)
        if lv != nv:
            diffs.append(f"{k}: legacy={lv} new={nv}")

    status = "OK" if not diffs else "DIFF"
    if diffs:
        all_match = False
    print(f"[{status}] {eid}")
    print(f"   legacy: total={leg_dev.get('total_on_seconds')}s sessions={leg_dev.get('sessions')} on={leg_dev.get('switch_on_count')} off={leg_dev.get('switch_off_count')}")
    print(f"   new:    total={new_dev.get('total_on_seconds')}s sessions={new_dev.get('sessions')} on={new_dev.get('switch_on_count')} off={new_dev.get('switch_off_count')}")
    for diff in diffs:
        print(f"   DIFF: {diff}")
    print()

# 对比汇总
print("=== 汇总对比 ===")
print(f"legacy total_on_seconds: {legacy_result.get('total_on_seconds')}")
print(f"legacy device_count: {legacy_result.get('device_count')}")
new_all = svc.device_usage(days=7, category="light", include_timeline=False)
print(f"new total_on_seconds: {new_all.get('total_on_seconds')}")
print(f"new device_count: {new_all.get('device_count')}")
print(f"\n总体: {'ALL_MATCH' if all_match else 'HAS_DIFFS'}")
