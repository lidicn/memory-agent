"""临时冒烟测试：验证 ACP 路由可加载且核心接口逻辑正确。"""
import sys
from types import SimpleNamespace

# 1) 路由聚合是否包含 ACP
from memory_worker.api import get_routes
paths = sorted({r.path for r in get_routes()})
acp_paths = [p for p in paths if p.startswith("/api/acp")]
assert acp_paths, "ACP 路由未注册!"
print("ACP routes:", acp_paths)

# 2) 用 TestClient 打接口（stub auth + runtime）
from starlette.testclient import TestClient
import memory_worker.api.acp_routes as A

# fake token store / config
_store = {}

class FakeTokens:
    def list_tokens(self):
        return list(_store.values())
    def generate(self, name, prefix="acp_", kind="acp"):
        tok = prefix + "x" * 12
        _store[name] = {"name": name, "prefix": prefix, "kind": kind,
                        "token": tok, "created_at": "2026-08-13 10:00:00",
                        "use_count": 0, "last_used_at": None}
        return {"ok": True, "name": name, "token": tok, "prefix": prefix,
                "created_at": "2026-08-13 10:00:00"}
    def revoke(self, name):
        return _store.pop(name, None) is not None

class FakeConfig:
    autoflow_acp_url = "http://autoflow:8080"
    autoflow_acp_token = "secret"

fake_rt = SimpleNamespace(config=FakeConfig(), tokens=FakeTokens())
A.runtime = lambda req: fake_rt
A.require_user = lambda req: (None, None)

# 重新构造 app 仅含 acp 路由
from starlette.routing import Mount, Route
from starlette.applications import Starlette
app = Starlette(routes=A.ROUTES)
client = TestClient(app)

r = client.get("/api/acp/info")
assert r.status_code == 200, r.text
info = r.json()
print("info.ok=", info["ok"], "tools=", [t["name"] for t in info["tools"]])
assert info["endpoint"] == "/acp"
assert any(t["name"] == "delegate_to_autoflow" for t in info["tools"])

r = client.post("/api/acp/selftest")
assert r.status_code == 200, r.text
st = r.json()
print("selftest.ok=", st["ok"], "summary=", st["summary"])
assert st["ok"] is True

r = client.post("/api/acp/tokens", json={"name": "peer1"})
assert r.status_code == 200, r.text
tok = r.json()
print("create token prefix=", tok["prefix"], "kind=", tok["kind"])
assert tok["kind"] == "acp"

r = client.get("/api/acp/tokens")
assert r.status_code == 200
print("token list count=", len(r.json()["tokens"]))
assert len(r.json()["tokens"]) == 1

r = client.request("DELETE", "/api/acp/tokens", json={"name": "peer1"})
assert r.status_code == 200, r.text
print("revoke ok=", r.json()["ok"])

# 出站配置读取（config 优先）
A.runtime = lambda req: fake_rt
r = client.get("/api/acp/info")
assert r.json()["outbound"]["configured"] is True
print("outbound configured=", r.json()["outbound"]["configured"])

print("\nALL ACP SMOKE CHECKS PASSED")
