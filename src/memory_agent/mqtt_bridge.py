"""MQTT 实时推送桥（v0.4）

为什么需要它
------------
TVPilot / DeskPilot 希望在状态变化时**被推送**，而不是轮询 MA。本模块把 MA 内部
的关键事件发布到 MQTT broker，供 TV / PC 订阅后渲染实时卡片：

* ``ma/presence``      成员在场快照（谁在哪个房间、通过什么方式识别）
* ``ma/device-health`` 设备健康状态变化（在线 ↔ 失联 ↔ 失效）
* ``adm/memory-agent/status|caps`` 本仓在线与能力摘要（retained，ADM 契约 §三）
* ``butler/inbox/notify`` 请 DB 说话（ADM 公共收件箱，白名单内、不 retained）

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
import uuid
from typing import Any

from . import house_time

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


def _new_trace_id() -> str:
    """契约表 §二：MA 域名下每条载荷都带 trace_id，DB/AF 拿它串起"发了什么、为什么没动"。"""
    return uuid.uuid4().hex


# ── ADM presence / 收件箱（契约表 §三、§五） ────────────────────────────────
#
# 主题与上限照 homesdk.presence 的口径写死在本模块一份：库在场时直接调库（真源在库，
# 白名单与 fail-closed 由库执行），库缺席时 MA 用**同一形状**直发。两条路必须产出
# 逐字节同构的载荷——否则"装了库"会改变 DB 侧解析结果，而交付形态目前还未裁定。

ADM_MEMBER = "memory-agent"                       # 契约表 ADM_MEMBERS 里 MA 的域名
ADM_STATUS_TOPIC = f"adm/{ADM_MEMBER}/status"
ADM_CAPS_TOPIC = f"adm/{ADM_MEMBER}/caps"
ADM_ONLINE = "online"
ADM_OFFLINE = "offline"

#: 公共收件箱白名单（DB 拥有）。投递侧只写这三条，不碰 DB 内部语义主题。
INBOX_TOPICS = frozenset({"butler/inbox/speak", "butler/inbox/notify", "butler/inbox/tv"})
INBOX_NOTIFY_TOPIC = "butler/inbox/notify"
INBOX_MAX_TITLE = 80
INBOX_MAX_BODY = 500

#: homesdk.presence 探测结果缓存：None=未探测，False=不可用，Module=可用
_presence_probe: Any = None


def homesdk_presence() -> Any:
    """返回 ``homesdk.presence`` 模块，缺席则 ``False``（结果缓存，探测只发生一次）。"""
    global _presence_probe
    if _presence_probe is None:
        try:
            from homesdk import presence as _presence  # noqa: PLC0415
        except Exception:  # noqa: BLE001 - 可选依赖，缺席只是走 MA 同构直发
            _presence_probe = False
        else:
            _presence_probe = _presence if hasattr(_presence, "notify") else False
    return _presence_probe


def reset_presence_probe() -> None:
    """清掉 presence 探测缓存（热更新后库可能才到位，与 house_time 同一口径）。"""
    global _presence_probe
    _presence_probe = None


def _bounded(text: Any, limit: int, field: str) -> str:
    """按契约上限收口：库的口径是"超长即拒绝投递"，MA 先截断再发，
    好让告警不会因为一句话超长就整条消失（截断会打日志，不静默）。"""
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    print(f"[MQTT] {field} 超长（{len(value)}>{limit}），截断后投递")
    return value[: limit - 1] + "…"


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
        # LWT 必须在 connect() **之前**设：will 是 CONNECT 报文里的字段，
        # 连上之后再 will_set，broker 永远不会知道（kill -9 后也就没人代发 offline）。
        client.will_set(ADM_STATUS_TOPIC, ADM_OFFLINE, qos=1, retain=True)
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
        self._advertised = False  # presence 是否已广播（断连重连后重置，见 ensure_advertised）

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
            "advertised": self._advertised,
            "homesdk_presence": bool(homesdk_presence()),
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
            client.publish(topic, body, qos=1, retain=retain)  # WO-ADM-001 R-49: 统一 QoS=1，与 butler subscribe 对齐
            return True
        except Exception as exc:  # noqa: BLE001 - 旁路能力，失败不得上抛
            print(f"[MQTT] 发布 {topic} 失败: {exc}")
            return False

    def publish_raw(self, topic: str, payload: Any, retain: bool = False) -> bool:
        """按完整 topic 发布（不加前缀）。仅用于 MA 自有域名下的跨服务约定主题。"""
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
            client.publish(topic, body, qos=1, retain=retain)  # WO-ADM-001 R-49: 统一 QoS=1，与 butler subscribe 对齐
            return True
        except Exception as exc:  # noqa: BLE001 - 旁路能力，失败不得上抛
            print(f"[MQTT] 发布 {topic} 失败: {exc}")
            return False

    def publish_presence(self, members: list[dict], ts: str) -> bool:
        """成员在场快照。``retain=True`` 让新订阅者立刻拿到当前状态。"""
        return self.publish(
            "presence",
            {"trace_id": _new_trace_id(), "members": members, "total": len(members), "ts": ts},
            retain=True,
        )

    def publish_health_change(self, entity_id: str, from_state: str, to_state: str,
                              stable_id: str = "") -> bool:
        """设备健康状态变化（A3 告警出口）。

        DCD 裁定 20261002 Q2：契约形状是 ``{device_id, status}``，MA 原有的是
        ``{entity_id, to}``。裁定取"加别名、保留原键"——`from`/`to` 携带的迁移方向
        是 DB 判「失联 vs 恢复」的依据，只留 `status` 会把它丢掉。
        """
        return self.publish(
            "device-health",
            {
                "trace_id": _new_trace_id(),  # 契约表 §二 每条 ma/* 载荷都带它
                "entity_id": entity_id,
                # 契约字段名（同一值的两个别名，DB 按契约写就不落空）
                "device_id": entity_id,
                "status": to_state,
                "stable_id": stable_id,
                "from": from_state,
                "to": to_state,
                # 跨仓 payload 的时间戳必须是家庭墙钟：`time.strftime` 读的是机器时区，
                # UTC 容器里发出去的比 MA 库里的 `ts` 早 8 小时（契约 §四 同一口径）。
                "ts": house_time.now_local(self.config.tz_offset_hours).isoformat(),
            },
        )

    # ── ADM presence（契约表 §三：retained status + caps + LWT） ────────────

    def advertise(self, caps: dict | None = None) -> bool:
        """广播本仓在线与能力摘要（两条都 retained，QoS=1）。

        不记状态，重复调就重复发；运行时请用 ``ensure_advertised()``。
        """
        if not self.enabled or self._closed:
            return False
        client = self._ensure_client()
        if client is None:
            return False
        if not client.is_connected():
            # 与 publish() 同一个理由：未连上时的 publish 会"成功"但发不出去，
            # 让 presence 也照实返回 False，运行时下轮重试，别把没送达记成已广播。
            # 同时把标记清掉——否则断连期间标记还停在 True，重连后 ensure_advertised
            # 会认为"已经广播过"，而 broker 重启早把 retained 丢了，探测方永远读不到在线。
            print("[MQTT] 客户端尚未连接，跳过 presence 广播（下一轮重试）")
            self._advertised = False
            return False
        presence = homesdk_presence()
        if presence:
            try:
                presence.advertise(client, ADM_MEMBER, caps=caps)
                self._advertised = True
                return True
            except Exception as exc:  # noqa: BLE001 - 旁路能力，失败不上抛
                print(f"[MQTT] presence 广播失败（homesdk 口径）: {exc}")
                return False
        ok = self.publish_raw(ADM_STATUS_TOPIC, ADM_ONLINE, retain=True)
        if caps is not None:
            ok = self.publish_raw(ADM_CAPS_TOPIC, caps, retain=True) and ok
        self._advertised = ok
        return ok

    def ensure_advertised(self, caps: dict | None = None) -> bool:
        """连上后广播一次；断开重连（边沿）再发一次。

        重发的理由不是心跳：broker 重启会丢 retained 消息，而探测方是按"读 retained
        零往返"来判在线的，不重发就会长期把在线的 MA 读成离线。
        """
        if not self.enabled or self._closed:
            return False
        client = self._client
        if self._advertised and client is not None and client.is_connected():
            return True
        return self.advertise(caps)

    def publish_notify(self, title: str, body: str, *, trace_id: str,
                       channel: str = "", priority: int = 0) -> bool:
        """请 DB 推送通知（``butler/inbox/notify``，不 retained）。

        ``trace_id`` 缺失即拒发——契约把它定为跨仓排障锚点（fail-closed）。MA 是旁路
        能力，所以"拒绝"的表现是返回 False + 打日志，而不是把异常抛进采集主链路。
        """
        if not self.enabled or self._closed:
            return False
        tid = (trace_id or "").strip()
        if not tid:
            print("[MQTT] 收件箱投递被拒：缺 trace_id（契约要求必填）")
            return False
        client = self._ensure_client()
        if client is None:
            return False
        if not client.is_connected():
            print(f"[MQTT] 客户端尚未连接，跳过收件箱投递 {INBOX_NOTIFY_TOPIC}")
            return False
        safe_title = _bounded(title, INBOX_MAX_TITLE, "title")
        safe_body = _bounded(body, INBOX_MAX_BODY, "body")
        presence = homesdk_presence()
        if presence:
            try:
                presence.notify(client, safe_title, safe_body, trace_id=tid,
                                channel=channel, priority=priority)
                return True
            except Exception as exc:  # noqa: BLE001
                print(f"[MQTT] 收件箱投递失败（homesdk 口径）: {exc}")
                return False
        # 库缺席时的同构载荷：ts 用 epoch（库那份就是 int(time.time())），
        # channel/priority 为空或 0 时**不写进载荷**——和库的 include 规则一致，
        # 否则"装没装库"会改变 DB 侧看到的字段集。
        payload: dict[str, Any] = {"trace_id": tid,
                                   # 裁定 20261002 Q6 的**登记特例**：事件类载荷的 ts 用家庭墙钟
                                   # ISO（见 publish_health_change），但收件箱是 DB 侧消费、要按
                                   # epoch 排序，所以这一条保留 epoch int，不跟墙钟统一。
                                   "ts": int(time.time()),
                                   "title": safe_title, "body": safe_body}
        if channel:
            payload["channel"] = channel
        if priority:
            payload["priority"] = priority
        return self.publish_raw(INBOX_NOTIFY_TOPIC, payload)

    def _publish_adm_state(self, state: str) -> None:
        """尽力把 status 落到 retained（关停路径用，不判定成功）。"""
        client = self._client
        if client is None:
            return
        try:
            info = client.publish(ADM_STATUS_TOPIC, state, qos=1, retain=True)
            # 关停时 loop 线程随时会停，给这条 retained 一个有界的送达窗口
            waiter = getattr(info, "wait_for_publish", None)
            if callable(waiter):
                waiter(timeout=1.0)
        except Exception as exc:  # noqa: BLE001
            print(f"[MQTT] 下线状态未送达（不阻塞关停）: {exc}")

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
        self._advertised = False  # 换了新客户端就得重新广播（LWT 也是随 CONNECT 才生效）
        return client

    def close(self) -> None:
        """关闭连接；之后 publish() 一律空转（关停后残留任务不得再推送）。"""
        self._closed = True
        self._retry_after = 0.0
        client = self._client
        # 优雅下线：主动把 retained 置成 offline，而不是等 broker 的 LWT——
        # LWT 只在异常断连时代发，正常 disconnect 不会触发，否则探测方会一直读到 online。
        if client is not None and self._advertised:
            self._publish_adm_state(ADM_OFFLINE)
        self._client = None
        self._advertised = False
        if client is None:
            return
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
