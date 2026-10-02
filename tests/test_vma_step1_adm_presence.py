"""ADM 联动计划 第 1 步：presence（retained status/caps）+ LWT + 收件箱双通道。

锁四件事：
1. LWT 一定在 ``connect()`` **之前**登记（will 是 CONNECT 报文字段，连上再设永不生效）；
2. 库在场 / 不在场两条路产出的主题与载荷**同构**——交付形态还没裁，DB 不该因为
   "MA 这次带了库"看到不同字段集；
3. 收件箱的三道护栏（trace_id 必填、长度上界、只写白名单主题）；
4. 告警双通道共用一枚 trace_id（契约把它定为跨仓排障锚点）。

不连真 broker：注入桩客户端，断言调用顺序与 publish 参数。
"""
import json
import os
import sys
import inspect
from types import SimpleNamespace

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import mqtt_bridge as mb  # noqa: E402
from memory_agent import runtime as runtime_mod  # noqa: E402
from memory_agent.config import Config  # noqa: E402
from memory_agent.mqtt_bridge import MqttBridge  # noqa: E402
from memory_agent.vision_service import VisionService  # noqa: E402


class _Client:
    """记录 publish 参数，可切换连接态。"""

    def __init__(self, connected=True):
        self.published = []
        self._connected = connected

    def is_connected(self):
        return self._connected

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append(
            {"topic": topic, "payload": payload, "qos": qos, "retain": retain}
        )

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def topics(self):
        return [p["topic"] for p in self.published]


def _cfg(**kw) -> Config:
    c = Config()
    c.tv_mqtt_host = "192.168.2.200"
    c.tv_mqtt_port = 1883
    c.ma_mqtt_enabled = True
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _bridge(client, presence=False):
    """presence=False 走 MA 同构直发；给模块则走库的口径。"""
    bridge = MqttBridge(_cfg(), client_factory=lambda cfg: client)
    mb._presence_probe = presence
    return bridge


def _last(client, topic):
    for entry in reversed(client.published):
        if entry["topic"] == topic:
            return entry
    raise AssertionError(f"未发布到 {topic}：{client.topics()}")


@pytest.fixture(autouse=True)
def _restore_probe():
    """presence 探测缓存是模块级的：不留桩就污染同批其它文件。"""
    saved = mb._presence_probe
    yield
    mb._presence_probe = saved


# ── 1. LWT 的登记时机 ───────────────────────────────────────────────────────

def test_lwt_is_registered_before_connect(monkeypatch):
    """will_set 必须早于 connect()：这是第 1 步 ③「kill -9 后 broker 代发 offline」的机制前提。"""
    order = []

    class _WillClient:
        def reconnect_delay_set(self, min_delay, max_delay):
            pass

        def username_pw_set(self, user, pw):
            pass

        def will_set(self, topic, payload, qos=0, retain=False):
            order.append("will_set")
            self.will = (topic, payload, qos, retain)

        def connect(self, host, port, keepalive=60):
            order.append("connect")

        def loop_start(self):
            order.append("loop_start")

    made = {}

    class _StubPaho:
        CallbackAPIVersion = SimpleNamespace(VERSION1="v1")

        @staticmethod
        def Client(api, client_id=None):
            c = _WillClient()
            made["client"] = c
            return c

    monkeypatch.setattr(mb, "mqtt", _StubPaho)
    monkeypatch.setattr(mb, "MQTT_AVAILABLE", True)
    client = mb._default_client_factory(_cfg())
    assert order == ["will_set", "connect", "loop_start"]
    assert client.will == ("adm/memory-agent/status", "offline", 1, True)


# ── 2. presence：两条路同构 ─────────────────────────────────────────────────

def test_advertise_publishes_retained_status_and_caps_without_library():
    client = _Client()
    bridge = _bridge(client)
    assert bridge.advertise({"mcp": True, "tools": ["ask_memory"], "version": "1.0.0"}) is True
    status = _last(client, "adm/memory-agent/status")
    caps = _last(client, "adm/memory-agent/caps")
    assert status["payload"] == "online" and status["retain"] is True and status["qos"] == 1
    assert caps["retain"] is True and caps["qos"] == 1
    assert json.loads(caps["payload"])["tools"] == ["ask_memory"]


def test_advertise_without_caps_publishes_only_status():
    client = _Client()
    _bridge(client).advertise()
    assert client.topics() == ["adm/memory-agent/status"]


def _stub_presence(captured):
    """按 homesdk.presence.advertise 的真实行为做替身（retained online + caps）。"""

    def advertise(client, name, caps=None, offline=False):
        captured["name"] = name
        if offline:
            client.publish(f"adm/{name}/status", "offline", qos=1, retain=True)
            return
        client.publish(f"adm/{name}/status", "online", qos=1, retain=True)
        if caps is not None:
            client.publish(f"adm/{name}/caps", json.dumps(caps, ensure_ascii=False),
                           qos=1, retain=True)

    return SimpleNamespace(advertise=advertise)


def test_library_and_library_free_paths_are_indistinguishable_on_the_wire():
    """装没装库，broker 上看到的主题/载荷必须一致——否则交付形态裁定会改变对端行为。"""
    caps = {"mcp": True, "tools": ["ask_memory", "search_events"], "version": "1.0.0"}
    plain = _Client()
    _bridge(plain).advertise(caps)

    captured = {}
    lib_client = _Client()
    _bridge(lib_client, presence=_stub_presence(captured)).advertise(caps)

    assert captured["name"] == "memory-agent"
    assert [p["topic"] for p in lib_client.published] == [p["topic"] for p in plain.published]
    for lib, ours in zip(lib_client.published, plain.published):
        assert lib["retain"] == ours["retain"] and lib["qos"] == ours["qos"]
        assert lib["payload"] == ours["payload"]  # status 是裸串、caps 是 JSON 串，两边逐字相同


def test_advertise_refuses_when_the_client_is_not_connected():
    """未连上时的 publish 会「成功」但一条都发不出去——presence 不许把它记成已广播。"""
    client = _Client(connected=False)
    bridge = _bridge(client)
    assert bridge.advertise({"mcp": True}) is False
    assert client.published == []
    assert bridge.status()["advertised"] is False


def test_ensure_advertised_resends_only_on_the_reconnect_edge():
    client = _Client()
    bridge = _bridge(client)
    assert bridge.ensure_advertised({"mcp": True}) is True
    first = len(client.published)
    assert bridge.ensure_advertised({"mcp": True}) is True
    assert len(client.published) == first  # 已在线且不重发，不刷屏

    client._connected = False
    assert bridge.ensure_advertised({"mcp": True}) is False
    assert bridge._advertised is False  # 断连时标记必须一起清掉，否则重连后不会再发
    client._connected = True
    assert bridge.ensure_advertised({"mcp": True}) is True
    assert len(client.published) > first  # broker 重启丢 retained 后重新落上


def test_close_publishes_retained_offline():
    """正常断开不会触发 LWT，所以关前要主动把 retained 置 offline。"""
    client = _Client()
    bridge = _bridge(client)
    bridge.advertise({"mcp": True})
    bridge.close()
    status = _last(client, "adm/memory-agent/status")
    assert status["payload"] == "offline" and status["retain"] is True


def test_new_client_resets_the_advertised_flag():
    client = _Client()
    bridge = _bridge(client)
    bridge.advertise({"mcp": True})
    assert bridge._advertised is True
    bridge._client = None
    bridge._ensure_client()
    assert bridge._advertised is False  # 换了客户端，LWT 与 presence 都要重来


# ── 3. 收件箱护栏 ───────────────────────────────────────────────────────────

def test_notify_requires_trace_id_and_publishes_nothing_without_it():
    client = _Client()
    bridge = _bridge(client)
    assert bridge.publish_notify("标题", "正文", trace_id="") is False
    assert bridge.publish_notify("标题", "正文", trace_id="   ") is False
    assert client.published == []


def test_notify_targets_only_the_whitelisted_inbox_topic():
    client = _Client()
    _bridge(client).publish_notify("标题", "正文", trace_id="t-1")
    assert client.topics() == ["butler/inbox/notify"]
    assert "butler/inbox/notify" in mb.INBOX_TOPICS


def test_notify_bounds_are_applied_before_publish():
    """库的口径是"超长即拒绝投递"，MA 先截断到界内：告警不能因为一句话超长整条消失。"""
    client = _Client()
    _bridge(client).publish_notify("标" * 200, "文" * 2000, trace_id="t-2")
    payload = json.loads(_last(client, "butler/inbox/notify")["payload"])
    assert len(payload["title"]) <= mb.INBOX_MAX_TITLE
    assert len(payload["body"]) <= mb.INBOX_MAX_BODY
    assert payload["title"].endswith("…")


def test_notify_omits_empty_channel_and_zero_priority_like_the_library_does():
    client = _Client()
    _bridge(client).publish_notify("标题", "正文", trace_id="t-3", channel="", priority=0)
    plain = json.loads(_last(client, "butler/inbox/notify")["payload"])
    assert set(plain) == {"trace_id", "ts", "title", "body"}

    client2 = _Client()
    _bridge(client2).publish_notify("标题", "正文", trace_id="t-4", channel="vision", priority=5)
    full = json.loads(_last(client2, "butler/inbox/notify")["payload"])
    assert set(full) == {"trace_id", "ts", "title", "body", "channel", "priority"}
    # ts 是 epoch 秒（库那份就是 int(time.time())）：墙钟口径只用于人读的时间串
    assert isinstance(full["ts"], int)


def _stub_notify(captured):
    def notify(client, title, body, *, trace_id, channel="", priority=0, qos=1):
        if len(title) > mb.INBOX_MAX_TITLE or len(body) > mb.INBOX_MAX_BODY:
            raise ValueError("超长，拒绝投递")
        captured["args"] = (title, body, trace_id, channel, priority)
        payload = {"trace_id": trace_id, "ts": 1_700_000_000, "title": title, "body": body}
        if channel:
            payload["channel"] = channel
        if priority:
            payload["priority"] = priority
        client.publish("butler/inbox/notify", json.dumps(payload, ensure_ascii=False),
                       qos=1, retain=False)

    return SimpleNamespace(notify=notify, advertise=lambda *a, **k: None)


def test_library_notify_path_is_used_when_present_and_never_over_bounds():
    captured = {}
    client = _Client()
    _bridge(client, presence=_stub_notify(captured)).publish_notify(
        "标" * 200, "正文", trace_id="t-5", channel="vision", priority=5
    )
    title, body, trace_id, channel, priority = captured["args"]
    assert len(title) <= mb.INBOX_MAX_TITLE  # 越界已在 MA 侧收口，库那条拒绝路径不会触发
    assert (body, trace_id, channel, priority) == ("正文", "t-5", "vision", 5)
    assert _last(client, "butler/inbox/notify")["retain"] is False


def test_reset_presence_probe_unlocks_the_cache():
    mb._presence_probe = False
    mb.reset_presence_probe()
    assert mb._presence_probe is None


def test_status_exposes_the_presence_backend():
    client = _Client()
    bridge = _bridge(client)
    snap = bridge.status()
    assert snap["homesdk_presence"] is False and snap["advertised"] is False
    bridge.advertise({"mcp": True})
    assert bridge.status()["advertised"] is True


# ── 4. 运行时接线 ───────────────────────────────────────────────────────────

def test_reload_config_resets_the_presence_probe():
    """第七轮的教训：探测缓存必须可解，否则热更新装上的库永远不被看见。"""
    src = inspect.getsource(runtime_mod.AppRuntime.reload_config)
    assert "reset_presence_probe()" in src
    assert "reset_presence_probe" in inspect.getsource(runtime_mod)


def test_adm_caps_shape_follows_the_contract_table():
    caps = runtime_mod.AppRuntime.adm_caps(SimpleNamespace())
    assert set(caps) == {"mcp", "tools", "version"}
    assert caps["mcp"] is True
    from memory_agent import tool_schema

    assert caps["tools"] == list(tool_schema.TOOL_NAMES)
    assert caps["version"]


def test_startup_advertises_before_the_first_interval():
    src = inspect.getsource(runtime_mod.AppRuntime.startup)
    assert "ensure_advertised(self.adm_caps())" in src
    loop = inspect.getsource(runtime_mod.AppRuntime._periodic_mqtt_presence)
    assert "ensure_advertised(self.adm_caps())" in loop  # 断连重连后补发


# ── 5. 告警双通道 ───────────────────────────────────────────────────────────

class _AlertMqtt:
    def __init__(self):
        self.raw = []
        self.notifies = []

    def publish_raw(self, topic, payload, retain=False):
        self.raw.append((topic, payload, retain))
        return True

    def publish_notify(self, title, body, *, trace_id, channel="", priority=0):
        self.notifies.append(
            {"title": title, "body": body, "trace_id": trace_id,
             "channel": channel, "priority": priority}
        )
        return True


def _vision(**kw):
    base = dict(vision_alert_mqtt_enabled=True, vision_alert_mqtt_topic="ma/insights",
                tz_offset_hours=8.0)
    base.update(kw)
    return VisionService(SimpleNamespace(**base), store=None, ha=None)


def test_stranger_alert_fans_out_to_both_channels_with_one_trace_id():
    mqtt = _AlertMqtt()
    svc = _vision()
    svc.mqtt = mqtt
    svc._maybe_publish_alert("客厅", [{"name": "陌生人"}], "http://nas/snap.jpg", None)

    assert [t for t, _, _ in mqtt.raw] == ["ma/insights"]
    assert len(mqtt.notifies) == 1
    insight_payload = mqtt.raw[0][1]
    notify = mqtt.notifies[0]
    assert insight_payload["trace_id"] == notify["trace_id"]  # 同一条告警，一个锚点
    assert insight_payload["snapshot_url"] == "http://nas/snap.jpg"
    assert "snapshot_url" not in notify  # 播报用不到内部存储路径，别把它送上公共收件箱
    assert notify["channel"] == "vision" and notify["priority"] == 5


def test_known_member_triggers_neither_channel():
    mqtt = _AlertMqtt()
    svc = _vision()
    svc.mqtt = mqtt
    svc._maybe_publish_alert("客厅", [{"name": "顾安恒"}], "", None)
    assert mqtt.raw == [] and mqtt.notifies == []


def test_alert_still_works_when_the_notify_channel_refuses():
    """收件箱是旁路：它失败不能把 ma/insights 那半边一起带走。"""
    mqtt = _AlertMqtt()
    mqtt.publish_notify = lambda *a, **k: False
    svc = _vision()
    svc.mqtt = mqtt
    svc._maybe_publish_alert("书房", [{"name": "unknown"}], "", None)
    assert len(mqtt.raw) == 1


# ── 6. 计划验收：越权主题清零 ───────────────────────────────────────────────

def test_ma_domain_payloads_carry_trace_id_and_ts():
    """契约表 §二 给 ma/* 定的字段里有 trace_id 与 ts——MA 原先两条都不带（补齐为加法，不改键名）。"""
    client = _Client()
    bridge = _bridge(client)
    assert bridge.publish_presence([{"name": "顾", "room": "客厅"}], "2026-10-02T13:00:00")
    presence = json.loads(_last(client, "ma/presence")["payload"])
    assert set(presence) >= {"trace_id", "ts", "members", "total"}

    assert bridge.publish_health_change("sensor.a", "online", "stale")
    health = json.loads(_last(client, "ma/device-health")["payload"])
    assert set(health) >= {"trace_id", "ts", "entity_id", "from", "to"}

    # ts 必须是家庭墙钟而不是容器机器钟（UTC 容器里差 8 小时）
    assert not health["ts"].endswith("Z") and len(health["ts"]) == 19


def test_stranger_alert_payload_has_the_contract_anchor_fields():
    mqtt = _AlertMqtt()
    svc = _vision()
    svc.mqtt = mqtt
    svc._maybe_publish_alert("客厅", [{"name": "陌生人"}], "", None)
    payload = mqtt.raw[0][1]
    assert set(payload) >= {"trace_id", "ts", "room", "message", "alert_type"}


def test_no_butler_trigger_topics_left_in_src():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    hits = []
    for dirpath, _, files in os.walk(root):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                if "butler/trigger/" in fh.read():
                    hits.append(path)
    assert hits == []
