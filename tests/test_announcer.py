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
    """没有收件箱可回落时，缺 tts 实体 = 整条链路无路可走（HA 那条不通）。"""
    ha = FakeHA()
    a = _ann(ha, tts_entity="")  # 无 tts 实体、无 mqtt
    assert a.enabled is False
    assert a.announce(_ev("face_known")) is False
    assert ha.calls == []


def test_announce_disabled_without_target():
    """只配 tts 实体、未配播放设备 → HA 那条未就绪（HA 会只合成不出声）。"""
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


# ── DCD 20261007 §四 Q1 裁 B：两条路并存，按优先级 ──────────────────────────

class FakeMqtt:
    """收件箱替身：只记 `publish_speak` 的入参，返回值可控（桥自己会记契约码）。"""

    def __init__(self, enabled=True, ok=True):
        self.enabled = enabled
        self.ok = ok
        self.calls = []

    def publish_speak(self, text, *, trace_id, **kw):
        self.calls.append({"text": text, "trace_id": trace_id, "kw": kw})
        return self.ok


def _ann_inbox(**kw):
    """缺 HA 那两键的常见形态：实体没配（或播放设备没配），但收件箱在场。"""
    ha = kw.pop("ha", FakeHA())
    mqtt = kw.pop("mqtt", FakeMqtt())
    kw.setdefault("tts_entity", "")
    kw.setdefault("target", "")
    kw.setdefault("enabled", True)
    return ha, mqtt, Announcer(ha, None, mqtt=mqtt, **kw)


def test_inbox_fallback_when_ha_route_is_not_configured():
    """裁 B 的正面：HA 直发没配起来时播报不该就此沉默，改投 `butler/inbox/speak`。"""
    ha, mqtt, a = _ann_inbox()
    assert a.enabled is False and a.inbox_ready is True
    assert a.announce(_ev("face_known", {"friendly_name": "妈妈"})) is True
    assert mqtt.calls[0]["text"] == "客厅的妈妈回来了"
    assert ha.calls == [], "HA 那条不通就不该往 HA 打"


def test_missing_target_falls_back_too():
    """只配实体不配播放设备 = HA 会只合成不出声，那个洞正是回落要接的那一个。"""
    ha, mqtt, a = _ann_inbox(tts_entity=TTS, target="")
    assert a.announce(_ev("cry")) is True
    assert [c["text"] for c in mqtt.calls] == ["客厅的婴儿在哭"]
    assert ha.calls == []


def test_fallback_carries_a_real_trace_id():
    """契约 §七 E 的 `trace_id` 必填且桥会拒空串：给个空的等于把这条投递变成必然失败。"""
    _, mqtt, a = _ann_inbox()
    a.announce(_ev("face_unknown"))
    tid = mqtt.calls[0]["trace_id"]
    assert len(tid) == 32 and all(c in "0123456789abcdef" for c in tid), tid


def test_ha_route_wins_and_inbox_stays_silent():
    """**优先级而不是并行**：两键都配了就走 HA 直发，同一次事件绝不能两处同时说话——
    裁定驳回 A/C 时点名的就是那个形状。"""
    ha, mqtt, a = _ann_inbox(tts_entity=TTS, target=TARGET)
    assert a.enabled is True
    assert a.announce(_ev("face_known")) is True
    assert len(ha.calls) == 1
    assert mqtt.calls == []


def test_no_route_means_false_not_true():
    """两条路都不通时必须报 False。报成 True 是把"配错了"洗成"听起来一切正常"。"""
    _, _, a = _ann_inbox(mqtt=None)
    assert a.announce(_ev("face_known")) is False
    ha2, mqtt2, a2 = _ann_inbox(mqtt=FakeMqtt(enabled=False))
    assert a2.inbox_ready is False
    assert a2.announce(_ev("face_known")) is False
    assert mqtt2.calls == [] and ha2.calls == []


def test_master_switch_blocks_both_routes():
    """`announce_enabled` 仍是总闸：关掉时 HA 和收件箱都不该收到东西。"""
    ha, mqtt, a = _ann_inbox(enabled=False, tts_entity=TTS, target=TARGET)
    assert a.announce(_ev("cry")) is False
    assert ha.calls == [] and mqtt.calls == []


def test_cooldown_applies_on_the_fallback_path_too():
    """冷却是"同一类事件别刷屏"，不该因为换了条路就失效。"""
    _, mqtt, a = _ann_inbox(cooldown_sec=1000)
    assert a.announce(_ev("face_known")) is True
    assert a.announce(_ev("face_known")) is False
    assert len(mqtt.calls) == 1


def test_inbox_rejection_is_reported_as_not_announced():
    """桥拒发（载荷不合法 / broker 不可达）时返回值要如实是 False，桥自己记的码不在这里重造。"""
    _, mqtt, a = _ann_inbox(mqtt=FakeMqtt(ok=False))
    assert a.announce(_ev("face_known")) is False
    assert len(mqtt.calls) == 1


def test_inbox_exception_does_not_escape_the_perception_loop():
    """采集主链路不能被一条播报的异常拖下水——兜成 False 才是旁路该有的样子。"""
    class _Boom(FakeMqtt):
        def publish_speak(self, text, *, trace_id, **kw):
            raise RuntimeError("broker 线程炸了")

    _, _, a = _ann_inbox(mqtt=_Boom())
    assert a.announce(_ev("face_known")) is False


def test_ha_rejection_is_reported_as_not_announced():
    """HA 回了 `ok=False`（服务调用 4xx/实体不在场）时返回值必须如实是 False。

    回落那条有对偶用例（`test_inbox_rejection_is_reported_as_not_announced`），HA 这条是
    变异档 N10 逼出来的盲区：把 `return ok` 写成 `return True` 时全仓 67 条用例一条都不响，
    而调用方拿这个返回值决定"要不要记一次播报"——谎报等于把配置错误洗成一切正常。
    """
    class _Reject(FakeHA):
        def execute_action(self, action):
            self.calls.append(action)
            return {"ok": False, "status_code": 404, "error": "entity_id not found"}

    ha = _Reject()
    a = _ann(ha, cooldown_sec=0)
    assert a.announce(_ev("face_known")) is False
    assert len(ha.calls) == 1, "确实打出去了，只是没成——两者要能分开"


def test_ha_non_dict_reply_is_not_announced_and_does_not_escape():
    """HA 客户端回非 dict（None / 字符串 / 异常包装体）时不许抛穿采集链路。

    N12 档：把 `isinstance(res, dict)` 那层判断删掉，`res.get(...)` 当场 AttributeError，
    而 `announce()` 是被周期任务调的——旁路能力炸穿主链路正是审计反复点名的形状。
    """
    class _Garbage(FakeHA):
        def __init__(self, reply):
            super().__init__()
            self.reply = reply

        def execute_action(self, action):
            self.calls.append(action)
            return self.reply

    for reply in (None, "unexpected", 42, ["ok"]):
        ha = _Garbage(reply)
        a = _ann(ha, cooldown_sec=0)
        assert a.announce(_ev("cry")) is False, reply
