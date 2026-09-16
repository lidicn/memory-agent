"""MCP 接入管理路由：Token 生成 / 列表 / 撤销、接入信息、连通自检"""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.routing import Route

from .. import mcp_server
from .deps import error, json_body, ok, require_user, runtime


def _base_url(request: Request) -> str:
    """推导对外可访问的基础地址，兼容反向代理。"""
    headers = request.headers
    proto = headers.get("x-forwarded-proto") or request.url.scheme
    host = headers.get("x-forwarded-host") or headers.get("host") or request.url.netloc
    return f"{proto}://{host}".rstrip("/")


def _client_snippets(url: str, token_placeholder: str) -> dict:
    auth = f"Bearer {token_placeholder}"
    return {
        "claude": json.dumps(
            {
                "mcpServers": {
                    "memory-agent": {
                        "command": "npx",
                        "args": [
                            "-y",
                            "mcp-remote",
                            url,
                            "--header",
                            f"Authorization: {auth}",
                        ],
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        "cursor": json.dumps(
            {
                "mcpServers": {
                    "memory-agent": {
                        "url": url,
                        "headers": {"Authorization": auth},
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        "opencode": json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "mcp": {
                    "memory-agent": {
                        "type": "remote",
                        "url": url,
                        "enabled": True,
                        "headers": {"Authorization": auth},
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
    }


async def mcp_info(request: Request):
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    url = f"{_base_url(request)}/mcp"
    sse_url = f"{_base_url(request)}/mcp/sse"
    info = mcp_server.describe()
    return ok(
        {
            "url": url,
            "sse_url": sse_url,
            "transport": info["transport"],
            "available": info["available"],
            "error": info["error"],
            "tools": info["tools"],
            # 工具说明直接取自 MCP 注册表，避免页面文案与实际注册的工具脱节
            "catalog": info.get("catalog", []),
            "token_count": rt.tokens.count(),
            "snippets": _client_snippets(url, "<你的 Token>"),
        }
    )


async def list_tokens(request: Request):
    _, err = require_user(request)
    if err:
        return err
    return ok({"tokens": runtime(request).tokens.list_tokens()})


async def create_token(request: Request):
    """生成 Token。明文只在本次响应中出现，之后无法再取回。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    name = body.get("name") or ""
    rt = runtime(request)
    # prefix / kind 可选：默认生成 mcp_ 令牌（MCP 用途）；
    # 传 kind="acp"、prefix="acp_" 可生成 ACP（拓扑 X peer）令牌。
    result = rt.tokens.generate(
        name,
        prefix=body.get("prefix"),
        kind=body.get("kind"),
        # v0.7.5：未指定 scopes 时默认只读；写工具需显式带 write
        scopes=body.get("scopes"),
    )
    if not result.get("ok"):
        return error(result.get("error", "生成失败"))
    url = f"{_base_url(request)}/mcp"
    return ok(
        {
            "name": result["name"],
            "token": result["token"],
            "prefix": result["prefix"],
            "created_at": result["created_at"],
            "scopes": result.get("scopes", []),
            "url": url,
            "snippets": _client_snippets(url, result["token"]),
            "message": "Token 已生成，请立即复制保存，页面关闭后无法再次查看",
        }
    )


async def revoke_token(request: Request):
    """撤销 Token。用 POST + body，规避部分客户端不支持 DELETE 带 body。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    name = body.get("name") or request.query_params.get("name") or ""
    if not name:
        return error("缺少 name")
    if not runtime(request).tokens.revoke(name):
        return error("Token 不存在", 404)
    return ok({"message": f"Token 已撤销: {name}"})


async def update_token_scopes(request: Request):
    """调整令牌权限（v0.7.5：read / read+write）。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    name = (body.get("name") or "").strip()
    scopes = body.get("scopes")
    if not name:
        return error("缺少 name")
    if scopes is None:
        return error("缺少 scopes")
    if not runtime(request).tokens.update_scopes(name, scopes):
        return error("Token 不存在", 404)
    return ok({"message": f"Token 权限已更新: {name}",
               "scopes": runtime(request).tokens.scopes(name)})


async def list_audit(request: Request):
    """MCP 调用审计（v0.7.5-2 只读查询入口）。

    ?limit=&token=&tool=&failed=1
    """
    _, err = require_user(request)
    if err:
        return err
    q = request.query_params
    rows = runtime(request).store.list_mcp_audit(
        limit=int(q.get("limit") or 100),
        token_name=q.get("token") or "",
        tool=q.get("tool") or "",
        only_failed=(q.get("failed") or "").strip() in ("1", "true", "yes"),
    )
    return ok({"audit": rows, "total": len(rows)})


async def selftest(request: Request):
    """本地握手自检。

    直接以 ASGI 方式调用 MCP 子应用，验证 initialize 能否返回
    ``result.protocolVersion`` —— 这正是重构前必然失败的一步。
    """
    _, err = require_user(request)
    if err:
        return err
    if mcp_server.mcp_app is None:
        return error(mcp_server.MCP_IMPORT_ERROR or "MCP 服务不可用", 503)

    try:
        import httpx

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "memory-agent-selftest", "version": "1.0"},
            },
        }
        transport = httpx.ASGITransport(app=mcp_server.mcp_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://mcp-selftest", timeout=20
        ) as client:
            resp = await client.post(
                "/",
                json=payload,
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
            )
        raw = resp.text
        parsed = _parse_jsonrpc(raw)
        if resp.status_code >= 400 or parsed is None:
            return error(
                f"握手失败 HTTP {resp.status_code}: {raw[:300]}", 502
            )
        if "error" in parsed:
            return error(f"握手返回错误: {parsed['error']}", 502)
        result = parsed.get("result", {})

        # SSE 端点连通自检：确认 GET /sse 能立即收到 endpoint 事件。
        # 这正是部分客户端（如 DeepSeek++）报 "did not provide a POST endpoint" 的关键。
        sse_ok = False
        sse_detail = ""
        if mcp_server.mcp_sse_app is not None:
            try:
                sse_transport = httpx.ASGITransport(app=mcp_server.mcp_sse_app)
                async with httpx.AsyncClient(
                    transport=sse_transport,
                    base_url="http://mcp-selftest",
                    timeout=10,
                ) as sse_client:
                    async with sse_client.stream(
                        "GET", "/sse", headers={"accept": "text/event-stream"}
                    ) as sse_resp:
                        if sse_resp.status_code == 200:
                            async for sse_line in sse_resp.aiter_lines():
                                if sse_line.startswith("event: endpoint"):
                                    sse_ok = True
                                    break
                        else:
                            sse_detail = f"HTTP {sse_resp.status_code}"
            except Exception as sse_exc:  # 自检失败不应掩盖主握手结果
                sse_detail = str(sse_exc)

        return ok(
            {
                "message": "MCP 握手成功",
                "protocol_version": result.get("protocolVersion", ""),
                "server_info": result.get("serverInfo", {}),
                "tools": mcp_server.TOOL_NAMES,
                "tools_count": len(mcp_server.TOOL_NAMES),
                "sse": {"ok": sse_ok, "detail": sse_detail},
            }
        )
    except Exception as exc:
        return error(f"自检异常: {exc}", 500)


def _parse_jsonrpc(raw: str) -> dict | None:
    """兼容 JSON 与 SSE 两种响应体。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            candidate = line[5:].strip()
            if candidate.startswith("{"):
                try:
                    return json.loads(candidate)
                except ValueError:
                    continue
    return None


# ── 旧端点别名 ─────────────────────────────────────────────────────────────

async def add_agent_token(request: Request):
    return await create_token(request)


async def remove_agent_token(request: Request):
    return await revoke_token(request)


ROUTES = [
    Route("/api/mcp/info", mcp_info, methods=["GET"]),
    Route("/api/mcp/tokens", list_tokens, methods=["GET"]),
    Route("/api/mcp/tokens", create_token, methods=["POST"]),
    Route("/api/mcp/tokens/revoke", revoke_token, methods=["POST"]),
    Route("/api/mcp/tokens/scopes", update_token_scopes, methods=["POST"]),
    Route("/api/mcp/audit", list_audit, methods=["GET"]),
    Route("/api/mcp/selftest", selftest, methods=["POST", "GET"]),
    # 旧端点别名
    Route("/api/config/agent-token", add_agent_token, methods=["POST"]),
    Route("/api/config/agent-token", remove_agent_token, methods=["DELETE"]),
]
