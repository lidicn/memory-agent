"""TaskRegistry：统一收口 asyncio 后台任务（路线图 3.2）。

解决的问题：散落在各模块的裸 ``asyncio.create_task`` 需要各自维护
「强引用集合 + done_callback discard」样板代码（稳定性审计缺陷1/2/6 系列），
且异常常被静默吞掉。本模块提供模块级单例：

* ``create()`` 自动持强引用，防止 task 被 GC 提前回收；
* 任务异常退出时统一记录日志（含任务名），取消则静默；
* ``cancel_all()`` 供 shutdown 流程一次性取消所有 pending 任务；
* ``status()`` 暴露 pending 数量与任务名列表，供调试端点查询。

无运行中事件循环时 ``create()`` 直接抛 ``RuntimeError``（不吞，由调用方决定降级策略）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional, Union

logger = logging.getLogger("memory_agent.task_registry")


class TaskRegistry:
    """后台任务注册表：强引用 + 异常记录 + 统一取消。"""

    def __init__(self) -> None:
        self._pending: dict[asyncio.Task, str] = {}

    def create(
        self,
        coro_or_factory: Union[Awaitable, Callable[[], Awaitable]],
        name: Optional[str] = None,
    ) -> asyncio.Task:
        """创建任务并持强引用；返回 task 本身（调用方可继续赋回原属性）。

        接受协程/未来对象，或返回协程的无参工厂函数。
        """
        awaitable = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
        task = asyncio.create_task(awaitable, name=name)
        self._pending[task] = name or task.get_name()
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task) -> None:
        label = self._pending.pop(task, task.get_name())
        if task.cancelled():
            return  # 关停期取消属预期，不当异常记录
        exc = task.exception()
        if exc is not None:
            logger.warning("[Tasks] 后台任务异常退出: %s: %r", label, exc)

    async def cancel_all(self) -> int:
        """统一取消所有 pending 任务并等待其结束；返回取消的任务数。"""
        tasks = list(self._pending)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        return len(tasks)

    def status(self) -> dict:
        """pending 数量 + 任务名列表（供 /api/debug/tasks 查询）。"""
        return {
            "pending": len(self._pending),
            "tasks": sorted(self._pending.values()),
        }


task_registry = TaskRegistry()
