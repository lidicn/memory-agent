"""审计 P1-12 的回归锁：首次身份对账既不能阻塞启动，也不能把启动炸掉。

原实现在 ``startup()`` 里同步 ``await asyncio.to_thread(reconcile)``：
1. 它是启动路径上唯一没有超时的外部调用，HA 注册表越大、"打开 WebUI 空白几秒"越明显；
2. ``res`` 只在 try 内赋值，``reconcile()`` 一旦抛异常，紧随其后的
   ``self._publish_health_changes(res, ...)`` 就踩在未绑定的名字上（UnboundLocalError），
   把注释里写着的"失败不影响启动"变成"启动直接炸"。
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

from memory_agent.runtime import AppRuntime  # noqa: E402

_STARTUP_SRC = inspect.getsource(AppRuntime.startup)


class _Reconciler:
    def __init__(self, result=None, raises=None):
        self.result = result
        self.raises = raises
        self.calls = 0

    def reconcile(self):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.result


class _Stub:
    """只实现被测协程用到的两个成员，其余交给被测函数自己约束。"""

    def __init__(self, result=None, raises=None):
        self.identity_reconciler = _Reconciler(result=result, raises=raises)
        self.published = []

    def _publish_health_changes(self, res):
        self.published.append(res)


def _run(stub) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        asyncio.run(AppRuntime._first_identity_reconcile(stub))
    return buf.getvalue()


def test_reconciler_failure_does_not_propagate():
    """对账抛异常 → 协程安静退出，健康推送一次都不发。"""
    stub = _Stub(raises=RuntimeError("HA registry exploded"))
    out = _run(stub)
    assert stub.published == [], f"失败路径不该推送：{stub.published}"
    assert "首次对账失败" in out and "HA registry exploded" in out, out


def test_success_publishes_the_reconcile_result():
    res = {"entities": 3, "devices": 2, "merged": 1, "remapped": 0,
           "stale": 0, "health_changes": [{"entity_id": "light.a", "from": "ok", "to": "unavailable"}]}
    stub = _Stub(result=res)
    out = _run(stub)
    assert stub.published == [res]
    assert "首次对账完成" in out and "实体 3" in out, out


def test_non_dict_result_is_coerced_before_publishing():
    """对账器返回 None/字符串时不能把 None 喂给 ``res.get``。"""
    stub = _Stub(result=None)
    _run(stub)
    assert stub.published == [{}], f"非 dict 结果没有归一化：{stub.published}"


def test_startup_no_longer_awaits_the_first_reconcile():
    """startup 只做任务注册；首次对账进后台协程。"""
    assert "asyncio.to_thread(self.identity_reconciler.reconcile)" not in _STARTUP_SRC, (
        "startup 里又出现了同步等待的对账调用——这是 P1-12 的原始形态")
    assert "self._first_identity_reconcile()" in _STARTUP_SRC, (
        "startup 必须注册首次对账，否则逻辑映射要等到第一个周期才有")
