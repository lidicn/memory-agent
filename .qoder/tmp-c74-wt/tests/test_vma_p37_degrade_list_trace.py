# -*- coding: utf-8 -*-
"""第十四轮 P3-7：`_degrade` 对非 dict 降级值不补 error —— 「空」与「坏了」必须可分。

审计原话（`doc/审计报告/元宝/memory-agent_第十四轮审计报告.md:89-124`）给的对比是：

```
room_names()      失败 → []                            （无处放 error）
entity_catalog()  失败 → {'items': [], 'total': 0, 'error': '数据库连接失败'}
```

21 个 `@_degrade` 里只有 `room_names` 的工厂返回 `list`，所以这一格是"同一个装饰器两种
可观测性"。修法不能是把工厂换成 dict —— 那会改掉 4 个生产调用点看到的返回形状
（`behavior_routes.py:580`、`skills.py:158`、`insights_legacy.py:345/:391` 都在按列表用）。
这里用 `list` 子类**加一个属性**，列表行为一字不动，失败原因另挂在 `degraded_error` 上。

锁的口径：该响的时刻真响过（T1），该不响的不响（T2），并且"不是缺陷"的那一面也要锁
（T4 锁 dict 分支照旧、T3 锁 JSON/切片/迭代与 `list` 同值）。
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from memory_agent.insights.api import (  # noqa: E402
    DegradedList,
    InsightService,
    _degrade,
)
from memory_agent.store import Store  # noqa: E402


@pytest.fixture
def svc(tmp_path):
    st = Store(str(tmp_path / "t.db"))
    st.init_schema()
    yield InsightService(st)
    conn = getattr(st, "_conn", None)
    if conn is not None:
        conn.close()


def test_list_degrade_carries_the_reason(svc, monkeypatch):
    """真响过：resolver 抛异常时，返回的空列表必须带得上失败原因。"""
    def _boom(only_enabled=True):
        raise RuntimeError("数据库连接失败")

    monkeypatch.setattr(svc.resolver, "rooms", _boom)
    out = svc.room_names()
    assert list(out) == []
    assert isinstance(out, list)                       # 对下游仍是列表
    assert "数据库连接失败" in out.degraded_error       # 但"坏了"看得见


def test_normal_path_is_a_plain_list_without_the_marker(svc):
    """该不响的不响：正常返回不许被包成降级值，否则空房间会被读成故障。"""
    out = svc.room_names()
    assert isinstance(out, list)
    assert getattr(out, "degraded_error", None) is None
    assert not isinstance(out, DegradedList)


def test_degraded_list_behaves_exactly_like_a_list():
    items = ["客厅", "主卧", "书房"]
    d = DegradedList(items, "boom")
    assert list(d) == items
    assert d[1] == "主卧"
    assert d[:2] == ["客厅", "主卧"]
    assert len(d) == 3 and ("主卧" in d)
    assert json.dumps(d) == json.dumps(items)          # 出境序列化同值
    assert "boom" not in json.dumps(d)                 # 失败原因不进载荷，只在进程内
    assert isinstance(d + ["厨房"], list)


def test_dict_degrade_still_fills_the_error_key():
    """dict 分支的既有契约不许被这次改动打掉。"""

    @_degrade(lambda: {"items": [], "total": 0})
    def _f():
        raise RuntimeError("表不存在")

    out = _f()
    assert out["items"] == [] and out["total"] == 0
    assert "表不存在" in out["error"]


def test_value_error_branch_also_leaves_a_trace():
    """`ValueError` 走的是 LOG.info 那条分支，同样要能区分空与坏。"""

    @_degrade(list)
    def _g():
        raise ValueError("参数不合法")

    out = _g()
    assert list(out) == [] and "参数不合法" in out.degraded_error

    @_degrade(lambda: {"answer": ""})
    def _h():
        raise ValueError("参数不合法")

    assert "参数不合法" in _h()["error"]


def test_non_container_degrade_is_returned_unchanged():
    """工厂返回既非 dict 也非 list 时保持原样（不臆造形状）。"""

    @_degrade(lambda: 42)
    def _f():
        raise RuntimeError("x")

    assert _f() == 42
