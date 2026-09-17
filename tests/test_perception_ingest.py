from memory_agent import perception_ingest as pi
from memory_agent.store import Store


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
