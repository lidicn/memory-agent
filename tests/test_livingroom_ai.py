from memory_agent import perception_ingest as pi
from memory_agent.livingroom_ai import LivingRoomAIIngest
from memory_agent.store import Store


class FakeHA:
    """用内存字典模拟 HA REST 客户端，供单元测试。"""

    def __init__(self, states: dict):
        self._states = states
        self.discover_calls = 0
        self.get_calls: list[str] = []

    def discover_entities(self):
        self.discover_calls += 1
        rooms = {}
        for eid, st in self._states.items():
            room = st.get("_room", "客厅")
            rooms.setdefault(room, {"entities": {}})
            rooms[room]["entities"][eid] = {
                "name": eid, "domain": "event", "state": st.get("state")
            }
        return {"ok": True, "rooms": rooms}

    def get_state(self, eid):
        self.get_calls.append(eid)
        return self._states.get(eid)


class BulkFakeHA(FakeHA):
    """额外提供 get_states()（批量），用于验证轮询只发一次请求。"""

    def __init__(self, states: dict):
        super().__init__(states)
        self.bulk_calls = 0

    def get_states(self):
        self.bulk_calls += 1
        return [dict(st, entity_id=eid) for eid, st in self._states.items()]


def _state(last_changed: str, attrs=None, room="客厅"):
    return {
        "state": last_changed,
        "last_changed": last_changed,
        "last_updated": last_changed,
        "attributes": attrs or {},
        "_room": room,
    }


def _store() -> Store:
    st = Store(":memory:")
    st.init_schema()
    return st


def test_poll_once_routes_and_dedup():
    eid = "event.chuangmi_camera_051a01_known_face_e_8_8"
    ha = FakeHA({eid: _state("2026-09-17T00:34:00+00:00", {"friendly_name": "熟人"})})
    store = _store()
    ing = LivingRoomAIIngest(ha, store, interval_seconds=10)

    # 首次轮询：仅记录基线，不写入
    assert ing.poll_once() == 0
    # 未变化：仍 0
    assert ing.poll_once() == 0
    # 触发新事件（last_changed 改变）
    ha._states[eid]["last_changed"] = "2026-09-17T00:35:00+00:00"
    ha._states[eid]["state"] = "2026-09-17T00:35:00+00:00"
    assert ing.poll_once() == 1

    rows = store.list_perception_events(source="edge_ai")
    assert len(rows) == 1
    assert rows[0]["kind"] == "face_known"
    assert rows[0]["room"] == "客厅"

    # 重复轮询：不新增
    assert ing.poll_once() == 0
    assert len(store.list_perception_events()) == 1


def test_real_world_entity_id_form():
    """线上米家集成实体 id 带设备号段（chuangmi_cn_<uid>_<did>_*），也须命中。"""
    eid = "event.chuangmi_cn_1072229835_051a01_unkonw_face_e_8_7"
    ha = FakeHA({eid: _state("2026-09-17T05:46:49+00:00", {"event_type": "识别到陌生人"})})
    store = _store()
    ing = LivingRoomAIIngest(ha, store, interval_seconds=10)

    assert ing.poll_once() == 0                      # 基线
    ha._states[eid]["last_changed"] = "2026-09-17T05:47:49+00:00"
    assert ing.poll_once() == 1
    rows = store.list_perception_events(source="edge_ai")
    assert rows[0]["kind"] == "face_unknown"
    assert rows[0]["entity_id"] == eid


def test_poll_once_ignores_high_frequency_motion_entities():
    """物体/人形移动是高频环境事件，有意不入库（也不参与轮询）。"""
    eid = "event.chuangmi_cn_1072229835_051a01_people_motion_e_8_2"
    ha = FakeHA({eid: _state("2026-09-17T05:00:00+00:00")})
    store = _store()
    ing = LivingRoomAIIngest(ha, store, interval_seconds=10)
    ing._discover()
    assert ing._entity_ids == []                     # 发现阶段即排除
    assert ing.poll_once() == 0
    assert store.list_perception_events() == []


def test_poll_once_ignores_non_chuangmi():
    eid = "event.some_automation_triggered"
    ha = FakeHA({eid: _state("2026-09-17T00:34:00+00:00")})
    store = _store()
    ing = LivingRoomAIIngest(ha, store, interval_seconds=10)
    # 非 chuangmi 事件实体：发现阶段被过滤，首次轮询即 0 且不报错
    assert ing.poll_once() == 0
    assert len(store.list_perception_events()) == 0


def test_bulk_states_single_request():
    """有 get_states() 时走批量：一次请求覆盖所有实体，不再逐个 get_state。"""
    e1 = "event.chuangmi_cn_1072229835_051a01_known_face_e_8_8"
    e2 = "event.chuangmi_cn_1072229835_051a01_key_area_human_e_11_1"
    ha = BulkFakeHA({
        e1: _state("2026-09-17T05:00:00+00:00"),
        e2: _state("2026-09-17T05:00:00+00:00"),
    })
    store = _store()
    ing = LivingRoomAIIngest(ha, store, interval_seconds=10)

    assert ing.poll_once() == 0                      # 基线
    assert ha.bulk_calls == 1
    assert ha.get_calls == []                        # 未退化为逐实体请求

    ha._states[e1]["last_changed"] = "2026-09-17T05:01:00+00:00"
    ha._states[e1]["state"] = "2026-09-17T05:01:00+00:00"
    ha._states[e2]["last_changed"] = "2026-09-17T05:02:00+00:00"
    ha._states[e2]["state"] = "2026-09-17T05:02:00+00:00"
    assert ing.poll_once() == 2
    assert {r["kind"] for r in store.list_perception_events()} == {"face_known", "human"}


def test_ingest_skips_unmapped_kind():
    """未在映射表中的摄像机事件（如巡航/通话）不写入总线。"""
    eid = "event.chuangmi_cn_1072229835_051a01_start_cruise_e_10_1"
    assert pi.kind_from_chuangmi_entity(eid) is None
