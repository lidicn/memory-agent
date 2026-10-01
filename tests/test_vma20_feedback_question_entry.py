"""DCD 2026-10-01 R1：反馈入口必须携带"当初问了什么"（vMA-2.0 badcase 门的取料口）。

裁定 A 保留知识图谱门（≥10 条需要多跳推理的 badcase），但门要可测：
生产反馈面此前只接 `memory_id + useful`，👎 事后无法还原问题文本，
badcase 永远攒不出来。这里锁三件事——问题/评论能落库、落库前走同一套
PII 脱敏（顺带结掉审计债 D1：comment 过去脱敏完没有列可写，纯空转）、
取料口默认只返回带问题文本的 👎。
"""

import asyncio
import os
import sys
import tempfile
import types

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from starlette.requests import Request  # noqa: E402

from memory_agent.agent_memory import AgentMemoryService  # noqa: E402
from memory_agent.api import agent_memory_routes as routes  # noqa: E402
from memory_agent.store import Store  # noqa: E402
from memory_agent import store as store_mod  # noqa: E402


class FakeHistory:
    def add(self, *a, **k):
        return None


def _store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Store(path)
    conn = st.connect()
    conn.execute(
        "INSERT INTO members(id,name,created_at,updated_at) VALUES('m-k','张三',?,?)",
        ("2026-10-01T00:00:00", "2026-10-01T00:00:00"),
    )
    conn.commit()
    return st, path


def _add_memory(st, text="客厅灯在 23 点被打开"):
    conn = st.connect()
    conn.execute(
        """INSERT INTO agent_memories(memory_id, session_id, text, state, trust,
               ttl_days, feedback_up, feedback_down, created_at, updated_at, expires_at)
           VALUES(?,?,?,'staging',0.3,30,0,0,?,?,?)""",
        ("mem-1", "sess-1", text,
         "2026-10-01T00:00:00", "2026-10-01T00:00:00", "2026-10-31"),
    )
    conn.commit()


def test_columns_exist_after_migration():
    st, path = _store()
    try:
        cols = {r[1] for r in st.connect().execute("PRAGMA table_info(agent_memories)")}
        assert {"feedback_question", "feedback_comment"} <= cols
    finally:
        st.close()
        os.remove(path)


def test_question_and_comment_are_persisted_and_masked():
    st, path = _store()
    try:
        _add_memory(st)
        res = st.record_agent_feedback(
            "mem-1", False,
            question="张三昨晚 23 点是不是把客厅灯关了？手机13812345678",
            comment="答错了，其实是 Kevin 开的",
        )
        assert res["question"] and "张三" not in res["question"]
        assert "13812345678" not in res["question"]
        row = st.get_agent_memory("mem-1")
        assert "张三" not in row["feedback_question"]
        assert "成员" in row["feedback_question"]
        assert row["feedback_comment"] and "张三" not in row["feedback_comment"]
        assert row["feedback_down"] == 1
    finally:
        st.close()
        os.remove(path)


def test_feedback_text_is_truncated():
    st, path = _store()
    try:
        _add_memory(st)
        long_q = "灯" * (store_mod._FEEDBACK_TEXT_MAX + 200)
        res = st.record_agent_feedback("mem-1", True, question=long_q)
        assert len(res["question"]) == store_mod._FEEDBACK_TEXT_MAX
    finally:
        st.close()
        os.remove(path)


def test_negative_feedback_gate_material():
    st, path = _store()
    try:
        _add_memory(st)
        conn = st.connect()
        conn.execute(
            """INSERT INTO agent_memories(memory_id, session_id, text, state, trust,
                   ttl_days, feedback_up, feedback_down, created_at, updated_at, expires_at)
               VALUES('mem-2','sess-1','无问题文本的 👎','staging',0.1,30,0,1,?,?,?)""",
            ("2026-10-01T00:00:01", "2026-10-01T00:00:01", "2026-10-31"),
        )
        conn.execute(
            """INSERT INTO agent_memories(memory_id, session_id, text, state, trust,
                   ttl_days, feedback_up, feedback_down, created_at, updated_at, expires_at)
               VALUES('mem-3','sess-1','有 question 但 👍','staging',0.5,30,1,0,?,?,?)""",
            ("2026-10-01T00:00:02", "2026-10-01T00:00:02", "2026-10-31"),
        )
        conn.commit()
        st.record_agent_feedback("mem-1", False, question="上周三谁开了空调又关了灯")

        only_q = st.list_negative_feedback()
        ids = [r["memory_id"] for r in only_q]
        assert ids == ["mem-1"], ids
        assert only_q[0]["feedback_question"] == "上周三谁开了空调又关了灯"

        everything = st.list_negative_feedback(with_question_only=False)
        assert {r["memory_id"] for r in everything} == {"mem-1", "mem-2"}
    finally:
        st.close()
        os.remove(path)


def test_service_layer_forwards_question():
    st, path = _store()
    try:
        _add_memory(st)
        svc = AgentMemoryService(types.SimpleNamespace(
            tz_offset_hours=8, agent_trust_step=0.2), st, FakeHistory())
        r = svc.feedback_memory("mem-1", False, comment="答非所问", question="昨晚谁在客厅")
        assert r["ok"] and r["question"] == "昨晚谁在客厅"
        listed = svc.list_negative_feedback(10)
        assert listed["count"] == 1
        assert listed["items"][0]["feedback_question"] == "昨晚谁在客厅"
    finally:
        st.close()
        os.remove(path)


def _fake_request(query_string=b""):
    return Request({"type": "http", "method": "GET", "path": "/x",
                    "query_string": query_string, "headers": []})


def test_http_routes_plumb_question_and_register_read_surface(monkeypatch):
    captured = {}

    class _Svc:
        def feedback_memory(self, memory_id, useful, comment="", question=""):
            captured.update(memory_id=memory_id, useful=useful,
                             comment=comment, question=question)
            return {"ok": True, "question": question}

        def list_negative_feedback(self, limit):
            captured["limit"] = limit
            return {"ok": True, "count": 0, "items": []}

    monkeypatch.setattr(routes, "require_user", lambda request: ({"user": "u"}, None))
    monkeypatch.setattr(routes, "runtime", lambda request: types.SimpleNamespace(
        agent_memory=_Svc()))

    async def _json_body(request):
        return {"memory_id": "mem-1", "useful": False,
                "question": "Kevin 和 Emily 昨天有没有同时在客厅", "comment": "答错了"}
    monkeypatch.setattr(routes, "json_body", _json_body)

    resp = asyncio.run(routes.feedback(_fake_request()))
    assert resp.status_code == 200
    assert captured["question"] == "Kevin 和 Emily 昨天有没有同时在客厅"
    assert captured["comment"] == "答错了"

    resp = asyncio.run(routes.negative_feedback(_fake_request(b"limit=7")))
    assert resp.status_code == 200 and captured["limit"] == 7
    resp = asyncio.run(routes.negative_feedback(_fake_request(b"limit=abc")))
    assert captured["limit"] == 50
    paths = {r.path for r in routes.ROUTES}
    assert "/api/agent/memories/negative_feedback" in paths
