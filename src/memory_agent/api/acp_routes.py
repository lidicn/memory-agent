"""ACP（Agent Client Protocol）接入与委派配置路由。

拓扑 X（peer-to-peer）下，本端同时扮演两个角色：
* 入站：作为 ACP server 被 autoflow 调用，地址 ``/acp``，鉴权用 ``acp_`` 令牌
  （复用 agent_tokens，kind=acp，与 MCP 的 mcp_ 令牌隔离）。
* 出站：作为 ACP client 主动委派任务给对端 autoflow，对端地址 / 令牌来自
  ``config.autoflow_acp_url`` / ``autoflow_acp_token``（WebUI 可改）。

本模块在 WebUI 与后端之间补齐「展示 / 签发 / 自检」三块运营能力，
与 mcp_routes 同构（信息 + 令牌 + 自检），并额外暴露出站对端配置。
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from starlette.requests import Request
from starlette.routing import Route

from ..acp_server import acp_handle, build_acp_tools
from .deps import error, json_body, ok, require_user, runtime

ACP_TOKEN_PREFIX = "acp_"
DOC_URL = "/docs/acp-integration.md"


def _acp_token_count(rt) -> int:
    """统计 kind=acp 的接入令牌数量。"""
    try:
        return sum(
            1 for t in rt.tokens.list_tokens() if t.get("kind") == "acp"
        )
    except Exception:
        return 0


async def acp_info(request: Request):
    """返回 ACP 接入端点、工具清单与状态，供 WebUI 展示。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    cfg = rt.config
    tools = [
        {"name": t.get("name"), "description": t.get("description", "")}
        for t in (build_acp_tools() or [])
    ]
    url = (getattr(cfg, "autoflow_acp_url", "") or "").strip()
    token_set = bool((getattr(cfg, "autoflow_acp_token", "") or "").strip())
    return ok(
        {
            "endpoint": "/acp",
            "transport": "HTTP + SSE（JSON-RPC 2.0 / Agent Client Protocol）",
            "protocol_version": "ACP (Agent Client Protocol)",
            "auth": "Bearer acp_... 令牌（创建于下方令牌管理）",
            "tools": tools,
            "help_url": DOC_URL,
            "token_count": _acp_token_count(rt),
            "outbound": {
                "autoflow_url": url,
                "configured": bool(url) and token_set,
            },
        }
    )


async def acp_selftest(request: Request):
    """对 ACP server 做一次内部握手自检（initialize），验证协议与工具注册可用。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    steps = []
    try:
        resp, _ = await acp_handle(rt, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
    except Exception as exc:  # noqa: BLE001
        return error(f"ACP 握手失败: {type(exc).__name__}: {exc}")

    if not isinstance(resp, dict):
        return error("ACP 握手返回格式异常")
    result = resp.get("result") or {}
    tools = result.get("tools") or []
    names = [t.get("name") for t in tools]
    steps.append(
        {"step": "initialize 握手", "ok": True, "detail": f"返回 {len(tools)} 个工具"}
    )
    has_delegate = "delegate_to_autoflow" in names
    steps.append(
        {
            "step": "工具注册校验",
            "ok": has_delegate,
            "detail": "包含 delegate_to_autoflow"
            if has_delegate
            else f"缺少 delegate_to_autoflow（已注册: {names}）",
        }
    )
    configured = _acp_token_count(rt) > 0
    steps.append(
        {
            "step": "接入令牌就绪",
            "ok": configured,
            "advisory": True,
            "detail": f"kind=acp 令牌 {_acp_token_count(rt)} 个"
            if configured
            else "尚未创建 acp_ 令牌，对端无法鉴权调用本端（可随后在下方签发）",
        }
    )
    # 握手 + 工具注册为硬性通过项；令牌就绪为运营就绪提示（不阻塞）
    ok_all = steps[0]["ok"] and steps[1]["ok"]
    return ok(
        {
            "ok": ok_all,
            "summary": "ACP 服务就绪" if ok_all else "ACP 自检未通过，详见步骤",
            "steps": steps,
            "tools": names,
        }
    )


async def acp_tokens_list(request: Request):
    """列出 kind=acp 的接入令牌。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    items = [
        t for t in rt.tokens.list_tokens() if t.get("kind") == "acp"
    ]
    return ok({"tokens": items})


async def acp_token_create(request: Request):
    """创建 kind=acp 的接入令牌（明文仅返回一次）。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    name = (body.get("name") or "").strip()
    if not name:
        return error("请提供令牌名称")
    rt = runtime(request)
    res = rt.tokens.generate(name, prefix=ACP_TOKEN_PREFIX, kind="acp")
    if not res.get("ok"):
        return error(res.get("error", "创建失败"))
    return ok(
        {
            "name": res["name"],
            "token": res["token"],
            "prefix": res.get("prefix", ""),
            "created_at": res.get("created_at", ""),
            "kind": "acp",
        }
    )


async def acp_token_revoke(request: Request):
    """撤销 kind=acp 的接入令牌。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    name = (body.get("name") or "").strip()
    if not name:
        return error("请提供令牌名称")
    rt = runtime(request)
    if not rt.tokens.revoke(name):
        return error("令牌不存在或已撤销")
    return ok({"message": "已撤销", "name": name})


async def acp_test_outbound(request: Request):
    """对配置的出站对端 autoflow 做一次 initialize 握手，验证可达且支持委派。

    优先使用请求体里的 url/token（便于「先填后测」），否则回退到已保存配置。
    """
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    rt = runtime(request)
    cfg = rt.config
    url = (body.get("url") or "").strip() or (
        getattr(cfg, "autoflow_acp_url", "") or ""
    ).strip()
    token = (body.get("token") or "").strip() or (
        getattr(cfg, "autoflow_acp_token", "") or ""
    ).strip()
    if not url or not token:
        return error("请先填写对端 ACP URL 与 Token 再测试连通性")
    endpoint = url.rstrip("/") + "/acp"
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {},
    }
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            async with client.stream(
                "POST", endpoint, json=payload, headers=headers
            ) as resp:
                if resp.status_code not in (200, 202):
                    return error(f"对端返回 HTTP {resp.status_code}")
                raw = ""
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        raw = line[5:].strip()
                        break
                data = json.loads(raw) if raw else {}
                if data.get("error"):
                    return error(f"对端握手失败：{data['error']}")
                result = data.get("result") or {}
                tools = [t.get("name") for t in (result.get("tools") or [])]
                delegate_ok = "delegate_to_autoflow" in tools
                return ok(
                    {
                        "ok": True,
                        "reachable": True,
                        "delegate_supported": delegate_ok,
                        "tools": tools,
                        "detail": "对端 ACP 可达"
                        + ("，已支持 delegate_to_autoflow 委派" if delegate_ok
                           else "，但未见 delegate_to_autoflow 工具"),
                    }
                )
    except Exception as exc:  # noqa: BLE001
        return error(f"无法连接对端 ACP：{type(exc).__name__}: {exc}")


ROUTES = [
    Route("/api/acp/info", acp_info, methods=["GET"]),
    Route("/api/acp/selftest", acp_selftest, methods=["POST"]),
    Route("/api/acp/test-outbound", acp_test_outbound, methods=["POST"]),
    Route("/api/acp/tokens", acp_tokens_list, methods=["GET"]),
    Route("/api/acp/tokens", acp_token_create, methods=["POST"]),
    Route("/api/acp/tokens", acp_token_revoke, methods=["DELETE"]),
]
