"""MQTT 实时推送桥（v0.4）

为什么需要它
------------
TVPilot / DeskPilot 希望在状态变化时**被推送**，而不是轮询 MA。本模块把 MA 内部
的关键事件发布到 MQTT broker，供 TV / PC 订阅后渲染实时卡片：

* ``ma/presence``      成员在场快照（谁在哪个房间、通过什么方式识别）
* ``ma/device-health`` 设备健康状态变化（在线 ↔ 失联 ↔ 失效）

设计约束
--------
1. **旁路能力，绝不拖累主链路**：任何异常都吞掉并返回 False，
   推送失败不得影响采集、对账或查询。
2. **未启用即空转**：``ma_mqtt_enabled`` 未开、broker 未配、或环境缺
   ``paho-mqtt`` 时，``publish()`` 直接返回 False，不抛错、不建连。
3. **复用现有 broker**：连接参数沿用 ``tv_mqtt_host/port/user/pass``
   （与 TV Cam 同一 broker），不引入第二套 broker 配置。
4. **只在变化时发**：在场快照做内容去重，避免周期性重复消息刷屏。
"""

from __future__ import annotations

import json
import time
from typing import Any

try:  # paho-mqtt 是可选依赖：容器装了才真正推送，缺了只是不推
    import paho.mqtt.client as mqtt

    MQTT_AVAILABLE = True
    MQTT_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - 取决于运行环境
    mqtt = None  # type: ignore[assignment]
    MQTT_AVAILABLE = False
    MQTT_IMPORT_ERROR = str(exc)


# CONNACK 返回码 → 中文说明（rc != 0 时连接实际不可用）
_CONNACK_TEXT = {
    0: "连接成功",
    1: "协议版本不支持",
    2: "客户端标识被拒绝",
    3: "服务器不可用",
    4: "用户名或密码错误",
    5: "未授权（broker 要求认证，请检查 tv_mqtt_user / tv_mqtt_pass）",
}


def _on_connect(client, userdata, flags, rc, *args):  # noqa: ANN001
    """连接结果回调。

    ``client.connect()`` 是异步的：它只发起连接，失败（如 broker 拒绝匿名连接
    rc=5）**不会抛异常**，而后续的 ``publish()`` 仍会返回成功，导致消息被静默
    丢弃、订阅方永远收不到。这里显式打印结果，让连接问题可见。
    """
    if rc == 0:
        print("[MQTT] 已连接 broker")
    else:
        print(f"[MQTT] 连接被拒绝: {_CONNACK_TEXT.get(rc, f'rc={rc}')}")


def _default_client_factory(cfg: Any):
    """按配置建一个已连接的 paho 客户端；失败返回 None。"""
    if not MQTT_AVAILABLE:
        return None
    client_id = f"memory-agent-{int(time.time())}"
    try:
        # paho v2 要求显式 CallbackAPIVersion；v1 没有该参数，两种都兼容
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        except AttributeError:
            client = mqtt.Client(client_id=client_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[MQTT] 创建客户端失败: {exc}")
        return None

    try:  # 连接建立后若 broker 抖动，由 paho 自动退避重连（v0.6 增强）
        client.reconnect_delay_set(5, 60)
    except Exception:  # noqa: BLE001
        pass

    user = getattr(cfg, "tv_mqtt_user", "") or ""
    if user:
        client.username_pw_set(user, getattr(cfg, "tv_mqtt_pass", "") or "")
    try:
        client.on_connect = _on_connect
        client.connect(
            getattr(cfg, "tv_mqtt_host", "") or "",
            int(getattr(cfg, "tv_mqtt_port", 1883) or 1883),
            keepalive=60,
        )
        client.loop_start()
    except Exception as exc:  # noqa: BLE001
        print(f"[MQTT] 连接 broker 失败: {exc}")
        return None
    return client


class MqttBridge:
    """把 MA 的内部事件发布到 MQTT。"""

    def __init__(self, config: Any = None, client_factory=None):
        self.config = config
        self._factory = client_factory or _default_client_factory
        self._client = None
        # 退避重连时间戳：> 0 表示冷却中，期间不再尝试建连（避免每轮刷日志）。
        # 冷却结束后再次尝试，达成「broker 恢复后自动重连」，无需重启 MA（v0.6 修复）。
        self._retry_after = 0.0
        self._closed = False  # close() 后置位：关停后残留任务不得再推送

    # ── 状态 ──────────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        cfg = self.config
        if cfg is None:
            return False
        return bool(getattr(cfg, "ma_mqtt_enabled", False)) and bool(
            getattr(cfg, "tv_mqtt_host", "") or ""
        )

    @property
    def available(self) -> bool:
        """依赖与环境是否具备推送能力（缺 paho 时即使启用也推不了）。"""
        return self.enabled and MQTT_AVAILABLE

    # ── 状态（供 /api/health 暴露） ───────────────────────────────────────

    def status(self) -> dict:
        """连接 / 退避 / 关停状态快照。"""
        retry_after = max(0.0, self._retry_after - time.time())
        return {
            "enabled": self.enabled,
            "available": self.available,
            # v0.7 修复：原先用「客户端对象是否存在」判断，但 paho 在被 broker
            # 拒绝（如未授权 rc=5）后仍保留对象并持续重连，导致健康出口长期
            # 显示 connected=true 而消息其实一条都发不出去，严重误导排查。
            "connected": bool(self._client is not None and self._client.is_connected()),
            "retry_after": round(retry_after, 1),
            "closed": self._closed,
        }

    # ── 发布 ──────────────────────────────────────────────────────────────

    def publish(self, topic_suffix: str, payload: Any, retain: bool = False) -> bool:
        """发布一条 JSON 消息。成功 True；未启用 / 失败一律 False（不抛错）。"""
        if not self.enabled or self._closed:
            return False
        topic = f"{self._prefix()}/{topic_suffix.lstrip('/')}"
        try:
            client = self._ensure_client()
            if client is None:
                return False
            if not client.is_connected():
                # connect() 异步发起，broker 拒绝（如未授权 rc=5）时既不会抛错，
                # publish() 也会「成功」。此处显式判失败，让上层保留快照待重试，
                # 避免误以为推送成功。paho 的 loop 线程会持续自动重连。
                print(f"[MQTT] 客户端尚未连接，跳过发布 {topic}")
                return False
            body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            client.publish(topic, body, qos=0, retain=retain)
            return True
        except Exception as exc:  # noqa: BLE001 - 旁路能力，失败不得上抛
            print(f"[MQTT] 发布 {topic} 失败: {exc}")
            return False

    def publish_raw(self, topic: str, payload: Any, retain: bool = False) -> bool:
        """按完整 topic 发布（不加前缀）。用于跨服务约定主题（如 butler/trigger/*）。"""
        if not self.enabled or self._closed:
            return False
        topic = (topic or "").strip()
        if not topic:
            return False
        try:
            client = self._ensure_client()
            if client is None:
                return False
            body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            client.publish(topic, body, qos=0, retain=retain)
            return True
        except Exception as exc:  # noqa: BLE001 - 旁路能力，失败不得上抛
            print(f"[MQTT] 发布 {topic} 失败: {exc}")
            return False

    def publish_presence(self, members: list[dict], ts: str) -> bool:
        """成员在场快照。``retain=True`` 让新订阅者立刻拿到当前状态。"""
        return self.publish(
            "presence", {"members": members, "total": len(members), "ts": ts}, retain=True
        )

    def publish_health_change(self, entity_id: str, from_state: str, to_state: str,
                              stable_id: str = "") -> bool:
        """设备健康状态变化（A3 告警出口）。"""
        return self.publish(
            "device-health",
            {
                "entity_id": entity_id,
                "stable_id": stable_id,
                "from": from_state,
                "to": to_state,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
        )

    # ── 内部 ──────────────────────────────────────────────────────────────

    def _prefix(self) -> str:
        return (getattr(self.config, "ma_mqtt_topic_prefix", "") or "ma").strip("/")

    def _reconnect_interval(self) -> float:
        """退避窗口（秒），可由配置 ma_mqtt_reconnect_interval 覆盖，缺省 30。"""
        return float(getattr(self.config, "ma_mqtt_reconnect_interval", 30) or 30)

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if self._closed:
            return None
        if time.time() < self._retry_after:
            return None  # 退避冷却中，不重复尝试建连（避免每轮刷日志）
        client = self._factory(self.config)
        if client is None:
            wait = self._reconnect_interval()
            self._retry_after = time.time() + wait
            print(f"[MQTT] 连接失败，将在 {int(wait)}s 后重试（不影响主流程）")
            return None
        self._client = client
        self._retry_after = 0.0
        return client

    def close(self) -> None:
        """关闭连接；之后 publish() 一律空转（关停后残留任务不得再推送）。"""
        self._closed = True
        self._retry_after = 0.0
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
