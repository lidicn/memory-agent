"""MCP 错误模型统一（v0.9 契约完善，路线图 §五-2）。

业务失败一律以 ``isError=True`` 的 ``CallToolResult`` 返回，正文为结构化
``{"ok": false, "error": {"code": "...", "message": "..."}}``——终结
「``ok:false`` 被模型当正文」的契约缺陷。

工具函数内部仍可返回朴素 ``{"ok": False, "error": "..."}``；本模块在**工具调用
出口统一后处理**（``normalize_tool_result``），无需逐个改工具。
"""
from __future__ import annotations

import json
from typing import Any


class ErrorCode:
    """统一错误码枚举（协议层错误与业务失败分离）。"""

    NOT_FOUND = "NOT_FOUND"                        # 资源不存在（成员/模板/记忆/skill…）
    INVALID_PARAM = "INVALID_PARAM"                # 参数缺失/非法
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"  # 依赖不可达（HA/LLM/chroma/未启用）
    DENIED = "DENIED"                              # 权限不足
    RATE_LIMITED = "RATE_LIMITED"                  # 限流/请求过多
    INTERNAL = "INTERNAL"                          # 未分类内部错误


ALL_CODES = (
    ErrorCode.NOT_FOUND, ErrorCode.INVALID_PARAM, ErrorCode.UPSTREAM_UNAVAILABLE,
    ErrorCode.DENIED, ErrorCode.RATE_LIMITED, ErrorCode.INTERNAL,
)

# 关键词 → 错误码（显式 code 缺失时的兜底推断）
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (ErrorCode.DENIED, ("权限", "denied", "无 write", "越权", "未授权", "forbidden")),
    (ErrorCode.RATE_LIMITED, ("频繁", "限流", "rate limit", "too many", "429")),
    (ErrorCode.UPSTREAM_UNAVAILABLE,
     ("不可用", "unavailable", "超时", "timeout", "连接失败", "unreachable", "未启用", "未就绪")),
    (ErrorCode.NOT_FOUND, ("不存在", "not found", "没有名为", "未找到", "无此", "找不到")),
    (ErrorCode.INVALID_PARAM, ("不能为空", "必须", "非法", "invalid", "缺少", "参数", "需要")),
)


def infer_error_code(message: str) -> str:
    """按消息关键词推断错误码（工具未显式给 code 时使用）。"""
    m = (message or "").lower()
    for code, kws in _KEYWORDS:
        if any(k.lower() in m for k in kws):
            return code
    return ErrorCode.INTERNAL


def _is_error(result: Any) -> bool:
    """兼容 mcp 协议字段命名：``CallToolResult`` 用 camelCase ``isError``。"""
    return bool(getattr(result, "is_error", False) or getattr(result, "isError", False))


def normalize_tool_result(result: Any, tool_name: str = "") -> Any:
    """把工具的朴素 ``{"ok": false, ...}`` 结果升级为 ``isError=True`` 的结构化错误。

    - 已是 isError / 非「单文本块」/ 非 JSON / ``ok`` 非 False → 原样返回；
    - 命中 → 重造 ``CallToolResult(isError=True)``，正文含 ``error.code/message``，
      其余字段（available / reason 等）保留在 ``detail``，信息不丢。
    """
    if _is_error(result):
        return result
    try:
        contents = list(getattr(result, "content", None) or [])
        if len(contents) != 1 or getattr(contents[0], "type", "") != "text":
            return result
        data = json.loads(str(getattr(contents[0], "text", "")))
    except Exception:
        return result
    if not (isinstance(data, dict) and data.get("ok") is False):
        return result

    raw_err = data.get("error")
    if isinstance(raw_err, dict):  # 已是结构化 error
        message = str(raw_err.get("message") or "")
        code = raw_err.get("code") or infer_error_code(message)
    else:
        message = str(raw_err or "工具执行失败")
        code = data.get("code") or infer_error_code(message)
    if code not in ALL_CODES:
        code = infer_error_code(message)

    detail = {k: v for k, v in data.items() if k not in ("ok", "error", "code")}
    payload: dict = {"ok": False, "error": {"code": code, "message": message}}
    if detail:
        payload["detail"] = detail

    try:
        from mcp.types import CallToolResult, TextContent

        text = TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))
        # mcp 1.x 字段名 isError，2.x 为 is_error —— 按实际字段名构造（否则被 pydantic 静默忽略）
        flds = getattr(CallToolResult, "model_fields", None) or getattr(
            CallToolResult, "__fields__", {}) or {}
        kwargs: dict = {"content": [text]}
        if "is_error" in flds:
            kwargs["is_error"] = True
        elif "isError" in flds:
            kwargs["isError"] = True
        else:
            kwargs["is_error"] = True
        return CallToolResult(**kwargs)
    except Exception:
        return result
