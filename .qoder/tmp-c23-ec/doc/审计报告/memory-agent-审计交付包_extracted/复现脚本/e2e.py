import os, sys, json, traceback
os.environ.update({
    "JWT_SECRET": "test_secret_for_audit_0123456789abcdefghijklmnop",
    "DB_PATH": "/data/workspace/madata/memory_worker.db",
    "HASS_SERVER": "http://127.0.0.1:18123", "HASS_TOKEN": "dummy",
    "REDIS_HOST": "127.0.0.1", "CHROMA_HOST": "127.0.0.1",
    "LLM_API_URL": "http://127.0.0.1:19999/v1", "LLM_API_KEY": "dummy",
    "LLM_MODEL": "test", "TZ_OFFSET_HOURS": "8",
})
sys.path.insert(0, "/data/workspace/ma/src")
from starlette.testclient import TestClient
from memory_agent.app import combined_app

c = TestClient(combined_app)
paths = [
    "/", "/api/system/status", "/api/health", "/api/config", "/api/members",
    "/api/events", "/api/behaviors", "/api/insights/query", "/api/tv/analyze",
    "/api/vision/latest", "/api/arena/analytics", "/api/skills", "/api/mcp/tokens",
    "/v1/models", "/api/agent/memories", "/api/collect/status", "/api/templates",
]
print("="*70)
print("端到端实测：真实 HTTP 请求穿过完整 app 栈")
print("="*70)
for p in paths:
    try:
        r = c.get(p, timeout=15)
        body = r.text[:110].replace("\n"," ")
        print(f"{r.status_code:>4}  {p:<28} {body}")
    except Exception as e:
        print(f"EXCP  {p:<28} {type(e).__name__}: {str(e)[:90]}")
