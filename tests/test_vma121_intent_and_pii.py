"""vMA-1.2.1 测试：intent_inference 场景图双路径融合 + PII 脱敏 + 负样本聚类→规则建议。

覆盖：
1. 意图推断双路径：有场景图且规则带 scene_graph_triggers → 置信度加分；
   场景图缺失/为空/坏 JSON → 降级原路径不报错；
2. PII 脱敏：negative_samples.sanitize_text（姓名→成员N、手机/邮箱/身份证、
   ≥7 位连续数字兜底 ***）与 Store._sanitize_pii；记忆写入路径（teach_signal soft）
   落库文本无原始 PII；
3. 负样本 §5.1：rejected 候选规则 + feedback_down 记忆采集、三元组聚类（≥3 成簇）、
   建议规则落 staging/user_confirmed=0；
4. 红线 §5.1.4：staging 候选规则永不影响 rule_engine 匹配结果（已合规→补测）。
"""
import asyncio
import json
import os
import re
import sys
import tempfile
import types
from datetime import datetime, timedelta

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import intent_inference as imod  # noqa: E402
from memory_agent.intent_inference import infer_intent  # noqa: E402
from memory_agent.store import Store  # noqa: E402
from memory_agent.negative_samples import (  # noqa: E402
    cluster_negative_samples,
    collect_negative_samples,
    run_negative_sample_analysis,
    sanitize_text,
)
from memory_agent.rule_engine import ActiveRuleEngine  # noqa: E402


def make_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    st = Store(path)
    st.init_schema()
    return st


# ── 1. 意图推断双路径融合 ──────────────────────────────────────────────────

_SG_RULE = {
    "intent": "exercise",
    "label": "想运动",
    "triggers": ["客厅清空"],
    "secondary_triggers": [],
    "scene_graph_triggers": ["瑜伽垫"],
    "confidence": 0.5,
    "suggestion": "可播放运动音乐",
    "room": "客厅",
}


def _ev(with_sg):
    now = datetime.now().isoformat(timespec="seconds")
    ev = {"server_ts": now, "action": "客厅清空", "scene": "", "room": "客厅",
          "persons_json": "[]"}
    if with_sg is not None:
        ev["scene_graph_json"] = with_sg
    return [ev]


def test_intent_scene_graph_boosts_confidence(monkeypatch):
    monkeypatch.setattr(imod, "INTENT_RULES", [_SG_RULE])
    sg = json.dumps({"objects": ["瑜伽垫"], "relations": []}, ensure_ascii=False)
    with_sg = infer_intent(_ev(sg), window_min=10)
    without_sg = infer_intent(_ev(None), window_min=10)
    assert with_sg and without_sg
    assert with_sg["intent"] == without_sg["intent"] == "exercise"
    # 场景图命中 scene_graph_triggers → 高置信路径
    assert with_sg["confidence"] > without_sg["confidence"]
    assert with_sg["confidence"] == pytest.approx(0.54, abs=0.01)
    assert without_sg["confidence"] == pytest.approx(0.5, abs=0.001)


def test_intent_scene_graph_missing_or_empty_degrades(monkeypatch):
    monkeypatch.setattr(imod, "INTENT_RULES", [_SG_RULE])
    for sg_val in (None, "{}", '{"objects": [], "relations": []}'):
        out = infer_intent(_ev(sg_val), window_min=10)
        assert out is not None and out["confidence"] == pytest.approx(0.5, abs=0.001)


def test_intent_bad_scene_graph_json_no_crash(monkeypatch):
    monkeypatch.setattr(imod, "INTENT_RULES", [_SG_RULE])
    out = infer_intent(_ev("这不是JSON{{{"), window_min=10)
    assert out is not None and out["intent"] == "exercise"
    # 坏场景图按无场景图处理，置信度回落到原路径
    assert out["confidence"] == pytest.approx(0.5, abs=0.001)


def test_intent_scene_graph_dict_form(monkeypatch):
    monkeypatch.setattr(imod, "INTENT_RULES", [_SG_RULE])
    out = infer_intent(_ev({"objects": ["瑜伽垫"], "relations": []}), window_min=10)
    assert out["confidence"] > 0.5


def test_intent_default_rules_untouched_with_scene_graph():
    """内置规则（无 scene_graph_triggers）：带场景图不改变原结果、不报错。"""
    now = datetime.now().isoformat(timespec="seconds")
    ev = [{"server_ts": now, "action": "电视打开", "scene": "", "room": "客厅",
           "persons_json": "[]",
           "scene_graph_json": json.dumps({"objects": ["遥控器"], "relations": []})}]
    base = infer_intent([dict(e) for e in ev if "scene_graph_json" not in e]
                        or [{"server_ts": now, "action": "电视打开", "scene": "",
                             "room": "客厅", "persons_json": "[]"}], window_min=10)
    with_sg = infer_intent(ev, window_min=10)
    assert base and with_sg
    assert with_sg["intent"] == base["intent"] == "watch_tv"
    assert with_sg["confidence"] == base["confidence"]


# ── 2. PII 脱敏 ────────────────────────────────────────────────────────────

def test_sanitize_text_contacts_and_digits():
    out = sanitize_text(
        "电话13812345678，邮箱abcdef@gmail.com，身份证11010119900307231X，订单123456789")
    assert "13812345678" not in out
    assert "abcdef@" not in out
    assert "11010119900307231X" not in out
    assert "123456789" not in out
    assert "***" in out
    # 兜底：结果中不再有 ≥7 位连续数字
    assert not re.search(r"\d{7,}", out)


def test_sanitize_text_member_names():
    out = sanitize_text("张三丰和张三明天去打球", ["张三丰", "张三"])
    assert "张三" not in out
    assert "成员1" in out and "成员2" in out


def test_store_sanitize_pii_existing():
    st = make_store()
    try:
        s = st._sanitize_pii("手机13812345678 邮箱someone@mail.com")
        assert "13812345678" not in s
        assert "someone@" not in s
    finally:
        st.close()
        os.remove(st.db_path)


class FakeCollection:
    def __init__(self):
        self._docs = {}

    def add(self, ids=None, documents=None, metadatas=None, embeddings=None):
        for i, d in enumerate(ids):
            self._docs[d] = documents[i] if documents else ""

    def get(self, ids=None, where=None, limit=None):
        if ids is not None:
            return {"ids": list(ids),
                    "documents": [self._docs.get(i, "") for i in ids],
                    "metadatas": [None] * len(ids or [])}
        return {"ids": list(self._docs), "documents": list(self._docs.values()),
                "metadatas": list(self._docs.values())}

    def query(self, query_texts=None, n_results=5, where=None):
        return {"ids": [[]], "distances": [[]], "metadatas": [[]], "documents": [[]]}

    def delete(self, ids=None, where=None):
        for i in (ids or []):
            self._docs.pop(i, None)

    def count(self):
        return len(self._docs)


class FakeChroma:
    def __init__(self):
        self._c = {}

    def collection(self, name, get_or_create=False):
        return self._c.setdefault(name, FakeCollection())


class FakeHistory:
    def __init__(self):
        self.agent_collection = FakeChroma().collection("agent_memory")

    @property
    def window_start(self):
        return (datetime.utcnow() - timedelta(days=14)).isoformat()


def test_teach_signal_soft_memory_sanitized():
    """记忆写入路径（soft 教学→agent_memories）落库文本无原始 PII。"""
    from memory_agent.agent_memory import AgentMemoryService
    from memory_agent.signal_learning import SignalLearningService

    st = make_store()
    try:
        # 注入一个成员名
        conn = st.connect()
        conn.execute(
            "INSERT INTO members(id,name,created_at,updated_at) VALUES(?,?,?,?)",
            ("m-kevin", "张三", datetime.now().isoformat(), datetime.now().isoformat()),
        )
        conn.commit()

        am = AgentMemoryService(types.SimpleNamespace(tz_offset_hours=8), st, FakeHistory())
        svc = SignalLearningService(st, am)
        r = svc.teach_signal(
            entity_id="media.tv", kind="soft", scope="watching_tv",
            text="张三的手机号是13812345678，邮箱abcdef@gmail.com，不应判为看电视",
        )
        assert r["ok"] and r["memory_id"]
        row = st.get_agent_memory(r["memory_id"])
        assert "13812345678" not in row["text"]
        assert "abcdef@" not in row["text"]
        assert "张三" not in row["text"]
        assert "成员" in row["text"] and "***" in row["text"]
    finally:
        st.close()
        os.remove(st.db_path)


# ── 3. 负样本采集/聚类/建议落点 ────────────────────────────────────────────

def _reject_rule(st, name, entity, window, infer):
    rid, _ = st.upsert_candidate_rule(
        name=name, steps=[{"entity_id": entity}], time_window=window,
        infer=infer, confidence=0.6, source="inference")
    st.set_candidate_rule_status(rid, "rejected")
    return rid


def test_collect_negative_samples_dual_source():
    st = make_store()
    try:
        _reject_rule(st, "规则A", "media.tv", "19:00-22:00", "看电视")
        for i in range(3):
            mid = st.add_agent_memory(
                session_id="s1", text=f"洞察{i}：客厅在看电视",
                topic_key="insight_watching_tv",
                tags_json=json.dumps(["member:张三"]),
                source_refs_json=json.dumps([f"entity:media.tv_{i}"]),
                ttl_days=30, state="live")
            st.record_agent_feedback(mid, useful=False)
        samples = collect_negative_samples(st)
        rejected = [s for s in samples if s["source"] == "rejected_candidate_rule"]
        memory = [s for s in samples if s["source"] == "memory_feedback_down"]
        assert len(rejected) == 1 and len(memory) == 3
        assert rejected[0]["entity_id"] == "media.tv"
        assert rejected[0]["predicted_label"] == "看电视"
        assert rejected[0]["time_slot"] == "19:00-22:00"
        assert memory[0]["predicted_label"] == "insight_watching_tv"
        assert memory[0]["entity_id"] == "member:张三"  # tags 前缀优先于 source_refs
        # 无「预测为 A 未发生」落库记录 → 不虚构第三源
        assert {s["source"] for s in samples} == {"rejected_candidate_rule",
                                                  "memory_feedback_down"}
    finally:
        st.close()
        os.remove(st.db_path)


def test_cluster_threshold_min_three():
    samples = ([{"entity_id": "a", "time_slot": "x", "predicted_label": "y",
                 "source": "s", "ref": f"r{i}", "detail": ""} for i in range(3)]
               + [{"entity_id": "b", "time_slot": "x", "predicted_label": "y",
                   "source": "s", "ref": "r9", "detail": ""}])
    clusters = cluster_negative_samples(samples, min_count=3)
    assert len(clusters) == 1
    assert clusters[0]["entity_id"] == "a" and clusters[0]["count"] == 3


def test_run_negative_sample_analysis_writes_staging(monkeypatch):
    st = make_store()
    try:
        conn = st.connect()
        conn.execute(
            "INSERT INTO members(id,name,created_at,updated_at) VALUES(?,?,?,?)",
            ("m-kevin", "张三", datetime.now().isoformat(), datetime.now().isoformat()))
        conn.commit()
        for i in range(3):
            _reject_rule(st, f"负测试规则{i}", "media.tv", "19:00-22:00", "看电视")
        # 记忆负样本文本带 PII → 建议 evidence 落库必须脱敏
        for i in range(3):
            mid = st.add_agent_memory(
                session_id="s", text=f"张三反馈13812345678不是看电视的证据{i}",
                topic_key="insight_x", tags_json="[]",
                source_refs_json=json.dumps(["entity:media.tv"]),
                ttl_days=30, state="live")
            st.record_agent_feedback(mid, useful=False)

        res = run_negative_sample_analysis(st)
        assert res["ok"] and res["total_negative"] == 6 and res["written"] >= 2

        staged = [r for r in st.list_candidate_rules(status="staging")
                  if r["source"] == "negative_cluster"]
        assert staged
        neg_tv = [r for r in staged if r["infer"] == "非看电视"]
        assert len(neg_tv) == 1
        rule = neg_tv[0]
        assert rule["status"] == "staging"
        assert rule.get("user_confirmed", 0) == 0
        assert rule["steps"][0]["entity_id"] == "media.tv"
        # 记忆簇建议的 evidence 文本已脱敏（原文含姓名+手机号）
        insight = [r for r in staged if r["infer"] == "非insight_x"]
        assert insight
        ev_text = json.dumps(insight[0]["evidence"], ensure_ascii=False)
        assert "13812345678" not in ev_text and "张三" not in ev_text
        # 幂等：再跑一次不新增（按 name 去重刷新）
        before = len(st.list_candidate_rules(status="staging"))
        run_negative_sample_analysis(st)
        assert len(st.list_candidate_rules(status="staging")) == before
        # 红线：不会自动改成 accepted/confirmed
        assert st.list_candidate_rules(status="accepted") == []
    finally:
        st.close()
        os.remove(st.db_path)


# ── 4. 红线：staging 永不影响规则引擎（已合规→补测） ───────────────────────

def _ensure_active_rules_table(st):
    st.connect().execute(
        """CREATE TABLE IF NOT EXISTS active_rules (
            rule_id TEXT PRIMARY KEY, name TEXT, description TEXT,
            condition_json TEXT, action_json TEXT, enabled INTEGER DEFAULT 1,
            cooldown_seconds INTEGER DEFAULT 300, rule_type TEXT DEFAULT 'static',
            created_at TEXT, updated_at TEXT)"""
    )
    st.connect().commit()


def test_rule_engine_ignores_staging_candidate_rules():
    st = make_store()
    try:
        _ensure_active_rules_table(st)
        engine = ActiveRuleEngine(st)
        engine.add_rule("陌生人告警", {"kind": "face_unknown", "room": "客厅"},
                        {"type": "log"}, enabled=True)
        event = {"kind": "face_unknown", "room": "客厅", "person": "陌生人",
                 "confidence": 0.8, "server_ts": datetime.now().isoformat()}
        baseline = [r["rule_id"] for r in engine.match_event(dict(event))]
        assert len(baseline) == 1

        # 产出 staging 负样本建议（kind/room 与事件完全同构的三元组）
        for i in range(3):
            _reject_rule(st, f"引擎隔离{i}", "face_unknown", "19:00-22:00", "face_unknown")
        res = run_negative_sample_analysis(st)
        assert res["written"] >= 1

        engine._index_dirty = True  # 强制重建索引，排除缓存因素
        after = [r["rule_id"] for r in engine.match_event(dict(event))]
        assert after == baseline, "staging 候选规则不应改变引擎匹配结果"

        # 引擎只消费 active_rules：候选建议不在其中
        names = [r["name"] for r in engine.list_rules(enabled_only=False)]
        assert all(not n.startswith("负样本建议") for n in names)

        # 禁用规则也不匹配（引擎生效的只有用户显式创建且 enabled 的规则）
        rid = baseline[0]
        engine.update_rule(rid, enabled=False)
        engine._index_dirty = True
        assert engine.match_event(dict(event)) == []
    finally:
        st.close()
        os.remove(st.db_path)


# ── 5. API 手动触发端点 ────────────────────────────────────────────────────

def _post_request(body, rt, user=None, path="/api/behaviors/negative-samples/suggestions"):
    from starlette.requests import Request

    app = types.SimpleNamespace(state=types.SimpleNamespace(runtime=rt))
    scope = {
        "type": "http", "method": "POST", "path": path,
        "query_string": b"", "headers": [], "app": app,
        "state": {"user": user} if user else {},
    }

    async def receive():
        return {"type": "http.request",
                "body": json.dumps(body).encode("utf-8"), "more_body": False}

    return Request(scope, receive)


def test_negative_samples_endpoint_requires_login():
    from memory_agent.api import behavior_routes

    st = make_store()
    try:
        rt = types.SimpleNamespace(store=st, config=types.SimpleNamespace(tz_offset_hours=8))
        req = _post_request({}, rt)
        resp = asyncio.run(behavior_routes.negative_sample_suggestions(req))
        assert resp.status_code == 401
    finally:
        st.close()
        os.remove(st.db_path)


def test_negative_samples_endpoint_manual_trigger():
    from memory_agent.api import behavior_routes

    st = make_store()
    try:
        for i in range(3):
            _reject_rule(st, f"端点规则{i}", "sensor.hall", "any", "有人")
        rt = types.SimpleNamespace(store=st, config=types.SimpleNamespace(tz_offset_hours=8))
        req = _post_request({"min_count": 3}, rt, user={"id": "u", "is_admin": True})
        body = json.loads(asyncio.run(behavior_routes.negative_sample_suggestions(req)).body)
        assert body["ok"] is True
        assert body["total_negative"] == 3 and body["written"] == 1
        staged = st.list_candidate_rules(status="staging")
        assert any(r["infer"] == "非有人" and r["source"] == "negative_cluster"
                   for r in staged)
    finally:
        st.close()
        os.remove(st.db_path)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
