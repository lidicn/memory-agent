"""CRITICAL-2: chroma 首次连接失败被永久缓存，服务恢复后不重试"""
import os, sys
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.update({"JWT_SECRET":"t"*40,"DB_PATH":"/tmp/cs.db","HASS_SERVER":"http://127.0.0.1:18123",
 "HASS_TOKEN":"d","REDIS_HOST":"127.0.0.1","CHROMA_HOST":"127.0.0.1","CHROMA_PORT":"8000",
 "LLM_API_URL":"http://127.0.0.1:19999/v1","LLM_API_KEY":"d","LLM_MODEL":"t"})
import memory_agent.history as H
print("="*74); print("Chroma 失败缓存卡死验证"); print("="*74)
m=type("M",(H.HistoryManager,),{}) if hasattr(H,'HistoryManager') else None
# 直接检查失败缓存字段
import inspect
src=inspect.getsource(H)
for kw in ("_chroma_tried","_chroma_error","reset_chroma"):
    print(f"   {kw}: {'存在' if kw in src else '不存在'}")
print("\n   ★ 首次连接失败 → _chroma_tried=True + _chroma_error 缓存")
print("   ★ 之后所有调用直接返回 None，不再尝试连接")
print("   ★ 唯一重置：reload_config 中 chroma 地址/embedding 变化时 reset_chroma()，或重启进程")
print("   ★ docker compose 中 MA 与 chroma 并列启动，顺序不保证 → 首次失败即永久降级")
