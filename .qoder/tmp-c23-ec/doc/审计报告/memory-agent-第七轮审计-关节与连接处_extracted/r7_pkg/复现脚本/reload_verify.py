"""CRITICAL-1: 配置热更新覆盖缺口 —— 7 组件热更新前后 config 对比"""
import os, sys, importlib
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.update({"JWT_SECRET":"t"*40,"DB_PATH":"/tmp/rv.db","HASS_SERVER":"http://127.0.0.1:18123",
 "HASS_TOKEN":"d","REDIS_HOST":"127.0.0.1","CHROMA_HOST":"127.0.0.1",
 "LLM_API_URL":"http://127.0.0.1:19999/v1","LLM_API_KEY":"d","LLM_MODEL":"t","TZ_OFFSET_HOURS":"8"})
from memory_agent.runtime import AppRuntime
rt=AppRuntime()
comps=[("insights",lambda r:r.insights),("ha_assist",lambda r:r.ha_assist),
       ("researcher",lambda r:r.researcher),("activity",lambda r:r.activity),
       ("collector",lambda r:r.collector),("vision",lambda r:r.vision),("tv",lambda r:r.tv)]
print("="*74); print("配置热更新覆盖验证"); print("="*74)
before={n:(getattr(g(rt),'config',None)) for n,g in comps}
snap={n:id(c) for n,c in before.items()}
print(f"\nreload 前 runtime.config id = {id(rt.config)}")
import memory_agent.config as C
os.environ["TZ_OFFSET_HOURS"]="0"; C._CONFIG=None; importlib.reload(C)
rt.reload_config()
print(f"reload 后 runtime.config id = {id(rt.config)}   tz={rt.config.tz_offset_hours}")
print(f"\n{'组件':<14}{'是否跟随新 config':<20}{'tz_offset'}")
print("-"*74)
stale=0
for n,g in comps:
    c=getattr(g(rt),'config',None)
    same = id(c)==id(rt.config)
    if not same: stale+=1
    tz=getattr(c,'tz_offset_hours',None)
    print(f"{n:<14}{'✓ 已更新' if same else '🔴 仍持旧对象':<20}{tz}")
print("-"*74)
print(f"🔴 {stale}/{len(comps)} 个组件热更新后仍持有旧配置对象")
