"""DCD 20261014《print 布尔出口五处修法》裁甲的行为腿（R1–R5）。

**钉的是载荷，⛔ stdout**：裁定 §四 明令「每处一条行为腿（注入 chroma／身份层异常，
断言载荷带降级声明，⛔ 不许只断言 print 出现）」。所以这里没有任何一条断言去看
`capsys`——print 出不出声与这一格有没有被消费方读到，是两件不同的事。

键形制（裁定 §二）：`degraded`（顶层快速判读位，恒在）＋ `degraded_scans`（结构化，
每项 `site`／`code`／`fields`／`reason`）。码取自 `adm_linkage.code()`，⛔ 在本仓另立
第二份字符串词表（裁定 §一 硬前置第 3 条）。

R2 的分数出 `null`＝判例 1（"降级反而更乐观"的值是最坏的形状）；R3 单独落 `ok=False`
＝裁定 §三（那一处本来就有一枚真失败，属"把假回执改真"，⛔ 新键）。
"""

import asyncio
import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

import pytest  # noqa: E402

from memory_agent import adm_linkage  # noqa: E402
from memory_agent.agent_memory import AgentMemoryService  # noqa: E402
from memory_agent.arena import ArenaService  # noqa: E402
from memory_agent.config import Config  # noqa: E402
from memory_agent.insights_legacy import InsightService as LegacyInsight  # noqa: E402
from memory_agent.store import Store  # noqa: E402

DEGRADED_FIELDS = {"degraded", "degraded_scans"}


# ── 假件 ───────────────────────────────────────────────────────────────────
class AmsvcConfig:
    """复刻 agent_memory 里 getattr 默认值的显式配置（与既有单测同口径）。"""

    agent_trust_step = 0.2
    trust_strict_threshold = -0.3
    agent_retrieve_k = 20
    agent_dup_sim = 0.92
    agent_conflict_sim = 0.85
    agent_promote_min_days = 2
    agent_corroborate_min_conf = 0.6
    agent_default_ttl_days = 30
    tz_offset_hours = 0.0


class Col:
    """可控的 chroma 集合桩：query／upsert 各自能"就是抛"，其余走空结果。"""

    def __init__(self, query_raises=False, upsert_raises=False, response=None):
        self.query_raises = query_raises
        self.upsert_raises = upsert_raises
        self.response = response or {"ids": [[]], "distances": [[]],
                                     "documents": [[]], "metadatas": [[]]}
        self.upserts = []

    def query(self, query_texts, where=None, n_results=5, include=None):
        if self.query_raises:
            raise RuntimeError("vector store is down")
        return self.response

    def upsert(self, ids, documents, metadatas):
        if self.upsert_raises:
            raise RuntimeError("flush failed")
        self.upserts.append((list(ids), list(documents), list(metadatas)))


class History:
    def __init__(self, col):
        self._col = col

    @property
    def agent_collection(self):
        return self._col

    @property
    def arena_collection(self):
        return self._col


class ArenaStore:
    def get_arena_snapshot(self, arena_id, version=None):
        return None

    def add_arena_result(self, *a, **k):
        return "arena_x"


def _amsvc(col):
    tmp = __import__("tempfile").mkdtemp(prefix="dcd1014_")
    db = os.path.join(tmp, "t.db")
    store = Store(db, tz_offset_hours=0.0)
    store.init_schema()
    store.insert_events([
        {"id": f"ev-{d}", "entity_id": "light.living", "ts": f"{d}T21:00:00", "day": d}
        for d in ("2026-08-03", "2026-08-05", "2026-08-06")
    ])
    svc = AgentMemoryService(AmsvcConfig(), store, History(col))
    svc._test_db = db
    return svc, svc.store


REFS = ["event:ev-2026-08-03", "event:ev-2026-08-05", "event:ev-2026-08-06"]


def _one_scan(payload, site):
    hits = [s for s in payload["degraded_scans"] if s["site"] == site]
    assert len(hits) == 1, f"期望 {site} 恰有一条声明，实读={payload['degraded_scans']}"
    return hits[0]


# ── R1｜agent_memory.merge_semantic_memory ────────────────────────────────
def test_r1_chroma缺席时载荷声明降级且相似度出null():
    svc, _store = _amsvc(None)
    r = svc.merge_semantic_memory(session_id="s1", text="客厅灯光。", topic_key="T",
                                  source_refs=REFS)
    assert r["ok"] is True and r["action"] == "added"
    assert r["degraded"] is True and r["similarity"] is None
    # 旧键语义不动（判例「旧键语义不动，新键承载新语义」）：note 仍在，值原样。
    assert r["note"] == "chroma 不可用，未合并"
    entry = _one_scan(r, "agent_memory.vector_recall")
    assert entry["code"] == "ADM_ERR_INTERNAL"
    assert "similarity" in entry["fields"] and "action" in entry["fields"]


def test_r1_召回抛异常与chroma缺席同形():
    """R1 的正病：同一种"没做成合并"，两条路以前一条有 note 一条没有。裁甲后同形。"""
    svc, _store = _amsvc(Col(query_raises=True))
    r = svc.merge_semantic_memory(session_id="s1", text="客厅灯光。", topic_key="T",
                                  source_refs=REFS)
    assert r["ok"] is True and r["action"] == "added"
    assert r["degraded"] is True and r["similarity"] is None
    entry = _one_scan(r, "agent_memory.vector_recall")
    assert entry["reason"] == "RuntimeError"  # 只给异常类型名，⛔ 原文进载荷


def test_r1_正常召回时两把键恒在且不谎报降级():
    """负控：⛔ 把"扫描做完了"也报成降级——永远红的声明与没有声明等价。"""
    svc, _store = _amsvc(Col())
    r = svc.merge_semantic_memory(session_id="s1", text="全新的记忆内容。",
                                  topic_key="NEW", source_refs=REFS)
    assert r["ok"] is True and r["action"] == "added"
    assert r["degraded"] is False and r["degraded_scans"] == []
    assert r["similarity"] == 0.0  # 真扫过 ⇒ 数还在（⛔ 借降级把有值改成 null）


# ── R2／R3｜arena.evaluate_creativity ─────────────────────────────────────
def _arena(col):
    return ArenaService(Config(), ArenaStore(), History(col), None, None)


def test_r2_向量查询抛异常时新颖度出null():
    svc = _arena(Col(query_raises=True))
    r = asyncio.run(svc.evaluate_creativity("study", "夜间护眼模式", "灯光联动", ["设备1"]))
    assert r["degraded"] is True
    assert r["creativity_score"] is None and r["novelty_score"] is None
    entry = _one_scan(r, "arena.vector_recall")
    assert entry["code"] == "ADM_ERR_INTERNAL"
    assert {"creativity_score", "novelty_score"} <= set(entry["fields"])
    assert "查重未完成" in r["feedback"]


def test_r2_题目库集合缺席同样声明():
    """chroma 整块缺席时 sim 停在初值 0.0 ⇒ novelty 会算成 1.0，与抛异常同病。"""
    svc = _arena(None)
    r = asyncio.run(svc.evaluate_creativity("study", "夜间护眼模式", "灯光联动", ["设备1"]))
    assert r["degraded"] is True
    assert r["creativity_score"] is None and r["novelty_score"] is None
    assert _one_scan(r, "arena.vector_recall")["reason"] == "题目库集合缺席"


def test_r2_正常查重时分数照常出且不谎报降级():
    svc = _arena(Col())
    r = asyncio.run(svc.evaluate_creativity("study", "夜间护眼模式", "灯光联动", ["设备1"]))
    assert r["degraded"] is False and r["degraded_scans"] == []
    assert r["novelty_score"] == 1.0  # 空题库：真的没有近邻，这个 1.0 是真读数


def test_r3_题目库写入失败回ok_false且不走降级键():
    """裁定 §三：这一处本来就有真失败，属"把假回执改真"，⛔ 新键、⛔ 拖慢 Q1。"""
    svc = _arena(Col(upsert_raises=True))
    r = asyncio.run(svc.evaluate_creativity("study", "夜间护眼模式", "灯光联动", ["设备1"]))
    assert r["ok"] is False
    assert r["code"] == "ADM_ERR_INTERNAL"
    assert "未锁定" in r["error"]
    assert r["degraded"] is False  # 查重做成了；写失败另有 ok=False 承载


# ── R4／R5｜insights_legacy.InsightService.device_health ─────────────────
class _Raise:
    def __init__(self, exc):
        self._exc = exc

    def list_all(self):
        raise self._exc


class _Eq:
    def __init__(self, entity_id="", logical_id=""):
        self.entity_id = entity_id
        self.logical_id = logical_id


class _Tpl:
    def __init__(self, eqs):
        self.entities = eqs


class _Templates:
    def __init__(self, raises=None):
        self._raises = raises
        self.tpls = [_Tpl([_Eq(logical_id="light.bed")])]

    def list_all(self):
        if self._raises:
            raise self._raises
        return self.tpls


class _Identity:
    def __init__(self, raises=False):
        self.raises = raises

    def resolve(self, lid):
        if self.raises:
            raise RuntimeError("identity layer is down")
        return ["dev.1"], {}


class _RT:
    def __init__(self, raises=None, identity_raises=False):
        self.templates = _Templates(raises)
        self.identity = _Identity(identity_raises)


def _health_svc(rt=None, quality_raises=False, identity_raises=False):
    svc = LegacyInsight(Config(), store=object())
    svc.entity_catalog = lambda *a, **k: {
        "total": 2,
        "rooms": {"书房": {"entities": [
            {"entity_id": "dev.1", "has_data": True, "stale_days": 0.0},
            {"entity_id": "dev.2", "has_data": True, "stale_days": 0.0},
        ]}},
    }
    svc._num_stale = lambda e: 0.0
    svc._get_rt = lambda: (rt if rt is not None
                           else _RT(raises=RuntimeError("templates gone"),
                                    identity_raises=identity_raises))
    svc.resolve_range = lambda *a, **k: ("2026-10-01T00:00:00", "2026-10-08T00:00:00", {})

    def _quality(*a, **k):
        if quality_raises:
            raise RuntimeError("scan blew up")
        return []

    svc.data_quality_issues = _quality
    return svc


def test_r4_引用标记整段失败时聚合成null并点名字段():
    svc = _health_svc()
    r = svc.device_health(days=7)
    assert r["ok"] is True  # 裁甲：⛔ 改 ok 的语义（乙/丙档被裁定否掉）
    assert r["degraded"] is True
    assert r["referenced_entities"] is None and r["referenced_entity_ids"] is None
    entry = _one_scan(r, "insights.device_health_reference_marks")
    assert entry["reason"] == "RuntimeError"
    assert {"referenced_entities", "referenced_entity_ids", "referenced"} <= set(entry["fields"])


def test_r4_身份层部分解析失败也声明():
    """内层 except 以前是 pass：一台设备没解析上，聚合数就少报，同样⛔静默。"""
    svc = _health_svc(rt=_RT(raises=None, identity_raises=True))
    r = svc.device_health(days=7)
    assert r["degraded"] is True
    assert r["referenced_entities"] is None
    assert "部分解析失败" in _one_scan(r, "insights.device_health_reference_marks")["reason"]


def test_r5_质量扫描失败时数据面出null并独立声明():
    """R4/R5 同载荷：裁定 §2.2 裁"两件独立失败 ⇒ 各自可声明"，只给顶层＝又分不清哪格没扫。"""
    svc = _health_svc(quality_raises=True)
    r = svc.device_health(days=7)
    assert r["ok"] is True and r["degraded"] is True
    assert r["data_quality_issues"] is None and r["data_quality_count"] is None
    entry = _one_scan(r, "insights.device_health_quality_scan")
    assert entry["code"] == "ADM_ERR_INTERNAL"
    assert {"data_quality_issues", "data_quality_count"} <= set(entry["fields"])
    # 两处失败同时成立时，两条声明各自在位（⛔ 互相顶掉）
    assert len(r["degraded_scans"]) == 2


def test_r4r5_干净路径两把键恒在():
    svc = _health_svc(rt=_RT(raises=None))
    r = svc.device_health(days=7)
    assert r["degraded"] is False and r["degraded_scans"] == []
    # dev.1 经身份层解析出真引用 ⇒ 聚合数是真的（⛔ 借"恒在键"把有值抹成 null）
    assert r["referenced_entities"] == 1 and r["referenced_entity_ids"] == ["dev.1"]
    assert r["data_quality_issues"] == [] and r["data_quality_count"] == 0


# ── 形制锁 ────────────────────────────────────────────────────────────────
def test_降级声明用的码不在本仓另立词表():
    """裁定 §一 硬前置第 3 条：码必须来自 adm.errors 那一组常量语义。"""
    payload = {"ok": True}
    entry = adm_linkage.degrade_scan("x.y", "ADM_ERR_INTERNAL", ("a",), "r")
    assert adm_linkage.is_adm_err(entry["code"]) is True
    # 传错名字⛔ 悄悄造出一个新码：走 code() 的兜底档
    bad = adm_linkage.degrade_scan("x.y", "ADM_ERR_NOT_A_CODE", ("a",), "r")
    assert bad["code"] == "ADM_ERR_INTERNAL"
    out = adm_linkage.with_degradation(payload, [entry])
    assert set(out) >= DEGRADED_FIELDS
    assert out["degraded"] is True and out["degraded_scans"] == [entry]


def test_摘掉声明的载荷会被行为腿判红():
    """负控的负控：把 with_degradation 摘掉 ⇒ 键直接没了，行为腿当场红（⛔ 静默通过）。"""
    out = adm_linkage.with_degradation({"ok": True}, [])
    assert out["degraded"] is False and out["degraded_scans"] == []
    assert "degraded_scans" in out  # 恒在：⛔ 只在出事时才有这一格
