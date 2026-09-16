"""调试用 LLM 运行控制接口（R1~R4）。

设计要点：
- 触发接口 ``POST /api/debug/llm/run`` 立即返回 ``run_id``（202），
  后台 ``asyncio`` 任务执行工具循环，便于从外部 abort / 轮询状态。
- 工具循环在生成器内**逐轮 yield** ``tool_call`` 事件（含 name/arguments/
  result/llm_raw），边跑边吐 trace，并在每轮前检查取消标志（R2/R3）。
- ``dry_run`` 模式：LLM 正常产出 tool_calls，但**不真正执行工具**，
  仅把拟调用列表留在 tool_trace 中，用于验证 deepseek2api 的 function call 解析。
- 调试令牌以 ``dbg_`` 前缀、kind=``debug`` 存储于既有 token 库，复用 require_user；
  仅限 /api 调试接口，与 mcp_ 令牌（仅 /mcp）隔离。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
import uuid

# 审计 O8：调试端点的完整 traceback 仅 MA_DEBUG_TRACEBACK=1 时回传；
# 生产默认只回异常摘要，避免已认证的普通用户看到内部栈。
_DEBUG_TRACEBACK = os.getenv("MA_DEBUG_TRACEBACK", "").strip().lower() in ("1", "true", "yes", "on")


def _tb() -> str:
    """按 MA_DEBUG_TRACEBACK 决定是否附带完整栈（审计 O8）。"""
    return traceback.format_exc() if _DEBUG_TRACEBACK else ""

from starlette.requests import Request
from starlette.routing import Route
from starlette.responses import JSONResponse

from ..skills import skill_prompt_for
from .deps import (
    error,
    json_body,
    ok,
    require_admin,
    require_user,
    runtime,
    sse_pack,
    sse_response,
)
from .llm_routes import MEMORY_TOOLS, MAX_TOOL_RESULT_CHARS, _run_memory_tool

# ── 运行态存储 ───────────────────────────────────────────────────────────────
_RUNS: dict[str, "DebugRun"] = {}
_CONV: dict[str, list] = {}  # conversation_id -> 完整 messages 上下文
_MAX_RUNS = 200

_TERMINAL = object()  # 内部哨兵：标记流结束（不进 history）


class DebugRun:
    def __init__(
        self,
        *,
        run_id: str,
        instruction: str,
        model,
        mode: str,
        max_rounds: int,
        conversation_id,
        temperature,
    ) -> None:
        self.run_id = run_id
        self.instruction = instruction
        self.model = model
        self.mode = mode
        self.max_rounds = max_rounds
        self.conversation_id = conversation_id
        self.temperature = temperature

        self.status = "running"  # running | done | aborted | error
        self.cancelled = False
        self.terminal = False  # 流是否已结束（done/aborted/error）
        self.history: list[str] = []  # 已完成事件的 SSE 文本，供订阅前回放
        self.subscribers: list[asyncio.Queue] = []
        self.tool_trace: list[dict] = []
        self.final_answer: str | None = None
        self.rounds_done = 0
        self.error: str | None = None
        self.created_at = time.time()

    def emit(self, event_type: str, data: dict) -> None:
        packed = sse_pack(event_type, data)
        self.history.append(packed)
        for q in list(self.subscribers):
            try:
                q.put_nowait(packed)
            except Exception:
                pass

    def finish(self) -> None:
        if self.terminal:
            return
        self.terminal = True
        for q in list(self.subscribers):
            try:
                q.put_nowait(_TERMINAL)
            except Exception:
                pass

    def abort(self) -> None:
        self.cancelled = True


def _register(run: DebugRun) -> None:
    _RUNS[run.run_id] = run
    if len(_RUNS) > _MAX_RUNS:
        finished = sorted(
            (r for r in _RUNS.values() if r.terminal),
            key=lambda r: r.created_at,
        )
        for r in finished[: len(_RUNS) - _MAX_RUNS]:
            _RUNS.pop(r.run_id, None)


async def _execute_run(rt, run: DebugRun, tools=None, run_tool=None) -> None:
    """后台任务：逐轮跑工具循环，边跑边 emit 结构化事件。

    ``tools`` / ``run_tool`` 为可选覆盖项：ACP server 通过它们注入专属工具表
    （builtin 集 + delegate_to_autoflow），默认行为与内置 LLM 完全一致。
    """
    try:
        # 续轮：加载既有对话上下文并追加本次指令
        if run.conversation_id and run.conversation_id in _CONV:
            messages = list(_CONV[run.conversation_id])
            messages.append({"role": "user", "content": run.instruction})
        else:
            messages = [
                {"role": "system", "content": skill_prompt_for(rt)},
                {"role": "user", "content": run.instruction},
            ]

        final_answer: str | None = None

        for i in range(run.max_rounds):
            if run.cancelled:
                run.status = "aborted"
                run.emit("aborted", {"message": "已在轮次间中止", "round": i})
                break

            try:
                resp = await rt.llm.chat(
                    messages,
                    model=run.model,
                    temperature=run.temperature,
                    tools=tools if tools is not None else MEMORY_TOOLS,
                )
            except Exception as exc:
                run.status = "error"
                run.error = str(exc)
                run.emit("error", {"message": f"LLM 调用失败: {exc}"})
                break

            backend = resp.get("_backend")
            if backend:
                run.emit(
                    "backend",
                    {
                        "model": backend.get("model"),
                        "name": backend.get("name"),
                        "provider": backend.get("provider"),
                    },
                )

            tool_calls = resp.get("tool_calls") or []
            if not tool_calls:
                final_answer = resp.get("content") or ""
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": resp.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )
            llm_raw = resp.get("_raw")  # 该轮 LLM 原始输出，用于排查 JSON 解析失败

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}") or "{}")
                except Exception:
                    args = {}

                if run.mode == "dry_run":
                    # 只读分析工具多为零副作用；dry_run 彻底不执行，仅记录拟调用
                    result_disp = None
                    tool_content = "(dry_run 未执行)"
                else:
                    result = await (run_tool or _run_memory_tool)(rt, name, args)
                    tool_content = json.dumps(result, ensure_ascii=False, default=str)
                    if len(tool_content) > MAX_TOOL_RESULT_CHARS:
                        tool_content = (
                            tool_content[:MAX_TOOL_RESULT_CHARS]
                            + "\n...(结果过长已截断)"
                        )
                    result_disp = tool_content

                run.tool_trace.append(
                    {
                        "round": i,
                        "name": name,
                        "arguments": args,
                        "result": result_disp,
                    }
                )
                run.emit(
                    "tool_call",
                    {
                        "round": i,
                        "name": name,
                        "arguments": args,
                        "result": result_disp,
                        "llm_raw": llm_raw,
                    },
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "name": name,
                        "content": tool_content,
                    }
                )

            run.rounds_done = i + 1
        else:
            # 达到最大轮数仍未收敛：提示模型直接作答收尾
            messages.append(
                {
                    "role": "system",
                    "content": "工具调用已达到最大轮数，请基于已有信息直接回答用户问题。",
                }
            )
            try:
                resp = await rt.llm.chat(
                    messages,
                    model=run.model,
                    temperature=run.temperature,
                )
                final_answer = resp.get("content") or ""
            except Exception as exc:
                run.status = "error"
                run.error = str(exc)
                run.emit("error", {"message": f"收尾作答失败: {exc}"})

        if run.status == "running":
            run.status = "done"
            run.final_answer = final_answer
            run.emit("done", {"answer": final_answer, "tool_trace": run.tool_trace})

        if run.conversation_id:
            _CONV[run.conversation_id] = messages

        run.finish()

    except asyncio.CancelledError:
        run.status = "aborted"
        run.emit("aborted", {"message": "后台任务被取消"})
        run.finish()
    except Exception as exc:
        run.status = "error"
        run.error = str(exc)
        run.emit("error", {"message": str(exc)})
        run.finish()


# ── HTTP 接口 ────────────────────────────────────────────────────────────────


async def debug_run(request: Request):
    """R1 触发接口：202 立即返回 run_id，后台异步启动。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    instruction = (body.get("instruction") or "").strip()
    if not instruction:
        return error("instruction 不能为空")
    model = body.get("model") or None
    mode = body.get("mode") or "run"
    if mode not in ("run", "dry_run"):
        mode = "run"

    max_rounds = body.get("max_tool_rounds") or 3
    try:
        max_rounds = int(max_rounds)
    except (TypeError, ValueError):
        max_rounds = 3
    max_rounds = max(1, min(max_rounds, 20))  # 调试可设 1~2 快速止血，硬上限兜底

    conversation_id = body.get("conversation_id") or None
    temperature = body.get("temperature")
    try:
        temperature = float(temperature) if temperature is not None else None
    except (TypeError, ValueError):
        temperature = None

    rt = runtime(request)
    run_id = uuid.uuid4().hex
    run = DebugRun(
        run_id=run_id,
        instruction=instruction,
        model=model,
        mode=mode,
        max_rounds=max_rounds,
        conversation_id=conversation_id,
        temperature=temperature,
    )
    _register(run)
    asyncio.create_task(_execute_run(rt, run))
    return JSONResponse(
        {
            "run_id": run_id,
            "status": "running",
            "mode": mode,
            "stream_url": f"/api/debug/llm/run/{run_id}/stream",
            "abort_url": f"/api/debug/llm/run/{run_id}/abort",
        },
        status_code=202,
    )


async def debug_stream(request: Request):
    """R2 可观测：SSE 实时推送 backend/tool_call/done/aborted/error 事件。"""
    try:
        _, err = require_user(request)
        if err:
            return err
        run_id = request.path_params.get("run_id", "")
        run = _RUNS.get(run_id)
        if run is None:
            return error("run 不存在", 404)

        async def gen():
            # 先订阅再取快照，避免漏发/重复；订阅后产生的事件只会进 own 队列
            own = asyncio.Queue()
            run.subscribers.append(own)
            snapshot = list(run.history)
            try:
                for packed in snapshot:
                    yield packed
                if run.terminal:
                    return
                while True:
                    item = await own.get()
                    if item is _TERMINAL:
                        break
                    yield item
            finally:
                try:
                    run.subscribers.remove(own)
                except ValueError:
                    pass

        return sse_response(gen())
    except Exception as exc:
        return error(f"debug_stream 内部异常: {exc}\n{_tb()}", 500)


async def debug_status(request: Request):
    """R4 状态查询：status / 已用轮数 / trace 摘要。"""
    try:
        _, err = require_user(request)
        if err:
            return err
        run_id = request.path_params.get("run_id", "")
        run = _RUNS.get(run_id)
        if run is None:
            return error("run 不存在", 404)
        return ok(
            {
                "run_id": run_id,
                "status": run.status,
                "mode": run.mode,
                "max_rounds": run.max_rounds,
                "rounds_done": run.rounds_done,
                "instruction": run.instruction,
                "answer": run.final_answer,
                "tool_trace": run.tool_trace,
                "error": run.error,
            }
        )
    except Exception as exc:  # 调试端点必须暴露真实异常，而非吞成裸 500
        return error(f"debug_status 内部异常: {exc}\n{_tb()}", 500)


async def debug_abort(request: Request):
    """R3 中止：置取消标志，工具循环在下一轮前退出。"""
    try:
        _, err = require_user(request)
        if err:
            return err
        run_id = request.path_params.get("run_id", "")
        run = _RUNS.get(run_id)
        if run is None:
            return error("run 不存在", 404)
        if run.status != "running":
            return ok(
                {"message": f"run 已结束（{run.status}），无需中止", "status": run.status}
            )
        run.abort()
        return ok({"message": "已请求中止，将在下一轮工具调用前停止", "status": "aborting"})
    except Exception as exc:
        return error(f"debug_abort 内部异常: {exc}\n{_tb()}", 500)


# ── dbg_ 调试令牌管理（需管理员，区别于 mcp_ 令牌） ───────────────────────────


async def debug_create_token(request: Request):
    """生成 dbg_ 前缀、kind=debug 的长期调试令牌。明文仅本次返回。"""
    _, err = require_admin(request)
    if err:
        return err
    body = await json_body(request)
    name = (body.get("name") or "").strip()
    if not name:
        return error("缺少 name")
    rt = runtime(request)
    result = rt.tokens.generate(name, prefix="dbg_", kind="debug")
    if not result.get("ok"):
        return error(result.get("error", "生成失败"))
    return ok(
        {
            "name": result["name"],
            "token": result["token"],
            "prefix": result["prefix"],
            "created_at": result["created_at"],
            "message": "dbg_ 调试令牌已生成，请立即复制保存，页面关闭后无法再次查看",
        }
    )


async def debug_list_tokens(request: Request):
    _, err = require_admin(request)
    if err:
        return err
    items = [
        t
        for t in runtime(request).tokens.list_tokens()
        if t.get("kind") == "debug"
    ]
    return ok({"tokens": items})


async def debug_revoke_token(request: Request):
    _, err = require_admin(request)
    if err:
        return err
    body = await json_body(request)
    name = (body.get("name") or request.query_params.get("name") or "").strip()
    if not name:
        return error("缺少 name")
    if not runtime(request).tokens.revoke(name):
        return error("Token 不存在", 404)
    return ok({"message": f"已撤销调试令牌: {name}"})


ROUTES = [
    # 调试令牌（管理员）
    Route("/api/debug/tokens", debug_list_tokens, methods=["GET"]),
    Route("/api/debug/tokens", debug_create_token, methods=["POST"]),
    Route("/api/debug/tokens/revoke", debug_revoke_token, methods=["POST"]),
    # LLM 运行控制
    Route("/api/debug/llm/run", debug_run, methods=["POST"]),
    Route("/api/debug/llm/run/{run_id}", debug_status, methods=["GET"]),
    Route("/api/debug/llm/run/{run_id}/stream", debug_stream, methods=["GET"]),
    Route("/api/debug/llm/run/{run_id}/abort", debug_abort, methods=["POST"]),
]
