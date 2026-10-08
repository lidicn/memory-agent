"""AutoFlow 竞技场对接：核心逻辑与 ACP 作用域测试。

契约见 docs/交接单_AutoFlow竞技场对接.md。
用轻量 fake 替代 store/insights/history/llm，不依赖 HA / chroma / LLM。
"""

import asyncio
import json
import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.arena import ArenaService  # noqa: E402
from memory_agent.config import Config  # noqa: E402


# ── 轻量 fake ──────────────────────────────────────────────────────────────
class FakeInsights:
    def __init__(self, events):
        self._events = events

    def search_events(self, **kw):
        return {"events": self._events, "window": {}}


class FakeStore:
    def __init__(self, snapshot=None):
        self._saved = []
        self._snapshots = {}
        if snapshot is not None:
            self._snapshots["study"] = snapshot

    def save_arena_snapshot(self, arena_id, room, devices, history_days, snapshot_json):
        self._saved.append(json.loads(snapshot_json))
        return {"version": len(self._saved), "id": len(self._saved)}

    def get_arena_snapshot(self, arena_id, version=None):
        return self._snapshots.get(arena_id)

    def add_arena_result(self, *a, **k):
        return "arena_abc123"


class FakeCollection:
    """内存版 chroma 集合，仅做精确匹配以验证去重链路。"""

    def __init__(self):
        self.docs = {}
        self.metas = {}

    def query(self, query_texts, n_results=3, include=None):
        q = query_texts[0]
        if not self.docs:
            return {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        tid = next(iter(self.docs))
        doc = self.docs[tid]
        d = 0.0 if doc == q else 1.5
        return {
            "documents": [[doc]],
            "metadatas": [[self.metas[tid]]],
            "distances": [[d]],
        }

    def upsert(self, ids, documents, metadatas):
        for i, d, m in zip(ids, documents, metadatas):
            self.docs[i] = d
            self.metas[i] = m


class FakeHistory:
    def __init__(self, col):
        self._col = col

    @property
    def arena_collection(self):
        return self._col


def _svc(insights=None, store=None, history=None, llm=None):
    return ArenaService(Config(), store or FakeStore(), history, insights, llm)


# ── 需求 1：快照脱敏 ───────────────────────────────────────────────────────
def test_build_snapshot_desensitizes_and_versions():
    events = [
        {"entity_id": f"dev.{i}", "ts": f"2026-09-03T{10 + i}:00:00"}
        for i in range(3)
    ]
    store = FakeStore()
    svc = _svc(insights=FakeInsights(events), store=store)
    snap = svc.build_snapshot("study", "书房", ["dev.0", "dev.1"], 30)

    assert snap["version"] == 1
    labels = [e["label"] for e in snap["entities"]]
    assert labels == ["设备1", "设备2", "设备3"]
    # 真实 entity_id 保留在 original 字段，但对外灵感只暴露 label
    assert all(e["original"].startswith("dev.") for e in snap["entities"])
    # 版本化：再写一次版本递增
    snap2 = svc.build_snapshot("study", "书房", ["dev.0"], 30)
    assert snap2["version"] == 2


# ── 需求 1：灵感生成 ───────────────────────────────────────────────────────
def test_get_inspiration_generates_and_empty_without_snapshot():
    snap_dict = {
        "arena_id": "study", "history_days": 30,
        "entities": [{"label": "设备1", "original": "dev.0", "count": 10, "busy_hours": [18]}],
    }
    store = FakeStore(snapshot={"snapshot_json": json.dumps(snap_dict, ensure_ascii=False)})
    svc = _svc(store=store)

    res = asyncio.run(svc.get_arena_inspiration("study", "all", 5))
    assert res["ok"] and res["count"] == 1
    it = res["items"][0]
    assert it["creativity_score"] == round(min(1.0, 10 / 40.0), 3)
    assert "suggested_flow" in it and "entity_hints" in it and it["entity_hints"] == ["设备1"]

    # 无快照：返回空 items + hint，不报错
    empty = asyncio.run(svc.get_arena_inspiration("nope", "all", 5))
    assert empty["count"] == 0 and empty["items"] == [] and empty["hint"]


# ── 需求 2：创造力三层评估 + 去重 ──────────────────────────────────────────
def test_evaluate_creativity_detects_duplicate():
    snap = {"arena_id": "study", "entities": [{"label": "设备1"}]}
    store = FakeStore(snapshot={"snapshot_json": json.dumps(snap, ensure_ascii=False)})
    col = FakeCollection()
    svc = _svc(store=store, history=FakeHistory(col))

    r1 = asyncio.run(svc.evaluate_creativity("study", "夜间护眼模式", "灯光联动", ["设备1"]))
    assert r1["is_duplicate"] is False and r1["duplicate_of"] is None
    assert set(r1) >= {"creativity_score", "novelty_score", "relevance_score", "feedback"}

    # 同题二次提交：题目库已存在 → 判定重复
    r2 = asyncio.run(svc.evaluate_creativity("study", "夜间护眼模式", "灯光联动", ["设备1"]))
    assert r2["is_duplicate"] is True and r2["duplicate_of"] is not None


def test_evaluate_creativity_relevance_without_snapshot_is_neutral():
    store = FakeStore(snapshot=None)
    col = FakeCollection()
    svc = _svc(store=store, history=FakeHistory(col))
    r = asyncio.run(svc.evaluate_creativity("study", "全新题目X", "desc", ["设备9"]))
    # 无快照实体时 relevance 取中性 0.5
    assert r["relevance_score"] == 0.5


# ── 需求 3：结果记录 ───────────────────────────────────────────────────────
def test_record_arena_result():
    svc = _svc(store=FakeStore())
    r = asyncio.run(svc.record_arena_result(
        "study", "t", "d", "dsl", True, 100, "a1", ["get_arena_inspiration"]))
    assert r["recorded"] and r["insight_id"] == "arena_abc123"


# ── ACP 作用域：arena 令牌只看到 3 个竞技场工具 ───────────────────────────
def test_acp_tools_arena_scope_isolation():
    from memory_agent.acp_server import build_acp_tools

    arena = build_acp_tools("arena")
    arena_names = {t["name"] for t in arena}
    assert arena_names == {
        "get_arena_inspiration", "evaluate_creativity", "record_arena_result"
    }

    full = build_acp_tools("acp")
    full_names = {t["name"] for t in full}
    assert "delegate_to_autoflow" in full_names
    # 竞技场工具不应出现在默认 acp 工具集中（作用域隔离）
    assert not (arena_names & full_names)
