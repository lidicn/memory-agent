import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from memory_agent.config import Config
from memory_agent.store import Store
from memory_agent.agent_memory import AgentMemoryService, make_activity_id
from memory_agent.signal_learning import SignalLearningService
from memory_agent.insights import InsightService


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


# ── 检测层：硬排除改变判定 / 区间真实性 / 持续占用门槛 ──
# 任务 #23（P2-5）翻新：下面三条用例原先整段 `pytest.skip("_detect_activities 待适配")`——
# 那是把「用例还没搬」冻结成「用例不判」，缺陷现场照样绿。现行引擎是
# `InsightService.infer_activities`（内置语义规则 + 时段启发式兜底），三条判据逐条搬过去，
# 口径差异写在各自 docstring 里。
DAY = "2026-01-05"
DAY_B = "2026-01-06"


def _day(day):
    return {"start": "%sT00:00:00" % day, "end": "%sT23:59:59" % day}


def _ev(eid, ts, state, room, name):
    return {"entity_id": eid, "ts": ts, "new_state": state, "room": room,
            "domain": eid.split(".")[0], "attrs": {"friendly_name": name}}


def _acts(out):
    return list(out.get("activities") or [])


def _kinds(out):
    return {a.get("activity") for a in _acts(out)}


def _build_insights(events):
    """事件先落库、再建门面：EntityResolver 的设备目录是从 events 聚合出来的。"""
    st = make_store()
    st.insert_events(events)
    return st, InsightService(st, Config())


def test_insights_respects_signal_exclusion():
    """硬排除必须改变**判定**，而不只是少几条事件。

    翻新口径：legacy 断言的是 `watching_tv` / `working` 这类英文活动名和"证据里写不予采信"，
    现行引擎的对照物是内置规则 key（`bath` = 洗澡）。被搬过来的性质是
    「点名排除的实体不再撑起那条活动」——`tests/test_vma_activity_semantic.py:305` 只锁到
    「矩阵里少了几条事件」，判定级的影响在这里补上。
    """
    st, insvc = _build_insights([
        _ev("binary_sensor.bath_presence", "%sT21:00:00" % DAY, "on", "卫生间", "卫生间存在传感器"),
        _ev("binary_sensor.bath_presence", "%sT21:20:00" % DAY, "off", "卫生间", "卫生间存在传感器"),
        _ev("water_heater.bath_heater", "%sT21:01:00" % DAY, "on", "卫生间", "卫生间热水器"),
        _ev("water_heater.bath_heater", "%sT21:19:00" % DAY, "off", "卫生间", "卫生间热水器"),
    ])
    try:
        assert "bath" in _kinds(insvc.infer_activities(**_day(DAY))), "对照组应判出洗澡"

        exclusion_id = st.upsert_signal_exclusion(
            "binary_sensor.bath_presence", "all", "垄断型噪声", "user")
        out = insvc.infer_activities(**_day(DAY))
        assert "bath" not in _kinds(out), "必要信号已被排除却仍判出洗澡 ⇒ 排除只是装饰"
        assert out["excluded_entities"]["count"] == 1, out["excluded_entities"]

        # 撤销（revoked=1）后判定必须恢复：排除表读的是现行状态，不是一次性缓存
        st.revoke_signal_exclusion(exclusion_id)
        restored = insvc.infer_activities(**_day(DAY))
        assert "bath" in _kinds(restored), "撤销排除后应恢复判定"
        assert restored["excluded_entities"]["count"] == 0, restored["excluded_entities"]
    finally:
        st.close()
        os.remove(st.db_path)


def test_bathing_emits_interval_from_occupancy_pulse():
    """洗澡必须给真实区间，短脉冲不许胀成整天。

    翻新口径：legacy 用「占用脉冲 + 开灯 ≥20min」重建区间；现行 `bath` 规则是
    「卫生间存在 ≥5min + 热水器」，区间取命中信号会话的首尾
    （`ActivityMatch.start/end` → `start_ts/end_ts`），分钟级断言照样成立。
    短脉冲对照放在**另一日**：现行规则按「日窗 + 会话聚合」算指标，同日的两次占用
    会把 `first` 拉到早间那一次，那是引擎口径，不是缺陷。
    """
    st, insvc = _build_insights([
        # 洗澡样片段：占用 60min + 热水器 -> 应判洗澡且带真实区间
        _ev("binary_sensor.bath_presence", "%sT13:00:00" % DAY, "on", "卫生间", "卫生间存在传感器"),
        _ev("water_heater.bath_heater", "%sT13:00:05" % DAY, "on", "卫生间", "卫生间热水器"),
        _ev("binary_sensor.bath_presence", "%sT14:00:00" % DAY, "off", "卫生间", "卫生间存在传感器"),
        _ev("water_heater.bath_heater", "%sT14:00:05" % DAY, "off", "卫生间", "卫生间热水器"),
        # 如厕短脉冲（另一日）：3min、无热水器 -> 不应判洗澡
        _ev("binary_sensor.bath_presence", "%sT08:00:00" % DAY_B, "on", "卫生间", "卫生间存在传感器"),
        _ev("binary_sensor.bath_presence", "%sT08:03:00" % DAY_B, "off", "卫生间", "卫生间存在传感器"),
    ])
    try:
        bath = [a for a in _acts(insvc.infer_activities(**_day(DAY))) if a["activity"] == "bath"]
        assert len(bath) == 1, f"应仅由 60min 片段判出 1 条洗澡，实际: {bath}"
        b = bath[0]
        assert b["start"].endswith("13:00:00"), b
        assert b["end"].endswith("14:00:05"), b
        assert b["start_hour"] == 13 and b["end_hour"] == 14, b
        assert b["duration_minutes"] == 60.0, b
        # 全天误报的判据：区间长度远小于一日
        assert b["end_ts"] - b["start_ts"] < 2 * 3600, b

        assert "bath" not in _kinds(insvc.infer_activities(**_day(DAY_B))), \
            "3 分钟占用脉冲不足以判洗澡"
    finally:
        st.close()
        os.remove(st.db_path)


def test_study_requires_sustained_presence():
    """学习（书房持续占用）必须过时长门槛，且给真实区间。

    本用例是 legacy `test_working_requires_sustained_presence_or_computer` 的翻新版：
    现行引擎里没有 `working` 这条内置规则，对应的判据是内置 `study`（学习，书房 ≥30min）。
    原第三条断言「电脑在线即判工作」**没有对应的现行规则**——那属于规则词表缺口，
    归任务 #38（裁6 Q6-1，等 SP 本居活动清单后往 `activity_rules` 落覆盖行），
    这里不悄悄丢掉，也不临时编一条内置规则来凑绿。
    """
    st, insvc = _build_insights([
        # 持续占用 45min -> 应判学习且带真实区间
        _ev("binary_sensor.office_presence", "%sT10:00:00" % DAY, "on", "书房", "书房存在传感器"),
        _ev("binary_sensor.office_presence", "%sT10:45:00" % DAY, "off", "书房", "书房存在传感器"),
        # 单 blip 占用（30 秒）-> 不应判学习（另一日，避免同日会话聚合成同一段落）
        _ev("binary_sensor.office_presence", "%sT10:00:00" % DAY_B, "on", "书房", "书房存在传感器"),
        _ev("binary_sensor.office_presence", "%sT10:00:30" % DAY_B, "off", "书房", "书房存在传感器"),
    ])
    try:
        assert "study" not in _kinds(insvc.infer_activities(**_day(DAY_B))), \
            "30 秒的单次占用不足以判学习"

        wk = [a for a in _acts(insvc.infer_activities(**_day(DAY))) if a["activity"] == "study"]
        assert len(wk) == 1, wk
        s = wk[0]
        assert s["source"] == "semantic", s
        assert s["start"].endswith("10:00:00"), s
        assert s["end"].endswith("10:45:00"), s
        assert s["duration_minutes"] == 45.0, s
        assert s["signals"][0]["satisfied"] is True, s
    finally:
        st.close()
        os.remove(st.db_path)


if __name__ == "__main__":
    test_store_signal_exclusion_crud()
    test_signal_learning_service_teach_and_priority()
    test_insights_respects_signal_exclusion()
    test_bathing_emits_interval_from_occupancy_pulse()
    test_study_requires_sustained_presence()
    print("OK: all signal_learning tests passed")
