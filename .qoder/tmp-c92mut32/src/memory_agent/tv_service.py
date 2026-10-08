"""电视截屏多模态识别（按需调用）

对应 docs/电视截屏多模态识别功能_交接单.md。职责：

1. 从 HA 的 ``media_player`` 实体（xiaomi_miot）读当前状态与应用信息；
2. 用实体 ``attributes.capture`` 里**当次取到的** URL 直连电视抓一张截图；
3. 按需读 TV Cam 项目广播的 MQTT 状态（retained）作为识别上下文；
4. 调已有多模态 VLM（与视觉巡检同一个网关）识别画面内容。

设计约束
--------
* **按需调用，不做定时轮询**：本模块没有常驻任务，所有方法都是同步阻塞实现，
  由路由层用 ``asyncio.to_thread`` 包一层，不占事件循环。
* **截图 URL 绝不缓存**：``capture`` 带 ``timestamp`` + ``opaque`` 签名且会过期，
  缓存下来的 URL 第二次必然 401/403。每次都重新读实体状态拿新 URL。
* **电视息屏要可辨识地失败**：拿不到截图时不抛裸异常，统一抛 ``TVError``
  并带 ``code``（``tv_off`` / ``no_capture`` / ``bad_image`` …），
  路由层据此返回 503 与可执行的排查提示。
"""

from __future__ import annotations

import json
import os
import re
import socket
import struct
import time
from typing import Any

import httpx

from .store import now_local


class TVError(Exception):
    """电视截屏链路的可预期失败。``code`` 供调用方分支，``message`` 给用户看。"""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


# HA 的这些状态说明「电视没在输出画面」，截图必然取不到
_OFF_STATES = frozenset({"off", "standby", "unavailable", "unknown"})

# VLM 输出的 content_type 白名单：模型偶尔会自造词，收敛后再给调用方
_CONTENT_TYPES = (
    "movie", "series", "variety", "anime", "documentary", "sports", "news",
    "music", "kids", "short_video", "live", "game", "desktop", "settings",
    "app_list", "other",
)


def _split_app(app_current: str) -> tuple[str, str]:
    """把 ``桌面 - com.mitv.tvhome`` 拆成 (应用名, 包名)。"""
    text = (app_current or "").strip()
    if not text:
        return "", ""
    if " - " in text:
        name, package = text.rsplit(" - ", 1)
        return name.strip(), package.strip()
    # 只有包名或只有名字的情况
    if re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", text):
        return "", text
    return text, ""


def _extract_capture_ts(url: str) -> int | None:
    """从 capture URL 里挖 ``timestamp``（毫秒），用于给截图打时间戳。"""
    m = re.search(r"timestamp=(\d+)", url or "")
    return int(m.group(1)) if m else None


class TVService:
    """电视屏幕截图 + 多模态识别。同步实现，调用方负责 to_thread。"""

    def __init__(self, config, ha, vision=None) -> None:
        self.config = config
        self.ha = ha
        # 复用视觉服务的 VLM 通道（同一个 doubao2api 网关与凭据），
        # 不另起一套多模态配置。
        self.vision = vision

    def reconfigure(self, config, ha, vision=None) -> None:
        self.config = config
        self.ha = ha
        if vision is not None:
            self.vision = vision

    # ── 状态（HA 实体）─────────────────────────────────────────────────

    def state(self, include_mqtt: bool = False) -> dict:
        """读 HA media_player 实体，返回规范化状态 + 应用信息 + 当次截图 URL。

        每次调用都会重新请求 HA，**不复用**上次的 capture URL。
        """
        entity = (self.config.tv_media_player_entity or "").strip()
        if not entity:
            raise TVError("no_entity", "未配置电视实体 tv_media_player_entity")

        raw = self.ha.get_state(entity)
        if not isinstance(raw, dict):
            raise TVError(
                "ha_unreachable",
                f"HA 未返回实体 {entity}",
                hint="检查 hass_token 是否有效、实体 ID 是否正确（HA 开发者工具→状态里确认）",
            )
        attrs = raw.get("attributes") or {}
        entity_state = str(raw.get("state") or "").strip()
        app_current = str(attrs.get("app_current") or "").strip()
        app_name, app_package = _split_app(app_current)
        capture_url = str(attrs.get("capture") or "").strip()

        info: dict[str, Any] = {
            "entity_id": entity,
            "friendly_name": str(attrs.get("friendly_name") or entity),
            "entity_state": entity_state,          # playing / paused / idle / off / standby
            "app_current": app_current,            # 原始值，如 "桌面 - com.mitv.tvhome"
            "app_name": app_name,
            "app_package": app_package,
            "app_page": str(attrs.get("app_page") or "").strip(),
            "has_capture": bool(capture_url),
            "capture_ts": _extract_capture_ts(capture_url),
            "screen_on": entity_state.lower() not in _OFF_STATES,
            "media_playing": entity_state.lower() == "playing",
            # URL 带签名且会过期，仅本次调用有效；不写入任何缓存
            "_capture_url": capture_url,
        }

        if include_mqtt:
            mqtt = self.mqtt_state()
            info["tv_state"] = mqtt
            if mqtt and isinstance(mqtt.get("screen_on"), bool):
                # TV 端广播比 HA 实体状态更准确（HA 状态有约 30s 更新延迟）
                info["screen_on"] = mqtt["screen_on"]
                info["media_playing"] = bool(mqtt.get("media_playing"))
                if not info["app_name"] and mqtt.get("foreground_app_name"):
                    info["app_name"] = str(mqtt["foreground_app_name"])
                if not info["app_package"] and mqtt.get("foreground_app"):
                    info["app_package"] = str(mqtt["foreground_app"])
        return info

    # ── 截图 ───────────────────────────────────────────────────────────

    def _refresh_capture_entity(self) -> None:
        """强制 HA 刷新电视实体，重新生成带新鲜签名的 capture URL。

        HA 会**缓存** media_player 实体的 ``capture`` 属性（签名里的 timestamp
        是上一次轮询的时间），复用过期签名直接 GET 电视 6095 端口会得到 404。
        主动调用 ``homeassistant/update_entity`` 让 HA 立刻重新读取设备并写出
        新的签名 URL，是稳定拿到可截图的唯一可靠手段。刷新失败不致命，仍可
        退而求其次尝试缓存 URL。
        """
        entity = (self.config.tv_media_player_entity or "").strip()
        if not entity:
            return
        try:
            self.ha.call_service("homeassistant", "update_entity", entity)
        except Exception:  # noqa: BLE001
            pass
        # HA 重新读取设备需要一点时间才能写出新的 capture 属性
        time.sleep(float(self.config.tv_capture_refresh_wait_s or 2.0))

    def capture(self, state: dict | None = None, max_retry: int = 2) -> tuple[bytes, dict]:
        """抓一张当前电视截图，返回 ``(jpeg 字节, 状态 dict)``。

        直接访问电视的 capture URL（不经过 HA 代理，少一跳）；该 URL 是当次
        ``state()`` 拿到的新鲜签名 URL。

        截图 URL 由 HA 缓存且带短时签名，复用过期签名会 404。这里**每次尝试都
        先强制 HA 刷新实体**以拿到全新签名 URL，并在瞬时错误（签名过期 / 404）
        上自动重试，避免把「重试一次」的负担甩给调用方。``tv_off`` / ``bad_image``
        / ``ha_unreachable`` 等非瞬时错误不重试，直接抛出。
        """
        info = state if state is not None else None
        last_exc: Exception | None = None
        for attempt in range(max_retry + 1):
            try:
                # 每次都强制刷新实体拿新鲜签名 URL（HA 缓存 capture 属性会过期）
                self._refresh_capture_entity()
                info = self.state()
                if not info.get("screen_on"):
                    raise TVError(
                        "tv_off",
                        f"电视当前未亮屏（HA 状态 {info.get('entity_state') or '未知'}）",
                        hint="息屏时电视不会产出截图，先开机再试",
                    )
                url = info.get("_capture_url") or ""
                if not url:
                    raise TVError(
                        "no_capture",
                        "HA 实体未提供 capture 截图地址",
                        hint="确认 xiaomi_miot 集成已开启屏幕截图，且电视处于开机状态",
                    )
                timeout = float(self.config.tv_capture_timeout_s or 15)
                try:
                    resp = httpx.get(url, timeout=timeout)
                except Exception as exc:  # noqa: BLE001
                    raise TVError(
                        "capture_failed",
                        f"拉取电视截图失败: {exc}",
                        hint="电视需与 memory-agent 同网段且 6095 端口可达；截图 URL 有时效，本服务每次都重新获取",
                    ) from exc
                if resp.status_code != 200:
                    raise TVError(
                        "capture_failed",
                        f"电视返回 HTTP {resp.status_code}",
                        hint="多为截图 URL 签名过期或电视已息屏，重试一次即可",
                    )
                frame = resp.content
                if len(frame) < 1024 or not frame.startswith(b"\xff\xd8\xff"):
                    raise TVError(
                        "bad_image",
                        f"截图内容异常（{len(frame)} 字节，非 JPEG）",
                        hint="电视可能返回了错误页；确认实体 capture 属性指向的是截图资源",
                    )
                return frame, info
            except TVError as exc:
                last_exc = exc
                # 非瞬时错误直接上抛，不重试
                if exc.code in ("tv_off", "no_capture", "no_entity", "ha_unreachable", "bad_image"):
                    raise
                # 瞬时错误（签名过期 404 等）：下一轮重新刷新实体拿新 URL
                info = None
                if attempt < max_retry:
                    continue
                raise
        if last_exc:
            raise last_exc
        raise TVError("capture_failed", "截图重试后仍失败")  # 兜底，理论不可达

    # ── 快照落盘 ───────────────────────────────────────────────────────

    def save_snapshot(self, frame: bytes) -> str:
        """把截图存到 ``{data_dir}/snapshots/{day}/`` ，返回容器内路径。"""
        now = now_local(self.config.tz_offset_hours)
        rel_dir = os.path.join("snapshots", now.strftime("%Y-%m-%d"))
        abs_dir = os.path.join(self.config.data_dir, rel_dir)
        os.makedirs(abs_dir, exist_ok=True)
        filename = f"tv_{now.strftime('%Y%m%d_%H%M%S')}.jpg"
        abs_path = os.path.join(abs_dir, filename)
        with open(abs_path, "wb") as f:
            f.write(frame)
        return f"/data/{rel_dir}/{filename}"

    # ── MQTT（TV Cam 状态广播，可选）───────────────────────────────────

    def mqtt_state(self) -> dict | None:
        """按需连一次 MQTT，读 ``tv/livingroom/state`` 的 retained 消息。

        TV Cam 每 5s 广播一次且设为 retained，所以「连上 → 订阅 → 收到一条 →
        断开」就能拿到最新状态，无需常驻订阅任务。未启用 / 连不上 / 超时
        一律返回 ``None``，识别链路照常继续（只是少了这段上下文）。
        """
        if not self.config.tv_mqtt_enabled:
            return None
        return read_retained_mqtt(
            host=self.config.tv_mqtt_host,
            port=int(self.config.tv_mqtt_port or 1883),
            topic=self.config.tv_mqtt_topic,
            user=self.config.tv_mqtt_user,
            password=self.config.tv_mqtt_pass,
            timeout=float(self.config.tv_mqtt_timeout_s or 3),
        )

    # ── 多模态识别 ─────────────────────────────────────────────────────

    def build_prompt(self, info: dict, tv_state: dict | None,
                     detail_level: str = "brief") -> str:
        """构造电视画面识别提示词。``detail_level``: brief / normal / detailed。"""
        ctx_lines = [
            f"当前应用：{info.get('app_current') or '未知'}",
            f"HA 播放状态：{info.get('entity_state') or '未知'}",
        ]
        if tv_state:
            ctx_lines.append(
                "TV 端状态："
                f"前台应用 {tv_state.get('foreground_app_name') or tv_state.get('foreground_app') or '未知'}"
                f"，媒体标题 {tv_state.get('media_title') or '无'}"
                f"，播放中 {bool(tv_state.get('media_playing'))}"
                f"，暂停 {bool(tv_state.get('media_paused'))}"
            )
        extra_fields = ""
        if detail_level == "detailed":
            extra_fields = (
                ',"episode":"第几集/第几分钟，看不出填空字符串",'
                '"on_screen_text":"画面上最醒目的一行文字，没有填空字符串",'
                '"genre":"题材，如 喜剧/悬疑/动作"'
            )
        return (
            "这是家里客厅电视的屏幕截图。"
            + "\n".join(ctx_lines)
            + "\n\n请判断画面内容并只输出如下 JSON（不要输出多余解释）：\n"
            '{"content_type":"' + "|".join(_CONTENT_TYPES) + '",'
            '"title":"片名/节目名，识别不出就填空字符串",'
            '"playing":true或false,'
            '"paused":true或false,'
            '"description":"一句话描述这个场景"'
            + extra_fields
            + "}\n"
            "content_type 取值说明：movie=电影，series=电视剧，variety=综艺，"
            "anime=动画，desktop=桌面/主页，settings=设置界面，app_list=应用列表，"
            "无法判断用 other。标题只在画面上确实能看到片名时才填，不要猜。"
        )

    def analyze(
        self, *, include_tv_state: bool = True, detail_level: str = "brief",
        save_snapshot: bool = True,
    ) -> dict:
        """截屏 → 多模态识别 → 返回结构化结果。全程同步，异常统一为 TVError。"""
        if self.vision is None:
            raise TVError("no_vision", "多模态服务未就绪（VLM 未初始化）")
        level = detail_level if detail_level in ("brief", "normal", "detailed") else "brief"

        info = self.state()
        frame, info = self.capture(info)
        tv_state = self.mqtt_state() if include_tv_state else None
        prompt = self.build_prompt(info, tv_state, level)

        t0 = time.monotonic()
        text, vlm_ms = self.vision.vlm_analyze(frame, prompt)
        total_ms = int((time.monotonic() - t0) * 1000)

        parsed = _parse_vlm_json(text)
        content_type = str(parsed.get("content_type") or "").strip().lower()
        if content_type not in _CONTENT_TYPES:
            content_type = "other" if content_type else "unknown"

        snapshot_path = self.save_snapshot(frame) if save_snapshot else ""
        ts = info.get("capture_ts") or int(time.time() * 1000)
        return {
            "ok": True,
            "content_type": content_type,
            "title": str(parsed.get("title") or "").strip(),
            "playing": _as_bool(parsed.get("playing"), fallback=bool(info.get("media_playing"))),
            "paused": _as_bool(parsed.get("paused"), fallback=False),
            "description": str(parsed.get("description") or "").strip() or text[:500],
            "raw_response": text[:4000],
            "app_current": info.get("app_current") or "",
            "app_name": info.get("app_name") or "",
            "app_package": info.get("app_package") or "",
            "entity_state": info.get("entity_state") or "",
            "screen_on": bool(info.get("screen_on")),
            "tv_state": tv_state,
            "screenshot_url": f"/api/tv/screenshot?ts={ts}",
            "snapshot_path": snapshot_path,
            "detail_level": level,
            "timestamp": ts,
            "vlm_latency_ms": vlm_ms,
            "total_latency_ms": total_ms,
        }


# ── 辅助 ───────────────────────────────────────────────────────────────────

def _parse_vlm_json(text: str) -> dict:
    """从 VLM 输出里容错抠出 JSON（剥 markdown 围栏 / 前后杂文）。"""
    if not text:
        return {}
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _as_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "是", "播放中")
    if isinstance(value, (int, float)):
        return bool(value)
    return fallback


# ── 极简 MQTT 3.1.1 客户端（只读 retained 消息）──────────────────────────
#
# 只为「按需读一条 retained 状态」服务：CONNECT → SUBSCRIBE → 等第一条
# PUBLISH → 断开。用标准库实现，避免为一个只读特性引入 paho-mqtt 依赖
# （那会触发整镜像重建）。失败一律返回 None，不影响主链路。

def _mqtt_str(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack("!H", len(raw)) + raw


def _mqtt_packet(cmd: int, body: bytes) -> bytes:
    out = bytearray([cmd])
    rem = len(body)
    while True:
        byte = rem % 128
        rem //= 128
        if rem:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out) + body


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("连接被对端关闭")
        buf += chunk
    return buf


def _read_packet(sock: socket.socket) -> tuple[int, bytes]:
    cmd = _read_exact(sock, 1)[0]
    multiplier, value = 1, 0
    while True:
        byte = _read_exact(sock, 1)[0]
        value += (byte & 0x7F) * multiplier
        if not (byte & 0x80):
            break
        multiplier *= 128
    return cmd, (_read_exact(sock, value) if value else b"")


def read_retained_mqtt(
    host: str, port: int = 1883, topic: str = "tv/livingroom/state",
    user: str = "", password: str = "", timeout: float = 3.0,
) -> dict | None:
    """连一次 MQTT，订阅 topic 并取回第一条（retained）消息，然后断开。

    返回解析后的 dict；任何环节失败都返回 ``None`` 并打印一行日志。
    """
    if not host or not topic:
        return None
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            flags = 0x02  # clean session
            if user:
                flags |= 0x80
                if password:
                    flags |= 0x40
            body = (
                _mqtt_str("MQTT")
                + bytes([0x04, flags])
                + struct.pack("!H", 60)
                + _mqtt_str(f"memory-agent-{os.getpid()}")
            )
            if user:
                body += _mqtt_str(user)
                if password:
                    body += _mqtt_str(password)
            sock.sendall(_mqtt_packet(0x10, body))

            payload = _mqtt_str(topic) + bytes([0x00])  # QoS 0
            sock.sendall(_mqtt_packet(0x82, struct.pack("!H", 1) + payload))

            deadline = time.monotonic() + max(0.5, timeout)
            while time.monotonic() < deadline:
                cmd, body = _read_packet(sock)
                if cmd >> 4 != 3:  # 不是 PUBLISH（CONNACK/SUBACK）就继续等
                    continue
                if len(body) < 2:
                    continue
                tlen = struct.unpack("!H", body[:2])[0]
                msg_topic = body[2 : 2 + tlen].decode("utf-8", errors="replace")
                rest = body[2 + tlen :]
                qos = (cmd >> 1) & 0x03
                if qos:  # QoS1/2 有 2 字节 packet id
                    rest = rest[2:]
                try:
                    data = json.loads(rest.decode("utf-8", errors="replace"))
                except Exception:
                    data = None
                if msg_topic == topic and isinstance(data, dict):
                    return data
            return None
    except Exception as exc:  # noqa: BLE001
        print(f"[TV] 读取 MQTT 电视状态失败（已忽略，不影响识别）: {exc}")
        return None
