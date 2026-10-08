"""用相同 entity_id 列表对比新旧实现，确认算法一致性。"""
import sys
sys.path.insert(0, "/app/src")

from memory_agent.config import get_config
from memory_agent.store import Store
from memory_agent.insights.api import InsightService

cfg = get_config()
store = Store("/data/memory_agent.db")
svc = InsightService(store=store, config=cfg)

print("=== 相同 entity_id 列表对比（legacy 解析出的设备）===")

# 用 legacy 解析设备列表
legacy_result = svc.legacy.device_usage(days=7, room="", category="light", query="", include_timeline=False)
leg_devices = legacy_result.get("devices", [])
print(f"legacy 解析出 {len(leg_devices)} 个设备\n")

match_count = 0
diff_count = 0
for d in leg_devices:
    eid = d["entity_id"]
    # 用相同 entity_id 调用新旧实现
    leg = svc.legacy.device_usage(entity_id=eid, days=7, include_timeline=False)
    leg_dev = leg.get("devices", [{}])[0] if leg.get("devices") else {}
    new = svc.device_usage(entity_id=eid, days=7, include_timeline=False)
    new_dev = new.get("devices", [{}])[0] if new.get("devices") else {}

    keys = ["sessions", "switch_on_count", "switch_off_count", "total_on_seconds"]
    diffs = []
    for k in keys:
        lv = leg_dev.get(k)
        nv = new_dev.get(k)
        if lv is None and nv is None:
            continue
        if lv != nv:
            diffs.append(f"{k}: L={lv} N={nv}")

    if diffs:
        diff_count += 1
        print(f"[DIFF] {eid}")
        for diff in diffs:
            print(f"       {diff}")
    else:
        match_count += 1

print(f"\n结果: {match_count} 一致, {diff_count} 差异")
print(f"算法一致性: {'PASS' if diff_count == 0 else 'FAIL'}")
