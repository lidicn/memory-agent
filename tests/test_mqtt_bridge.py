"""MQTT 实时推送桥测试（v0.4）

不连真 broker：client_factory 注入桩客户端，断言主题、载荷与 retain 语义。
重点验证「旁路能力」的三条承诺：
1. 未启用 / 建连失败时 publish 返回 False 且不抛错
2. 建连失败只试一次，不反复重连刷日志
3. 主流程不受推送失败影响
"""

import json
import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config  # noqa: E402
from memory_agent.mqtt_bridge import MQTT_AVAILABLE, MqttBridge  # noqa: E402


class _FakeClient:
    def __init__(self):
        self.published = []
        self.loop_started = False
        self.disconnected = False

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append(
            {"topic": topic, "payload": payload, "qos": qos, "retain": retain}
        )

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        pass

    def disconnect(self):
        self.disconnected = True


def _cfg(**kw) -> Config:
    c = Config()
    c.tv_mqtt_host = "192.168.2.200"
    c.tv_mqtt_port = 1883
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _bridge(cfg, client=None, factory=None):
    return MqttBridge(cfg, client_factory=factory or (lambda c: client))


# ── 启用判定 ────────────────────────────────────────────────────────────────

def test_disabled_when_flag_off():
    """未启用时 publish 直接空转：不建连、不抛错。"""
    bridge = _bridge(_cfg(ma_mqtt_enabled=False), client=_FakeClient())
    assert bridge.enabled is False
    assert bridge.publish("presence", {"a": 1}) is False


def test_disabled_without_broker_host():
    cfg = _cfg(ma_mqtt_enabled=True)
    cfg.tv_mqtt_host = ""
    assert _bridge(cfg, client=_FakeClient()).enabled is False


def test_available_requires_paho():
    bridge = _bridge(_cfg(ma_mqtt_enabled=True), client=_FakeClient())
    assert bridge.available == (bridge.enabled and MQTT_AVAILABLE)


# ── 发布语义 ────────────────────────────────────────────────────────────────

def test_publish_builds_topic_and_json_payload():
    client = _FakeClient()
    bridge = _bridge(_cfg(ma_mqtt_enabled=True), client=client)
    assert bridge.publish("presence", {"members": []}) is True
    assert client.published[0]["topic"] == "ma/presence"
    assert json.loads(client.published[0]["payload"]) == {"members": []}


def test_custom_topic_prefix():
    client = _FakeClient()
    bridge = _bridge(_cfg(ma_mqtt_enabled=True, ma_mqtt_topic_prefix="home"), client=client)
    bridge.publish("device-health", {"x": 1})
    assert client.published[0]["topic"] == "home/device-health"


def test_presence_is_retained():
    """在场用 retain：新订阅者能立刻拿到当前状态。"""
    client = _FakeClient()
    bridge = _bridge(_cfg(ma_mqtt_enabled=True), client=client)
    bridge.publish_presence([{"name": "Kevin", "room": "客厅"}], "2026-09-09T10:00:00")
    msg = client.published[0]
    assert msg["topic"] == "ma/presence"
    assert msg["retain"] is True
    body = json.loads(msg["payload"])
    assert body["total"] == 1 and body["members"][0]["name"] == "Kevin"


def test_health_change_payload():
    client = _FakeClient()
    bridge = _bridge(_cfg(ma_mqtt_enabled=True), client=client)
    bridge.publish_health_change("light.a", "active", "stale", "light__客厅灯")
    msg = client.published[0]
    assert msg["topic"] == "ma/device-health"
    assert msg["retain"] is False
    body = json.loads(msg["payload"])
    assert body["entity_id"] == "light.a"
    assert body["stable_id"] == "light__客厅灯"
    assert body["from"] == "active" and body["to"] == "stale"
    assert body["ts"]


# ── 失败兜底 ────────────────────────────────────────────────────────────────

def test_factory_failure_only_attempted_once():
    """建连失败后只试一次，避免每轮推送都重连刷日志。"""
    calls = []

    def factory(cfg):
        calls.append(cfg)
        return None

    bridge = MqttBridge(_cfg(ma_mqtt_enabled=True), client_factory=factory)
    assert bridge.publish("presence", {}) is False
    assert bridge.publish("presence", {}) is False
    assert len(calls) == 1


def test_publish_exception_is_swallowed(monkeypatch):
    """推送抛错不得上抛——旁路能力不能拖垮主流程。"""

    class _Boom:
        def publish(self, *a, **kw):
            raise RuntimeError("broker 炸了")

    bridge = _bridge(_cfg(ma_mqtt_enabled=True), client=_Boom())
    assert bridge.publish("presence", {}) is False


def test_close_disconnects():
    client = _FakeClient()
    bridge = _bridge(_cfg(ma_mqtt_enabled=True), client=client)
    bridge.publish("presence", {})
    bridge.close()
    assert client.disconnected is True
    # 关闭后再推不会重建连接
    assert bridge.publish("presence", {}) is False


def test_periodic_reconnect_after_cooldown(monkeypatch):
    """broker 长期不可达时，越过退避窗口后会重新尝试建连（不再永久放弃，v0.6 修复）。"""
    clock = {"t": 1000.0}
    monkeypatch.setattr("memory_agent.mqtt_bridge.time.time", lambda: clock["t"])
    calls = []

    def factory(cfg):
        calls.append(clock["t"])
        return None

    cfg = _cfg(ma_mqtt_enabled=True, ma_mqtt_reconnect_interval=10)
    bridge = MqttBridge(cfg, client_factory=factory)
    assert bridge.publish("presence", {}) is False  # t=1000 首次尝试
    assert bridge.publish("presence", {}) is False  # 冷却中，不重试
    assert len(calls) == 1
    clock["t"] = 1011                                # 越过 10s 窗口
    assert bridge.publish("presence", {}) is False  # 再次尝试建连
    assert len(calls) == 2


def test_reconnects_when_broker_returns(monkeypatch):
    """broker 恢复后，越过冷却窗口再试即可成功建连并推送，无需重启 MA。"""
    clock = {"t": 2000.0}
    monkeypatch.setattr("memory_agent.mqtt_bridge.time.time", lambda: clock["t"])
    state = {"up": False}

    def factory(cfg):
        return _FakeClient() if state["up"] else None

    cfg = _cfg(ma_mqtt_enabled=True, ma_mqtt_reconnect_interval=10)
    bridge = MqttBridge(cfg, client_factory=factory)
    assert bridge.publish("presence", {}) is False  # 还没起来
    state["up"] = True
    clock["t"] = 2011                               # 越过冷却
    assert bridge.publish("presence", {}) is True   # 重连成功并推送
    assert bridge._client is not None
    assert len(bridge._client.published) == 1
