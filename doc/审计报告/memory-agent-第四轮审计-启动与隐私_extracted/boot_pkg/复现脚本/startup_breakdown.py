"""真实 AppRuntime 启动逐阶段计时（第四轮审计核心证据）"""
import os, sys, time
sys.path.insert(0, "/data/workspace/ma/src")
os.environ.update({"JWT_SECRET":"t"*40, "DB_PATH":"/tmp/ma30.db",
 "HASS_SERVER":"http://127.0.0.1:18123","HASS_TOKEN":"d","REDIS_HOST":"127.0.0.1",
 "CHROMA_HOST":"127.0.0.1","LLM_API_URL":"http://127.0.0.1:19999/v1",
 "LLM_API_KEY":"d","LLM_MODEL":"t","TZ_OFFSET_HOURS":"8"})

def t(label, fn):
    t0=time.time(); r=fn(); dt=(time.time()-t0)*1000
    flag="🔴" if dt>3000 else ("🟠" if dt>500 else "  ")
    print(f"{flag} {label:<38} {dt:>9.0f} ms")
    return r

print("="*72); print("启动阶段耗时分解（30天库 /tmp/ma30.db 104MB）"); print("="*72)
t("import memory_agent.runtime", lambda: __import__("memory_agent.runtime"))
from memory_agent.store import Store
from memory_agent.config import get_config
cfg=get_config(); print(f"   db_path = {cfg.db_path}")
s=Store(cfg.db_path, cfg.tz_offset_hours)
t("check_and_recover (integrity_check)", s.check_and_recover)
t("init_schema", s.init_schema)
t("mark_stale_jobs", s.mark_stale_jobs)
t("ensure_default_insight_jobs", s.ensure_default_insight_jobs)
from memory_agent.runtime import AppRuntime
t("AppRuntime.__init__", AppRuntime)
print("   （seed_builtin_skills 触发 import mcp_server，约 41s，含 import mcp ≈10.7s）")
print("   （identity_reconciler.reconcile 约 5.1s，取决于 HA 可达性）")
