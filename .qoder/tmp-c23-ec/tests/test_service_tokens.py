"""统一服务令牌（vMA-1.2.3 3.3.3）的鉴权与回归锁。

按 DCD 20261001 裁定 Q1-Q5 逐条锁死：
* Q1 additive —— 旧 env 凭据并行接受，新令牌不要求旧凭据下线；
* Q2 作用域自带 —— 「方法:路径」清单，方法不匹配一律 403（F-2 的根治）；
* Q3 一实例一令牌 —— env 密钥只读导入成可审计记录，不写盘；
* Q4 不做 TTL —— 记录里不得出现过期字段，只做吊销 + 使用计数；
* Q5 source 派生保留 —— 写记忆的来源按令牌派生，调用方自报无效。
"""

import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import app as app_module  # noqa: E402
from memory_agent import service_tokens as st  # noqa: E402
from memory_agent.app import AuthMiddleware  # noqa: E402
from memory_agent.config import Config  # noqa: E402

SCOPE_OK = ["GET:/api/members", "POST:/api/events"]


@pytest.fixture
def cfg():
    c = Config()
    c.butler_token = ""
    c.app_token = ""
    c.app_tokens = {}
    c.service_tokens = {}
    c.save = lambda: None
    st.reset_legacy_stats()
    return c


@pytest.fixture
def store(cfg):
    return st.ServiceTokenStore(cfg)


# ── Q2：作用域语法与匹配 ─────────────────────────────────────────────────────

def test_parse_scope_accepts_method_and_path():
    assert st.parse_scope("GET:/api/members") == ("GET", "/api/members")
    assert st.parse_scope(" post:/api/events ") == ("POST", "/api/events")
    assert st.parse_scope("GET:/api/members/*") == ("GET", "/api/members/*")


def test_parse_scope_rejects_junk():
    for raw in ("", "/api/members", "GET", "GET:no_slash", "HACK:/api/members",
                "GET:/api/../etc", None, 7):
        assert st.parse_scope(raw) is None, raw


def test_scope_matches_exact_and_subtree():
    scopes = ["GET:/api/members", "GET:/api/members/*", "POST:/api/events"]
    assert st.scope_matches("GET", "/api/members", scopes)
    assert st.scope_matches("GET", "/api/members/abc123", scopes)
    assert st.scope_matches("post", "/api/events", scopes)  # 方法大小写不敏感


def test_scope_matches_is_method_specific():
    """F-2 的判据：路径在白名单里、方法不在，也必须拒。"""
    scopes = ["POST:/api/agent/memories"]
    assert st.scope_matches("POST", "/api/agent/memories", scopes)
    for m in ("GET", "PUT", "PATCH", "DELETE"):
        assert not st.scope_matches(m, "/api/agent/memories", scopes), m


def test_scope_matches_fails_closed_on_empty():
    assert st.scope_matches("GET", "/api/members", []) is False
    assert st.scope_matches("GET", "/api/members", None) is False


def test_validate_scopes_blocks_privileged_prefixes():
    assert st.validate_scopes([])[0] is False
    assert st.validate_scopes(["GET:/api/config"])[0] is False
    assert st.validate_scopes(["GET:/api/auth/login"])[0] is False
    assert st.validate_scopes(["GET:/api/members"])[0] is True


# ── Q3/Q4：签发、计数、吊销、无 TTL ───────────────────────────────────────────

def test_generate_requires_scopes(store):
    assert store.generate("tv", [])["ok"] is False
    assert store.generate("tv", ["GET:/api/config"])["ok"] is False


def test_generate_source_must_be_in_whitelist(store):
    res = store.generate("tv", SCOPE_OK, source="attacker")
    assert res["ok"] is False
    assert "白名单" in res["error"]
    assert store.generate("tv", SCOPE_OK, source="vision")["ok"] is True


def test_generate_verify_revoke_roundtrip(store):
    res = store.generate("tvpilot", SCOPE_OK, source="vision")
    assert res["ok"] and res["token"].startswith(st.TOKEN_PREFIX)
    rec = store.verify(res["token"])
    assert rec["name"] == "tvpilot" and rec["source"] == "vision"
    assert rec["scopes"] == sorted(SCOPE_OK)
    assert store.verify("svc_nope") is None
    assert store.revoke("tvpilot")["ok"] is True
    assert store.verify(res["token"]) is None


def test_issued_record_has_expiry_field_nowhere(store, cfg):
    """Q4：本次只做可吊销 + 使用计数，不做 TTL。"""
    store.generate("tvpilot", SCOPE_OK)
    record = cfg.service_tokens["tvpilot"]
    assert "expires_at" not in record and "expiry" not in record
    assert record["use_count"] == 0


def test_reserved_names_cannot_be_issued(store):
    assert store.generate(st.BUTLER_ENV_NAME, SCOPE_OK)["ok"] is False


def test_records_carry_service_kind(store, cfg):
    """计划卡 3.3.3 ①：记录要带族别位 ``kind="service"``。

    消费方面前同时摆着 app_token / arena / debug 几套令牌族，没有族别就只能靠
    字段形状猜"这条是不是服务令牌"。env 导入的记录走同一个面，所以也带。
    """
    store.generate("tvpilot", SCOPE_OK)
    assert cfg.service_tokens["tvpilot"]["kind"] == st.KIND_SERVICE
    cfg.butler_token = "btl_env_secret"
    assert store.legacy_records()[st.BUTLER_ENV_NAME]["kind"] == st.KIND_SERVICE
    # 存量记录（加这个字段之前签发的）不带 kind，读出来按服务令牌报，不需要迁移配置
    cfg.service_tokens["old-row"] = {"prefix": "svc_old", "created_at": "", "scopes": []}
    rows = {r["name"]: r for r in store.list_tokens()}
    assert rows["tvpilot"]["kind"] == st.KIND_SERVICE
    assert rows["old-row"]["kind"] == st.KIND_SERVICE
    assert rows[st.BUTLER_ENV_NAME]["kind"] == st.KIND_SERVICE


# ── Q3：env 只读导入 ─────────────────────────────────────────────────────────

def test_env_butler_secret_is_imported_readonly(cfg, monkeypatch):
    calls = []
    cfg.butler_token = "btl_env_secret"
    cfg.save = lambda: calls.append(1)
    monkeypatch.setattr(st, "get_config", lambda: cfg)

    store = st.get_service_token_store()
    rec = store.verify("btl_env_secret")
    assert rec is not None and rec["name"] == st.BUTLER_ENV_NAME
    assert rec["channel"] == "butler"
    assert rec["scopes"] == st.BUTLER_SCOPES
    # 鉴权热路径不得改写 config.json（容器里那是生产配置文件）
    assert calls == []
    assert store.revoke(st.BUTLER_ENV_NAME)["ok"] is False
    rows = [t for t in store.list_tokens() if t["name"] == st.BUTLER_ENV_NAME]
    assert rows and rows[0]["persisted"] is False and rows[0]["use_count"] == 1


def test_env_import_is_idempotent_and_absent_when_unconfigured(cfg, monkeypatch):
    monkeypatch.setattr(st, "get_config", lambda: cfg)
    store = st.get_service_token_store()
    assert store.verify("anything") is None
    cfg.app_token = "app_env_secret"
    assert store.verify("app_env_secret")["name"] == st.APP_ENV_NAME
    assert store.verify("app_env_secret")["channel"] == "app"
    assert store.count() == 1


# ── Q2 回归锁：导入记录 = 旧通道现有授权面，不扩不缺 ──────────────────────────

def test_butler_scopes_are_faithful_to_existing_whitelist():
    get_paths = set(AuthMiddleware.BUTLER_GET_PATHS)
    post_paths = set(AuthMiddleware.BUTLER_POST_PATHS)
    parsed = [st.parse_scope(s) for s in st.BUTLER_SCOPES]
    assert all(parsed), st.BUTLER_SCOPES
    assert {p for m, p in parsed if m == "GET" and not p.endswith("/*")} == get_paths
    assert {p for m, p in parsed if m == "POST"} == post_paths
    # /api/members/{id} 的 GET 与 PATCH 子树（原实现在 _butler_allowed 里硬编码）
    assert ("GET", "/api/members/*") in parsed
    assert ("PATCH", "/api/members/*") in parsed
    # 导入记录不得比旧白名单多出任何方法
    assert {m for m, _ in parsed} <= {"GET", "POST", "PATCH"}


def test_app_scopes_cover_registered_methods_only():
    parsed = [st.parse_scope(s) for s in st.APP_SCOPES]
    assert all(parsed)
    assert {p for _, p in parsed} == set(app_module.APP_ENDPOINTS)


# ── app.py 接线：遗留无作用域记录保持原样，带作用域的按作用域判 ────────────────

def test_app_allowed_legacy_record_without_scopes_unchanged():
    assert AuthMiddleware._app_allowed("/api/agent/memories") is True
    assert AuthMiddleware._app_allowed("/api/config") is False
    # v0.6 早期签发的记录没有 scopes 字段 → 仍按路径判，不能因为没迁移就拒
    assert AuthMiddleware._app_allowed("/api/agent/memories", "DELETE", []) is True


def test_app_allowed_with_scopes_enforces_method():
    scopes = ["POST:/api/agent/memories"]
    assert AuthMiddleware._app_allowed("/api/agent/memories", "POST", scopes) is True
    assert AuthMiddleware._app_allowed("/api/agent/memories", "GET", scopes) is False
    assert AuthMiddleware._app_allowed("/api/members", "GET", scopes) is False


# ── 端到端：中间件按令牌作用域放行/拒绝 ───────────────────────────────────────

async def _probe(path: str, headers: dict, method: str = "GET"):
    reached: list = []
    statuses: list = []

    async def downstream(scope, receive, send):
        reached.append(scope.get("state", {}).get("user"))

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        if message.get("type") == "http.response.start":
            statuses.append(message.get("status"))

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
    }
    await AuthMiddleware(downstream)(scope, receive, send)
    return bool(reached), (statuses[0] if statuses else 200), (reached[0] if reached else None)


@pytest.fixture
def wired(cfg, monkeypatch):
    monkeypatch.setattr(app_module, "get_config", lambda: cfg)
    monkeypatch.setattr(st, "get_config", lambda: cfg)
    res = st.get_service_token_store().generate("tvpilot", ["POST:/api/agent/memories"])
    assert res["ok"]
    return res["token"]


@pytest.mark.asyncio
async def test_service_token_hits_only_its_own_scope(wired):
    bearer = {"authorization": f"Bearer {wired}"}
    reached, status, user = await _probe("/api/agent/memories", bearer, method="POST")
    assert reached and status == 200
    assert user["service"] and user["username"] == "service:tvpilot"
    # 同一路径换方法 → 403（这就是 F-2 被堵死的读数）
    for m in ("GET", "DELETE"):
        reached, status, _ = await _probe("/api/agent/memories", bearer, method=m)
        assert not reached and status == 403, m
    # 白名单外的路径 → 403
    reached, status, _ = await _probe("/api/members", bearer)
    assert not reached and status == 403


@pytest.mark.asyncio
async def test_service_token_can_be_revoked_and_old_env_still_works(cfg, wired, monkeypatch):
    """Q1 additive：吊销新签发令牌不影响旧 env 凭据继续可用。"""
    bearer = {"authorization": f"Bearer {wired}"}
    assert st.get_service_token_store().revoke("tvpilot")["ok"] is True
    reached, status, _ = await _probe("/api/agent/memories", bearer, method="POST")
    assert not reached and status == 401

    cfg.butler_token = "btl_env_secret"
    reached, status, user = await _probe("/api/members", {"authorization": "Bearer btl_env_secret"})
    assert reached and status == 200 and user["butler"] is True


@pytest.mark.asyncio
async def test_imported_butler_keeps_member_routes_flag(cfg, monkeypatch):
    """导入记录必须仍带 butler 身份位：member_routes 按它判，丢了下线前就断管家。"""
    cfg.butler_token = "btl_env_secret"
    monkeypatch.setattr(app_module, "get_config", lambda: cfg)
    monkeypatch.setattr(st, "get_config", lambda: cfg)
    reached, _, user = await _probe(
        "/api/members/abc", {"authorization": "Bearer btl_env_secret"}, method="PATCH"
    )
    assert reached and user.get("butler") is True


# ── Q5：来源派生不被合并降级 ──────────────────────────────────────────────────

def test_source_is_derived_from_token_not_caller(cfg, monkeypatch):
    from memory_agent.api.agent_memory_routes import _resolve_source

    monkeypatch.setattr("memory_agent.api.agent_memory_routes.get_config", lambda: cfg)
    svc_user = {"service": True, "app_source": "vision"}
    assert _resolve_source("butler", svc_user) == "vision"
    app_user = {"app": True, "app_source": "vision"}
    assert _resolve_source("manual", app_user) == "vision"
    # 无令牌来源时仍走白名单，非法自报值回落 ma
    assert _resolve_source("evil", {}) == "ma"
    assert _resolve_source("manual", {}) == "manual"


# ── 对外契约：管理端点存在且只走 admin ────────────────────────────────────────

def test_service_token_admin_routes_registered():
    from memory_agent.api.config_routes import ROUTES

    registered = {(r.path, m) for r in ROUTES for m in (r.methods or set())}
    assert ("/api/config/service-tokens", "GET") in registered
    assert ("/api/config/service-tokens", "POST") in registered
    assert ("/api/config/service-tokens/{name}", "DELETE") in registered
    # 旧通道不能因为合并而消失
    assert ("/api/config/app-tokens", "GET") in registered
    assert ("/api/config", "POST") in registered


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
