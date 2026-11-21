"""MA 事件族发布（契约 §1.2）：`ma/behavior-state` / `ma/prediction`。

真源＝`homesdk.events`（0.3.5）：主题常量与 `payload_version` 只从这里取，
⛔ 本仓复制一份同值常量（DCD 20261120 Q3 甲：直接 vendor 0.3.5，⛔ 本地兜底双写）。

库缺席时（运行面仍是 0.3.1／未重烤镜像）**整族显式降级**：不抛、不发、给理由。
降级不是缺陷，是窗口边界——重烤后同一段代码自动切到真源。

发布口径（DCD 20261117 §三 ＋ 20261120 Q3）：
- `ma/behavior-state`：行为状态机 `state` **变更边沿**才发（⛔ 周期心跳）；
  时长类边沿带可选 `duration`（前一状态持续秒数）。
- `ma/prediction`：`next_arrival` **跨阈值**才发（漂移 ≥15min）＋ 15–30min 轮询。
- 两条都 ⛔ retain（事件类默认永不 retained）。

载荷形状以 AF 消费面 `af_linkage_feed` 的白名单为准：
behavior-state 业务键 `as_of/state/confidence/since/duration/member/member_id/room/trigger`；
prediction 业务键 `as_of/next_arrival/upcoming_device_hints/confidence/room`。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from . import house_time

#: 契约 §三：`source` 固定 `"ma"`（AF 门禁用例逐字用它）。
SOURCE = "ma"
#: 与 AF 消费面 `af_linkage_feed.KIND_*` 对齐（`type` 字段就是它）。
TYPE_BEHAVIOR_STATE = "behavior_state"
TYPE_PREDICTION = "prediction"
#: `ma/prediction` 跨阈值口径（DCD 20261120 Q3：`next_arrival` 漂移 ≥15min 才发）。
PREDICTION_DRIFT_MIN = 15.0

__all__ = [
    "SOURCE", "TYPE_BEHAVIOR_STATE", "TYPE_PREDICTION", "PREDICTION_DRIFT_MIN",
    "homesdk_events", "reset_events_probe", "events_available", "unavailable_reason",
    "status", "build_envelope", "publish_behavior_state", "publish_prediction",
    "latest_by_scope", "seed_behavior_states", "diff_behavior_states",
    "prediction_drift_minutes", "should_publish_prediction", "next_arrival_iso",
]

#: `homesdk.events` 探测结果缓存：None=未探测，False=不可用，Module=可用。
_events_probe: Any = None


def homesdk_events() -> Any:
    """返回 `homesdk.events`，缺席则 ``False``（探测只发生一次，与 house_time 同口径）。"""
    global _events_probe
    if _events_probe is None:
        try:
            from homesdk import events as _events  # noqa: PLC0415
        except Exception:  # noqa: BLE001 - 可选依赖，缺席只是整族降级
            _events_probe = False
        else:
            _events_probe = _events if getattr(_events, "EVENT_TOPICS", None) else False
    return _events_probe


def reset_events_probe() -> None:
    """清掉探测缓存（重烤/热更新后库可能才到位，与 `reset_presence_probe` 同口径）。"""
    global _events_probe
    _events_probe = None


def events_available() -> bool:
    return bool(homesdk_events())


def unavailable_reason() -> str:
    """事件族为什么没发（给运行时日志读，不是让人猜）。"""
    if homesdk_events():
        return ""
    try:
        import homesdk
        version = str(getattr(homesdk, "__version__", "?"))
    except Exception:  # noqa: BLE001 - 排障信息读不到不影响降级本身
        version = "absent"
    return f"homesdk.events 不在场（homesdk={version}；需 0.3.5 进运行面＝重烤镜像）"


def status() -> dict:
    """给健康面/排障用的事件族状态（可用性＋真源版本＋主题集）。"""
    ev = homesdk_events()
    if not ev:
        return {"available": False, "reason": unavailable_reason()}
    return {
        "available": True,
        "payload_version": str(getattr(ev, "PAYLOAD_VERSION", "")),
        "topics": sorted(getattr(ev, "EVENT_TOPICS", frozenset())),
    }


def _house_tz():
    return house_time.house_tz()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_house_tz())
    return dt


def _as_house_iso(value: Any) -> str:
    """把 naive/UTC/任意偏移的时刻统一成家庭时区**带偏移**的 ISO（契约 `ts` 口径）。"""
    dt = _parse_iso(value)
    if dt is None:
        return str(value or "")
    return dt.astimezone(_house_tz()).isoformat()


def build_envelope(event_type: str, *, ts: str | None = None) -> dict:
    """公共信封七键（真源 `homesdk.events`）。库缺席时抛 RuntimeError（调用方应先探测）。"""
    ev = homesdk_events()
    if not ev:
        raise RuntimeError(unavailable_reason())
    now_house = datetime.now(_house_tz()).replace(microsecond=0).isoformat()
    return {
        "type": str(event_type),
        "source": SOURCE,
        "payload_version": str(getattr(ev, "PAYLOAD_VERSION", "1.0")),
        "event_id": uuid.uuid4().hex,
        "trace_id": uuid.uuid4().hex,
        "ts": _as_house_iso(ts) if ts else now_house,
        "emitted_at": _utc_iso(),
    }


def publish_behavior_state(bridge, *, state, confidence=0.0, member="", member_id="",
                           room="", since="", duration_s=None, as_of=None,
                           trigger="", ts=None) -> bool:
    """发一条 `ma/behavior-state`（边沿）。库缺席返回 False，不抛。"""
    ev = homesdk_events()
    if not ev or bridge is None:
        return False
    payload = build_envelope(TYPE_BEHAVIOR_STATE, ts=ts)
    payload["as_of"] = as_of or _utc_iso()
    payload["state"] = str(state or "")
    payload["confidence"] = round(float(confidence or 0.0), 3)
    if member:
        payload["member"] = str(member)
    if member_id:
        payload["member_id"] = str(member_id)
    if room:
        payload["room"] = str(room)
    if since:
        payload["since"] = _as_house_iso(since)
    if duration_s is not None:
        payload["duration"] = round(float(duration_s), 1)
    if trigger:
        payload["trigger"] = str(trigger)
    return bool(bridge.publish_raw(
        getattr(ev, "TOPIC_BEHAVIOR_STATE"), payload, retain=False))


def publish_prediction(bridge, *, next_arrival, upcoming_device_hints=None,
                       confidence=None, room="", as_of=None, ts=None) -> bool:
    """发一条 `ma/prediction`（跨阈值）。库缺席返回 False，不抛。"""
    ev = homesdk_events()
    if not ev or bridge is None:
        return False
    payload = build_envelope(TYPE_PREDICTION, ts=ts)
    payload["as_of"] = as_of or _utc_iso()
    payload["next_arrival"] = str(next_arrival or "")
    payload["upcoming_device_hints"] = list(upcoming_device_hints or [])
    if confidence is not None:
        payload["confidence"] = round(float(confidence), 2)
    if room:
        payload["room"] = str(room)
    return bool(bridge.publish_raw(
        getattr(ev, "TOPIC_PREDICTION"), payload, retain=False))


# ── 边沿判定（纯函数，便于单测）─────────────────────────────────────────

def latest_by_scope(states) -> dict:
    """按房间取「最新一条状态」（输入按 ts DESC 排好）。"""
    out: dict = {}
    for st in states or []:
        room = str(st.get("room") or "")
        if room not in out:
            out[room] = st
    return out


def seed_behavior_states(states) -> dict:
    """首次启动的静默基线：只为后续边沿比对建 prev，**不发**（避免启动即全家桶）。"""
    out: dict = {}
    for room, st in latest_by_scope(states).items():
        out[room] = {
            "activity": str(st.get("activity") or ""),
            "member": str(st.get("member") or ""),
            "since": str(st.get("ts") or ""),
        }
    return out


def _seconds_between(a: Any, b: Any) -> float | None:
    da, db = _parse_iso(a), _parse_iso(b)
    if da is None or db is None:
        return None
    return max(0.0, (db - da).total_seconds())


def diff_behavior_states(prev: dict, states) -> tuple[list[dict], dict]:
    """比较上一轮 prev 与当前最新状态，返回 (边沿列表, 新 prev)。

    边沿＝某房间的 activity 变了（或首次出现）。`duration` 携带**前一状态**持续秒数
    （时长类边沿，如「电视看满 1h」）；无前一状态则不带该键。
    """
    edges: list[dict] = []
    new_prev = dict(prev)
    for room, st in latest_by_scope(states).items():
        activity = str(st.get("activity") or "")
        ts = str(st.get("ts") or "")
        old = prev.get(room)
        if old is not None and old.get("activity") == activity:
            new_prev[room] = old          # 状态没变：保留原 since，不刷新
            continue
        edge = dict(st)
        if old is not None and old.get("since"):
            dur = _seconds_between(old.get("since"), ts)
            if dur is not None:
                edge["duration"] = round(dur, 1)
        edges.append(edge)
        new_prev[room] = {"activity": activity, "member": str(st.get("member") or ""),
                          "since": ts}
    return edges, new_prev


def prediction_drift_minutes(prev_iso: Any, new_iso: Any) -> float | None:
    da, db = _parse_iso(prev_iso), _parse_iso(new_iso)
    if da is None or db is None:
        return None
    return abs((db - da).total_seconds()) / 60.0


def should_publish_prediction(prev_iso: Any, new_iso: Any,
                              threshold_min: float = PREDICTION_DRIFT_MIN) -> bool:
    """首次（无 prev）必发；之后只有漂移 ≥ 阈值才发（跨阈值，⛔ 周期心跳）。"""
    if prev_iso is None or prev_iso == "":
        return True
    drift = prediction_drift_minutes(prev_iso, new_iso)
    if drift is None:
        return True                   # 解析不了不掩盖：宁可多发一次，也不静默丢
    return drift >= float(threshold_min)


def next_arrival_iso(predicted_hour: Any, *, now: datetime | None = None) -> str:
    """预测小时（0–24 浮点）→ 家庭时区 ISO 时刻；已过今天该时刻则顺延到明天。"""
    try:
        hour = float(predicted_hour)
    except (TypeError, ValueError):
        return ""
    if hour < 0 or hour > 24:
        return ""
    tz = _house_tz()
    now_dt = now or datetime.now(tz)
    base = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    target = base + timedelta(hours=hour)
    if target <= now_dt:
        target += timedelta(days=1)
    return target.isoformat()
