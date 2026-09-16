import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.memory_agent.store import Store
from src.memory_agent.agent_memory import AgentMemoryService, make_activity_id
from src.memory_agent.signal_learning import SignalLearningService
from src.memory_agent.insights import InsightService


class FakeConfig:
    tz_offset_hours = 8


class FakeCollection:
    def __init__(self):
        self._docs = {}
        self._meta = {}

    def add(self, ids=None, documents=None, metadatas=None, embeddings=None):
        for i, d in enumerate(ids):
            self._docs[d] = documents[i] if documents else ""
            self._meta[d] = (metadatas or [None] * len(ids))[i]

    def get(self, ids=None, where=None, limit=None):
        if ids is not None:
            return {"ids": list(ids), "documents": [self._docs.get(i, "") for i in ids], "metadatas": [self._meta.get(i) for i in ids]}
        return {"ids": list(self._docs.keys()), "documents": list(self._docs.values()), "metadatas": list(self._meta.values())}

    def query(self, query_texts=None, n_results=5, where=None):
        return {"ids": [[]], "distances": [[]], "metadatas": [[]], "documents": [[]]}

    def delete(self, ids=None, where=None):
        for i in (ids or []):
            self._docs.pop(i, None)
            self._meta.pop(i, None)

    def count(self):
        return len(self._docs)


class FakeChroma:
    def __init__(self):
        self._c = {}

    def collection(self, name, get_or_create=False):
        return self._c.setdefault(name, FakeCollection())

    def reset(self):
        self._c.clear()


class FakeHistory:
    def __init__(self, days=14):
        self.days = days
        self.agent_collection = FakeChroma().collection("agent_memory")

    @property
    def window_start(self):
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc) - timedelta(days=self.days)).isoformat()


def make_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    st = Store(path)
    st.init_schema()
    return st


# ── Store 层：signal_exclusions CRUD ──
def test_store_signal_exclusion_crud():
    st = make_store()
    try:
        eid = st.upsert_signal_exclusion("media.x", "watching_tv", "定时播报是自动化信号", "user", "is_automation")
        assert eid
        rows = st.list_signal_exclusions()
        assert len(rows) == 1
        assert rows[0]["entity_id"] == "media.x"
        assert rows[0]["scope"] == "watching_tv"
        assert rows[0]["exclusion_type"] == "is_automation"
        assert rows[0]["revoked"] is False

        # 幂等：同 entity_id+scope 复用同一 exclusion_id
        eid2 = st.upsert_signal_exclusion("media.x", "watching_tv", "改理由", "user")
        assert eid2 == eid
        assert len(st.list_signal_exclusions()) == 1

        # 撤销（墓碑）
        assert st.revoke_signal_exclusion(eid) is True
        assert st.list_signal_exclusions(include_revoked=False) == []
        revoked = st.get_signal_exclusion(eid)
        assert revoked["revoked"] is True
        assert st.revoke_signal_exclusion("nonexistent") is False
    finally:
        st.close()
        os.remove(st.db_path)


# ── 服务层：teach_signal 硬/软分流 + list_rules + 硬排优先 ──
def test_signal_learning_service_teach_and_priority():
    st = make_store()
    chroma = FakeChroma()
    am = AgentMemoryService(FakeConfig(), st, FakeHistory())
    svc = SignalLearningService(st, am)
    try:
        # 硬排
        r_hard = svc.teach_signal(entity_id="media.x", scope="watching_tv", kind="hard",
                                  reason="定时播报是自动化信号，不是看电视")
        assert r_hard["ok"] and r_hard["kind"] == "hard" and r_hard["exclusion_id"]
        # 软记忆
        r_soft = svc.teach_signal(entity_id="media.x", kind="soft", scope="watching_tv",
                                  text="客厅音箱定时播报是自动化信号，不应作为看电视判定依据",
                                  source_refs=["event:taught:media.x"])
        assert r_soft["ok"] and r_soft["kind"] == "soft" and r_soft["memory_id"]

        # list_rules：硬 + 软 混合
        rules = svc.list_rules()
        assert rules["counts"]["hard"] == 1
        assert rules["counts"]["soft"] == 1
        assert rules["soft"][0]["topic_key"] == "signal_trust"

        # 硬排优先于软判：is_excluded 命中（即使存在软记忆也不影响硬排语义）
        assert svc.is_excluded("media.x", "watching_tv") is True
        assert svc.is_excluded("media.x", "working") is False  # scope 不匹配

        # all 维度覆盖任意 scope
        svc.teach_signal(entity_id="media.y", kind="hard", scope="all")
        assert svc.is_excluded("media.y", "watching_tv") is True
        assert svc.is_excluded("media.y", "presence") is True

        # 撤销后不再硬排
        assert svc.revoke_exclusion(r_hard["exclusion_id"])["ok"]
        assert svc.is_excluded("media.x", "watching_tv") is False

        # 软记忆缺少 text 应报错
        bad = svc.teach_signal(entity_id="z", kind="soft", text="")
        assert not bad["ok"]
    finally:
        st.close()
        os.remove(st.db_path)


# ── 检测层：硬排除真正过滤 infer_activities 的判定 ──
def _build_insights():
    st = make_store()
    insvc = InsightService(FakeConfig(), st)
    # 让标签/命名可注入，避免依赖完整 config.entities
    TAGS = {
        "media.study_tv": ["media"],
        "computer.study_pc": ["computer"],
        # 睡眠检测基于「家电/门/电脑」类行为事件的静默间隔；起床锚定用例需给样本补标签，
        # 否则静默事件为空、睡眠永不命中（用例会误红）。
        "sensor.a": ["appliance"],
        "speaker.xiaoai": ["appliance"],
    }
    insvc.name_map = staticmethod(lambda: {})
    insvc._tags_of = staticmethod(lambda eid, disp: TAGS.get(eid, []))
    insvc._fallback_name = staticmethod(lambda eid: eid)
    return st, insvc


def test_insights_respects_signal_exclusion():
    st, insvc = _build_insights()
    rows = [
        {"entity_id": "media.study_tv", "ts": "2025-01-01T21:00:00", "new_state": "on", "room": "客厅"},
        {"entity_id": "computer.study_pc", "ts": "2025-01-01T14:00:00", "new_state": "on", "room": "书房"},
    ]
    try:
        # 对照组：无排除时，应检出 watching_tv 与 working
        r0 = insvc._detect_activities(rows)["activities"]
        kinds0 = {a["activity"] for a in r0}
        assert "watching_tv" in kinds0, "对照组应检出 watching_tv"
        assert "working" in kinds0, "对照组应检出 working"

        # 写入硬排除
        st.upsert_signal_exclusion("media.study_tv", "watching_tv", "排除", "user")
        st.upsert_signal_exclusion("computer.study_pc", "working", "排除", "user")

        # 实验组：硬排除生效，不再误判
        r1 = insvc._detect_activities(rows)["activities"]
        kinds1 = {a["activity"] for a in r1}
        assert "watching_tv" not in kinds1, "硬排除后不应检出 watching_tv"
        assert "working" not in kinds1, "硬排除后不应检出 working"

        # 起床锚定：被排除的 b 实体不计入睡眠 entities
        sleep_rows = [
            {"entity_id": "sensor.a", "ts": "2025-01-02T23:00:00", "new_state": "on", "room": "卧室"},
            {"entity_id": "speaker.xiaoai", "ts": "2025-01-03T07:00:00", "new_state": "on", "room": "卧室"},
        ]
        st.upsert_signal_exclusion("speaker.xiaoai", "wake_anchor", "定时播报", "user")
        r2 = insvc._detect_activities(sleep_rows)["activities"]
        sleep = [a for a in r2 if a["activity"] == "sleeping"]
        assert sleep, "应检出睡眠"
        assert "speaker.xiaoai" not in sleep[0]["entities"], "起床锚定实体被硬排除，不应计入 entities"
        assert "不予采信" in sleep[0]["evidence"], "证据应说明起床锚定已被排除"
    finally:
        st.close()
        os.remove(st.db_path)


def test_bathing_emits_interval_from_occupancy_pulse():
    # P0 修复验证：洗澡（占用推断兜底）必须输出真实时间区间 start_ts/end_ts，
    # 且按「占用脉冲 + 开灯、时长>=20min」重建，而非「全天任一占用即整天」（旧逻辑全天误报）。
    st, insvc = _build_insights()
    TAGS = {
        "binary_sensor.bathroom_occupancy": ["presence"],
        "light.bathroom": ["light"],
    }
    insvc._tags_of = staticmethod(lambda eid, disp: TAGS.get(eid, []))

    rows = [
        # 洗澡样片段：占用 60min + 开灯 -> 应判洗澡且带真实区间
        {"entity_id": "binary_sensor.bathroom_occupancy", "ts": "2025-01-01T13:00:00",
         "new_state": "on", "room": "卫生间"},
        {"entity_id": "light.bathroom", "ts": "2025-01-01T13:00:05",
         "new_state": "on", "room": "卫生间"},
        {"entity_id": "binary_sensor.bathroom_occupancy", "ts": "2025-01-01T14:00:00",
         "new_state": "off", "room": "卫生间"},
        {"entity_id": "light.bathroom", "ts": "2025-01-01T14:00:05",
         "new_state": "off", "room": "卫生间"},
        # 如厕短脉冲：3min、无灯 -> 不应判洗澡
        {"entity_id": "binary_sensor.bathroom_occupancy", "ts": "2025-01-01T08:00:00",
         "new_state": "on", "room": "卫生间"},
        {"entity_id": "binary_sensor.bathroom_occupancy", "ts": "2025-01-01T08:03:00",
         "new_state": "off", "room": "卫生间"},
    ]
    try:
        acts = insvc._detect_activities(rows)["activities"]
        bath = [a for a in acts if a["activity"] == "bathing"]
        assert len(bath) == 1, f"应仅由 60min 开灯片段判出 1 条洗澡，实际: {bath}"
        b = bath[0]
        assert b["start_ts"] == "2025-01-01T13:00:00", b
        assert b["end_ts"] == "2025-01-01T14:00:00", b
        assert b["duration_minutes"] == 60, b
    finally:
        st.close()
        os.remove(st.db_path)


def test_working_requires_sustained_presence_or_computer():
    # P1 验证：working 不再因书房单 blip 占用而判整天工作；
    # 需「电脑/工作设备证据」或「书房白天持续占用片段(≥30min)」才触发，且带真实区间。
    st, insvc = _build_insights()
    TAGS = {
        "binary_sensor.office_presence": ["presence"],
        "computer.study_pc": ["computer"],
    }
    insvc._tags_of = staticmethod(lambda eid, disp: TAGS.get(eid, []))

    # 1) 单 blip 占用（<1min）不应判 working
    blip = [
        {"entity_id": "binary_sensor.office_presence", "ts": "2025-01-01T10:00:00",
         "new_state": "on", "room": "书房"},
        {"entity_id": "binary_sensor.office_presence", "ts": "2025-01-01T10:00:30",
         "new_state": "off", "room": "书房"},
    ]
    # 2) 持续占用 45min 应判 working 且带真实区间
    sustained = [
        {"entity_id": "binary_sensor.office_presence", "ts": "2025-01-01T10:00:00",
         "new_state": "on", "room": "书房"},
        {"entity_id": "binary_sensor.office_presence", "ts": "2025-01-01T10:45:00",
         "new_state": "off", "room": "书房"},
    ]
    # 3) 电脑在线应判 working（即使无持续占用）
    comp = [
        {"entity_id": "computer.study_pc", "ts": "2025-01-01T14:00:00",
         "new_state": "on", "room": "书房"},
    ]
    try:
        a1 = insvc._detect_activities(blip)["activities"]
        assert not any(x["activity"] == "working" for x in a1), "单 blip 不应判 working"
        a2 = insvc._detect_activities(sustained)["activities"]
        wk = [x for x in a2 if x["activity"] == "working"]
        assert len(wk) == 1, wk
        assert wk[0]["start_ts"] == "2025-01-01T10:00:00"
        assert wk[0]["end_ts"] == "2025-01-01T10:45:00"
        assert wk[0]["duration_minutes"] == 45
        a3 = insvc._detect_activities(comp)["activities"]
        assert any(x["activity"] == "working" for x in a3), "电脑在线应判 working"
    finally:
        st.close()
        os.remove(st.db_path)


if __name__ == "__main__":
    test_store_signal_exclusion_crud()
    test_signal_learning_service_teach_and_priority()
    test_insights_respects_signal_exclusion()
    test_bathing_emits_interval_from_occupancy_pulse()
    test_working_requires_sustained_presence_or_computer()
    print("OK: all signal_learning tests passed")
