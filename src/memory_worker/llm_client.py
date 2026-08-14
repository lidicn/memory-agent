"""多厂商 LLM 代理池（fallback router）。

- ``LLMProvider`` : 单个后端（厂商 + key + 模型 + 参数），封装一次完整调用。
- ``LLMRouter``   : 有序后端列表，按优先级依次尝试；上游调用遇到 429 / 5xx /
  超时 / 鉴权失败，自动 fallback 到下一个可用后端，从而解决单一厂商限流
  （如智谱 HTTP 429 code 1302）导致的对话中断。

对外暴露的公开方法（chat / stream_chat / ping / reconfigure / close）与旧的
``LLMClient`` 保持一致，runtime 仅需把 ``LLMClient(self.config)`` 改为
``LLMRouter(self.config)`` 即可。
"""
from __future__ import annotations

import json
import asyncio
import httpx
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

try:
    from .config import Config
except ImportError:
    Config = None

HTTPX_AVAILABLE = True

DEFAULT_TIMEOUT = 120

_logger = logging.getLogger(__name__)


def normalize_chat_url(url: str) -> str:
    """规范化聊天补全接口地址，确保以 /chat/completions 结尾。"""
    if not url:
        return url
    url = url.strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return url + "/chat/completions"
    if url.endswith("/v1/"):
        return url + "chat/completions"
    return url + "/chat/completions"


def _mask_secret(secret: str, keep: int = 4) -> str:
    """对密钥做掩码显示。keep 指定尾部保留的明文位数。"""
    if not secret:
        return ""
    if len(secret) <= keep + 2:
        return "********"
    return "********" + secret[-keep:]


def _num(value: Any, default: float) -> float:
    """把可空字段安全地转成数值，失败回退 default。"""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class LLMError(Exception):
    """LLM 调用统一异常。router 捕获后触发 fallback。"""
    pass


def _resolve_endpoint(base: str) -> str:
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1") or base.endswith("/v1/"):
        return base.rstrip("/") + "/chat/completions"
    return base.rstrip("/") + "/chat/completions"


class LLMProvider:
    """单个 LLM 后端。参数优先取 backend 自身字段，缺失时回落到全局 config。"""

    def __init__(self, backend: Dict[str, Any], config: Optional["Config"] = None):
        self.config = config
        self.name = (backend.get("name") or backend.get("provider") or "未命名后端").strip()
        self.provider = (backend.get("provider") or "").strip()
        self.model = (backend.get("model") or "").strip()
        self.api_key = backend.get("api_key") or ""
        self.api_url = normalize_chat_url(backend.get("api_url") or "")
        cfg = config
        self.timeout = _num(
            backend.get("timeout"),
            _num(getattr(cfg, "llm_timeout", None), DEFAULT_TIMEOUT),
        )
        self.temperature = _num(
            backend.get("temperature"),
            _num(getattr(cfg, "llm_temperature", None), 0.7),
        )
        self.max_tokens = _num(
            backend.get("max_tokens"),
            _num(getattr(cfg, "llm_max_tokens", None), 4096),
        )
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def endpoint(self) -> str:
        return _resolve_endpoint(self.api_url)

    @property
    def label(self) -> str:
        return f"{self.name} · {self.model}" if self.model else self.name

    def _ensure_client(self) -> httpx.AsyncClient:
        if not HTTPX_AVAILABLE:
            raise LLMError("httpx 未安装，无法调用大模型接口")
        if self._client is None or self._client.is_closed:
            timeout_cfg = httpx.Timeout(self.timeout)
            self._client = httpx.AsyncClient(timeout=timeout_cfg)
        return self._client

    def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._client.aclose())
                else:
                    loop.run_until_complete(self._client.aclose())
            except Exception:
                pass
        self._client = None

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, messages, model, temperature, max_tokens, tools=None, tool_choice="auto") -> Dict[str, Any]:
        model = model or self.model
        temperature = temperature if temperature is not None else self.temperature
        max_tokens = max_tokens if max_tokens is not None else self.max_tokens
        # max_tokens 必须是整数，配置或 JSON 反序列化可能传入 float
        if max_tokens is not None:
            max_tokens = int(max_tokens)
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        return payload

    async def ping(self) -> Dict[str, Any]:
        if not self.api_key or not self.model:
            return {
                "connected": False,
                "error": "缺少 api_key 或 model",
                "endpoint": self.endpoint,
                "model": self.model,
            }
        client = self._ensure_client()
        try:
            resp = await client.post(
                self.endpoint,
                headers=self._headers(),
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "stream": False,
                    "max_tokens": 1,
                    "temperature": 0,
                },
            )
            if resp.status_code == 200:
                return {"connected": True, "endpoint": self.endpoint, "model": self.model}
            return {
                "connected": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                "endpoint": self.endpoint,
                "model": self.model,
            }
        except Exception as e:
            return {
                "connected": False,
                "error": str(e)[:200],
                "endpoint": self.endpoint,
                "model": self.model,
            }

    async def chat(self, messages, model=None, temperature=None, max_tokens=None,
                   tools=None, tool_choice="auto") -> Dict[str, Any]:
        if not self.api_key:
            raise LLMError("API Key 未配置")
        model = model or self.model
        temperature = temperature if temperature is not None else self.temperature
        max_tokens = max_tokens if max_tokens is not None else self.max_tokens
        if max_tokens is not None:
            max_tokens = int(max_tokens)
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        _logger.info(
            "[%s] chat request model=%s endpoint=%s payload_keys=%s",
            self.name, model, self.endpoint, list(payload.keys())
        )
        timeout_cfg = httpx.Timeout(self.timeout)
        last_exc = None
        for _ in range(2):  # 一次 5xx / 超时重试，每次用新连接
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                try:
                    resp = await client.post(self.endpoint, headers=self._headers(), json=payload)
                    _logger.info("[%s] chat response status=%s", self.name, resp.status_code)
                    if resp.status_code == 200:
                        raw_text = resp.text
                        data = json.loads(raw_text)
                        choice = data["choices"][0]
                        message = choice.get("message", {})
                        content = message.get("content", "") or ""
                        reasoning = (
                            message.get("reasoning_content")
                            or message.get("reasoning")
                            or message.get("thinking")
                            or ""
                        )
                        tool_calls = message.get("tool_calls")
                        usage = data.get("usage", {})
                        finish_reason = choice.get("finish_reason")
                        return {
                            "content": content,
                            "reasoning": reasoning,
                            "tool_calls": tool_calls,
                            "usage": usage,
                            "model": model,
                            "truncated": finish_reason == "length",
                            "_raw": raw_text,
                        }
                    elif 500 <= resp.status_code < 600:
                        last_exc = f"[{self.name}] HTTP {resp.status_code}: {resp.text[:300]}"
                        await asyncio.sleep(1)
                        continue
                    else:
                        raise LLMError(f"[{self.name}] HTTP {resp.status_code}: {resp.text[:300]}")
                except (LLMError, asyncio.TimeoutError, httpx.TimeoutException) as e:
                    last_exc = str(e)
                    await asyncio.sleep(1)
                    continue
                except Exception as e:
                    raise LLMError(f"[{self.name}] {e}")
        raise LLMError(last_exc or f"[{self.name}] 调用失败")

    async def stream_chat(self, messages, model=None, temperature=None, max_tokens=None,
                          tools=None, tool_choice="auto") -> AsyncIterator[Dict[str, Any]]:
        if not self.api_key:
            yield {"type": "error", "delta": "API Key 未配置"}
            return
        model = model or self.model
        temperature = temperature if temperature is not None else self.temperature
        max_tokens = max_tokens if max_tokens is not None else self.max_tokens
        if max_tokens is not None:
            max_tokens = int(max_tokens)
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        _logger.info(
            "[%s] stream_chat request model=%s endpoint=%s payload_keys=%s",
            self.name, model, self.endpoint, list(payload.keys())
        )
        timeout_cfg = httpx.Timeout(self.timeout)
        try:
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                async with client.stream("POST", self.endpoint, headers=self._headers(), json=payload) as resp:
                    _logger.info("[%s] stream_chat response status=%s", self.name, resp.status_code)
                    if resp.status_code != 200:
                        body = await resp.aread()
                        text = body.decode("utf-8", errors="replace")
                        yield {"type": "error", "delta": f"[{self.name}] HTTP {resp.status_code}: {text[:300]}"}
                        return
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        line = line.strip()
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if not line or line == "[DONE]":
                            continue
                        try:
                            data = json.loads(line)
                        except Exception:
                            continue
                        if data.get("error"):
                            err = data["error"]
                            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                            yield {"type": "error", "delta": f"API 错误: {msg}"}
                            return
                        choices = data.get("choices") or []
                        if not choices:
                            usage = data.get("usage")
                            if usage:
                                yield {"type": "usage", "usage": usage}
                            continue
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        if delta.get("reasoning_content") is not None:
                            yield {"type": "reasoning", "delta": delta["reasoning_content"]}
                        elif delta.get("reasoning") is not None:
                            yield {"type": "reasoning", "delta": delta["reasoning"]}
                        elif delta.get("thinking") is not None:
                            yield {"type": "reasoning", "delta": delta["thinking"]}
                        if delta.get("content") is not None:
                            yield {"type": "content", "delta": delta["content"]}
                        finish_reason = choice.get("finish_reason")
                        if finish_reason:
                            if finish_reason == "length":
                                yield {
                                    "type": "truncated",
                                    "delta": "模型输出已达到长度上限，结论可能不完整，请尝试简化问题或增加 max_tokens。",
                                }
                            usage = data.get("usage")
                            if usage:
                                yield {"type": "usage", "usage": usage}
                            yield {"type": "done"}
        except Exception as e:
            yield {"type": "error", "delta": f"[{self.name}] {e}"}


class LLMRouter:
    """多厂商代理池：按列表顺序 fallback 的 LLM 封装。"""

    def __init__(self, config: Optional["Config"] = None):
        self.config = config
        self.providers: List[LLMProvider] = []
        self.reconfigure(config)

    def _resolve_backends(self, config: Optional["Config"]) -> List[Dict[str, Any]]:
        backs = getattr(config, "llm_backends", None) or []
        backs = [b for b in backs if isinstance(b, dict)]
        if backs:
            return backs
        # 旧版单组 llm_* 字段兼容
        if (
            getattr(config, "llm_model", None)
            and getattr(config, "llm_api_key", None)
            and getattr(config, "llm_api_url", None)
        ):
            return [{
                "name": "默认模型",
                "provider": getattr(config, "llm_provider", ""),
                "model": config.llm_model,
                "api_url": config.llm_api_url,
                "api_key": config.llm_api_key,
                "temperature": getattr(config, "llm_temperature", 0.7),
                "max_tokens": getattr(config, "llm_max_tokens", 4096),
                "timeout": getattr(config, "llm_timeout", 120),
                "enabled": True,
            }]
        return []

    def reconfigure(self, config: Optional["Config"] = None) -> None:
        self.config = config
        backs = self._resolve_backends(config)
        enabled = [b for b in backs if b.get("enabled", True)]
        # 若所有后端被禁用，仍保留第一条作为兜底，避免彻底瘫痪
        self.providers = [LLMProvider(b, config) for b in (enabled or backs[:1])]

    def close(self) -> None:
        for p in self.providers:
            try:
                p.close()
            except Exception:
                pass

    @property
    def primary_backend(self) -> Optional[LLMProvider]:
        return self.providers[0] if self.providers else None

    @property
    def primary_model(self) -> str:
        p = self.primary_backend
        if p:
            return p.model
        if self.config:
            return getattr(self.config, "llm_model", "") or ""
        return ""

    def _order(self, model: Optional[str]) -> List[LLMProvider]:
        """按优先级排序：若用户指定了 model，把 model 匹配的后端提到最前，
        其余保持原列表顺序作为 fallback。model 仅用于排序，不会覆盖各后端
        自身的模型名（不同厂商 model 名不通用的关键）。"""
        if not model:
            return self.providers
        matched = [p for p in self.providers if p.model == model]
        others = [p for p in self.providers if p.model != model]
        return matched + others

    async def ping(self) -> Dict[str, Any]:
        results = []
        connected_any = False
        for p in self.providers:
            r = await p.ping()
            results.append({
                "name": p.name,
                "model": p.model,
                "provider": p.provider,
                "connected": r.get("connected"),
                "error": r.get("error"),
                "endpoint": r.get("endpoint"),
            })
            if r.get("connected"):
                connected_any = True
        primary = self.primary_backend
        return {
            "connected": connected_any,
            "configured": any(bool(p.api_key) for p in self.providers),
            "model": primary.model if primary else "",
            "endpoint": primary.endpoint if primary else "",
            "provider": primary.provider if primary else "",
            "backends": results,
        }

    async def chat(self, messages, model=None, temperature=None, max_tokens=None,
                   tools=None, tool_choice="auto") -> Dict[str, Any]:
        if not self.providers:
            raise LLMError("未配置任何大模型后端")
        errors: list[str] = []
        for provider in self._order(model):
            try:
                result = await provider.chat(
                    messages, model=None, temperature=temperature,
                    max_tokens=max_tokens, tools=tools, tool_choice=tool_choice,
                )
                result["_backend"] = {
                    "model": provider.model,
                    "name": provider.name,
                    "provider": provider.provider,
                }
                return result
            except LLMError as exc:
                errors.append(str(exc))
                continue
            except Exception as exc:
                errors.append(str(exc))
                continue
        summary = "; ".join(errors) if errors else "所有大模型后端均不可用"
        raise LLMError(summary)

    async def stream_chat(self, messages, model=None, temperature=None, max_tokens=None,
                          tools=None, tool_choice="auto") -> AsyncIterator[Dict[str, Any]]:
        if not self.providers:
            yield {"type": "error", "delta": "未配置任何大模型后端"}
            return
        errors: list[str] = []
        for provider in self._order(model):
            produced = False
            async for chunk in provider.stream_chat(
                messages, model=None, temperature=temperature, max_tokens=max_tokens,
                tools=tools, tool_choice=tool_choice,
            ):
                t = chunk.get("type")
                if t in ("content", "reasoning"):
                    if not produced:
                        produced = True
                        yield {
                            "type": "backend",
                            "model": provider.model,
                            "name": provider.name,
                            "provider": provider.provider,
                        }
                    yield chunk
                elif t == "usage":
                    yield chunk
                elif t == "error":
                    if produced:
                        yield chunk
                        return
                    errors.append(chunk.get("delta") or f"[{provider.name}] 未知错误")
                    break
                elif t == "done":
                    yield chunk
                    return
        summary = "; ".join(errors) if errors else "所有大模型后端均不可用"
        yield {"type": "error", "delta": summary}


# 向后兼容别名：旧代码 `from .llm_client import LLMClient` 仍可工作
LLMClient = LLMRouter
