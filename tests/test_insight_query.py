"""v0.3 对外查询接口测试。

覆盖：
- ``app_token`` 白名单隔离（只放行 /api/insights/query）
- ``POST /api/insights/query`` 两种模式：按模板 / 按逻辑设备即时查询
- 身份层只读接口 ``/api/identity/devices`` 与 ``/api/identity/health`` 的鉴权与返回

不依赖真实 HA / LLM / 网络：runtime 与身份层均为桩。
"""

import asyncio
import json
import os
import sys
import types

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import app as app_mod  # noqa: E402
from memory_agent.api import identity_routes, insight_routes  # noqa: E402
from memory_agent.config import Config  # noqa: E402
from memory_agent.templates import BehaviorInsight, EntityQuery  # noqa: E402


# ── 桩 ─────────────────────────────────────────────────────────────────────

class _FakeInsights:
    def resolve_range(self, days=7, start="", end=""):
        return "2026-09-01T00:00:00", "2026-09-08T00:00:00", {
            "start": "2026-09-01T00:00:00",
            "end": "2026-09-08T00:00:00",
            "days": days,
            "label": "近7天",
        }

    def name_map(self):
        return {}

    def _fallback_name(self, eid):
        return eid

    def _usage_one(self, entity_id, start_iso, end_iso, on_set, debounce, include_timeline):
        return {"entity_id": entity_id, "total_seconds": 3600, "sessions": 2,
                "total_on_human": "1小时", "daily_average_human": "0.5小时"}

    def _usage_by_attr(self, entity_id, attribute, value, pattern, start_iso, end_iso,
                       debounce=60, include_timeline=True):
        return {"entity_id": entity_id, "attribute": attribute, "total_seconds": 1800,
                "sessions": 1, "total_on_human": "30分钟", "daily_average_human": "30分钟"}

    def _count_by_filter(self, entity_id, attribute, value, pattern, start_iso, end_iso):
        return {"entity_id": entity_id, "match_count": 5, "by_day_count": {}}


class _FakeTemplates:
    def __init__(self, templates):
        self.templates = templates

    def list_all(self):
        return self.templates

    def get(self, template_id):
        for t in self.templates:
            if t.id == template_id:
                return t
        return None


class _StubIdentity:
    def __init__(self, mapping, devices=None, health=None):
        self.mapping = mapping
        self._devices = devices or []
        self._health = health or []
        self.store = self

    def resolve(self, ref):
        got = self.mapping.get(ref)
        return (got, None) if got else ([], "unresolved")

    def list_devices(self):
        return self._devices

    def list_device_health(self, state=""):
        if not state:
            return self._health
        return [h for h in self._health if h.get("state") == state]


class _FakeRT:
    def __init__(self, templates, identity=None):
        self.templates = _FakeTemplates(templates)
        self.insights = _FakeInsights()
        self.identity = identity


def _request(body, rt, user=None, path="/api/insights/query", query_string=b""):
    """构造一个可用的 Starlette Request（把 runtime 挂在 app.state 上）。"""
    from starlette.requests import Request

    app = types.SimpleNamespace(state=types.SimpleNamespace(runtime=rt))
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "query_string": query_string,
        "headers": [],
        "app": app,
        "state": {"user": user} if user else {},
    }

    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

    return Request(scope, receive)


def _tpl(tid="tpl_x"):
    return BehaviorInsight(
        id=tid, name="测试模板", description="", category="media",
        entities=[EntityQuery(entity_id="media_player.tv", attribute="source",
                              pattern="equals", value="HDMI 3", metric="duration")],
        default_days=7,
    )


def _json(resp):
    return json.loads(resp.body)


# ── app_token 白名单隔离 ────────────────────────────────────────────────────

def test_app_endpoints_whitelist():
    assert app_mod.APP_ENDPOINTS == ("/api/insights/query",)
    assert app_mod.AuthMiddleware._app_allowed("/api/insights/query") is True
    # 越权路径一律拒绝：一个应用层令牌拿不到 WebUI 其他接口
    for path in ("/api/members", "/api/vision/presence", "/api/identity/devices", "/api/config"):
        assert app_mod.AuthMiddleware._app_allowed(path) is False, path


def test_app_token_disabled_when_unset(monkeypatch):
    """未配置 app_token 时通道关闭，避免空令牌放行。"""
    cfg = Config()
    cfg.app_token = ""
    monkeypatch.setattr(app_mod, "get_config", lambda: cfg)
    assert app_mod.AuthMiddleware._app_matches("") is False
    assert app_mod.AuthMiddleware._app_matches("anything") is False


def test_app_token_matches_when_configured(monkeypatch):
    cfg = Config()
    cfg.app_token = "tv-secret-token"
    monkeypatch.setattr(app_mod, "get_config", lambda: cfg)
    assert app_mod.AuthMiddleware._app_matches("tv-secret-token") is True
    assert app_mod.AuthMiddleware._app_matches("wrong-token") is False


def test_config_exposes_app_token_field():
    assert "app_token" in Config.__dataclass_fields__
    assert isinstance(Config().app_token, str)


# ── POST /api/insights/query ────────────────────────────────────────────────

def test_query_by_template_id():
    rt = _FakeRT([_tpl()])
    req = _request({"template_id": "tpl_x", "days": 2}, rt)
    out = _json(asyncio.run(insight_routes.insight_query(req)))
    assert out["ok"] is True
    assert out["entities"][0]["entity_id"] == "media_player.tv"
    assert out["entities"][0]["result"]["total_seconds"] == 1800


def test_query_unknown_template_returns_404():
    rt = _FakeRT([_tpl()])
    req = _request({"template_id": "nope"}, rt)
    resp = asyncio.run(insight_routes.insight_query(req))
    assert resp.status_code == 404
    assert _json(resp)["ok"] is False


def test_query_by_logical_device():
    """按逻辑设备名即时查询：身份层解析为当前 entity_id 后再算。"""
    identity = _StubIdentity({"客厅电视": ["media_player.live_9"]})
    rt = _FakeRT([], identity=identity)
    req = _request({"logical_id": "客厅电视", "attribute": "source", "value": "HDMI 3",
                    "metric": "duration", "days": 2}, rt)
    out = _json(asyncio.run(insight_routes.insight_query(req)))
    assert out["ok"] is True
    e = out["entities"][0]
    assert e["entity_id"] == "media_player.live_9"
    assert e["resolved"] == ["media_player.live_9"]


def test_query_unresolvable_device_reports_stale():
    """解析不到时显式报 stale，而不是静默返回空（A3 的核心承诺）。"""
    rt = _FakeRT([], identity=_StubIdentity({}))
    req = _request({"logical_id": "不存在"}, rt)
    out = _json(asyncio.run(insight_routes.insight_query(req)))
    e = out["entities"][0]
    assert e["stale"] is True
    assert "失效" in e["result"]["error"]


def test_query_requires_target():
    rt = _FakeRT([])
    req = _request({}, rt)
    resp = asyncio.run(insight_routes.insight_query(req))
    assert resp.status_code == 400
    assert _json(resp)["ok"] is False


# ── 身份层只读接口 ──────────────────────────────────────────────────────────

def test_identity_devices_requires_login():
    req = _request({}, _FakeRT([]), path="/api/identity/devices")
    resp = asyncio.run(identity_routes.device_list(req))
    assert resp.status_code == 401


def test_identity_devices_returns_list():
    devices = [{"stable_id": "media_player__客厅电视", "display_name": "客厅电视",
                "primary_entity": "media_player.live", "candidates": [],
                "provenance": "auto-merged", "device_class": "media_player"}]
    rt = _FakeRT([], identity=_StubIdentity({}, devices=devices))
    req = _request({}, rt, user={"username": "u", "is_admin": True},
                   path="/api/identity/devices")
    out = _json(asyncio.run(identity_routes.device_list(req)))
    assert out["ok"] is True
    assert out["total"] == 1
    assert out["devices"][0]["stable_id"] == "media_player__客厅电视"


def test_identity_health_filters_by_state():
    health = [
        {"entity_id": "light.a", "state": "stale", "referenced": 1, "stable_id": "",
         "last_seen": "2026-09-01T10:00:00", "last_data_ts": "", "note": "", "updated_at": ""},
        {"entity_id": "light.b", "state": "active", "referenced": 0, "stable_id": "",
         "last_seen": "2026-09-08T10:00:00", "last_data_ts": "", "note": "", "updated_at": ""},
    ]
    rt = _FakeRT([], identity=_StubIdentity({}, health=health))

    # 留空返回全部
    req_all = _request({}, rt, user={"username": "u", "is_admin": True},
                       path="/api/identity/health")
    out = _json(asyncio.run(identity_routes.health_list(req_all)))
    assert out["total"] == 2 and out["state"] == "all"

    # 带 state=stale 过滤
    req_stale = _request({}, rt, user={"username": "u", "is_admin": True},
                         path="/api/identity/health", query_string=b"state=stale")
    out2 = _json(asyncio.run(identity_routes.health_list(req_stale)))
    assert out2["total"] == 1 and out2["health"][0]["entity_id"] == "light.a"
    assert out2["state"] == "stale"
