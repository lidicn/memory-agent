"""拓扑 X 的 ACP client：用 httpx 消费对端（autoflow）的 /acp SSE，供 delegate 工具调用。

memory-worker 主动委派任务给 autoflow 时，通过此 client 调用 autoflow 的
HTTP ACP 端点。目标地址 / 令牌来自环境变量（AUTOFLOW_ACP_URL / AUTOFLOW_ACP_TOKEN）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx


def _autoflow_url() -> Optional[str]:
    return (os.getenv("AUTOFLOW_ACP_URL") or "").strip() or None


def _autoflow_token() -> Optional[str]:
    return (os.getenv("AUTOFLOW_ACP_TOKEN") or "").strip() or None


def _resolve_peer(rt: Any) -> tuple[Optional[str], Optional[str]]:
    """解析对端 autoflow 的地址与令牌。

    优先级：运行时配置（WebUI 可改，``config.autoflow_acp_url/token``）
    → 环境变量（兼容仅在容器环境注入变量的部署）。
    """
    url = token = None
    if rt is not None:
        cfg = getattr(rt, "config", None)
        if cfg is not None:
            url = (getattr(cfg, "autoflow_acp_url", "") or "").strip() or None
            token = (getattr(cfg, "autoflow_acp_token", "") or "").strip() or None
    # env 兜底（兼容只在容器环境注入变量的部署）
    if not url:
        url = _autoflow_url()
    if not token:
        token = _autoflow_token()
    return url, token


async def acp_prompt_remote(
    url: str,
    token: str,
    instruction: str,
    session_id: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 300.0,
) -> str:
    """调用远程 ACP 的 prompt，收集 session_update 内容，返回最终文本。"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "prompt",
        "params": {
            "messages": [{"role": "user", "content": instruction}],
            "sessionId": session_id,
            "model": model,
        },
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    collected: list[str] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST", url, json=payload, headers=headers
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise RuntimeError(
                    f"autoflow ACP 调用失败 {resp.status_code}: "
                    + body.decode("utf-8", errors="replace")
                )
            event = ""
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data = line.split(":", 1)[1].strip()
                    if event == "message":
                        try:
                            msg = json.loads(data)
                        except Exception:
                            continue
                        params = msg.get("params", {})
                        for blk in params.get("content", []):
                            btype = blk.get("type")
                            if btype == "text":
                                collected.append(blk.get("text", ""))
                            elif btype == "tool_call":
                                collected.append(
                                    f"[autoflow 调用工具 {blk.get('name')}]"
                                )
                        if params.get("status") in ("completed", "aborted", "error"):
                            break
    return "\n".join(collected).strip()


async def delegate_to_autoflow(rt: Any, args: dict) -> dict:
    """delegate_to_autoflow 工具实现（供 ACP 循环内 LLM 主动调用）。"""
    task = (args or {}).get("task") or ""
    if not task:
        return {"error": "delegate_to_autoflow 需要提供 task 参数"}
    url, token = _resolve_peer(rt)
    if not url or not token:
        return {
            "error": "未配置对端 autoflow 的 ACP 地址/令牌，无法委派。"
            "请在 WebUI 的「ACP」页填写出站对端（AUTOFLOW_ACP_URL / AUTOFLOW_ACP_TOKEN），"
            "或在 .env 中配置后重试。",
        }
    try:
        result = await acp_prompt_remote(url, token, task)
        return {"delegated": True, "to": "autoflow", "result": result}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"委派 autoflow 失败: {exc}"}
