"""新框架 InsightService 全方法验证（生产库）。"""
import sys, json, traceback
sys.path.insert(0, "/app/src")
from datetime import datetime, timedelta
from memory_agent.store import Store
from memory_agent.insights.api import InsightService

store = Store("/data/memory_agent.db")
svc = InsightService(store)

results = []

def test(name, fn):
    try:
        result = fn()
        ok = True
        detail = json.dumps(result, ensure_ascii=False, default=str)[:200] if result else "empty"
    except Exception as e:
        ok = False
        detail = f"{type(e).__name__}: {e}"
    results.append((name, ok, detail))
    status = "✅" if ok else "❌"
    print(f"{status} {name}: {detail}")

# 基础查询
test("data_coverage(1d)", lambda: svc.data_coverage(days=1))
test("data_coverage(7d)", lambda: svc.data_coverage(days=7))
test("search_events(客厅,1d)", lambda: svc.search_events(room="客厅", days=1, limit=5))
test("get_events(1d)", lambda: svc.get_events(days=1, limit=5))

# 设备使用
test("device_usage(客厅,1d)", lambda: svc.device_usage(room="客厅", days=1))
test("device_health(1d)", lambda: svc.device_health(days=1))

# 行为洞察
test("behavior_insights(1d)", lambda: svc.behavior_insights(days=1))
test("behavior_insights(7d)", lambda: svc.behavior_insights(days=7))
test("compare_insights(7d)", lambda: svc.compare_insights(compare_days=7))

# 活动推断
test("infer_activities(1d)", lambda: svc.infer_activities(days=1))

# 异常报告
test("anomaly_report(7d)", lambda: svc.anomaly_report(days=7))

# 数据质量
test("get_data_quality(7d)", lambda: svc.get_data_quality(days=7))

# 用户画像
test("get_user_persona(14d)", lambda: svc.get_user_persona(days=14))

# 实体目录
test("entity_catalog(客厅)", lambda: svc.entity_catalog(room="客厅", limit=5))

# 报告
test("insights_report(7d,json)", lambda: svc.insights_report(days=7, fmt="json"))

print(f"\n=== 汇总: {sum(1 for _,ok,_ in results if ok)}/{len(results)} 通过 ===")
for name, ok, detail in results:
    if not ok:
        print(f"  ❌ {name}: {detail}")
