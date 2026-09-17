from memory_agent.announcer import Announcer
from memory_agent.ha_client import HAClient
from memory_agent.perception_ingest import PerceptionEvent

TTS = "tts.edgetts_zh_cn_xiaoxiaoneural"
TARGET = "media_player.living_speaker"


class FakeHA:
    def __init__(self):
        self.calls = []

    def execute_action(self, action):
        self.calls.append(action)
        return {"ok": True, "status_code": 200}


def _ev(kind, payload=None):
    return PerceptionEvent(source="edge_ai", kind=kind, room="客厅", payload=payload or {})


def _ann(ha, **kw):
    kw.setdefault("tts_entity", TTS)
    kw.setdefault("target", TARGET)
    kw.setdefault("enabled", True)
    return Announcer(ha, None, **kw)


def test_announce_enabled_face_known():
    ha = FakeHA()
    a = _ann(ha, cooldown_sec=0)
    assert a.announce(_ev("face_known", {"friendly_name": "爸爸"})) is True
    call = ha.calls[0]
    assert call["params"]["message"] == "客厅的爸爸回来了"
    # tts.speak 必须带播放设备，否则只合成不发声
    assert call["params"]["target"] == TARGET


def test_announce_unknown_and_cry_messages():
    ha = FakeHA()
    a = _ann(ha, cooldown_sec=0)
    a.announce(_ev("face_unknown"))
    a.announce(_ev("cry"))
    msgs = [c["params"]["message"] for c in ha.calls]
    assert "客厅有陌生人出现" in msgs
    assert "客厅的婴儿在哭" in msgs


def test_announce_disabled_without_entity():
    ha = FakeHA()
    a = _ann(ha, tts_entity="")  # 无 tts 实体 → 强制关闭
    assert a.enabled is False
    assert a.announce(_ev("face_known")) is False
    assert ha.calls == []


def test_announce_disabled_without_target():
    """只配 tts 实体、未配播放设备 → 未就绪（HA 会静默不发声）。"""
    ha = FakeHA()
    a = _ann(ha, target="")
    assert a.enabled is False
    assert a.announce(_ev("face_known")) is False
    assert ha.calls == []


def test_announce_cooldown_suppresses_repeat():
    ha = FakeHA()
    a = _ann(ha, cooldown_sec=1000)
    assert a.announce(_ev("face_known")) is True
    # 同类事件在冷却期内被抑制
    assert a.announce(_ev("face_known")) is False
    assert len(ha.calls) == 1


def test_execute_action_speak_sets_media_player_entity_id(monkeypatch):
    """ha_client 侧：speak 命令须把 target 映射为 media_player_entity_id。"""
    captured: dict = {}

    def fake_call_service(domain, service, entity_id, service_data=None):
        captured.update({"domain": domain, "service": service,
                         "entity_id": entity_id, "data": service_data or {}})
        return {"ok": True}

    cfg = type("C", (), {"hass_server": "http://ha.local", "hass_token": "t"})()
    ha = HAClient(cfg)
    monkeypatch.setattr(ha, "call_service", fake_call_service)

    ha.execute_action({"device": TTS, "command": "speak",
                       "params": {"message": "你好", "speaker": TTS, "target": TARGET}})
    assert captured["domain"] == "tts" and captured["service"] == "speak"
    assert captured["entity_id"] == TTS
    assert captured["data"]["media_player_entity_id"] == TARGET
    assert captured["data"]["message"] == "你好"

    # 未给 target 时不额外塞字段（保持向后兼容）
    captured.clear()
    ha.execute_action({"device": TTS, "command": "speak", "params": {"message": "hi"}})
    assert "media_player_entity_id" not in captured["data"]
