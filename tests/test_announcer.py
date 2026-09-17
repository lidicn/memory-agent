from memory_agent.announcer import Announcer
from memory_agent.perception_ingest import PerceptionEvent


class FakeHA:
    def __init__(self):
        self.calls = []

    def execute_action(self, action):
        self.calls.append(action)
        return {"ok": True, "status_code": 200}


def _ev(kind, payload=None):
    return PerceptionEvent(source="edge_ai", kind=kind, room="客厅", payload=payload or {})


def test_announce_enabled_face_known():
    ha = FakeHA()
    a = Announcer(ha, None, tts_entity="tts.doubao_tts", enabled=True, cooldown_sec=0)
    assert a.announce(_ev("face_known", {"friendly_name": "爸爸"})) is True
    assert ha.calls and ha.calls[0]["params"]["message"] == "客厅的爸爸回来了"


def test_announce_unknown_and_cry_messages():
    ha = FakeHA()
    a = Announcer(ha, None, tts_entity="tts.doubao_tts", enabled=True, cooldown_sec=0)
    a.announce(_ev("face_unknown"))
    a.announce(_ev("cry"))
    msgs = [c["params"]["message"] for c in ha.calls]
    assert "客厅有陌生人出现" in msgs
    assert "客厅的婴儿在哭" in msgs


def test_announce_disabled_without_entity():
    ha = FakeHA()
    a = Announcer(ha, None, tts_entity="", enabled=True)  # 无 tts 实体 → 强制关闭
    assert a.enabled is False
    assert a.announce(_ev("face_known")) is False
    assert ha.calls == []


def test_announce_cooldown_suppresses_repeat():
    ha = FakeHA()
    a = Announcer(ha, None, tts_entity="tts.doubao_tts", enabled=True, cooldown_sec=1000)
    assert a.announce(_ev("face_known")) is True
    # 同类事件在冷却期内被抑制
    assert a.announce(_ev("face_known")) is False
    assert len(ha.calls) == 1
