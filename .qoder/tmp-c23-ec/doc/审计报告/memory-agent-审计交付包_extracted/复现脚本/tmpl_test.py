import os, sys, traceback
os.environ.update({"JWT_SECRET":"t"*40,"DB_PATH":"/data/workspace/madata/memory_worker.db"})
sys.path.insert(0,"/data/workspace/ma/src")
from memory_agent.insights import InsightService
from memory_agent.config import get_config
from memory_agent.store import Store
cfg=get_config(); store=Store(cfg.db_path, cfg.tz_offset_hours)
ins=InsightService(store, cfg)

print("="*66)
print("实测：模板渲染核心算力方法在生产 InsightService 上是否存在")
print("="*66)
calls = [
    ("_usage_one",      "时长类指标（开灯多久）"),
    ("_usage_by_attr",  "属性类时长（亮度>50 多久）"),
    ("_count_by_filter","次数类指标（开门几次）"),
    ("_fallback_name",  "实体中文名兜底"),
    ("_parse",          "查询解析"),
]
for m, desc in calls:
    print(f"  {m:<18} {desc:<22} → {'存在' if hasattr(ins,m) else '✗ 缺失 → 调用即 AttributeError'}")

print()
print("="*66)
print("实测：直接调用 _compute_entity 走真实模板渲染路径")
print("="*66)
from memory_agent.templates import _compute_entity
class EQ:
    attribute=""; value="on"; entity_id="light.study_desk"; pattern=""; time_range=None
class RT: pass
try:
    r=_compute_entity(RT(), ins, EQ(), "duration", "light.study_desk", "2026-09-01T00:00:00","2026-09-08T00:00:00", False)
    print("  结果:", r)
except Exception as e:
    print(f"  ✗ 抛异常: {type(e).__name__}: {str(e)[:120]}")
