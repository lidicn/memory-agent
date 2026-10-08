"""审计 P7 / 债务 D2：模板反馈的形参错位 + 自由文本无脱敏直入 Chroma metadata。

第七轮审计实测：唯一调用点 `api/nr_routes.py` 以 ``record_feedback(pattern_id, feedback)``
两个位置参调用，用户自由文本被当成 ``outcome``，``details`` 恒 None ⇒
置信度不动、备注不落库、异常只 print，接口却回「反馈已记录: {id} = {原文}」（PII 回显）。

这里锁三层：调用点把枚举与自由文本分流、自由文本先过 Store 的脱敏再入库、
失败不再声称成功。落库链用**真实 Store**（含 members 名册），不用桩子，
否则「脱敏有没有真跑」在读数上是看不出来的。
"""

import asyncio
import json
import os
import sys
import tempfile
import types

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from starlette.requests import Request  # noqa: E402

from memory_agent import store as store_mod  # noqa: E402
from memory_agent.api import nr_routes  # noqa: E402
from memory_agent.store import Store  # noqa: E402

# patterns 在模块层 import chromadb（本机无该依赖，容器内有）。这里只为拿到 PatternManager
# 类本身（用例全程用假 collection，不建真客户端），取完立刻把假模块摘掉，
# 免得别的用例 `pytest.importorskip("chromadb")` 的语义被污染。
import importlib.util  # noqa: E402

_REAL_CHROMA = importlib.util.find_spec("chromadb") is not None
if not _REAL_CHROMA:
    sys.modules["chromadb"] = types.SimpleNamespace(HttpClient=lambda *a, **k: None)

from memory_agent.patterns import PatternManager  # noqa: E402

if not _REAL_CHROMA:
    del sys.modules["chromadb"]


class _FakeCollection:
    """只承担 metadata 读写，不做向量检索。"""

    def __init__(self, metadata=None):
        self.metadata = dict(metadata or {})
        self.updated = []

    def get(self, ids=None, **kwargs):
        if not ids or ids[0] != self.metadata.get("id"):
            return {"metadatas": []}
        return {"metadatas": [dict(self.metadata)]}

    def update(self, ids=None, documents=None, metadatas=None):
        self.updated.append({"ids": list(ids or []), "metadatas": list(metadatas or [])})
        self.metadata.update(metadatas[0])


def _manager(metadata=None):
    pm = object.__new__(PatternManager)
    base = {"id": "pattern-1", "person": "张三", "category": "light",
            "description": "客厅灯", "status": "active", "confidence": 0.5,
            "sample_count": 3, "last_verified": ""}
    pm.collection = _FakeCollection({**base, **(metadata or {})})
    return pm


def _store_with_member():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    st = Store(path)
    st.init_schema()
    conn = st.connect()
    conn.execute(
        "INSERT INTO members(id,name,created_at,updated_at) VALUES('m-1','张三',?,?)",
        ("2026-10-03T00:00:00", "2026-10-03T00:00:00"),
    )
    conn.commit()
    return st, path


# ── patterns.record_feedback 本体 ──────────────────────────────────────────

def test_non_enum_outcome_does_not_move_confidence():
    pm = _manager()
    res = pm.record_feedback("pattern-1", "昨晚灯没关，答非所问")
    assert res["ok"] and res["outcome_applied"] is False
    assert res["new_confidence"] == 0.5


def test_enum_outcomes_move_confidence():
    for outcome, expected in (("success", 0.55), ("override", 0.4), ("failed", 0.45)):
        pm = _manager()
        res = pm.record_feedback("pattern-1", outcome)
        assert res["outcome_applied"] is True, outcome
        assert abs(res["new_confidence"] - expected) < 1e-9, (outcome, res)
        assert pm.collection.metadata["last_verified"], outcome


def test_note_only_feedback_does_not_claim_verification():
    """仅存档备注（置信度未动）不得把 last_verified 推进——它是「被验证过」的状态字。"""
    pm = _manager({"last_verified": "2026-01-01T00:00:00"})
    res = pm.record_feedback("pattern-1", "", details="只是备注")
    assert res["outcome_applied"] is False
    assert pm.collection.metadata["last_verified"] == "2026-01-01T00:00:00"
    assert pm.collection.metadata["last_feedback"] == "只是备注"


def test_details_are_truncated_to_feedback_max():
    pm = _manager()
    long_text = "灯" * (store_mod._FEEDBACK_TEXT_MAX + 200)
    res = pm.record_feedback("pattern-1", "success", details=long_text)
    assert res["details_recorded"] is True
    assert len(pm.collection.metadata["last_feedback"]) == store_mod._FEEDBACK_TEXT_MAX


def test_unknown_pattern_reports_not_ok():
    pm = _manager()
    res = pm.record_feedback("pattern-404", "success")
    assert res == {"ok": False, "error": "模板不存在"}


# ── 调用点：枚举 / 自由文本分流 + 脱敏 + 不再谎报成功 ──────────────────────

def _fake_request():
    return Request({"type": "http", "method": "POST", "path": "/api/nr/record-feedback",
                    "query_string": b"", "headers": []})


def _drive(body, pm, st, monkeypatch):
    async def _json_body(request):
        return body
    monkeypatch.setattr(nr_routes, "json_body", _json_body)
    monkeypatch.setattr(nr_routes, "runtime", lambda request=None: types.SimpleNamespace(
        patterns=pm, store=st))
    resp = asyncio.run(nr_routes.nr_record_feedback(_fake_request()))
    return resp.status_code, json.loads(resp.body)


def test_free_text_feedback_goes_to_details_not_outcome(monkeypatch):
    st, path = _store_with_member()
    try:
        pm = _manager()
        code, payload = _drive(
            {"pattern_id": "pattern-1", "feedback": "昨晚客厅灯其实没关"}, pm, st, monkeypatch)
        assert code == 200 and payload["ok"] is True
        stored = pm.collection.metadata
        assert stored["confidence"] == 0.5
        assert stored["last_feedback"] == "昨晚客厅灯其实没关"
    finally:
        st.close()
        os.remove(path)


def test_enum_value_in_feedback_field_still_counts_as_outcome(monkeypatch):
    st, path = _store_with_member()
    try:
        pm = _manager()
        code, payload = _drive({"pattern_id": "pattern-1", "feedback": "success"}, pm, st, monkeypatch)
        assert code == 200
        assert pm.collection.metadata["confidence"] == 0.55
        assert "置信度" in payload["message"]
    finally:
        st.close()
        os.remove(path)


def test_outcome_field_with_note_lands_on_both_columns(monkeypatch):
    st, path = _store_with_member()
    try:
        pm = _manager()
        _drive({"pattern_id": "pattern-1", "outcome": "override", "feedback": "用户手动改了"},
               pm, st, monkeypatch)
        assert pm.collection.metadata["confidence"] == 0.4
        assert pm.collection.metadata["last_feedback"] == "用户手动改了"
    finally:
        st.close()
        os.remove(path)


def test_raw_pii_never_reaches_storage_or_response(monkeypatch):
    """全链真跑：姓名→成员N、手机号打码，且响应不再回显原文。"""
    st, path = _store_with_member()
    try:
        pm = _manager()
        raw = "张三说客厅灯不对，联系 13812345678 处理"
        code, payload = _drive({"pattern_id": "pattern-1", "feedback": raw}, pm, st, monkeypatch)
        assert code == 200
        stored = pm.collection.metadata["last_feedback"]
        assert "张三" not in stored and "13812345678" not in stored
        assert "成员" in stored
        body_text = json.dumps(payload, ensure_ascii=False)
        assert "张三" not in body_text and raw not in body_text
    finally:
        st.close()
        os.remove(path)


def test_missing_pattern_id_returns_error_not_ok(monkeypatch):
    st, path = _store_with_member()
    try:
        code, payload = _drive({"feedback": "success"}, _manager(), st, monkeypatch)
        assert code == 400 and payload["ok"] is False
    finally:
        st.close()
        os.remove(path)


def test_unknown_pattern_returns_404_not_success(monkeypatch):
    st, path = _store_with_member()
    try:
        code, payload = _drive({"pattern_id": "pattern-404", "feedback": "success"},
                               _manager(), st, monkeypatch)
        assert code == 404 and payload["ok"] is False
    finally:
        st.close()
        os.remove(path)


def test_vector_store_failure_is_reported_as_failure(monkeypatch):
    st, path = _store_with_member()
    try:
        pm = _manager()

        def _boom(*a, **k):
            raise RuntimeError("chroma down")
        monkeypatch.setattr(pm, "record_feedback", _boom)
        code, payload = _drive({"pattern_id": "pattern-1", "feedback": "success"}, pm, st, monkeypatch)
        assert code == 503 and payload["ok"] is False
        assert "已记录" not in payload.get("error", "")
    finally:
        st.close()
        os.remove(path)


def test_patterns_unavailable_returns_503(monkeypatch):
    st, path = _store_with_member()
    try:
        code, payload = _drive({"pattern_id": "pattern-1", "feedback": "success"}, None, st, monkeypatch)
        assert code == 503 and payload["ok"] is False
    finally:
        st.close()
        os.remove(path)
