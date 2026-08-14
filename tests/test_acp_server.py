"""ACP server / 协议 / 鉴权 端到端测试（无需真实 LLM 或网络）。

运行：在 src 父目录下 ``python -m pytest tests/test_acp_server.py -q``。
采用 asyncio.run 包裹异步用例，兼容未安装 pytest-asyncio 的环境。
"""

import asyncio
import json
import os
import sys

# 让 tests/ 能 import src 下的 memory_worker 包
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

import memory_worker.acp_server as acp  # noqa: E402
from memory_worker.acp_protocol import sse_message  # noqa: E402
from memory_worker.acp_auth import ACPTokenMiddleware  # noqa: E402
from memory_worker.api.debug_routes import DebugRun  # noqa: E402

import pytest  # noqa: E402


# ── 1. 工具目录 ──────────────────────────────────────────────────────────
def test_build_acp_tools_contains_delegate_and_builtin():
    tools = acp.build_acp_tools()
    names = {t["name"] for t in tools}
    assert "delegate_to_autoflow" in names
    # builtin 暴露的只读工具应被包含
    from memory_worker.tool_schema import build_openai_tools

    builtin = {t["function"]["name"] for t in build_openai_tools(("builtin",))}
    assert builtin <= names
    # 每个工具具备 ACP 形状
    for t in tools:
        assert set(t.keys()) >= {"name", "description", "inputSchema"}
        assert t["inputSchema"]["type"] == "object"


# ── 2. 指令提取 ────────────────────────────────────────────────────────
def test_extract_instruction_variants():
    assert acp._extract_instruction({"prompt": "直接指令"}) == "直接指令"
    assert (
        acp._extract_instruction(
            {"messages": [{"role": "user", "content": "你好"}, {"role": "user", "content": "最后一条"}]}
        )
        == "最后一条"
    )
    assert acp._extract_instruction({"messages": [{"role": "assistant", "content": "x"}]}) == "x"
    assert acp._extract_instruction({}) == ""


# ── 3. 内部事件 → ACP content block 映射 ───────────────────────────────
def _notif(ev, data, sid="s1", rid=1):
    return acp._event_to_notification(ev, data, sid, rid)


def test_event_mapping_tool_call():
    n = _notif("tool_call", {"name": "get_member_persona", "arguments": {"x": 1}, "result": "r"})
    assert n["method"] == "session_update"
    assert n["params"]["status"] == "running"
    blk = n["params"]["content"][0]
    assert blk["type"] == "tool_call"
    assert blk["name"] == "get_member_persona"
    assert blk["arguments"] == {"x": 1}
    assert blk["result"] == "r"


def test_event_mapping_done():
    n = _notif("done", {"answer": "最终答案"})
    assert n["params"]["status"] == "completed"
    assert n["params"]["content"][0] == {"type": "text", "text": "最终答案"}


def test_event_mapping_aborted_error():
    assert _notif("aborted", {"message": "x"})["params"]["status"] == "aborted"
    assert _notif("error", {"message": "boom"})["params"]["status"] == "error"


def test_event_mapping_unknown_returns_none():
    assert _notif("garbage", {}) is None


# ── 4. JSON-RPC 分发（非流式方法，无需 rt） ─────────────────────────────
def test_handle_initialize():
    resp, gen = asyncio.run(
        acp.acp_handle(None, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    )
    assert gen is None
    assert resp["result"]["agent"]["name"] == "memory-worker"
    assert resp["result"]["capabilities"]["streaming"] is True
    assert any(t["name"] == "delegate_to_autoflow" for t in resp["result"]["tools"])


def test_handle_session_lifecycle():
    async def _run():
        _, _ = await acp.acp_handle(None, {"id": 1, "method": "session.new", "params": {}})
        # session.new 返回了 sessionId；这里直接用返回的 resp
        r, _ = await acp.acp_handle(None, {"id": 2, "method": "session.new", "params": {}})
        sid = r["result"]["sessionId"]
        lst, _ = await acp.acp_handle(None, {"id": 3, "method": "session.list", "params": {}})
        assert any(s["sessionId"] == sid for s in lst["result"]["sessions"])
        # 取消不存在的 run 应报会话无任务（而不是崩溃）
        err, _ = await acp.acp_handle(
            None, {"id": 4, "method": "cancel", "params": {"sessionId": sid}}
        )
        assert err["error"]["code"] == -32001
        # 删除
        d, _ = await acp.acp_handle(
            None, {"id": 5, "method": "session.delete", "params": {"sessionId": sid}}
        )
        assert d["result"]["deleted"] is True

    asyncio.run(_run())


def test_handle_unknown_method():
    resp, _ = asyncio.run(
        acp.acp_handle(None, {"jsonrpc": "2.0", "id": 1, "method": "nope", "params": {}})
    )
    assert resp["error"]["code"] == -32601


# ── 5. prompt 流式（mock _execute_run，验证事件→SSE 透传） ──────────────
def test_prompt_streams_events():
    async def fake_execute_run(rt, run, tools=None, run_tool=None):
        run.emit("tool_call", {"name": "get_member_persona", "arguments": {}, "result": "ok"})
        run.emit("done", {"answer": "最终答案"})

    async def _run():
        orig = acp._execute_run
        acp._execute_run = fake_execute_run
        try:
            _, gen = await acp.acp_handle(
                None,
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "prompt",
                    "params": {"messages": [{"role": "user", "content": "hi"}]},
                },
            )
            chunks = []
            async for c in gen:
                chunks.append(c)
        finally:
            acp._execute_run = orig

        # 解析 SSE：每条应为 event: message
        assert chunks and all("event: message" in c for c in chunks)
        last = json.loads(chunks[-1].split("data: ", 1)[1])
        assert last["method"] == "session_update"
        assert last["params"]["status"] == "completed"
        assert last["params"]["content"][0]["text"] == "最终答案"
        # sessionId 应回传给客户端
        assert last["params"]["sessionId"]

    asyncio.run(_run())


# ── 6. ACPTokenMiddleware ───────────────────────────────────────────────
class _FakeStore:
    def __init__(self, valid=False, kind=None):
        self._valid = valid
        self._kind = kind

    def verify(self, token):
        return "tokname" if self._valid else None

    def kind(self, name):
        return self._kind


def _make_scope(method="POST", headers=None, path="/acp"):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers or [],
        "query_string": b"",
    }


async def _fake_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _collect(messages):
    statuses = [m["status"] for m in messages if m["type"] == "http.response.start"]
    return statuses


def _make_send(messages):
    """构造 ASGI send：必须是 async（返回 awaitable），否则 middleware 的 await send(...) 会失败。"""

    async def _s(m):
        messages.append(m)

    return _s


def test_auth_missing_token():
    async def _run():
        messages = []
        mw = ACPTokenMiddleware(_fake_app, lambda: _FakeStore())
        await mw(
            _make_scope(headers=[(b"x", b"y")]),
            None,
            _make_send(messages),
        )
        assert _collect(messages)[0] == 401

    asyncio.run(_run())


def test_auth_wrong_kind():
    async def _run():
        messages = []
        mw = ACPTokenMiddleware(
            _fake_app, lambda: _FakeStore(valid=True, kind="mcp")
        )
        await mw(
            _make_scope(headers=[(b"authorization", b"Bearer acp_xxx")]),
            None,
            _make_send(messages),
        )
        assert _collect(messages)[0] == 403

    asyncio.run(_run())


def test_auth_ok():
    async def _run():
        messages = []
        mw = ACPTokenMiddleware(
            _fake_app, lambda: _FakeStore(valid=True, kind="acp")
        )
        await mw(
            _make_scope(headers=[(b"authorization", b"Bearer acp_xxx")]),
            None,
            _make_send(messages),
        )
        assert _collect(messages)[0] == 200

    asyncio.run(_run())


def test_auth_options_passthrough():
    async def _run():
        messages = []
        mw = ACPTokenMiddleware(
            _fake_app, lambda: _FakeStore(valid=False)
        )
        await mw(
            _make_scope(method="OPTIONS", headers=[]),
            None,
            _make_send(messages),
        )
        assert _collect(messages)[0] == 200

    asyncio.run(_run())


# ── 7. 协议信封 ─────────────────────────────────────────────────────────
def test_sse_message_envelope():
    s = sse_message({"jsonrpc": "2.0", "method": "session_update", "params": {}})
    assert s.startswith("event: message\ndata: ")
    assert s.endswith("\n\n")
    assert json.loads(s[len("event: message\ndata: ") :].strip())["method"] == "session_update"
