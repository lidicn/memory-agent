"""v1.0 任务 1：arena 分析闭环（store 层聚合，无需 runtime/chroma）。

覆盖：
1. cohort 划分：used_memory_tools 非空 vs 空；
2. with_memory / without_memory 的成功率、平均 token、计数；
3. by_arena / by_agent 分组；
4. arena_id 过滤。
"""
import os
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.store import Store  # noqa: E402


@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="ma_arena_an_")
    db = os.path.join(tmp, "test.db")
    s = Store(db, tz_offset_hours=0.0)
    s.init_schema()
    yield s


def _rec(s, arena_id, agent_id, success, token, used):
    return s.add_arena_result(
        arena_id, agent_id, "task-" + agent_id, "desc", "flow",
        success, token, used,
    )


def test_cohort_split_and_rates(store):
    # with memory: 1 成功(100 tok) + 1 失败(120 tok)
    _rec(store, "arenaA", "agent1", True, 100, ["get_behavior_insights"])
    _rec(store, "arenaA", "agent1", False, 120, ["get_entity_catalog"])
    # without memory: 1 成功(200 tok)
    _rec(store, "arenaA", "agent2", True, 200, [])

    d = store.get_arena_analytics()
    assert d["total"] == 3
    wm = d["with_memory"]
    assert wm["count"] == 2
    assert wm["success_rate"] == 0.5
    assert wm["avg_token"] == 110.0
    assert wm["total_token"] == 220
    wom = d["without_memory"]
    assert wom["count"] == 1
    assert wom["success_rate"] == 1.0
    assert wom["avg_token"] == 200.0
    # 最近记录按时间倒序，首条为最后一次写入（without memory）
    assert d["recent"][0]["agent_id"] == "agent2"


def test_group_by_arena_and_agent(store):
    _rec(store, "arenaA", "agent1", True, 100, ["x"])
    _rec(store, "arenaB", "agent2", False, 50, [])
    d = store.get_arena_analytics()
    by_arena = {g["arena_id"]: g for g in d["by_arena"]}
    assert set(by_arena.keys()) == {"arenaA", "arenaB"}
    assert by_arena["arenaA"]["with_memory"]["count"] == 1
    by_agent = {g["agent_id"]: g for g in d["by_agent"]}
    assert set(by_agent.keys()) == {"agent1", "agent2"}


def test_filter_by_arena_id(store):
    _rec(store, "arenaA", "agent1", True, 100, ["x"])
    _rec(store, "arenaB", "agent2", True, 100, ["x"])
    d = store.get_arena_analytics(arena_id="arenaA")
    assert d["total"] == 1
    assert d["with_memory"]["count"] == 1
