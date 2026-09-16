"""交接单_顾安恒专属对话整合：MA 侧配合项单测（无需网络/DB）。

覆盖：
1. frame_url：go2rtc 帧外链构造（含 null 语义）；
2. vlm_analyze payload：会话归集 conversation_id + keep_conversation；
3. _maybe_publish_alert：陌生人异常 → MQTT（默认关短路 / 已知成员不触发）。
"""
import os
import sys
from types import SimpleNamespace

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import vision_service as vs_mod  # noqa: E402
from memory_agent.vision_service import VisionService  # noqa: E402


def _cfg(**kw):
    base = dict(
        go2rtc_base_url="http://192.168.2.200:1984",
        vlm_base_url="http://192.168.2.200:9090",
        vlm_endpoint_path="/v1/chat/completions",
        vlm_model="doubao",
        vlm_api_key="k",
        vlm_max_retries=1,
        vlm_timeout_s=5,
        vlm_conversation_id="",
        vlm_keep_conversation=True,
        vision_alert_mqtt_enabled=False,
        vision_alert_mqtt_topic="butler/trigger/gu_anheng_alert",
        tz_offset_hours=8.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _svc(cfg):
    return VisionService(cfg, store=None, ha=None)


def test_frame_url():
    svc = _svc(_cfg())
    assert svc.frame_url("cam_客厅") == (
        "http://192.168.2.200:1984/api/frame.jpeg?src=cam_%E5%AE%A2%E5%8E%85"
    )
    assert svc.frame_url("") is None
    assert _svc(_cfg(go2rtc_base_url="")).frame_url("cam_x") is None


def test_vlm_payload_conversation(monkeypatch):
    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["payload"] = json
        return _Resp()

    monkeypatch.setattr(vs_mod.httpx, "post", fake_post)

    svc = _svc(_cfg(vlm_conversation_id="38440360274498562", vlm_keep_conversation=True))
    svc.vlm_analyze(b"\xff\xd8jpg", "hi")
    assert captured["payload"]["conversation_id"] == "38440360274498562"
    assert captured["payload"]["keep_conversation"] is True
    # silent=True 规避 doubao2api 缓存 system prompt 时的注入 500 bug
    assert captured["payload"]["silent"] is True

    captured.clear()
    svc2 = _svc(_cfg(vlm_conversation_id=""))
    svc2.vlm_analyze(b"\xff\xd8jpg", "hi")
    assert "conversation_id" not in captured["payload"]
    assert "keep_conversation" not in captured["payload"]


def test_maybe_publish_alert():
    class FakeMqtt:
        def __init__(self):
            self.sent = []

        def publish_raw(self, topic, payload, retain=False):
            self.sent.append((topic, payload))
            return True

    svc = _svc(_cfg(vision_alert_mqtt_enabled=True))
    svc.mqtt = FakeMqtt()
    svc._maybe_publish_alert("起居室", [{"name": "陌生人"}], "http://x/f.jpg", "陌生人在客厅")
    assert len(svc.mqtt.sent) == 1
    topic, payload = svc.mqtt.sent[0]
    assert topic == "butler/trigger/gu_anheng_alert"
    assert payload["alert_type"] == "stranger"
    assert payload["snapshot_url"] == "http://x/f.jpg"

    # 已知成员 → 不推送
    svc._maybe_publish_alert("起居室", [{"name": "lidicn"}], "u", "a")
    assert len(svc.mqtt.sent) == 1

    # 默认关 → 不推送
    svc2 = _svc(_cfg(vision_alert_mqtt_enabled=False))
    svc2.mqtt = FakeMqtt()
    svc2._maybe_publish_alert("起居室", [{"name": "陌生人"}], "u", "a")
    assert svc2.mqtt.sent == []
