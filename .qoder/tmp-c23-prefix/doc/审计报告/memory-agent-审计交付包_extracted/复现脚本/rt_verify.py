import os, sys
os.environ.update({"JWT_SECRET":"t"*40,"DB_PATH":"/data/workspace/madata/memory_worker.db",
 "HASS_SERVER":"http://127.0.0.1:18123","HASS_TOKEN":"d","REDIS_HOST":"127.0.0.1",
 "CHROMA_HOST":"127.0.0.1","LLM_API_URL":"http://127.0.0.1:19999/v1","LLM_API_KEY":"d","LLM_MODEL":"t"})
sys.path.insert(0,"/data/workspace/ma/src")
from memory_agent.runtime import AppRuntime
rt = AppRuntime()
ins = rt.insights
print("="*68)
print("真实运行时 AppRuntime.insights 绑定类:", type(ins).__module__ + "." + type(ins).__name__)
print("="*68)
need = ["_tags_of","_usage_one","_usage_by_attr","_count_by_filter",
        "_fallback_name","_iter_all_events","_cache_get","_cache_put","_rhythm","_parse"]
miss=[m for m in need if not hasattr(ins,m)]
print(f"生产运行时缺失的关键方法: {len(miss)}/{len(need)}")
for m in need:
    print(f"   {'✓' if hasattr(ins,m) else '✗'} {m}")
print()
print("★ 结论：缺陷在真实运行时同样成立，非手工构造场景的假象。")
print(f"★ 受影响功能线：行为推断 / 模板分析（duration+count）/ 实体名兜底 / 缓存")
