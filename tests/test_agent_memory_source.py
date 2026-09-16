"""v0.5 记忆统一入库测试：source 来源字段的写入、存储与过滤。

验证：
- 写入记忆时 source 落库（butler 生态 vs ma 原生）
- 缺省 source 为 'ma'
- 按 source 过滤 list
- chroma 元数据镜像含 source（检索侧可按来源隔离低置信摘要）
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.agent_memory import AgentMemoryService  # noqa: E402
from memory_agent.config import Config  # noqa: E402
from memory_agent.store import Store  # noqa: E402


class _FakeHistory:
    agent_collection = None  # 无 chroma：镜像同步会标记 dirty，但写入主路径不受影响


def _store():
    store = Store(":memory:", tz_offset_hours=8)
    store.init_schema()
    return store


def _svc(store):
    return AgentMemoryService(config=Config(), store=store, history=_FakeHistory())


def test_add_with_butler_source_tags_store():
    store = _store()
    svc = _svc(store)
    res = svc.add_semantic_memory(
        "butler-session",
        "孩子一般 22 点后还在玩电脑",
        source_refs=["insight:2026-x"],
        source="butler",
        dry_run=False,
    )
    assert res["ok"], res
    mem = store.get_agent_memory(res["memory_id"])
    assert mem["source"] == "butler"


def test_add_default_source_is_ma():
    store = _store()
    svc = _svc(store)
    res = svc.add_semantic_memory("s1", "客厅电视每晚开 3 小时", source_refs=["insight:abc"], dry_run=False)
    assert res["ok"]
    mem = store.get_agent_memory(res["memory_id"])
    assert mem["source"] == "ma"


def test_list_filters_by_source():
    store = _store()
    svc = _svc(store)
    svc.add_semantic_memory("s1", "原生记忆A", source_refs=["insight:a"], source="ma", dry_run=False)
    svc.add_semantic_memory("s2", "管家记忆B", source_refs=["insight:b"], source="butler", dry_run=False)

    ma_rows = store.list_agent_memories("all", source="ma")
    butler_rows = store.list_agent_memories("all", source="butler")
    assert len(ma_rows) == 1 and ma_rows[0]["source"] == "ma"
    assert len(butler_rows) == 1 and butler_rows[0]["source"] == "butler"

    # 服务层 list 透传 source，且返回体含 source 字段
    out = svc.list_agent_memories("all", "butler")
    assert out["source"] == "butler"
    assert out["memories"][0]["source"] == "butler"


def test_metadata_mirror_includes_source():
    meta = AgentMemoryService._to_metadata(
        {"state": "live", "trust": 0.5, "source": "butler",
         "expires_at": "", "session_id": "s", "memory_id": "m", "topic_key": "t"}
    )
    assert meta["source"] == "butler"
    # 缺省也应回落到 ma
    meta2 = AgentMemoryService._to_metadata({"state": "live", "trust": 0.0})
    assert meta2["source"] == "ma"
