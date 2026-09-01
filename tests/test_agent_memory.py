"""Agent 记忆写回（向量库参与式迭代）单测。

hermetic：临时 SQLite + 假 HistoryManager/Collection，不依赖真实 chroma/网络。
覆盖：溯源校验、staging→live 状态机、去重/矛盾护栏、re-rank 公式、信任步进、
rollback 镜像回写（bug #2 回归）、TTL 过期、sweep 自动晋升。
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

import pytest  # noqa: E402

from memory_agent.store import Store  # noqa: E402
from memory_agent.agent_memory import AgentMemoryService, AGENT_STATES  # noqa: E402


class FakeConfig:
    """复刻 agent_memory.py 中 getattr 默认值的显式配置，行为可预测。"""

    privileged_sessions = []
    agent_trust_step = 0.2
    trust_strict_threshold = -0.3
    agent_retrieve_k = 20
    agent_dup_sim = 0.92
    agent_conflict_sim = 0.85
    agent_promote_min_days = 2
    agent_corroborate_min_conf = 0.6
    agent_default_ttl_days = 30


class FakeCollection:
    """最小 chroma 集合桩：记录 upsert，query 返回可预设的邻居。"""

    def __init__(self):
        self.upserts = []          # [(ids, metadatas), ...]
        self.deletes = []
        self.response = {"ids": [[]], "distances": [[]],
                         "documents": [[]], "metadatas": [[]]}

    def set_neighbors(self, neighbors):
        # neighbors: list of {"memory_id","topic_key","distance"}
        self.response = {
            "ids": [[n["memory_id"] for n in neighbors]],
            "distances": [[n.get("distance", 0.0) for n in neighbors]],
            "documents": [[f"doc-{n['memory_id']}" for n in neighbors]],
            "metadatas": [[{"topic_key": n["topic_key"], "state": "live"} for n in neighbors]],
        }

    def upsert(self, ids, documents, metadatas):
        self.upserts.append((list(ids), list(metadatas)))

    def delete(self, ids):
        self.deletes.extend(ids)

    def query(self, query_texts, where=None, n_results=5):
        return self.response


class FakeHistory:
    def __init__(self, collection):
        self._collection = collection

    @property
    def agent_collection(self):
        return self._collection


def _seed_events(store, days):
    """插入跨多日的事件，满足 cross_day 晋升条件。"""
    store.insert_events([
        {"id": f"ev-{d}", "entity_id": "light.living", "ts": f"{d}T21:00:00", "day": d}
        for d in days
    ])


@pytest.fixture
def svc():
    tmp = __import__("tempfile").mkdtemp(prefix="mw_am_")
    db = os.path.join(tmp, "test.db")
    store = Store(db, tz_offset_hours=0.0)
    store.init_schema()
    _seed_events(store, ["2026-08-03", "2026-08-05", "2026-08-06"])
    col = FakeCollection()
    service = AgentMemoryService(FakeConfig(), store, FakeHistory(col))
    service._test_col = col
    service._test_db = db
    yield service
    try:
        os.remove(db)
    except OSError:
        pass


CROSS_DAY = ["event:ev-2026-08-03", "event:ev-2026-08-05", "event:ev-2026-08-06"]


# ── 写入与溯源校验 ──────────────────────────────────────────────────────────
def test_add_requires_source_refs(svc):
    r = svc.add_semantic_memory("s1", "文本", source_refs=[], dry_run=False)
    assert r["ok"] is False
    assert r["code"] == 422


def test_add_rejects_bad_prefix(svc):
    r = svc.add_semantic_memory("s1", "文本", source_refs=["foo:123"], dry_run=False)
    assert r["ok"] is False
    assert r["code"] == 422


def test_dry_run_does_not_persist(svc):
    before = len(svc.store.list_agent_memories())
    r = svc.add_semantic_memory("s1", "文本", source_refs=CROSS_DAY, dry_run=True)
    after = len(svc.store.list_agent_memories())
    assert r["ok"] is True and r["dry_run"] is True
    assert after == before  # 未落库


def test_add_writes_staging(svc):
    r = svc.add_semantic_memory("s1", "客厅夜间灯光频繁。",
                                source_refs=CROSS_DAY, topic_key="T", dry_run=False)
    assert r["ok"] is True and r["state"] == "staging"
    mem = svc.store.get_agent_memory(r["memory_id"])
    assert mem["state"] == "staging"


# ── 晋升条件（非 force）────────────────────────────────────────────────────
def test_promote_cross_day_no_conflict(svc):
    a = svc.add_semantic_memory("s1", "客厅夜间灯光频繁。",
                                source_refs=CROSS_DAY, topic_key="T", dry_run=False)
    svc._test_col.set_neighbors([])  # 无邻居 → 无护栏
    r = svc.promote_memory(a["memory_id"], session_id="s1", force=False)
    assert r["ok"] is True and r["state"] == "live"


def test_promote_blocked_on_duplicate(svc):
    a = svc.add_semantic_memory("s1", "客厅夜间灯光频繁。",
                                source_refs=CROSS_DAY, topic_key="T", dry_run=False)
    svc._test_col.set_neighbors([])
    svc.promote_memory(a["memory_id"], session_id="s1", force=False)  # A → live
    # B：同 topic 相同文本，邻居 sim=1.0(>=0.92) → duplicate
    b = svc.add_semantic_memory("s1", "客厅夜间灯光频繁。",
                                source_refs=CROSS_DAY, topic_key="T", dry_run=False)
    svc._test_col.set_neighbors([{"memory_id": a["memory_id"], "topic_key": "T", "distance": 0.0}])
    r = svc.promote_memory(b["memory_id"], session_id="s1", force=False)
    assert r["ok"] is False and r["state"] == "staging"
    assert r.get("conflict_scan", {}).get("duplicate") is True


def test_promote_conflict_goes_pending_review(svc):
    a = svc.add_semantic_memory("s1", "客厅夜间灯光频繁。",
                                source_refs=CROSS_DAY, topic_key="T", dry_run=False)
    svc._test_col.set_neighbors([])
    svc.promote_memory(a["memory_id"], session_id="s1", force=False)
    # C：同 topic 相反文本，邻居 distance=0.5 → sim=0.667(<=0.85) → conflict
    c = svc.add_semantic_memory("s1", "客厅夜间灯光从不开启。",
                                source_refs=CROSS_DAY, topic_key="T", dry_run=False)
    svc._test_col.set_neighbors([{"memory_id": a["memory_id"], "topic_key": "T", "distance": 0.5}])
    r = svc.promote_memory(c["memory_id"], session_id="s1", force=False)
    assert r["ok"] is False and r["state"] == "pending_review"


def test_promote_force_requires_privileged_session(svc):
    a = svc.add_semantic_memory("s1", "客厅夜间灯光频繁。",
                                source_refs=CROSS_DAY, topic_key="T", dry_run=False)
    svc._test_col.set_neighbors([])
    r = svc.promote_memory(a["memory_id"], session_id="s1", force=True)
    assert r["ok"] is False  # 非特权会话强行 force 被拒


# ── re-rank 公式 ──────────────────────────────────────────────────────────
def test_retrieve_rerank_formula(svc):
    a = svc.add_semantic_memory("s1", "客厅夜间灯光频繁。",
                                source_refs=CROSS_DAY, topic_key="T", dry_run=False)
    svc._test_col.set_neighbors([])
    svc.promote_memory(a["memory_id"], session_id="s1", force=False)
    # 让 retrieve 命中：邻居 distance=0.5 → sim=0.667, trust=0 → final=0.7*0.667+0.3*0.5
    svc._test_col.set_neighbors([{"memory_id": a["memory_id"], "topic_key": "T", "distance": 0.5}])
    res = svc.retrieve("客厅灯光")
    assert len(res) == 1
    expected = round(0.7 * (1 / 1.5) + 0.3 * ((0.0 + 1) / 2), 3)
    assert res[0]["final_score"] == expected


# ── 信任步进 ──────────────────────────────────────────────────────────────
def test_feedback_steps_trust(svc):
    a = svc.add_semantic_memory("s1", "客厅夜间灯光频繁。",
                                source_refs=CROSS_DAY, topic_key="T", dry_run=False)
    svc._test_col.set_neighbors([])
    svc.promote_memory(a["memory_id"], session_id="s1", force=False)
    # promote 自身已记一次正向反馈（+step），故晋升后信任已是 0.2
    assert float(svc.store.get_agent_memory(a["memory_id"])["trust"]) == pytest.approx(0.2, abs=1e-6)
    r = svc.feedback_memory(a["memory_id"], useful=True)
    assert r["ok"] is True
    # 再记一次正向反馈 → 0.4
    assert r["trust"] == pytest.approx(0.4, abs=1e-6)
    mem = svc.store.get_agent_memory(a["memory_id"])
    assert float(mem["trust"]) == pytest.approx(0.4, abs=1e-6)


# ── rollback 镜像回写（bug #2 回归）───────────────────────────────────────
def test_rollback_rewrites_mirror_to_revoked(svc):
    a = svc.add_semantic_memory("s1", "客厅夜间灯光频繁。",
                                source_refs=CROSS_DAY, topic_key="T", dry_run=False)
    svc._test_col.set_neighbors([])
    svc.promote_memory(a["memory_id"], session_id="s1", force=False)  # A → live (镜像 state=live)
    svc.rollback_agent_memory("s1")
    # 回滚必须 re-fetch 后用 revoked 状态回写镜像，否则 revoked 记忆继续污染检索
    rewrites = [m for (ids, m) in svc._test_col.upserts if a["memory_id"] in ids]
    assert rewrites, "rollback 未回写 chroma 镜像"
    assert rewrites[-1][0]["state"] == "revoked"
    assert svc.store.get_agent_memory(a["memory_id"])["state"] == "revoked"


# ── TTL 过期 ──────────────────────────────────────────────────────────────
def test_expire_overdue(svc):
    a = svc.add_semantic_memory("s1", "客厅夜间灯光频繁。",
                                source_refs=CROSS_DAY, topic_key="T", dry_run=False)
    svc._test_col.set_neighbors([])
    svc.promote_memory(a["memory_id"], session_id="s1", force=False)
    # 手动把过期时间设为过去
    conn = svc.store.connect()
    conn.execute("UPDATE agent_memories SET expires_at='2000-01-01' WHERE memory_id=?",
                 (a["memory_id"],))
    conn.commit()
    n = svc.store.expire_overdue_agent_memories()
    assert n >= 1
    assert svc.store.get_agent_memory(a["memory_id"])["state"] == "revoked"


# ── sweep 自动晋升 ──────────────────────────────────────────────────────────
def test_sweep_promotes_eligible_skips_duplicate(svc):
    # 先建一条 live（topic LIVE）作为重复判定基准
    base = svc.add_semantic_memory("s1", "基准记忆。", source_refs=CROSS_DAY,
                                   topic_key="LIVE", dry_run=False)
    svc._test_col.set_neighbors([])
    svc.promote_memory(base["memory_id"], session_id="s1", force=False)
    # 两条不同 topic 的 staging → 应被自动晋升
    x = svc.add_semantic_memory("s1", "记忆X。", source_refs=CROSS_DAY, topic_key="X", dry_run=False)
    y = svc.add_semantic_memory("s1", "记忆Y。", source_refs=CROSS_DAY, topic_key="Y", dry_run=False)
    # 一条同 topic=LIVE 的重复 staging → sweep 应跳过（不晋升）
    dup = svc.add_semantic_memory("s1", "基准记忆。", source_refs=CROSS_DAY,
                                  topic_key="LIVE", dry_run=False)
    # 让冲突扫描看到 LIVE 邻居（dup 同 topic 命中）
    svc._test_col.set_neighbors([{"memory_id": base["memory_id"], "topic_key": "LIVE", "distance": 0.0}])
    out = svc.sweep_promote_candidates()
    assert out["promoted"] == 2
    assert svc.store.get_agent_memory(x["memory_id"])["state"] == "live"
    assert svc.store.get_agent_memory(y["memory_id"])["state"] == "live"
    assert svc.store.get_agent_memory(dup["memory_id"])["state"] == "staging"  # 被跳过


def test_health_counts_and_mirror_dirty(svc):
    a = svc.add_semantic_memory("s1", "客厅夜间灯光频繁。",
                                source_refs=CROSS_DAY, topic_key="T", dry_run=False)
    svc._test_col.set_neighbors([])
    svc.promote_memory(a["memory_id"], session_id="s1", force=False)
    h = svc.health()
    assert h["states"]["live"] == 1
    assert h["mirror_dirty"] == 0
    assert h["chroma_available"] is True
    assert set(h["states"].keys()) == set(AGENT_STATES)
