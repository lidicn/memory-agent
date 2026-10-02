import os, sys, importlib
from datetime import datetime, timedelta
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.update({"JWT_SECRET":"t"*40,"DB_PATH":"/tmp/crx3.db","HASS_SERVER":"http://127.0.0.1:18123",
 "HASS_TOKEN":"d","REDIS_HOST":"127.0.0.1","CHROMA_HOST":"127.0.0.1",
 "LLM_API_URL":"http://127.0.0.1:19999/v1","LLM_API_KEY":"d","LLM_MODEL":"t","TZ_OFFSET_HOURS":"8"})
from memory_agent.runtime import AppRuntime
rt=AppRuntime()
print("="*82)
print("时区 × 热更新 交叉验证（初始 tz=+8，用户改为 0）")
print("="*82)
print(f"\nreload 前: config={rt.config.tz_offset_hours}  activity={rt.activity.config.tz_offset_hours}"
      f"  researcher={rt.researcher.config.tz_offset_hours}")
import memory_agent.config as C
os.environ["TZ_OFFSET_HOURS"]="0"; C._CONFIG=None; importlib.reload(C)
rt.reload_config()
print(f"reload 后: config={rt.config.tz_offset_hours}  activity={rt.activity.config.tz_offset_hours}"
      f"  researcher={rt.researcher.config.tz_offset_hours}")

def day(utc, tz): return (utc+timedelta(hours=tz)).strftime("%Y-%m-%d")
print("\n" + "="*82)
print("此刻若容器时钟处于 UTC 16:00–24:00（跨天窗口），各组件归入的 day：")
print("="*82)
for hh in (16, 20, 23):
    utc=datetime(2026,10,2,hh,30)
    d_new=day(utc, rt.config.tz_offset_hours)
    d_old=day(utc, rt.activity.config.tz_offset_hours)
    flag = "🔴 错位" if d_new!=d_old else "  一致"
    print(f"   UTC {hh:02d}:30  → store/identity: {d_new}   activity/researcher: {d_old}   {flag}")
print()
print("="*82)
print("★ reload_config() 显式同步了 store / identity / identity_reconciler 的 tz，")
print("  但 activity 与 researcher 通过 `self.config = runtime.config` 持有旧对象引用，")
print("  未被更新 → 改时区后，行为记录与事件记录会归到不同的『天』")
print("="*82)
