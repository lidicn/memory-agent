import os, sys, json, tempfile
sys.path.insert(0,"/data/workspace/ma/src")
d=tempfile.mkdtemp()
os.environ.update({"JWT_SECRET":"t"*40,"DB_PATH":os.path.join(d,"t.db"),
 "HASS_SERVER":"http://127.0.0.1:18123","HASS_TOKEN":"old_token",
 "REDIS_HOST":"127.0.0.1","CHROMA_HOST":"127.0.0.1",
 "LLM_API_URL":"http://127.0.0.1:19999/v1","LLM_API_KEY":"old_key","LLM_MODEL":"old-model",
 "USERS_FILE":os.path.join(d,"users.json"),"TZ_OFFSET_HOURS":"8",
 "DATA_RETENTION_DAYS":"30"})
import memory_agent.config as C
from memory_agent.runtime import AppRuntime

rt=AppRuntime()
print("="*78)
print("实测：配置热更新后，各组件读到的是新值还是旧值")
print("="*78)
before = {
 "runtime.config": rt.config.hass_token,
 "auth.config":    rt.auth.config.hass_token,
 "templates":      getattr(rt.templates,'config',None) and rt.templates.config.hass_token,
 "ha_assist":      rt.ha_assist.config.hass_token,
 "backup":         rt.backup.config.hass_token,
 "activity":       rt.activity.config.hass_token,
 "researcher":     rt.researcher.config.hass_token,
}
print(f"\n初始 hass_token = {rt.config.hass_token!r}\n")

# 改配置并热更新
C._CONFIG = None
os.environ["HASS_TOKEN"]="NEW_TOKEN_9999"
importlib_cfg = None
import importlib
importlib.reload(C)
rt.reload_config()
print("热更新后（HASS_TOKEN 改为 NEW_TOKEN_9999）：\n")
print(f"{'组件':<22}{'读到的 hass_token':<22}{'结论'}")
print("-"*78)
after = {
 "runtime.config": rt.config.hass_token,
 "auth.config":    rt.auth.config.hass_token,
 "templates":      getattr(rt.templates,'config',None) and rt.templates.config.hass_token,
 "ha_assist":      rt.ha_assist.config.hass_token,
 "backup":         rt.backup.config.hass_token,
 "activity":       rt.activity.config.hass_token,
 "researcher":     rt.researcher.config.hass_token,
}
for k in after:
    stale = after[k] != "NEW_TOKEN_9999"
    print(f"{k:<22}{str(after[k]):<22}{'🔴 仍是旧值' if stale else '✓ 已更新'}")
n_stale=sum(1 for k in after if after[k]!="NEW_TOKEN_9999")
print(f"\n★ {n_stale}/{len(after)} 个组件在热更新后仍持有旧配置")
print("★ 影响：用户在设置页改了 HA 地址/令牌，部分功能仍打向旧地址")
