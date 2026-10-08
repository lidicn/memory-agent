#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Memory Agent · 家庭行为记忆中枢 —— ASGI 入口

重构要点
--------
* 用标准 ``lifespan`` 在启动阶段构建 ``AppRuntime``，不再依赖「用户访问首页」
  才惰性创建后台服务（原来的 ``lazy_startup`` 正是采集与 API 割裂的根源）。
* ``AuthMiddleware`` 改为**纯 ASGI 中间件**：``BaseHTTPMiddleware`` 会给 SSE
  流式响应带来缓冲与生命周期问题，而本项目的 AI 助手强依赖流式输出。
* ``/mcp`` 由独立的 Token 中间件保护，与 WebUI 的 JWT 体系完全隔离。

⚠️ Basic Auth 分支必须保留：``nodered/water_purifier_flow.json`` 线上流
就是靠它通过鉴权的，删掉即断流。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import json
import logging
import os
import secrets
import traceback
from http.cookies import SimpleCookie
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from . import api, mcp_server
from .acp_auth import ACPTokenMiddleware
from .acp_server import acp_dispatcher
from .auth import AuthManager
from .config import get_config
from .logging_setup import configure_logging
from .mcp_auth import MCPTokenMiddleware
from .runtime import get_runtime, start_runtime, stop_runtime
from .service_tokens import get_service_token_store, scope_matches as _scope_matches

# 必须在任何模块产生日志**之前**配置：项目此前从未配置 logging，root logger 默认
# WARNING + lastResort，导致全部 logger.info() 被静默丢弃（线上看不到）。
configure_logging()

BASE_DIR = os.path.dirname(__file__)
STATIC_DIR = os.path.join(BASE_DIR, "static")
INDEX_FILE = os.path.join(STATIC_DIR, "index.html")

# 无需登录即可访问的路径
PUBLIC_PREFIXES = (
    "/static",
    "/mcp",
    "/acp",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/status",
    "/favicon.ico",
)
PUBLIC_EXACT = {"/", "/index.html", "/health", "/sw.js", "/manifest.webmanifest"}

# 设备端点：TV APK / HA 自动化等用独立 vision_device_token 鉴权，不与 WebUI
# 的 JWT 体系共享 Authorization 头。AuthMiddleware 对这些路径放行，改由路由层
# 的 _check_device_token 独立校验（与 /mcp、/acp 用独立 Token 中间件思路一致）。
# 否则同一 Authorization 头既无法是合法 JWT 又得是设备令牌，导致 TV 人脸事件
# 永远进不来（详见 docs/handoff_vision_identity.md §7）。
DEVICE_ENDPOINTS = (
    "/api/events/face",
    # 人脸识别节点池：节点（TV/手机）用独立设备令牌注册/心跳/同步人脸库
    "/api/face/node/register",
    "/api/face/node/heartbeat",
    "/api/face/node/lib",
)

# HA Assist（v1.0-2）：OpenAI 兼容会话端点，供 HA 的会话集成调用（家庭记忆问答）。
# AuthMiddleware 放行这些路径，改由路由层用独立 ha_assist_token 校验（与设备端点同思路）。
HA_ENDPOINTS = (
    "/v1/chat/completions",
    "/v1/models",
)

# 豆包管家（外部服务）用独立 butler_token 访问本服务的窄接口白名单。
# 令牌只在这几个路径上生效，避免一个外部服务的令牌拿到整个 WebUI 的权限。
# 契约见 docs/交接单_MA对接_成员档案与在场查询.md。
BUTLER_ENDPOINTS = (
    "/api/members",                 # GET 列表（带 profile_json）
    "/api/vision/presence",         # GET 在场查询
    "/api/vision/latest",           # GET 某房间最新巡检分析 + 截图外链（顾安恒专属对话整合）
    "/api/insights/member-schedule",  # GET 成员作息实测摘要
    "/api/behaviors",               # GET 当前行为状态（v0.9.5 主动感知，管家 M4 看板）
    "/api/events",                  # POST butler→MA 富化事件推送（感知层边界 §〇）
    # ── AutoForge v0.5.0 运行指标回灌（生态闭环，契约见 AutoForge docs/MA_METRICS_CONTRACT.md）──
    "/api/metrics/ingest",         # POST AutoForge 运行指标（按 dedupe_key 幂等合并）
    "/api/metrics",                # GET 已接收指标查询
    # ── 电视截屏多模态（按需调用，docs/电视截屏多模态识别功能_交接单.md）────
    "/api/tv/state",                # GET 当前电视 App 信息
    "/api/tv/screenshot",           # GET 当前电视截图
    "/api/tv/analyze",              # POST 截屏 + 多模态识别
)

# 竞技场（外部服务 AutoFlow）用独立 arena_ 令牌访问本服务的窄接口白名单。
# 令牌只在这几个路径上生效，与生产/管家/ACP 令牌三者隔离。
# 契约见 docs/交接单_AutoFlow竞技场对接.md。
ARENA_ENDPOINTS = (
    "/api/arena/snapshot",          # POST 生成脱敏版本化快照
    "/api/arena/snapshots",         # GET 列出/查看快照
)

# TVPilot / DeskPilot（豆包管家生态下的应用层）用独立 app_token 访问本服务的接口。
# 与 JWT/管家/竞技场/ACP/设备令牌五者隔离，仅放行白名单路径。
# 契约见 docs/交接卡_v0.3_对外查询接口.md 与 v0.5「记忆统一入库」。
# 写记忆时 source 由令牌派生（见 agent_memory_routes._resolve_source），调用方无法伪造来源。
APP_ENDPOINTS = (
    "/api/insights/query",          # POST 按模板 / 逻辑设备查询结构化结果（只读）
    "/api/agent/memories",          # POST 写入记忆（来源由令牌派生，防伪造，v0.5/v0.6）
    "/api/agent/memories/recall",   # POST 记忆召回（只读）
)


def _is_trusted_source(client_ip: str | None) -> bool:
    """判断客户端 IP 是否为可信来源（loopback 或内网段）。

    调试令牌（dbg_）仅允许从可信来源访问，防止令牌泄露后被外网利用。
    无法解析 IP 时视为不可信（fail-closed）。
    """
    if not client_ip:
        return False
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


class AuthMiddleware:
    """纯 ASGI 鉴权中间件，支持 Bearer JWT / Basic / Cookie 三种凭据。"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return

        # 设备上报端点：跳过全局 JWT 鉴权，交给路由层设备令牌校验
        if path in DEVICE_ENDPOINTS:
            await self.app(scope, receive, send)
            return

        # HA Assist 端点（/v1/*）：跳过全局 JWT 鉴权，交给路由层 ha_assist_token 校验
        if path in HA_ENDPOINTS:
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        client_ip = scope.get("client", (None, None))[0] if scope.get("client") else None
        # A3 §八「未卸载的同步 I/O」的真身在鉴权中间件，不在那两条路由里：`_authenticate` 是同步函数，
        # JWT 分支的 verify_token 每次 `open()`+`json.load()` 读一遍账号文件，Basic 分支更直接调
        # bcrypt.checkpw（百毫秒级、刻意慢；容器实测数见审计 §三十四 四）。两者都跑在事件循环上 = 每个非公开请求冻一次循环。
        # 这里可以卸载而 register 不行：`login`/`verify_token`/`_load_users` 全是只读，
        # 唯一的写盘是 MCPTokenStore._touch 的节流写，它自带 self._lock——没有「读→改→整份写回」被拆散。
        user = await asyncio.to_thread(self._authenticate, headers, client_ip)

        if not user:
            await self._reject(send, path)
            return

        # 管家令牌只放行白名单路径，超出范围一律 403
        if user.get("butler") and not self._butler_allowed(path, scope.get("method", "GET")):
            await self._reject(send, path, status=403, message="管家令牌无权访问该接口")
            return

        # 竞技场令牌只放行白名单路径，超出范围一律 403
        if user.get("arena") and not self._arena_allowed(path):
            await self._reject(send, path, status=403, message="竞技场令牌无权访问该接口")
            return

        # 应用令牌（TVPilot / DeskPilot）只放行白名单路径，超出范围一律 403
        if user.get("app") and not self._app_allowed(
            path, scope.get("method", "GET"), user.get("app_scopes")
        ):
            await self._reject(send, path, status=403, message="应用令牌无权访问该接口")
            return

        # 统一服务令牌（vMA-1.2.3 3.3.3，DCD 20261001 Q2）：授权面由令牌自带的
        # 「方法:路径」清单逐条匹配，不在清单内一律 403。作用域为空也拒绝。
        if user.get("service") and not _scope_matches(
            scope.get("method", "GET"), path, user.get("service_scopes") or []
        ):
            await self._reject(send, path, status=403, message="服务令牌作用域不含该接口")
            return

        # WO-MA-012 R-22：调试令牌（dbg_）只放行 /api/debug/* 前缀，超出范围一律 403。
        # 生产环境 debug_mode=False 时 dbg_ 令牌在 _authenticate 阶段已被拒绝（L297），
        # 此处是 debug_mode=True 时的路径收窄，防止调试令牌通吃所有 /api/*。
        if user.get("debug") and not path.startswith("/api/debug/"):
            await self._reject(send, path, status=403, message="调试令牌仅可访问 /api/debug/* 端点")
            return

        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["user"] = user
        await self.app(scope, receive, send)

    # 管家令牌允许的方法映射（审计 P0-3：白名单必须带方法，防止写操作被未授权访问）
    BUTLER_GET_PATHS = (
        "/api/members",
        "/api/vision/presence",
        "/api/vision/latest",
        "/api/insights/member-schedule",
        "/api/behaviors",
        "/api/metrics",
        "/api/tv/state",
        "/api/tv/screenshot",
    )
    BUTLER_POST_PATHS = (
        "/api/events",
        "/api/metrics/ingest",
        "/api/tv/analyze",
    )
    # WO-ADM-001 R-62：butler 需 PATCH 更新成员档案（profile_routes.py:95）
    BUTLER_PATCH_PATHS = (
        # /api/members/{member_id} 动态子路径允许 PATCH（局部更新成员档案）
    )

    @staticmethod
    def _butler_allowed(path: str, method: str = "GET") -> bool:
        """管家令牌的路径+方法白名单判定（含 /api/members/{id} 这类动态子路径）。

        安全加固（审计 P0-3）：白名单必须区分方法，GET 只读路径不允许 PUT/DELETE。
        /api/members/{id} 子路径：GET 允许查询，PATCH 允许局部更新（WO-ADM-001 R-62）。
        """
        if method == "GET":
            if path in AuthMiddleware.BUTLER_GET_PATHS:
                return True
            # /api/members/{id} 动态子路径仅 GET
            if path.startswith("/api/members/"):
                return True
        elif method == "POST":
            if path in AuthMiddleware.BUTLER_POST_PATHS:
                return True
        elif method == "PATCH":
            # WO-ADM-001 R-62：butler PATCH 更新成员档案
            if path.startswith("/api/members/"):
                return True
        return False

    @staticmethod
    def _arena_allowed(path: str) -> bool:
        """竞技场令牌的路径白名单判定（含 /api/arena/snapshots/{id} 动态子路径）。"""
        if path in ARENA_ENDPOINTS:
            return True
        return path.startswith("/api/arena/snapshots/")

    @staticmethod
    def _app_allowed(path: str, method: str = "GET", scopes: list | None = None) -> bool:
        """应用令牌（TVPilot / DeskPilot）白名单判定。

        vMA-1.2.3 3.3.3（DCD 20261001 Q2）：令牌自带作用域时**按「方法:路径」逐条判**，
        补掉原先"只比路径、方法不限"的 F-2。本轮之前签发、没有 scopes 字段的旧记录仍按
        ``APP_ENDPOINTS`` 判，保证既有部署不断连（additive）。
        """
        if scopes:
            return _scope_matches(method, path, scopes)
        return path in APP_ENDPOINTS

    @staticmethod
    def _butler_matches(token: str) -> bool:
        """常量时间比对管家令牌；未配置时通道关闭（返回 False）。"""
        expected = (get_config().butler_token or "").strip()
        if not expected:
            return False
        # compare_digest 要求两端均为 ASCII/bytes；遇到含非 ASCII 字符的非法令牌会抛
        # TypeError。此处 fail-safe 为「不匹配」，避免被一个脏令牌打挂整个鉴权链。
        try:
            return secrets.compare_digest(expected, token)
        except TypeError:
            return False

    def _app_verify(self, token: str) -> dict | None:
        """校验应用令牌（TVPilot / DeskPilot）；命中返回 ``{name, source}``，否则 ``None``。

        支持 v0.6 多令牌，并兼容 v0.3 遗留单 ``app_token``（见 ``app_tokens.AppTokenStore``）。
        """
        from .app_tokens import get_app_token_store

        return get_app_token_store().verify(token)

    def _service_verify(self, token: str) -> dict | None:
        """校验统一服务令牌（svc_）；命中返回 ``{name, source, scopes}``，否则 ``None``。"""
        return get_service_token_store().verify(token)

    def _authenticate(self, headers: dict, client_ip: str | None = None) -> dict | None:
        config = get_config()
        auth_manager = AuthManager(config)
        authorization = headers.get("authorization", "")

        if authorization.startswith("Bearer "):
            token = authorization[7:].strip()
            # 统一服务令牌（svc_）：作用域自带、一实例一令牌、可单独吊销并计数。
            # 排在遗留 butler/app 比对**之前**，从 env 导入进来的记录才会被计数。
            svc_rec = self._service_verify(token)
            if svc_rec:
                channel = svc_rec.get("channel") or ""
                return {
                    "username": "service:" + svc_rec["name"],
                    "is_admin": False,
                    "service": True,
                    "service_name": svc_rec["name"],
                    "service_scopes": svc_rec.get("scopes") or [],
                    "app_source": svc_rec.get("source", ""),
                    # 遗留通道导入后仍带原有身份位：member_routes 按 butler 判，
                    # 少了这个映射，豆包管家换成"同一条密钥的导入记录"就会被拒。
                    "butler": channel == "butler",
                    "app": channel == "app",
                }
            # 豆包管家服务令牌：与 JWT / 设备令牌三者隔离，仅放行 BUTLER_ENDPOINTS
            if self._butler_matches(token):
                return {"username": "butler", "is_admin": False, "butler": True}
            # TVPilot / DeskPilot 服务令牌：与 JWT/管家/设备令牌隔离，仅放行 APP_ENDPOINTS
            app_rec = self._app_verify(token)
            if app_rec:
                return {
                    "username": "app:" + app_rec["name"],
                    "is_admin": False,
                    "app": True,
                    "app_name": app_rec["name"],
                    "app_source": app_rec.get("source", ""),
                    "app_scopes": app_rec.get("scopes") or [],
                }
            user = auth_manager.verify_token(token)
            if user:
                return user
            # 竞技场令牌（kind=arena）：外部服务 AutoFlow 的窄接口令牌，不受 debug_mode 限制
            try:
                store = getattr(get_runtime(), "tokens", None)
            except Exception:  # noqa: BLE001
                store = None
            if store is not None:
                name = store.verify(token)
                if name and store.kind(name) == "arena":
                    return {"username": name, "is_admin": False, "arena": True}
            # 调试令牌（dbg_）回退校验：经运行时 Token 库识别 kind=debug 后授权
            # 访问 /api 调试接口。mcp_ 令牌仅用于 /mcp，不在此放行（保持隔离）。
            # 安全加固（审计 P0-2）：仅 debug_mode=True 且来源为 loopback/内网段时才放行。
            if not config.debug_mode or not _is_trusted_source(client_ip):
                return None
            if store is not None:
                name = store.verify(token)
                if name and store.kind(name) == "debug":
                    return {"username": name, "is_admin": False, "debug": True}
            return None

        if authorization.startswith("Basic "):
            # Node-RED 依赖此分支，不可删除
            try:
                decoded = base64.b64decode(authorization[6:]).decode("utf-8")
                username, password = decoded.split(":", 1)
            except Exception:
                return None
            # WO-MA-004 ⑤a：Basic Auth 与 JWT 登录路径汇到同一份爆破计数。
            # 原实现直接调 login()，无 login_allowed 检查、无 note_login_failure，
            # 等于给攻击者开了一条无限次尝试密码的旁路。
            ip = client_ip or "unknown"
            allowed, _retry = auth_manager.login_allowed(ip, username)
            if not allowed:
                return None
            result = auth_manager.login(username, password)
            if result.get("ok"):
                auth_manager.note_login_success(ip, username)
                return {
                    "username": result["username"],
                    "is_admin": result.get("is_admin", False),
                }
            auth_manager.note_login_failure(ip, username)
            return None

        cookie_header = headers.get("cookie", "")
        if cookie_header:
            cookies = SimpleCookie()
            try:
                cookies.load(cookie_header)
            except Exception:
                return None
            morsel = cookies.get("token")
            if morsel and morsel.value:
                return auth_manager.verify_token(morsel.value)
        return None

    @staticmethod
    async def _reject(send: Send, path: str, status: int = 401,
                      message: str = "未登录") -> None:
        body = json.dumps({"ok": False, "error": message}, ensure_ascii=False).encode()
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
        ]
        # 非 API 请求交还给前端外壳，由前端弹出登录视图，避免重定向循环
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


class NoCacheStaticMiddleware:
    """给 /static 资源注入 no-cache 头。

    避免浏览器长期缓存旧的 main.js / members.js 等，导致后端重启、
    前端代码更新后页面仍加载旧 JS（表现为组件未注册、页面白屏）。
    仅对 /static 路径生效，不影响 API 响应。
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or not scope.get("path", "").startswith("/static"):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                headers = [
                    h for h in headers
                    if h[0].lower() not in (b"cache-control", b"pragma", b"expires")
                ]
                headers.append((b"cache-control", b"no-cache, no-store, must-revalidate"))
                headers.append((b"pragma", b"no-cache"))
                headers.append((b"expires", b"0"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


async def homepage(request: Request):
    if not os.path.exists(INDEX_FILE):
        return PlainTextResponse("前端资源缺失：static/index.html 未找到", status_code=500)
    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    response = HTMLResponse(content)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


async def liveness(request: Request):
    """无需鉴权的存活探针。"""
    return JSONResponse({"ok": True, "service": "memory-agent"})


async def mcp_unavailable(request: Request):
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32000,
                "message": mcp_server.MCP_IMPORT_ERROR or "MCP 服务不可用",
            },
        },
        status_code=503,
    )


async def mcp_stats_endpoint(request: Request):
    """MCP 工具调用效率快照（Bug#8：可观测性）。运维/效率排查入口，需 WebUI JWT 鉴权。"""
    try:
        top_n = int(request.query_params.get("top_n", "20"))
    except ValueError:
        top_n = 20
    sort_by = request.query_params.get("sort_by", "calls")
    return JSONResponse(mcp_server.get_mcp_stats(top_n=top_n, sort_by=sort_by))


# ── v0.5.0 AutoForge 运行指标回灌（生态闭环）────────────────────────────
_METRICS_PATH = os.path.join(os.environ.get("DATA_DIR", "/data"), "af_metrics.json")


async def metrics_ingest_endpoint(request: Request):
    """接收 AutoForge 回灌的运行指标（butler 白名单）；按 dedupe_key 幂等合并。"""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    if not isinstance(payload, dict) or "automation_id" not in payload:
        return JSONResponse({"ok": False, "error": "missing automation_id"}, status_code=400)
    key = payload.get("dedupe_key") or payload["automation_id"]
    try:
        os.makedirs(os.path.dirname(_METRICS_PATH), exist_ok=True)
        store: dict = {}
        if os.path.exists(_METRICS_PATH):
            try:
                store = json.loads(Path(_METRICS_PATH).read_text(encoding="utf-8"))
            except Exception:
                store = {}
        store[key] = payload
        Path(_METRICS_PATH).write_text(
            json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    return JSONResponse({"ok": True, "dedupe_key": key})


async def metrics_query_endpoint(request: Request):
    """返回已接收的 AutoForge 运行指标（butler 白名单）。"""
    if not os.path.exists(_METRICS_PATH):
        return JSONResponse({"ok": True, "metrics": {}})
    try:
        store = json.loads(Path(_METRICS_PATH).read_text(encoding="utf-8"))
    except Exception:
        return JSONResponse({"ok": True, "metrics": {}})
    return JSONResponse({"ok": True, "metrics": store})


_log = logging.getLogger("mcp.dispatch")


def _no_buffer_send(send: Send) -> Send:
    """为 MCP 响应注入防缓冲头，避免反向代理缓冲 SSE 流导致 endpoint 事件延迟。

    健壮性：若应用已发送过响应头后又尝试发送第二个 ``http.response.start``
    （典型场景：SSE/流式响应中途抛出异常，框架的异常处理器再尝试回写 500 错误
    响应），直接吞掉重复的 start 及其后的 body，避免 uvicorn 抛
    ``Expected ASGI message 'http.response.body', but got 'http.response.start'``
    并干掉整个 worker（表现为周期性崩溃重启）。原始异常仍由上层记录。
    """

    sent_start = False
    suppress = False  # 已进入“丢弃错误回写”模式

    async def wrapped(message):
        nonlocal sent_start, suppress
        mtype = message.get("type")
        if mtype == "http.response.start":
            if sent_start:
                # 响应头已发送，重复 start 是框架的错误回写，丢弃以免击垮 worker。
                _log.warning(
                    "MCP: 丢弃重复的 http.response.start（响应头已发送，疑似流式响应中途异常）"
                )
                suppress = True
                return
            sent_start = True
            headers = list(message.get("headers", []))
            names = {h[0].lower() for h in headers}
            if b"cache-control" not in names:
                headers.append((b"cache-control", b"no-cache, no-transform"))
            if b"x-accel-buffering" not in names:
                headers.append((b"x-accel-buffering", b"no"))
            message = {**message, "headers": headers}
        elif mtype == "http.response.body" and suppress:
            # 错误回写 body，随上面的重复 start 一起丢弃。
            return
        await send(message)

    return wrapped


async def _mcp_dispatcher(scope: Scope, receive: Receive, send: Send):
    """在 /mcp 下同时兼容 Streamable HTTP 与旧版 SSE 协议。

    路由规则
    --------
    * ``POST /mcp``                    → Streamable HTTP（initialize / 工具调用）
    * ``GET  /mcp/sse`` 或 ``GET /mcp``（根路径）
                                       → SSE 传输：建立流并立即发送 ``endpoint`` 事件，
                                         兼容只连 base URL（不追加 /sse）的客户端
    * ``POST /mcp/messages/..``        → SSE 传输的消息上行通道

    Starlette>=0.33 的 Mount 不再从 scope["path"] 剥离挂载前缀，而是保留完整路径
    （如 /mcp/sse）并把前缀写入 scope["root_path"]。这里手动把 /mcp 前缀剥掉，让子
    应用收到它们期望的路径（/、/sse、/messages/...）；同时保证 root_path 以 /mcp 结尾，
    使 SSE 构建的 messages 端点 URL 带前缀，客户端 POST 回 /mcp/messages/.. 能正确路由。
    """
    if scope.get("type") != "http":
        await mcp_server.mcp_app(scope, receive, send)
        return

    path = scope.get("path", "")
    method = scope.get("method", "GET")
    if path.startswith("/mcp"):
        sub = path[len("/mcp"):]
        if not sub:
            sub = "/"
        elif not sub.startswith("/"):
            sub = "/" + sub
    else:
        sub = path

    scope = dict(scope)
    scope["path"] = sub

    # 判定传输类型并记录日志，便于排查客户端握手问题
    if method == "GET" and (sub == "/sse" or sub == "/"):
        transport = "sse"
    elif sub.startswith("/messages/"):
        transport = "sse-msg"
    else:
        transport = "streamable"
    _log.info("MCP dispatch %s %s -> %s", method, path, transport)

    if transport == "sse":
        # 兼容连 base URL（/mcp）而非 /mcp/sse 的客户端：把它当作 SSE 端点。
        # 确保 SSE 生成的 messages 端点 URL 带 /mcp 前缀，使回传路由一致。
        rp = scope.get("root_path", "") or ""
        if not rp.endswith("/mcp"):
            rp = rp.rstrip("/") + "/mcp"
        scope["root_path"] = rp
        scope["path"] = "/sse"
        target = mcp_server.mcp_sse_app
    elif transport == "sse-msg":
        target = mcp_server.mcp_sse_app
    else:
        target = mcp_server.mcp_app

    # 注入防缓冲头，避免任何反向代理缓冲 SSE 流，导致 endpoint 事件/响应延迟到达。
    await target(scope, receive, _no_buffer_send(send))


class _MCPRootApp(ASGIApp):
    """精确路径 /mcp（无尾斜杠）的 ASGI 入口。

    Starlette>=0.33 的 ``Mount`` 只对带子路径的请求（如 /mcp/sse、/mcp/messages）
    命中，对精确的 ``/mcp``（无尾斜杠）不会命中——而 Streamable HTTP 客户端把
    所有请求（含 initialize 握手）都发往 ``/mcp``。这里用一条精确 ``Route`` 兜底，
    复用与 Mount 完全相同的鉴权与分发逻辑。
    """

    def __init__(self) -> None:
        self._mw = MCPTokenMiddleware(_mcp_dispatcher, lambda: get_runtime().tokens)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._mw(scope, receive, send)


class _ACPRootApp(ASGIApp):
    """精确路径 /acp（无尾斜杠）的 ASGI 入口，复用 ACP 鉴权与分发逻辑。"""

    def __init__(self) -> None:
        self._mw = ACPTokenMiddleware(acp_dispatcher, lambda: get_runtime().tokens)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._mw(scope, receive, send)


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    runtime = await start_runtime()
    app.state.runtime = runtime

    mcp_lifespan = mcp_server.get_lifespan_context()
    try:
        if mcp_lifespan is not None and mcp_server.mcp_app is not None:
            # Streamable HTTP 的会话管理器必须在 lifespan 中启动，
            # 否则第一次 initialize 就会因 task group 未就绪而失败
            async with mcp_lifespan(mcp_server.mcp_app):
                print("[App] MCP 子应用已挂载于 /mcp")
                yield
        else:
            if mcp_server.MCP_IMPORT_ERROR:
                print(f"[App] MCP 未启用: {mcp_server.MCP_IMPORT_ERROR}")
            yield
    finally:
        await stop_runtime()


async def _service_worker(request: Request):
    """以根作用域提供 Service Worker，供 PWA 离线壳使用。"""
    p = os.path.join(STATIC_DIR, "sw.js")
    if not os.path.exists(p):
        return Response("not found", status_code=404)
    with open(p, "rb") as f:
        body = f.read()
    return Response(
        body,
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


async def _manifest(request: Request):
    """以正确 MIME 提供 PWA manifest。"""
    p = os.path.join(STATIC_DIR, "manifest.webmanifest")
    if not os.path.exists(p):
        return Response("not found", status_code=404)
    with open(p, "rb") as f:
        body = f.read()
    return Response(
        body,
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


def _build_routes() -> list:
    routes: list = list(api.get_routes())
    routes.append(Route("/health", liveness, methods=["GET"]))
    routes.append(Route("/api/mcp/stats", mcp_stats_endpoint, methods=["GET"]))
    # v0.5.0 AutoForge 运行指标回灌（生态闭环）
    routes.append(Route("/api/metrics/ingest", metrics_ingest_endpoint, methods=["POST"]))
    routes.append(Route("/api/metrics", metrics_query_endpoint, methods=["GET"]))

    if mcp_server.mcp_app is not None:
        mcp_mw = MCPTokenMiddleware(_mcp_dispatcher, lambda: get_runtime().tokens)
        routes.append(Mount("/mcp", app=mcp_mw))
        # Starlette>=0.33 的 Mount 对精确的 "/mcp"（无尾斜杠）不会命中，
        # 只有 /mcp/sse、/mcp/messages 等带子路径请求才命中。
        # Streamable HTTP 客户端把所有请求发往 /mcp，需单独兜底。
        routes.append(
            Route(
                "/mcp",
                endpoint=_MCPRootApp(),
                methods=["GET", "POST", "DELETE", "OPTIONS"],
            )
        )
    else:
        routes.append(Route("/mcp", mcp_unavailable, methods=["GET", "POST", "DELETE"]))

    # ACP（Agent Client Protocol，拓扑 X peer-to-peer）：与 MCP 同构的挂载方式。
    acp_mw = ACPTokenMiddleware(acp_dispatcher, lambda: get_runtime().tokens)
    routes.append(Mount("/acp", app=acp_mw))
    # Starlette>=0.33 的 Mount 对精确的 "/acp"（无尾斜杠）不会命中，用精确 Route 兜底。
    routes.append(
        Route(
            "/acp",
            endpoint=_ACPRootApp(),
            methods=["GET", "POST", "OPTIONS"],
        )
    )

    if os.path.isdir(STATIC_DIR):
        routes.append(
            Mount("/static", app=StaticFiles(directory=STATIC_DIR), name="static")
        )
    else:
        print(f"[App] 警告：静态目录不存在 {STATIC_DIR}")

    routes.append(Route("/sw.js", _service_worker, methods=["GET"]))
    routes.append(Route("/manifest.webmanifest", _manifest, methods=["GET"]))
    routes.append(Route("/", homepage, methods=["GET"]))
    return routes


combined_app = Starlette(
    routes=_build_routes(),
    middleware=[Middleware(AuthMiddleware), Middleware(NoCacheStaticMiddleware)],
    lifespan=lifespan,
)
# 避免 /mcp 被 307 重定向到 /mcp/，导致部分 MCP 客户端解析失败。
# Starlette>=0.33 已从 Starlette.__init__ 移除 redirect_slashes，
# 改为设置底层 Router 的属性。
combined_app.router.redirect_slashes = False


# 候选落盘路径：容器内 data 卷的挂载点在不同部署下可能不同，逐个尝试。
_TRACEBACK_PATHS = (
    "/app/data/last_traceback.txt",
    "/data/last_traceback.txt",
    "/tmp/last_traceback.txt",
)


async def _dump_traceback_handler(request: Request, exc: Exception) -> Response:
    """全局异常兜底。

    未捕获异常默认只会得到 uvicorn 的裸 ``Internal Server Error``（21 字节），
    无法定位根因。这里把真实栈同时写入容器日志与挂载目录，便于在宿主机直接读取。
    """
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logging.getLogger("app.error").error(
        "未捕获异常 %s %s\n%s", request.method, request.url.path, text
    )
    for path in _TRACEBACK_PATHS:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(text)
            break
        except OSError:
            continue
    # 安全（审计 AP1）：不再把 traceback 回传给客户端（未认证也能拿到，泄露内部
    # 结构/路径/依赖）。完整栈已写入日志与落盘文件，供运维排查。
    return JSONResponse(
        {"ok": False, "error": "internal_server_error"},
        status_code=500,
    )


# 必须在 _mount_slash_fix 包装之前注册：包装后 combined_app 是普通 ASGI 可调用对象，
# 不再具备 Starlette 的 add_exception_handler 方法。
combined_app.add_exception_handler(Exception, _dump_traceback_handler)


def _mount_slash_fix(app: ASGIApp) -> ASGIApp:
    """Starlette>=0.33 的 ``Mount`` 对精确的 ``/mcp``（无尾斜杠）不会命中，
    只有带子路径的 ``/mcp/sse``、``/mcp/messages`` 才命中；而 Streamable HTTP
    客户端把所有请求（含 initialize 握手）都发往 ``/mcp``。这里在入口处把
    精确的 ``/mcp`` 重写为 ``/mcp/``，让 ``Mount`` 能正常匹配并完成前缀剥离。
    """

    async def _wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/mcp":
            scope = dict(scope)
            scope["path"] = "/mcp/"
        await app(scope, receive, send)

    return _wrapped


combined_app = _mount_slash_fix(combined_app)

# 兼容旧引用
app = combined_app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(combined_app, host="0.0.0.0", port=8000)
