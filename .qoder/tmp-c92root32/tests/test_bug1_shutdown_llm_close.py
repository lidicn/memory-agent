"""BUG-1 回归：AppRuntime.shutdown 不得 await 同步的 LLMClient.close()。

`close()` 返回 None，`await None` 在关停路径上必然抛
"object NoneType can't be used in 'await' expression"（路线图 3.2 BUG-1）。
容器里关停日志只在进程退出时出现，CI 抓不到，所以这里用桩对象把 shutdown
整条路径跑一遍，并同时锁住源码口径（防止有人把 await 加回来）。
"""

import asyncio
import inspect
import io
import os
import sys
from contextlib import redirect_stdout

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.llm_client import LLMProvider, LLMRouter  # noqa: E402
from memory_agent.runtime import AppRuntime  # noqa: E402

_RUNTIME_SRC = os.path.join(os.path.dirname(inspect.getsourcefile(AppRuntime)), "runtime.py")


class _SyncClose:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True
        return None


class _AsyncStop:
    def __init__(self):
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


def _stub_runtime():
    rt = AppRuntime.__new__(AppRuntime)
    rt.mqtt = _SyncClose()
    rt.llm = _SyncClose()
    rt.vision = _AsyncStop()
    rt.collector = _AsyncStop()
    rt.store = _SyncClose()
    rt._started = True
    return rt


def test_llm_close_is_sync_and_returns_none():
    """await 的前提是 close() 是协程且返回 awaitable；两条都不成立 → await None 必炸。"""
    for cls in (LLMProvider, LLMRouter):
        assert not inspect.iscoroutinefunction(cls.close), f"{cls.__name__}.close 不能是协程"
        obj = cls.__new__(cls)
        obj._client = None      # LLMProvider 路径
        obj.providers = []      # LLMRouter 路径
        assert obj.close() is None, f"{cls.__name__}.close() 必须返回 None"


def test_shutdown_runs_llm_close_without_await():
    rt = _stub_runtime()
    buf = io.StringIO()
    with redirect_stdout(buf):
        asyncio.run(rt.shutdown())
    out = buf.getvalue()
    assert "关闭 LLM 客户端异常" not in out, out
    assert "取消后台任务异常" not in out, out
    assert "[Runtime] 已关闭" in out, out
    assert rt.llm.closed is True
    assert rt.store.closed is True
    assert rt._started is False


def test_shutdown_source_has_no_await_on_llm_close():
    with open(_RUNTIME_SRC, encoding="utf-8") as fh:
        text = fh.read()
    assert "await self.llm.close()" not in text
    assert "self.llm.close()" in text
