"""TaskRegistry 单测（路线图 3.2：异步任务统一收口）。

采用 asyncio.run 包裹异步用例，兼容未安装 pytest-asyncio 的环境。
"""
import asyncio
import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.task_registry import TaskRegistry  # noqa: E402


async def _noop():
    await asyncio.sleep(0.01)


def test_create_holds_strong_ref_and_status():
    async def _run():
        reg = TaskRegistry()
        task = reg.create(_noop(), name="t.demo")
        assert isinstance(task, asyncio.Task)
        st = reg.status()
        assert st["pending"] == 1
        assert st["tasks"] == ["t.demo"]
        await task
        # done_callback 是同步回调但由 loop 调度，让出一轮
        await asyncio.sleep(0)
        assert reg.status() == {"pending": 0, "tasks": []}

    asyncio.run(_run())


def test_create_accepts_factory():
    async def _run():
        reg = TaskRegistry()
        task = reg.create(lambda: _noop(), name="t.factory")
        await task
        await asyncio.sleep(0)
        assert reg.status()["pending"] == 0

    asyncio.run(_run())


def test_exception_logged_and_removed():
    import contextlib

    async def _boom():
        raise ValueError("boom")

    async def _run():
        reg = TaskRegistry()
        task = reg.create(_boom(), name="t.boom")
        with contextlib.suppress(ValueError):
            await task
        await asyncio.sleep(0)  # 等 done_callback 触发
        assert reg.status()["pending"] == 0
        assert task.done()

    asyncio.run(_run())


def test_exception_goes_to_logger(caplog):
    import logging

    async def _boom():
        raise ValueError("exploded")

    with caplog.at_level(logging.WARNING, logger="memory_agent.task_registry"):
        async def _run():
            reg = TaskRegistry()
            reg.create(_boom(), name="t.log")
            await asyncio.sleep(0.05)

        asyncio.run(_run())
    assert "t.log" in caplog.text
    assert "exploded" in caplog.text


def test_cancel_all():
    async def _forever():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise

    async def _run():
        reg = TaskRegistry()
        t1 = reg.create(_forever(), name="t.a")
        t2 = reg.create(_forever(), name="t.b")
        n = await reg.cancel_all()
        assert n == 2
        assert t1.cancelled() and t2.cancelled()
        assert reg.status() == {"pending": 0, "tasks": []}

    asyncio.run(_run())


def test_cancel_all_empty():
    async def _run():
        reg = TaskRegistry()
        assert await reg.cancel_all() == 0

    asyncio.run(_run())


def test_create_without_running_loop_raises():
    reg = TaskRegistry()
    coro = _noop()
    try:
        reg.create(coro, name="t.noloop")
        raised = False
    except RuntimeError:
        raised = True
    finally:
        coro.close()  # 避免未 await 协程告警
    assert raised  # 无事件循环时不吞，直接抛 RuntimeError
