import pytest
from datetime import datetime, timedelta

from memory_agent import perception_ingest as pi
from memory_agent.store import Store


def _recent_days(n: int) -> list[str]:
    """返回最近 n 天的日期字符串（含今天），避免固定日期随时间过期。"""
    today = datetime.now().date()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _store() -> Store:
    # 用内存库避免临时文件与 WAL 锁的清理问题
    s = Store(":memory:")
    s.init_schema()
    return s


def test_insert_and_list():
    s = _store()
    eid = s.insert_perception_event({
        "source": "edge_ai",
        "kind": "face_known",
        "room": "客厅",
        "entity_id": "event.chuangmi_camera_051a01_known_face_e_8_8",
        "payload": {"who": "爸爸"},
    })
    assert eid > 0
    rows = s.list_perception_events(source="edge_ai")
    assert len(rows) == 1
    assert rows[0]["kind"] == "face_known"
    assert rows[0]["payload"] == {"who": "爸爸"}


def test_kind_from_chuangmi():
    assert (
        pi.kind_from_chuangmi_entity("event.chuangmi_camera_051a01_unkonw_face_e_8_7")
        == "face_unknown"
    )
    assert (
        pi.kind_from_chuangmi_entity("event.chuangmi_camera_051a01_known_face_e_8_8")
        == "face_known"
    )
    assert (
        pi.kind_from_chuangmi_entity("event.chuangmi_camera_051a01_pet_event_e_8_6")
        == "pet"
    )
    assert pi.kind_from_chuangmi_entity("binary_sensor.unknown_motion") is None


def test_from_ha_event():
    ev = pi.from_ha_event(
        "event.chuangmi_camera_051a01_known_face_e_8_8",
        {"attributes": {"friendly_name": "熟人"}},
        room="客厅",
    )
    assert ev is not None
    assert ev.source == "edge_ai" and ev.kind == "face_known"
    assert ev.room == "客厅"


def test_filters():
    s = _store()
    s.insert_perception_event({"source": "edge_ai", "kind": "human", "room": "客厅", "entity_id": "entity.a"})
    s.insert_perception_event({"source": "vlm", "kind": "human", "room": "书房", "entity_id": "entity.b"})
    assert len(s.list_perception_events(kind="human")) == 2
    assert len(s.list_perception_events(source="vlm")) == 1
    assert len(s.list_perception_events(room="客厅")) == 1


def test_event_id_dedup():
    s = _store()
    payload = {
        "source": "edge_ai", "kind": "human", "room": "客厅",
        "entity_id": "entity.a", "server_ts": "2026-09-17T00:00:00",
    }
    assert s.insert_perception_event(payload) > 0
    # 相同 event_id 应被 IGNORE，不新增行
    assert s.insert_perception_event(payload) == 0
    assert len(s.list_perception_events()) == 1


def test_has_recent_edge_signal():
    s = _store()
    s.insert_perception_event({
        "source": "edge_ai", "kind": "human", "room": "客厅",
        "entity_id": "e1", "server_ts": "2026-09-17T10:00:00",
    })
    assert s.has_recent_edge_signal("客厅", "2026-09-17T09:00:00") is True
    assert s.has_recent_edge_signal("客厅", "2026-09-17T11:00:00") is False
    assert s.has_recent_edge_signal("书房", "2026-09-17T09:00:00") is False


def test_ingest_event_fail_closed():
    class BadStore:
        def insert_perception_event(self, p):
            raise RuntimeError("boom")

    n = pi.ingest_event(
        BadStore(), pi.PerceptionEvent(source="edge_ai", kind="human")
    )
    assert n == 0


# ── Phase 1.1 · Gate 层：edge_ai → behavior_events ──────────────────────────

def _behavior_rows(s):
    return s.list_behavior_events(limit=100)


def test_gate_promotes_human_event():
    s = _store()
    ev = pi.PerceptionEvent(
        source="edge_ai", kind="human", room="客厅",
        server_ts="2026-09-18T10:00:00",
        entity_id="event.chuangmi_cn_1_2_key_area_human_e_11_1",
    )
    bid = pi.gate_promote_to_behavior(s, ev)
    assert bid > 0
    rows = _behavior_rows(s)
    assert len(rows) == 1
    row = rows[0]
    assert row["room"] == "客厅"
    assert row["action"] == "有人出现"
    assert row["count"] == 1
    assert row["trigger"] == "edge_ai"
    assert row["persons"] == []
    assert row["status"] == "ok"


def test_gate_face_unknown_promotes_stranger():
    s = _store()
    ev = pi.PerceptionEvent(
        source="edge_ai", kind="face_unknown", room="客厅",
        server_ts="2026-09-18T10:01:00",
    )
    assert pi.gate_promote_to_behavior(s, ev) > 0
    row = _behavior_rows(s)[0]
    assert row["action"] == "陌生人出现"
    assert row["persons"] == [{
        "name": "陌生人", "via": "edge_ai",
        "match_confidence": 0.6, "member_id": None, "detail": {},
    }]
    assert row["confidence"] == 0.6


def test_gate_face_known_extracts_name_from_person_id():
    """face_known 从人物id 查映射表得人名（不是 friendly_name）。"""
    s = _store()
    ev = pi.from_ha_event(
        "event.chuangmi_cn_1_2_known_face_e_8_8",
        {"attributes": {"人物id": "98126558687947776", "friendly_name": "自动化场景名"},
         "last_changed": "2026-09-18T10:02:00"},
        room="客厅",
    )
    assert ev is not None
    assert pi.gate_promote_to_behavior(s, ev) > 0
    row = _behavior_rows(s)[0]
    assert row["action"] == "熟人出现"
    assert row["persons"][0]["name"] == "lidicn"
    assert row["persons"][0]["via"] == "edge_ai"


def test_gate_face_known_falls_back_to_generic():
    s = _store()
    ev = pi.PerceptionEvent(
        source="edge_ai", kind="face_known", room="客厅",
        server_ts="2026-09-18T10:03:00", payload={},
    )
    assert pi.gate_promote_to_behavior(s, ev) > 0
    row = _behavior_rows(s)[0]
    assert row["persons"][0]["name"] == "熟人"


def test_gate_skips_non_behavior_kinds():
    s = _store()
    # 昼夜切换 / 进出区域 / 长时无人 / 手势 都是环境信号，不晋升行为事件
    for kind in ("day_night", "fav_area", "no_human", "gesture"):
        ev = pi.PerceptionEvent(
            source="edge_ai", kind=kind, room="客厅",
            server_ts="2026-09-18T10:04:00",
        )
        assert pi.gate_promote_to_behavior(s, ev) == 0
    assert _behavior_rows(s) == []


def test_gate_skips_when_no_room():
    s = _store()
    ev = pi.PerceptionEvent(
        source="edge_ai", kind="human", room=None,
        server_ts="2026-09-18T10:05:00",
    )
    assert pi.gate_promote_to_behavior(s, ev) == 0
    assert _behavior_rows(s) == []


def test_gate_fail_closed_on_store_error():
    class BadStore:
        def insert_behavior_event(self, p):
            raise RuntimeError("boom")

    ev = pi.PerceptionEvent(
        source="edge_ai", kind="human", room="客厅",
        server_ts="2026-09-18T10:06:00",
    )
    # 落库失败返回 0 且不抛异常
    assert pi.gate_promote_to_behavior(BadStore(), ev) == 0


# ── Phase 1.2 · Identity 层：face_unknown 即时裁决 ──────────────────────────

class _FakeStore:
    """Mock store：roster + occupancy + 捕获 update 调用。"""
    def __init__(self, roster, occupancy):
        self._roster = roster
        self._occupancy = occupancy
        # 真实 Store 的契约：tz_offset_hours 是数值，回填窗口按家庭墙钟算。
        self.tz_offset_hours = 8.0
        self.updated = []
    def list_members(self):
        return self._roster
    def recent_occupancy(self, since):
        return self._occupancy
    def update_behavior_event_persons(self, event_id, persons, via=None):
        self.updated.append((event_id, persons))


def test_identity_resolves_unknown_by_elimination():
    """客厅陌生人 + 名册消除法等式成立 → 裁决为缺席成员。"""
    roster = [
        {"id": "m1", "name": "lidicn"},
        {"id": "m2", "name": "凯文", "rooms": ["客厅"]},
        {"id": "m3", "name": "Emily"},
    ]
    # 客厅 count=1，无已识别 → unknown=1；其他房间无事件
    # known_present=0, absent=3, unknown=1 → 等式不成立（1 != 3）
    # 需要让等式成立：lidicn 在其他房间，unknown=1, absent=1
    occupancy = [
        {"room": "书房", "persons": ["lidicn"], "count": 1},
        {"room": "客厅", "persons": [], "count": 1},  # 刚写的 face_unknown
    ]
    store = _FakeStore(roster, occupancy)
    ev = pi.PerceptionEvent(source="edge_ai", kind="face_unknown", room="客厅",
                            server_ts="2026-09-18T20:00:00")
    result = pi.identity_resolve_unknown(store, ev, 999)
    # 等式：unknown=1, absent=2（凯文+Emily）→ 1 != 2 → inconclusive
    # 调整：让 absent=1（凯文/Emily 中一个被识别在其他房间）
    occupancy2 = [
        {"room": "主卧", "persons": ["lidicn", "Emily"], "count": 2},
        {"room": "客厅", "persons": [], "count": 1},
    ]
    store2 = _FakeStore(roster, occupancy2)
    result2 = pi.identity_resolve_unknown(store2, ev, 1000)
    # known={lidicn,Emily}, absent={凯文}, unknown=1 → 等式成立 → 裁决凯文
    assert result2 == "凯文", f"got {result2}"
    assert len(store2.updated) == 1
    eid, persons = store2.updated[0]
    assert eid == 1000
    assert persons[0]["name"] == "凯文"
    assert persons[0]["via"] == "presence_fusion"
    assert persons[0]["member_id"] == "m2"


def test_identity_skips_when_equation_fails():
    """等式不成立（unknown != absent）→ 不裁决，保持陌生人。"""
    roster = [
        {"id": "m1", "name": "lidicn"},
        {"id": "m2", "name": "凯文"},
        {"id": "m3", "name": "Emily"},
    ]
    occupancy = [
        {"room": "客厅", "persons": [], "count": 2},  # unknown=2
    ]
    # known=0, absent=3, unknown=2 → 2 != 3 → inconclusive
    store = _FakeStore(roster, occupancy)
    ev = pi.PerceptionEvent(source="edge_ai", kind="face_unknown", room="客厅",
                            server_ts="2026-09-18T20:00:00")
    result = pi.identity_resolve_unknown(store, ev, 1001)
    assert result is None
    assert store.updated == []


def test_identity_skips_known_face():
    """face_known 不触发 Identity 裁决。"""
    store = _FakeStore([], [])
    ev = pi.PerceptionEvent(source="edge_ai", kind="face_known", room="客厅",
                            server_ts="2026-09-18T20:00:00")
    assert pi.identity_resolve_unknown(store, ev, 1) is None
    assert store.updated == []


def test_identity_skips_when_no_room():
    store = _FakeStore([], [])
    ev = pi.PerceptionEvent(source="edge_ai", kind="face_unknown", room=None,
                            server_ts="2026-09-18T20:00:00")
    assert pi.identity_resolve_unknown(store, ev, 1) is None


# ── Phase 2.1 候选晋升 ────────────────────────────────────────────────────────

def test_count_room_action_days():
    """同房间+同 action 跨天统计。"""
    s = _store()
    # 写 3 天不同天的「客厅/熟人出现」（用最近日期避免窗口过期）
    for day in _recent_days(3):
        s.insert_behavior_event({
            "server_ts": f"{day}T10:00:00",
            "room": "客厅",
            "action": "熟人出现",
            "persons": [{"name": "lidicn"}],
            "count": 1,
            "trigger": "edge_ai",
        })
    days = s.count_room_action_days("客厅", "熟人出现", 7)
    assert days == 3


def test_count_room_action_days_same_day():
    """同一天多条只算 1 天。"""
    s = _store()
    today = datetime.now().date().isoformat()
    for h in (10, 11, 12):
        s.insert_behavior_event({
            "server_ts": f"{today}T{h:02d}:00:00",
            "room": "客厅",
            "action": "陌生人出现",
            "persons": [{"name": "陌生人"}],
            "count": 1,
            "trigger": "edge_ai",
        })
    days = s.count_room_action_days("客厅", "陌生人出现", 7)
    assert days == 1


# ── Phase 2.3 原子写 + 权重截断 ─────────────────────────────────────────────────

def test_write_profile_atomic(tmp_path):
    from memory_agent.home_profile import write_profile_atomic
    p = tmp_path / "profile.md"
    write_profile_atomic(str(p), "# 测试\n内容")
    assert p.read_text(encoding="utf-8") == "# 测试\n内容"


def test_truncate_by_weight():
    from memory_agent.home_profile import truncate_by_weight
    mems = [
        {"text": "低权重记忆", "trust": 0.1},
        {"text": "高权重记忆", "trust": 0.9},
        {"text": "中权重记忆", "trust": 0.5},
    ]
    # max_chars=10：只能放一条（6 字符）
    kept = truncate_by_weight(mems, max_chars=6)
    # 高权重优先：保留高权重
    assert len(kept) == 1
    assert "高权重" in kept[0]["text"]


def test_build_profile_empty():
    from memory_agent.home_profile import build_profile
    s = _store()
    text = build_profile(s, max_chars=4000)
    assert "# 家庭画像" in text


# ── Phase 3 感知规则引擎 ────────────────────────────────────────────────────────

def test_rule_engine_match():
    from memory_agent.perception_rules import RuleEngine
    engine = RuleEngine()
    # face_unknown @ 客厅 → 命中 stranger_alert
    res = engine.evaluate("face_unknown", "客厅")
    assert len(res) == 1
    assert res[0]["rule_id"] == "stranger_alert"
    assert res[0]["action"] == "alert"


def test_rule_engine_cooldown():
    from memory_agent.perception_rules import RuleEngine
    engine = RuleEngine()
    # 第一次触发
    res1 = engine.evaluate("face_unknown", "客厅")
    assert len(res1) == 1
    # 冷却内再触发 → 不命中
    res2 = engine.evaluate("face_unknown", "客厅")
    assert len(res2) == 0


def test_rule_engine_room_wildcard():
    from memory_agent.perception_rules import RuleEngine
    engine = RuleEngine()
    # day_night 通配所有房间
    res = engine.evaluate("day_night", "任意房间")
    assert len(res) == 1
    assert res[0]["rule_id"] == "day_night_log"


# ── Phase 4 反馈闭环打包 ──────────────────────────────────────────────────────────

def test_build_feedback_pack(tmp_path):
    from memory_agent.feedback_pack import build_feedback_pack
    out = build_feedback_pack(
        snapshot_path="",
        trace="VLM 误识别：把猫认成狗",
        output_dir=str(tmp_path),
        label="test_case",
    )
    assert out is not None
    assert out.endswith(".tar.gz")
    import tarfile
    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
        assert "trace.txt" in names


# ── Phase 5 离家安防 + 家庭日常画像 ──────────────────────────────────────────────

def test_away_mode_transition():
    try:
        from memory_agent.away_mode import AwayMode
    except ImportError:
        pytest.skip("AwayMode 已重构为 AwayModeManager（需 store/room），本用例待适配")
    mode = AwayMode(no_human_threshold_seconds=1)
    assert mode.is_away() is False
    # 模拟无人超过阈值
    import time
    mode.last_human_ts = time.monotonic() - 2
    result = mode.report_no_human()
    assert result == "away"
    assert mode.is_away() is True
    # 有人 → 切回在家
    mode.report_human()
    assert mode.is_away() is False


def test_away_mode_alert_unknown():
    try:
        from memory_agent.away_mode import AwayMode
    except ImportError:
        pytest.skip("AwayMode 已重构为 AwayModeManager（需 store/room），本用例待适配")
    mode = AwayMode()
    assert mode.should_alert_unknown() is False
    mode.state = "away"
    assert mode.should_alert_unknown() is True


def test_daily_profile_baseline():
    from memory_agent.daily_profile import compute_return_time_baseline
    events = [
        {"server_ts": f"2026-09-{d:02d}T20:00:00", "persons": [{"name": "Kevin"}]}
        for d in range(10, 17)  # 7 天，每天 20:00 回家
    ]
    baseline = compute_return_time_baseline(events, "Kevin", min_days=3)
    assert baseline is not None
    assert baseline["days"] == 7
    assert abs(baseline["median_hour"] - 20.0) < 0.1


def test_daily_profile_anomaly():
    from memory_agent.daily_profile import check_return_time_anomaly
    baseline = {"median_hour": 20.0, "mad": 0.5}
    # 22:00 回家，偏离 2 小时 > 2σ → 异常
    result = check_return_time_anomaly(baseline, 22.0, sigma_threshold=2.0)
    assert result is not None
    assert result["anomaly"] is True
    # 20:30 回家，偏离 0.5 小时 → 正常
    result2 = check_return_time_anomaly(baseline, 20.5, sigma_threshold=2.0)
    assert result2 is None
