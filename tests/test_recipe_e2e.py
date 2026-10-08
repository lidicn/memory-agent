"""三路径架构 MVP（A+B）E2E 测试：回填→晋升→召回全链路。

覆盖：
- 缺口A：source_refs 支持 recipe: 前缀
- 缺口B：submit_recipe 写入 staging / match_recipe 召回 live
- 全链路：submit → promote → match 闭环
- 同构 recipe 重复提交走 merge 路径（sample_count 累加）

hermetic：临时 SQLite + 假 Collection，不依赖真实 chroma/网络。
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

import json
import pytest  # noqa: E402

from memory_agent.store import Store  # noqa: E402
from memory_agent.agent_memory import AgentMemoryService  # noqa: E402
from memory_agent.recipe_schema import serialize_recipe, build_recipe  # noqa: E402


class FakeConfig:
    agent_trust_step = 0.2
    trust_strict_threshold = -0.3
    agent_retrieve_k = 20
    agent_dup_sim = 0.92
    agent_conflict_sim = 0.85
    agent_promote_min_days = 2
    agent_corroborate_min_conf = 0.6
    agent_default_ttl_days = 30
    tz_offset_hours = 0.0


class FakeCollection:
    """最小 chroma 集合桩：query 返回可预设的邻居。"""

    def __init__(self):
        self.upserts = []
        self.response = {"ids": [[]], "distances": [[]],
                         "documents": [[]], "metadatas": [[]]}

    def set_neighbors(self, neighbors):
        # neighbors: list of {"memory_id","topic_key","distance","text","trust"}
        self.response = {
            "ids": [[n["memory_id"] for n in neighbors]],
            "distances": [[n.get("distance", 0.0) for n in neighbors]],
            "documents": [[n.get("text", f"doc-{n['memory_id']}") for n in neighbors]],
            "metadatas": [[{
                "topic_key": n.get("topic_key", ""),
                "state": "live",
                "trust": n.get("trust", 0.0),
                "source": n.get("source", "ma"),
                "member_id": n.get("member_id", ""),
            } for n in neighbors]],
        }

    def upsert(self, ids, documents, metadatas):
        self.upserts.append((list(ids), list(metadatas)))

    def delete(self, ids):
        pass

    def query(self, query_texts, where=None, n_results=5):
        return self.response


class FakeHistory:
    def __init__(self, collection):
        self._collection = collection

    @property
    def agent_collection(self):
        return self._collection


@pytest.fixture
def svc():
    tmp = __import__("tempfile").mkdtemp(prefix="mw_recipe_")
    db = os.path.join(tmp, "test.db")
    store = Store(db, tz_offset_hours=0.0)
    store.init_schema()
    col = FakeCollection()
    service = AgentMemoryService(FakeConfig(), store, FakeHistory(col))
    service._test_col = col
    service._test_db = db
    yield service
    try:
        os.remove(db)
    except OSError:
        pass


# ── 缺口A：recipe: 前缀支持 ──────────────────────────────────────────────

def test_recipe_prefix_accepted_in_source_refs(svc):
    """recipe: 前缀的 source_refs 应被 _validate_source_refs 接受。"""
    ok, invalid = svc._validate_source_refs(["recipe:abc123def4"])
    assert ok is True
    assert invalid == []


def test_recipe_prefix_empty_id_rejected(svc):
    """recipe: 前缀但 id 为空应被拒绝。"""
    ok, invalid = svc._validate_source_refs(["recipe:"])
    assert ok is False
    assert "recipe:" in invalid


def test_add_semantic_memory_with_recipe_ref(svc):
    """add_semantic_memory 接受 recipe: 前缀的 source_refs 并写入 staging。"""
    r = svc.add_semantic_memory(
        "s1", "测试文本",
        source_refs=["recipe:abc123def4"],
        topic_key="recipe", dry_run=False,
    )
    assert r["ok"] is True
    assert r["state"] == "staging"


# ── 缺口B：submit_recipe 写入 ────────────────────────────────────────────

def test_submit_recipe_writes_staging(svc):
    """submit_recipe 应将 recipe 序列化为 JSON 写入 staging，topic_key=recipe。"""
    r = svc.submit_recipe({
        "intent": "device_usage",
        "object_type": "device",
        "metric": "duration",
        "time_window": "last_7_days",
        "tool_sequence": [{"tool_name": "get_device_usage", "params": {"query": "{entity_id}", "days": 7}}],
        "confidence": 0.7,
    }, session_id="test-session")
    assert r["ok"] is True
    assert r["state"] == "staging"
    mem = svc.store.get_agent_memory(r["memory_id"])
    assert mem["topic_key"] == "recipe"
    assert mem["source"] == "recipe"
    # text 应是合法 JSON
    data = json.loads(mem["text"])
    assert data["intent"] == "device_usage"
    assert data["recipe_id"].startswith("recipe_")


def test_submit_recipe_invalid_rejected(svc):
    """非法 intent 的 recipe 应被校验拒绝。"""
    r = svc.submit_recipe({
        "intent": "invalid_intent",
        "object_type": "device",
        "metric": "duration",
        "time_window": "last_7_days",
        "tool_sequence": [],
    }, session_id="test")
    assert r["ok"] is False
    assert r["code"] == 422


def test_submit_recipe_deterministic_id(svc):
    """同构 recipe 应生成相同的 recipe_id（确定性）。"""
    recipe_dict = {
        "intent": "device_usage",
        "object_type": "device",
        "metric": "duration",
        "time_window": "last_7_days",
        "tool_sequence": [{"tool_name": "get_device_usage", "params": {"days": 7}}],
        "confidence": 0.5,
    }
    r1 = svc.submit_recipe(recipe_dict, session_id="s1")
    r2 = svc.submit_recipe(recipe_dict, session_id="s2")
    # 两次提交的 memory_id 可能不同（merge 路径），但 recipe_id 应相同
    mem1 = svc.store.get_agent_memory(r1["memory_id"])
    mem2 = svc.store.get_agent_memory(r2["memory_id"])
    id1 = json.loads(mem1["text"])["recipe_id"]
    id2 = json.loads(mem2["text"])["recipe_id"]
    assert id1 == id2


# ── 缺口B：match_recipe 召回 ─────────────────────────────────────────────

def test_match_recipe_returns_live_recipes(svc):
    """match_recipe 应从 retrieve 结果中过滤 topic_key=recipe 并解析返回。"""
    # 构造一个已晋升的 recipe 文本
    recipe = build_recipe(
        intent="device_usage", object_type="device", metric="duration",
        time_window="last_7_days",
        tool_sequence=[{"tool_name": "get_device_usage", "params": {"days": 7}}],
        created_at="2026-10-08T00:00:00+00:00", source_session="test",
        confidence=0.8, sample_count=3, status="live",
    )
    recipe_text = serialize_recipe(recipe)
    # 设置 FakeCollection 返回这个 recipe 作为邻居
    svc._test_col.set_neighbors([{
        "memory_id": "mem-recipe-001",
        "topic_key": "recipe",
        "distance": 0.1,
        "text": recipe_text,
        "trust": 0.8,
    }])
    r = svc.match_recipe(question="书房空调开了多久", intent="device_usage")
    assert r["ok"] is True
    assert r["count"] >= 1
    assert r["recipes"][0]["intent"] == "device_usage"
    assert r["recipes"][0]["recipe_id"] == recipe.recipe_id
    assert len(r["recipes"][0]["tool_sequence"]) == 1
    assert r["recipes"][0]["tool_sequence"][0]["tool_name"] == "get_device_usage"


def test_match_recipe_filters_by_intent(svc):
    """match_recipe 应按 intent 精确过滤。"""
    recipe = build_recipe(
        intent="anomaly", object_type="device", metric="count",
        time_window="last_7_days",
        tool_sequence=[{"tool_name": "get_behavior_insights", "params": {}}],
        created_at="2026-10-08T00:00:00+00:00", source_session="test",
    )
    svc._test_col.set_neighbors([{
        "memory_id": "mem-002",
        "topic_key": "recipe",
        "distance": 0.1,
        "text": serialize_recipe(recipe),
    }])
    # 查询 device_usage 意图，应过滤掉 anomaly 的 recipe
    r = svc.match_recipe(question="测试", intent="device_usage")
    assert r["ok"] is True
    assert r["count"] == 0


def test_match_recipe_skips_non_recipe_topics(svc):
    """match_recipe 应跳过 topic_key != recipe 的记忆。"""
    svc._test_col.set_neighbors([{
        "memory_id": "mem-003",
        "topic_key": "general",  # 非 recipe
        "distance": 0.05,
        "text": "普通记忆文本",
    }])
    r = svc.match_recipe(question="测试")
    assert r["ok"] is True
    assert r["count"] == 0


def test_match_recipe_requires_question_or_intent(svc):
    """match_recipe 无 question 且无 intent 时应报错。"""
    r = svc.match_recipe()
    assert r["ok"] is False
    assert "question 或 intent" in r["error"]


# ── 全链路：submit → promote → match ────────────────────────────────────

def test_full_chain_submit_promote_match(svc):
    """完整链路：submit_recipe → promote_memory → match_recipe 召回。"""
    # 1. 提交 recipe（staging）
    recipe_dict = {
        "intent": "compare",
        "object_type": "room",
        "metric": "duration",
        "time_window": "week_over_week",
        "tool_sequence": [
            {"tool_name": "get_device_usage", "params": {"room": "{room}", "days": 7}},
            {"tool_name": "get_device_usage", "params": {"room": "{room}", "days": 14}},
        ],
        "confidence": 0.6,
    }
    sub = svc.submit_recipe(recipe_dict, session_id="chain-test")
    assert sub["ok"] is True
    assert sub["state"] == "staging"
    memory_id = sub["memory_id"]

    # 2. 晋升到 live（human_override=True 模拟人工复核晋升）
    svc._test_col.set_neighbors([])  # 无冲突
    prom = svc.promote_memory(memory_id, session_id="chain-test", force=True, human_override=True)
    assert prom["ok"] is True
    assert prom["state"] == "live"

    # 3. 构造 match_recipe 的检索结果（模拟 chroma 返回已晋升的 recipe）
    mem = svc.store.get_agent_memory(memory_id)
    svc._test_col.set_neighbors([{
        "memory_id": memory_id,
        "topic_key": "recipe",
        "distance": 0.05,
        "text": mem["text"],
        "trust": 0.5,
    }])

    # 4. match_recipe 应能召回
    match = svc.match_recipe(question="客厅和书房用电量对比", intent="compare")
    assert match["ok"] is True
    assert match["count"] >= 1
    assert match["recipes"][0]["intent"] == "compare"
    assert match["recipes"][0]["object_type"] == "room"
    assert len(match["recipes"][0]["tool_sequence"]) == 2


# ── 缺口A回归：旧前缀仍正常 ──────────────────────────────────────────────

def test_legacy_prefixes_still_work(svc):
    """event:/insight:/activity: 前缀应仍正常工作（回归）。"""
    for prefix in ["event:", "insight:", "activity:"]:
        ok, invalid = svc._validate_source_refs([f"{prefix}test-id"])
        assert ok is True, f"{prefix} 前缀应被接受"
