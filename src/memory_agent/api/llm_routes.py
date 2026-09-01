"""内置 LLM 路由：通用对话（流式）与行为分析（流式）"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import AsyncIterator

from starlette.requests import Request
from starlette.routing import Route
from starlette.responses import JSONResponse

from ..llm_client import normalize_chat_url
from ..store import now_local
from ..tool_schema import build_openai_tools, dispatch
from ..skills import skill_prompt_for
from ..voice_util import (
    ask_cache_key,
    classify_intent,
    match_device,
    normalize_for_cache,
    render_device_usage,
    resolve_window,
    strip_wake_word,
    _seconds_from_human,
)
from .deps import error, json_body, ok, require_user, runtime, sse_pack, sse_response
from .insight_routes import build_insight

MAX_HISTORY_MESSAGES = 30
MAX_TOOL_ROUNDS = 3
MAX_TOOL_RESULT_CHARS = 6000
CANDIDATE_MODELS = [
    "glm-4.5-flash", "glm-4-flash", "glm-4-plus",
    "deepseek-chat", "deepseek-reasoner",
    "gpt-4o-mini", "qwen-plus",
]


async def llm_models(request: Request):
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    cfg = rt.config
    backends = [
        b for b in (cfg.llm_backends or [])
        if isinstance(b, dict) and b.get("enabled", True)
    ]
    models = []
    for b in backends:
        if b.get("model"):
            models.append(b["model"])
    models = list(dict.fromkeys(models + CANDIDATE_MODELS))
    primary = rt.llm.primary_model if backends else (cfg.llm_model or "")
    first = backends[0] if backends else {}
    return ok(
        {
            "current": primary,
            "models": [m for m in models if m],
            "provider": first.get("provider", cfg.llm_provider),
            "endpoint": normalize_chat_url(first.get("api_url") or cfg.llm_api_url),
            "temperature": cfg.llm_temperature,
            "configured": any(bool(b.get("api_key")) for b in backends)
            or bool((cfg.llm_api_key or "").strip()),
            "backends": [
                {
                    "name": b.get("name"),
                    "model": b.get("model"),
                    "provider": b.get("provider"),
                    "enabled": b.get("enabled", True),
                }
                for b in backends
            ],
        }
    )


def _sanitize_messages(raw) -> list[dict]:
    """清洗对话历史，同时保留 Function Calling 所需的 tool 消息与 tool_calls。

    注意：tool 消息的 content 可能是很大的 JSON 字符串（工具返回），这里不截断，
    截断在工具执行处完成。
    """
    messages: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role == "tool":
            messages.append({
                "role": "tool",
                "tool_call_id": item.get("tool_call_id", ""),
                "name": item.get("name", ""),
                "content": str(item.get("content", "")),
            })
            continue
        if role not in ("system", "user", "assistant"):
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        clean = {"role": role, "content": content}
        # 保留 assistant 端的工具调用声明，供下一轮补全 tool 结果
        if role == "assistant" and item.get("tool_calls"):
            clean["tool_calls"] = item["tool_calls"]
        messages.append(clean)
    # 只保留最近若干轮，避免上下文无限增长打爆 token 预算
    return messages[-MAX_HISTORY_MESSAGES:]


# ── Function Calling 工具表（OpenAI 兼容格式）─────────────────────────────
# 把 memory-agent InsightService 的查询方法直接暴露给 LLM，由模型自己决定
# 调哪个工具、怎么传参，取代之前脆弱的关键词硬编码路由。
# 内置 LLM 的工具表：从单一 schema（tool_schema.py）生成，与 MCP 共用同一份定义。
# 之前这里硬编码 5 个工具且缺 ask_memory，是"内置答不出、MCP 能答"割裂的根因；
# 现在内置暴露与 MCP 对等的只读查询工具集（13 个，expose 含 'builtin'），
# 新增工具只需在 tool_schema.TOOL_SPECS 加一条，两边自动同步。
MEMORY_TOOLS: list[dict] = build_openai_tools(("builtin",))

# 工具名 -> 后端方法、参数白名单、派发逻辑现统一收口到 tool_schema.py：
# - 工具定义：tool_schema.TOOL_SPECS
# - 参数过滤 + include_timeline 兜底：tool_schema.dispatch
# - 内置与 MCP 共用同一份，杜绝"同一问题一边能答一边答不出"的割裂。

async def _run_memory_tool(rt, name: str, args: dict) -> dict:
    """执行 LLM 选定的 memory-agent 工具，返回结果 dict。

    统一委托 tool_schema.dispatch：工具表与派发逻辑现已收口到单一 schema
    （tool_schema.py），内置 LLM 与 MCP 共用同一份定义。
    """
    return await dispatch(rt, name, args)


async def llm_chat(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    messages = _sanitize_messages(body.get("messages"))
    if not messages:
        return error("messages 不能为空")

    rt = runtime(request)
    model = body.get("model") or None
    temperature = body.get("temperature")
    try:
        temperature = float(temperature) if temperature is not None else None
    except (TypeError, ValueError):
        temperature = None

    last_user = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )

    # 注入内置 LLM 技能提示词：任务策略 + 本家庭真实房间清单 + 工具用法指引。
    # 若前端已传 system 消息则追加，否则新建一条，避免覆盖前端设定。
    skill_prompt = skill_prompt_for(rt)
    if messages and messages[0].get("role") == "system":
        messages[0] = {"role": "system", "content": messages[0]["content"] + "\n\n" + skill_prompt}
    else:
        messages.insert(0, {"role": "system", "content": skill_prompt})

    # 仅当疑似数据查询时启用 Function Calling，否则直接流式对话。
    # agent loop 支持多轮工具调用：LLM 可能先查目录，再查用量，最后总结。
    final_response: dict | None = None
    if True:  # 始终携带工具，由模型自行决定是否调用（消除与 MCP 的能力割裂）
        for _round in range(MAX_TOOL_ROUNDS):
            try:
                response = await rt.llm.chat(
                    messages, model=model, temperature=temperature, tools=MEMORY_TOOLS
                )
            except Exception as exc:
                # 工具调用阶段异常时回退到普通流式生成，由 LLM 直接回答或道歉
                _logger.warning("Tool-call chat failed: %s", exc)
                final_response = None
                break

            tool_calls = response.get("tool_calls") or []
            if not tool_calls:
                # 模型不再调用工具，拿到最终回答
                final_response = response
                break

            messages.append({
                "role": "assistant",
                "content": response.get("content") or "",
                "tool_calls": tool_calls,
            })
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}") or "{}")
                except Exception:
                    args = {}
                result = await _run_memory_tool(rt, name, args)
                result_str = json.dumps(result, ensure_ascii=False, default=str)
                if len(result_str) > MAX_TOOL_RESULT_CHARS:
                    result_str = result_str[:MAX_TOOL_RESULT_CHARS] + "\n...(结果过长已截断)"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": name,
                    "content": result_str,
                })
        else:
            # 达到最大轮数仍未收敛，提示模型直接作答
            messages.append({
                "role": "system",
                "content": "工具调用已达到最大轮数，请基于已有信息直接回答用户问题。",
            })

    async def generator() -> AsyncIterator[str]:
        yield sse_pack("start", {"model": model or rt.llm.primary_model})
        try:
            if final_response is not None:
                # 工具调用已收敛，用非流式拿到的最终回答模拟流式输出
                backend = final_response.get("_backend")
                if backend:
                    yield sse_pack(
                        "backend",
                        {
                            "model": backend.get("model"),
                            "name": backend.get("name"),
                            "provider": backend.get("provider"),
                        },
                    )
                reasoning = final_response.get("reasoning", "")
                content = final_response.get("content", "")
                if reasoning:
                    yield sse_pack("reasoning", {"delta": reasoning})
                for i in range(0, len(content), 4):
                    yield sse_pack("content", {"delta": content[i : i + 4]})
                if final_response.get("truncated"):
                    yield sse_pack(
                        "truncated",
                        {"message": "模型输出已达到长度上限，结论可能不完整。"},
                    )
                yield sse_pack("usage", final_response.get("usage", {}))
                yield sse_pack("done", {})
            else:
                async for chunk in rt.llm.stream_chat(
                    messages, model=model, temperature=temperature
                ):
                    kind = chunk.get("type")
                    if kind == "backend":
                        yield sse_pack(
                            "backend",
                            {
                                "model": chunk.get("model"),
                                "name": chunk.get("name"),
                                "provider": chunk.get("provider"),
                            },
                        )
                    elif kind in ("content", "reasoning"):
                        yield sse_pack(kind, {"delta": chunk.get("delta", "")})
                    elif kind == "usage":
                        yield sse_pack("usage", chunk.get("usage", {}))
                    elif kind == "truncated":
                        yield sse_pack(
                            "truncated",
                            {"message": chunk.get("delta", "")},
                        )
                    elif kind == "error":
                        yield sse_pack("error", {"message": chunk.get("delta", "")})
                    elif kind == "done":
                        yield sse_pack("done", {})
        except asyncio.CancelledError:
            # 客户端断开：已发过 http.response.start，禁止再向 Starlette 抛错，
            # 否则 ServerErrorMiddleware 会重复发 start -> RuntimeError -> 前端空白。
            return
        except Exception as exc:
            # 仅当连接仍存活时才回传错误，避免向已关闭连接写入
            try:
                yield sse_pack("error", {"message": str(exc)})
            except (asyncio.CancelledError, RuntimeError):
                return

    return sse_response(generator)


async def llm_analyze(request: Request):
    """行为分析：先从 SQLite 聚合数字画像，再交给 LLM 流式产出洞察。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    rt = runtime(request)

    rooms = body.get("rooms") if isinstance(body.get("rooms"), list) else None
    person = (body.get("person") or "").strip()
    focus = (body.get("focus") or "").strip()
    model = body.get("model") or None
    try:
        temperature = float(body["temperature"]) if "temperature" in body else None
    except (TypeError, ValueError):
        temperature = None

    start, end = rt.analysis.resolve_range(
        (body.get("start_day") or "").strip(),
        (body.get("end_day") or "").strip(),
        int(body.get("days") or 7),
    )

    async def generator() -> AsyncIterator[str]:
        try:
            yield sse_pack("start", {"range": {"start": start, "end": end}})
            digest = await asyncio.to_thread(
                rt.analysis.build_digest, start, end, rooms
            )
            yield sse_pack(
                "digest",
                {
                    "total_events": digest["total_events"],
                    "days_covered": digest["days_covered"],
                    "rooms": digest["rooms"],
                    "top_entities": digest["top_entities"][:10],
                },
            )
            if digest["total_events"] <= 0:
                yield sse_pack(
                    "error",
                    {"message": "所选范围内没有事件数据，请先执行数据采集或调整时间范围"},
                )
                return

            async for chunk in rt.analysis.stream_analyze(
                digest, focus=focus, person=person, model=model, temperature=temperature
            ):
                kind = chunk.get("type")
                if kind in ("content", "reasoning"):
                    yield sse_pack(kind, {"delta": chunk.get("delta", "")})
                elif kind == "usage":
                    yield sse_pack("usage", chunk.get("data", {}))
                elif kind == "error":
                    yield sse_pack("error", {"message": chunk.get("delta", "")})
                elif kind == "insight":
                    yield sse_pack("insight", chunk.get("data", {}))
                elif kind == "done":
                    yield sse_pack("done", {})
        except asyncio.CancelledError:
            # 客户端断开：已发过 http.response.start，禁止再向 Starlette 抛错，
            # 否则 ServerErrorMiddleware 会重复发 start -> RuntimeError -> 前端空白。
            return
        except Exception as exc:
            # 仅当连接仍存活时才回传错误，避免向已关闭连接写入
            try:
                yield sse_pack("error", {"message": str(exc)})
            except (asyncio.CancelledError, RuntimeError):
                return

    return sse_response(generator)


async def llm_analyze_preview(request: Request):
    """只算画像不调 LLM，用于前端预览「将要喂给模型的数据规模」。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    rt = runtime(request)
    rooms = body.get("rooms") if isinstance(body.get("rooms"), list) else None
    start, end = rt.analysis.resolve_range(
        (body.get("start_day") or "").strip(),
        (body.get("end_day") or "").strip(),
        int(body.get("days") or 7),
    )
    digest = await asyncio.to_thread(rt.analysis.build_digest, start, end, rooms)
    text = await asyncio.to_thread(rt.analysis.render_digest, digest)
    return ok(
        {
            "range": digest["range"],
            "total_events": digest["total_events"],
            "days_covered": digest["days_covered"],
            "rooms": digest["rooms"],
            "digest_chars": len(text),
            "preview": text[:4000],
        }
    )


async def llm_analyze_save(request: Request):
    """把分析产出的洞察草稿存为模板。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    insight = body.get("insight") if isinstance(body.get("insight"), dict) else body
    if not insight.get("id") or not insight.get("name"):
        return error("洞察缺少 id 或 name")
    try:
        saved = runtime(request).templates.save(build_insight(insight))
    except Exception as exc:
        return error(f"保存失败: {exc}")
    return ok({"message": f"已存为模板: {saved.name}", "id": saved.id})


_VOICE_SYSTEM_PROMPT = (
    "你是家庭行为助理「贾维斯」，回答关于智能家居设备使用情况的问题。"
    "基于工具返回的数据，用简洁、口语化、适合语音播报的中文作答，"
    "不要列表、不要 Markdown、不要解释过程，一句话说完。"
)


def _clean_speak(text: str) -> str:
    """把 LLM 文本整理成适合 TTS 的纯中文短句。"""
    if not text:
        return ""
    t = re.sub(r"[*`#>]", "", text)
    t = re.sub(r"\s+", "", t)
    return t[:120]


async def _llm_agent_answer(rt, question: str, model: str | None):
    """复用 memory-agent 工具的多轮 agent loop，返回 (文本, 工具名列表)。"""
    messages = [
        {"role": "system", "content": _VOICE_SYSTEM_PROMPT + "\n\n" + skill_prompt_for(rt)},
        {"role": "user", "content": question},
    ]
    final_text = "抱歉，我暂时无法回答这个问题。"
    tool_names: list[str] = []
    response = None
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = await rt.llm.chat(
                messages, model=model, temperature=0.3, tools=MEMORY_TOOLS
            )
        except Exception:
            break
        tool_calls = response.get("tool_calls") or []
        if not tool_calls:
            final_text = response.get("content") or final_text
            break
        messages.append({
            "role": "assistant",
            "content": response.get("content") or "",
            "tool_calls": tool_calls,
        })
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}") or "{}")
            except Exception:
                args = {}
            tool_names.append(name)
            result = await _run_memory_tool(rt, name, args)
            result_str = json.dumps(result, ensure_ascii=False, default=str)
            if len(result_str) > MAX_TOOL_RESULT_CHARS:
                result_str = result_str[:MAX_TOOL_RESULT_CHARS] + "\n...(结果过长已截断)"
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "name": name,
                "content": result_str,
            })
    return final_text, tool_names


async def llm_ask(request: Request):
    """语音问答：返回完整 JSON，供 Node-RED → 小爱 TTS 播报。

    高频「设备用量 + 时间窗口」类问题走确定性路径，直接调 insights 工具并模板渲染，
    **不调用 LLM**（并可命中精确答案缓存，亚秒级返回）。
    长尾 / 无法解析的问题回退到 LLM 多轮 agent loop。
    """
    _, err = require_user(request)
    if err:
        return err
    body = (await json_body(request)) or {}
    raw_q = (body.get("question") or body.get("q") or body.get("text") or "").strip()
    wake_word = (body.get("wake_word") or "").strip()
    model_override = (body.get("model") or "").strip() or None
    if not raw_q:
        return error("question 不能为空")

    rt = runtime(request)
    store = rt.store
    t0 = time.monotonic()

    clean = strip_wake_word(raw_q, wake_word)
    today = now_local(8.0)
    window = resolve_window(clean, today)
    device_match = match_device(clean, rt.insights)
    intent = classify_intent(clean)

    llm_used = False
    cached = False
    answer = speak = None
    data: dict = {}
    model = model_override

    # 临时调试：收集匹配候选供诊断
    from memory_agent.voice_util import _extract_device_query
    query_core = _extract_device_query(clean)
    candidates = []
    for eid, info in rt.insights.name_map().items():
        fn = (info.get("friendly_name") or "").strip()
        if not fn:
            continue
        if (query_core and query_core in fn) or (fn in clean):
            candidates.append({
                "entity_id": eid,
                "friendly_name": fn,
                "domain": info.get("domain"),
                "room": info.get("room"),
            })

    # ── 确定性路径：设备用量 + 时间窗口 ───────────────────────────────
    why_llm: str | None = None
    if window and device_match and intent == "device_usage":
        # 优先用 entity_id 精确查询，避免语义 query 把多个设备时间相加
        eid = device_match.get("entity_id") or ""
        room = device_match.get("room") or ""
        query_label = device_match.get("query") or ""
        if eid:
            cache_key = ask_cache_key("du", eid, window["start"][:10])
        elif room:
            cache_key = ask_cache_key("du", f"room:{room}", window["start"][:10])
        else:
            cache_key = ask_cache_key("du", query_label, window["start"][:10])

        hit = store.get_answer_cache(cache_key)
        if hit:
            cached = True
            blob = json.loads(hit["payload"])
            answer, speak, data = blob.get("answer"), blob.get("speak"), blob.get("data", {})
        else:
            if eid:
                usage = await asyncio.to_thread(
                    rt.insights.device_usage, eid, "", "", "", 7,
                    window["start"], window["end"],
                )
            elif room:
                usage = await asyncio.to_thread(
                    rt.insights.device_usage, "", room, "", "", 7,
                    window["start"], window["end"],
                )
            else:
                usage = await asyncio.to_thread(
                    rt.insights.device_usage, "", "", "", query_label, 7,
                    window["start"], window["end"],
                )
            rendered = render_device_usage(usage, window["label"], query_label)
            if rendered:
                answer, speak, data = (
                    rendered["answer"], rendered["speak"],
                    rendered.get("data", {}),
                )
                # 0 秒且无原始事件：大概率是实体没数据或不是想查的设备，
                # 给 why_llm 提示，方便用户诊断。
                raw_count = (data or {}).get("raw_event_count", 0)
                total_human = (data or {}).get("total_on_human") or "0分"
                if raw_count == 0 and _seconds_from_human(total_human) == 0:
                    why_llm = "no_on_events"
                store.save_answer_cache(
                    cache_key,
                    {"answer": answer, "speak": speak, "data": data},
                    intent="device_usage",
                )
            else:
                why_llm = "device_usage_empty"
    else:
        if not window:
            why_llm = "no_window"
        elif not device_match:
            why_llm = "no_device_match"
        elif intent != "device_usage":
            why_llm = "not_usage_intent"

    # ── 回退：先查 LLM 答案缓存，再跑 LLM ─────────────────────────────
    if answer is None:
        norm = normalize_for_cache(clean, today)
        llm_cache_key = ask_cache_key("llm", norm)
        llm_hit = store.get_answer_cache(llm_cache_key)
        if llm_hit:
            cached = True
            blob = json.loads(llm_hit["payload"])
            answer, speak, data = blob.get("answer"), blob.get("speak"), blob.get("data", {})
            if why_llm and isinstance(data, dict):
                data["why_llm"] = why_llm
        else:
            llm_used = True
            final_text, tool_names = await _llm_agent_answer(rt, clean, model_override)
            answer = final_text
            speak = _clean_speak(final_text)
            data = {"tool_calls": tool_names}
            if why_llm:
                data["why_llm"] = why_llm
            store.save_answer_cache(
                llm_cache_key,
                {"answer": answer, "speak": speak, "data": data},
                intent="llm",
            )

    latency = round((time.monotonic() - t0) * 1000)
    return JSONResponse({
        "ok": True,
        "question": raw_q,
        "answer": answer,
        "speak": speak,
        "data": {
            **(data or {}),
            "_debug": {
                "query_core": query_core,
                "device_match": device_match,
                "match_candidates": candidates,
            },
        },
        "cached": cached,
        "llm_used": llm_used,
        "model": model or "default",
        "latency_ms": latency,
    })


async def llm_cache_list(request: Request):
    """查看语音问答答案缓存。"""
    _, err = require_user(request)
    if err:
        return err
    try:
        limit = int(request.query_params.get("limit", "100"))
    except (TypeError, ValueError):
        limit = 100
    rows = runtime(request).store.list_answer_cache(limit)
    return ok({
        "count": len(rows),
        "items": [
            {
                "cache_key": r["cache_key"][:12],
                "intent": r["intent"],
                "hits": r["hits"],
                "last_used": r["last_used"],
                "answer": (json.loads(r["payload"]).get("answer") if r["payload"] else None),
            }
            for r in rows
        ],
    })


async def llm_cache_clear(request: Request):
    """清理语音问答答案缓存。?key=xxx 只删单条，否则全清。"""
    _, err = require_user(request)
    if err:
        return err
    key = request.query_params.get("key")
    deleted = runtime(request).store.clear_answer_cache(key)
    return ok({"deleted": deleted, "key": key})


ROUTES = [
    Route("/api/llm/models", llm_models, methods=["GET"]),
    Route("/api/llm/chat", llm_chat, methods=["POST"]),
    Route("/api/llm/ask", llm_ask, methods=["POST"]),
    Route("/api/llm/cache", llm_cache_list, methods=["GET"]),
    Route("/api/llm/cache", llm_cache_clear, methods=["DELETE"]),
    Route("/api/llm/analyze", llm_analyze, methods=["POST"]),
    Route("/api/llm/analyze/preview", llm_analyze_preview, methods=["POST"]),
    Route("/api/llm/analyze/save", llm_analyze_save, methods=["POST"]),
]
