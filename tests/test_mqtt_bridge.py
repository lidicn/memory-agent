"""MQTT 实时推送桥测试（v0.4）

不连真 broker：client_factory 注入桩客户端，断言主题、载荷与 retain 语义。
重点验证「旁路能力」的四条承诺：
1. 未启用 / 建连失败时 publish 返回 False 且不抛错
2. 建连失败只试一次，不反复重连刷日志
3. 主流程不受推送失败影响
4. 轮换 broker 口令后新值能上线（`reset_client()`；`reload_config()` 里那条接缝见本档末尾）
"""

import hashlib
import json
import os
import sys
import types

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import mqtt_bridge  # noqa: E402
from memory_agent.config import Config  # noqa: E402
from memory_agent.mqtt_bridge import MQTT_AVAILABLE, MqttBridge, conn_params  # noqa: E402


class _FakeClient:
    def __init__(self):
        self.published = []
        self.loop_started = False
        self.disconnected = False
        self._connected = True

    def is_connected(self):
        return self._connected

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
    # MA30b：重连退避改用 monotonic（`self._retry_after`），假时钟不给这一口就永远停在真实钟上
    monkeypatch.setattr("memory_agent.mqtt_bridge.time.monotonic", lambda: clock["t"])
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
    # MA30b：重连退避改用 monotonic（`self._retry_after`），假时钟不给这一口就永远停在真实钟上
    monkeypatch.setattr("memory_agent.mqtt_bridge.time.monotonic", lambda: clock["t"])
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


# ── 凭据轮换：建连参数变更后新值必须能上线（联动路线图 rev 6《⛔ 运营指令》①）────
#
# 依据：`_default_client_factory` 在**建连那一刻**把 `tv_mqtt_user/tv_mqtt_pass` 交给
# paho（mqtt_bridge.py:191-193），而 `_ensure_client()` 一旦有客户端就原样复用 ⇒
# 「换完口令本仓还能正常发布」这条判据原先只能靠重启进程满足。


def _recording_factory(box):
    def factory(cfg):
        client = _FakeClient()
        box.append(cfg)
        return client
    return factory


def test_client_is_cached_so_a_bare_config_rebind_changes_nothing():
    """负控：下面几条腿都建立在「客户端只建一次」这个事实上。它若塌了，重置腿就是空锁。"""
    seen = []
    cfg = _cfg(ma_mqtt_enabled=True, tv_mqtt_user="svc", tv_mqtt_pass="old")
    bridge = MqttBridge(cfg, client_factory=_recording_factory(seen))
    assert bridge.publish("presence", {}) is True
    cfg.tv_mqtt_pass = "new"                      # 只改配置、不重置
    assert bridge.publish("presence", {}) is True
    assert len(seen) == 1, "客户端没被缓存 ⇒ 「轮换后不生效」这个前提⛔ 在了，本档要重读"


def test_reset_client_makes_the_new_credential_take_effect():
    seen = []
    cfg = _cfg(ma_mqtt_enabled=True, tv_mqtt_user="svc", tv_mqtt_pass="old")
    bridge = MqttBridge(cfg, client_factory=_recording_factory(seen))
    assert bridge.publish("presence", {}) is True
    old_client = bridge._client
    cfg.tv_mqtt_pass = "new"
    assert bridge.reset_client() is True
    assert bridge.publish("presence", {}) is True
    assert len(seen) == 2, "重置后应当重新建连一次"
    assert old_client.disconnected is True          # 旧连接要收掉，别留僵尸连接
    assert bridge._client is not old_client


def test_reset_client_is_not_close():
    """`close()` 之后发布永久空转；`reset_client()` 之后必须还能发——两者⛔ 可互换。"""
    bridge = MqttBridge(_cfg(ma_mqtt_enabled=True, tv_mqtt_pass="p"),
                        client_factory=lambda c: _FakeClient())
    assert bridge.publish("presence", {}) is True
    bridge.reset_client()
    assert bridge._closed is False
    assert bridge.publish("presence", {}) is True

    closed = MqttBridge(_cfg(ma_mqtt_enabled=True, tv_mqtt_pass="p"),
                        client_factory=lambda c: _FakeClient())
    assert closed.publish("presence", {}) is True
    closed.close()
    assert closed.publish("presence", {}) is False


def test_reset_client_without_a_live_connection_is_quiet():
    bridge = MqttBridge(_cfg(ma_mqtt_enabled=True), client_factory=lambda c: _FakeClient())
    assert bridge.reset_client() is False


def test_conn_params_watches_only_build_time_parameters():
    cfg = _cfg(tv_mqtt_host="10.0.0.1", tv_mqtt_port=1883, tv_mqtt_user="svc", tv_mqtt_pass="old")
    before = conn_params(cfg)
    cfg.ma_mqtt_topic_prefix = "other"        # 前缀/间隔/超时每次发布现读，⛔ 烤进客户端
    cfg.ma_mqtt_presence_interval = 15
    cfg.tv_mqtt_timeout_s = 9.0
    assert conn_params(cfg) == before, "非建连参数进尺 ⇒ 改个主题前缀也会重连"
    cfg.tv_mqtt_pass = "new"
    assert conn_params(cfg) != before


def test_conn_params_tolerates_missing_attributes():
    import types

    assert conn_params(types.SimpleNamespace()) == ("", "", "", "")


def test_runtime_reload_resets_the_mqtt_client_on_conn_change():
    """接缝腿（AST，⛔ 文本锚点）：`reload_config()` 里必须有一条**条件本身就是建连参数比对**
    的重置调用。文本锚点接不住「条件被掏空」——`if False and conn_params(old) != …` 里
    两个子串都还在，腿照样绿（本档踩过：首版就是文本锚点，变异 M1 活了下来）。
    """
    import ast
    import inspect

    from memory_agent import runtime

    def _calls_reset(node) -> bool:
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "reset_client"):
                return True
        return False

    tree = ast.parse(inspect.getsource(runtime.AppRuntime.reload_config).strip())
    guards = []
    for node in ast.walk(tree):
        test = getattr(node, "test", None)
        if not (isinstance(node, ast.If) and isinstance(test, ast.Compare)
                and isinstance(test.ops[0], ast.NotEq)):
            continue
        sides = [test.left, *test.comparators]
        if all(isinstance(s, ast.Call) and getattr(s.func, "id", "") == "conn_params" for s in sides):
            guards.append(node)
    assert guards, "reload_config 里按 conn_params 比对的那条守卫⛔ 在了（换成常量/文本条件＝红）"
    assert any(_calls_reset(node) for node in guards), "守卫在了，但 body 里没调 reset_client() ⇒ 新口令仍不上线"


# ---- 判例 1：轮换的判据是"新口令真的建起过一次连"，⛔"配置文件已改" ----


def test_conn_fingerprint_carries_no_credential_value():
    cfg = _cfg(tv_mqtt_user="butler", tv_mqtt_pass="rotated-pass-value")
    fp = mqtt_bridge.conn_fingerprint(cfg)
    for secret in ("butler", "rotated-pass-value", "192.168.2.200"):
        assert secret not in fp, f"指纹里漏出了值本体 {secret}"
    host8, rest = fp.split(":", 1)
    port, user8, pass8 = rest.split("/")
    assert port == "1883"
    assert host8 == hashlib.sha256(b"192.168.2.200").hexdigest()[:8]
    assert user8 == hashlib.sha256(b"butler").hexdigest()[:8]
    assert pass8 == hashlib.sha256(b"rotated-pass-value").hexdigest()[:8]


def test_conn_fingerprint_is_stable_and_moves_with_the_password():
    old = _cfg(tv_mqtt_user="butler", tv_mqtt_pass="first-pass")
    same = _cfg(tv_mqtt_user="butler", tv_mqtt_pass="first-pass")
    new = _cfg(tv_mqtt_user="butler", tv_mqtt_pass="second-pass")
    assert mqtt_bridge.conn_fingerprint(old) == mqtt_bridge.conn_fingerprint(same)
    assert mqtt_bridge.conn_fingerprint(old).split("/")[-1] != mqtt_bridge.conn_fingerprint(new).split("/")[-1]


def test_conn_fingerprint_marks_unset_credentials_as_dash():
    fp = mqtt_bridge.conn_fingerprint(_cfg())
    assert fp.endswith("-/-"), f"未配的 user/pass 该读成 `-`，实读 {fp}"


class _StubPahoClient:
    """够 `_default_client_factory` 走完建连流程的最小 paho 面（⛔ 碰网络）。"""

    def __init__(self, *args, **kwargs):
        self.auth = None
        self.connect_args = None
        self.on_connect = None
        self.loop_started = False

    def reconnect_delay_set(self, *a):
        pass

    def username_pw_set(self, user, password):
        self.auth = (user, password)

    def will_set(self, topic, payload, qos=0, retain=False):
        pass

    def connect(self, host, port, keepalive=60):
        self.connect_args = (host, port, keepalive)

    def loop_start(self):
        self.loop_started = True


def _stub_paho(monkeypatch):
    monkeypatch.setattr(mqtt_bridge, "mqtt", types.SimpleNamespace(
        Client=_StubPahoClient,
        CallbackAPIVersion=types.SimpleNamespace(VERSION1=1),
    ))
    monkeypatch.setattr(mqtt_bridge, "MQTT_AVAILABLE", True)


def test_connect_success_log_names_the_credential_in_use(monkeypatch, capsys):
    _stub_paho(monkeypatch)
    cfg = _cfg(tv_mqtt_user="butler", tv_mqtt_pass="rotated-pass-value")
    client = mqtt_bridge._default_client_factory(cfg)
    assert client.auth == ("butler", "rotated-pass-value"), "桩客户端没收到这枚凭据＝后面读数无意义"
    capsys.readouterr()
    client.on_connect(client, None, {}, 0)
    out = capsys.readouterr().out
    assert "已连接 broker" in out
    assert mqtt_bridge.conn_fingerprint(cfg) in out, "成功行⛔ 带凭据指纹＝判例 1 那条判据仍然读不出来"
    assert "rotated-pass-value" not in out and "butler" not in out


def test_rejected_connect_log_names_the_credential_in_use(monkeypatch, capsys):
    _stub_paho(monkeypatch)
    cfg = _cfg(tv_mqtt_user="butler", tv_mqtt_pass="rotated-pass-value")
    client = mqtt_bridge._default_client_factory(cfg)
    capsys.readouterr()
    client.on_connect(client, None, {}, 5)
    out = capsys.readouterr().out
    assert "连接被拒绝" in out and "未授权" in out
    assert mqtt_bridge.conn_fingerprint(cfg) in out


def test_late_callback_reports_the_credential_that_actually_connected(monkeypatch, capsys):
    """指纹必须在**建连那一刻**定下来：同一枚 Config 事后被改了，旧客户端的迟到回调⛔ 能把旧连接报成新凭据。"""
    _stub_paho(monkeypatch)
    cfg = _cfg(tv_mqtt_user="butler", tv_mqtt_pass="first-pass")
    old_fp = mqtt_bridge.conn_fingerprint(cfg)
    client = mqtt_bridge._default_client_factory(cfg)
    cfg.tv_mqtt_pass = "second-pass"
    capsys.readouterr()
    client.on_connect(client, None, {}, 0)
    out = capsys.readouterr().out
    assert old_fp in out, "迟到回调丢了建连时的指纹 ⇒ 轮换读数无法归位到那次真建连"
    assert mqtt_bridge.conn_fingerprint(cfg) not in out, "回调读的是改过的 cfg＝假绿"
