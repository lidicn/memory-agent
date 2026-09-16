"""v0.9 任务3：MCP 可维护性契约测试（可在容器内 `python -m pytest tests/test_mcp_contract.py` 运行）。

覆盖三块：
1. 响应体上限与超限摘要（`_apply_response_cap`）；
2. 错误结果构造动态适配 mcp 1.x/2.x 字段名（`_build_tool_result`）；
3. 故障注入矩阵（`set_mcp_fault` / `_fault_result` / 分发层短路，不真正执行工具）。

均为纯函数 / 轻量协程，无需运行期 runtime 即可验证（审计落库失败被旁路 try 吞掉）。
"""
from __future__ import annotations

import asyncio

import pytest

from memory_agent import mcp_server as ms
from memory_agent.mcp_context import set_caller
from memory_agent.mcp_errors import ALL_CODES, ErrorCode, normalize_tool_result


def _make_result(text: str, is_err: bool = False):
    """构造一个近似 CallToolResult 的对象（避免强依赖 mcp 运行时版本字段名差异）。"""
    from mcp.types import CallToolResult, TextContent

    flds = getattr(CallToolResult, "model_fields", None) or getattr(
        CallToolResult, "__fields__", {}) or {}
    kwargs = {"content": [TextContent(type="text", text=text)]}
    if "is_error" in flds:
        kwargs["is_error"] = is_err
    elif "isError" in flds:
        kwargs["isError"] = is_err
    else:
        kwargs["is_error"] = is_err
    return CallToolResult(**kwargs)


def _is_err(r) -> bool:
    return bool(getattr(r, "is_error", False) or getattr(r, "isError", False))


def _text(r) -> str:
    for c in r.content:
        if getattr(c, "type", "") == "text":
            return str(c.text)
    return ""


# ── 1. 响应体上限 ────────────────────────────────────────────────────────────
def test_apply_response_cap_noop_under_limit():
    r = _make_result("hello world")
    out = ms._apply_response_cap(r, "x", max_bytes=100)
    assert _text(out) == "hello world"


def test_apply_response_cap_noop_when_disabled():
    r = _make_result("x" * 10000)
    out = ms._apply_response_cap(r, "x", max_bytes=0)
    assert _text(out) == "x" * 10000


def test_apply_response_cap_truncates_and_summarizes():
    big = "A" * 5000  # ~5KB
    r = _make_result(big)
    out = ms._apply_response_cap(r, "big_tool", max_bytes=1000)
    out_text = _text(out)
    # 原始正文被截到上限（1000 字节），摘要作为少量固定开销追加其后
    assert out_text.startswith("A" * 1000)
    assert "A" * 1001 not in out_text          # 超出上限的部分已被丢弃
    assert len(out_text.encode("utf-8")) <= 1000 + 300
    assert "响应已截断" in out_text
    assert "big_tool" in out_text


def test_apply_response_cap_preserves_error_flag():
    big = "E" * 5000
    r = _make_result(big, is_err=True)
    out = ms._apply_response_cap(r, "x", max_bytes=1000)
    assert _is_err(out) is True
    assert "响应已截断" in _text(out)


# ── 2. 错误结果构造动态字段 ──────────────────────────────────────────────────
def test_build_tool_result_is_error_flag():
    r = ms._build_tool_result("boom", is_error=True)
    # mcp 1.x 用 isError，2.x 用 is_error——统一兼容判定
    assert _is_err(r) is True
    assert _text(r) == "boom"


def test_normalize_contract_ok_false_becomes_is_error():
    payload = '{"ok": false, "error": "成员不存在"}'
    r = _make_result(payload)
    out = normalize_tool_result(r, "get_member")
    assert _is_err(out) is True
    assert "NOT_FOUND" in _text(out)


# ── 3. 故障注入矩阵 ──────────────────────────────────────────────────────────
def _fake_server():
    """会记录是否被真正调用；被调用即抛，确保故障注入短路生效。"""
    calls = {"n": 0}

    class Fake:
        async def call_tool(self, *a, **k):
            calls["n"] += 1
            raise AssertionError("工具不应被真正执行（故障注入应短路）")

    return Fake(), calls


@pytest.mark.parametrize("code", ALL_CODES)
def test_fault_injection_tool_specific_short_circuits(code):
    set_caller("verify", ["read", "write"], "test")
    ms.set_mcp_fault("get_member", code)
    try:
        fake, calls = _fake_server()
        r = asyncio.run(
            ms._tracked_call_tool(fake, "get_member", {})
        )
        assert calls["n"] == 0, "故障注入未短路，工具被真实调用"
        assert _is_err(r) is True
        assert code in _text(r)
    finally:
        ms.clear_mcp_faults()


def test_fault_injection_wildcard_applies_to_any_tool():
    set_caller("verify", ["read", "write"], "test")
    ms.set_mcp_fault("*", ErrorCode.UPSTREAM_UNAVAILABLE)
    try:
        fake, calls = _fake_server()
        r = asyncio.run(
            ms._tracked_call_tool(fake, "some_random_tool", {})
        )
        assert calls["n"] == 0
        assert _is_err(r) is True
        assert ErrorCode.UPSTREAM_UNAVAILABLE in _text(r)
    finally:
        ms.clear_mcp_faults()


def test_fault_injection_cleared_removes_short_circuit():
    set_caller("verify", ["read", "write"], "test")
    ms.set_mcp_fault("get_member", ErrorCode.INTERNAL)
    assert ms.list_mcp_faults().get("get_member") == ErrorCode.INTERNAL
    ms.clear_mcp_faults()
    assert ms.list_mcp_faults() == {}
    # 清除后故障分支不再命中；重新注入可再次短路，证明注册表双向可控、不依赖真实 MCPServer
    ms.set_mcp_fault("get_member", ErrorCode.DENIED)
    try:
        fake, calls = _fake_server()
        r = asyncio.run(ms._tracked_call_tool(fake, "get_member", {}))
        assert calls["n"] == 0
        assert ErrorCode.DENIED in _text(r)
    finally:
        ms.clear_mcp_faults()


def test_set_mcp_fault_rejects_unknown_code():
    with pytest.raises(ValueError):
        ms.set_mcp_fault("x", "NOT_A_REAL_CODE")
    ms.clear_mcp_faults()
