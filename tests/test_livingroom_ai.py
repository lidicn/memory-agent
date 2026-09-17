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


def _state(last_changed: str, attrs=None, room="客厅"):
    return {
        "state": last_changed,
        "last_changed": last_changed,
        "last_updated": last_changed,
        "attributes": attrs or {},
        "_room": room,
    }


def test_poll_once_routes_and_dedup():
    eid = "event.chuangmi_camera_051a01_known_face_e_8_8"
    ha = FakeHA({eid: _state("2026-09-17T00:34:00+00:00", {"friendly_name": "熟人"})})
    store = Store(":memory:")
    store.init_schema()
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


def test_poll_once_ignores_non_chuangmi():
    eid = "event.some_automation_triggered"
    ha = FakeHA({eid: _state("2026-09-17T00:34:00+00:00")})
    store = Store(":memory:")
    store.init_schema()
    ing = LivingRoomAIIngest(ha, store, interval_seconds=10)
    # 非 chuangmi 事件实体：发现阶段被过滤，首次轮询即 0 且不报错
    assert ing.poll_once() == 0
    assert len(store.list_perception_events()) == 0
