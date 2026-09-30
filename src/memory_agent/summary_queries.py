"""路线图 3.5：MCP 查询工具面补全 —— 三个只读汇总工具的服务端实现。

与 mcp_server 的解耦方式：本模块只做纯计算，入参第一恒为 ``Store``，
不依赖 runtime / mcp，可独立单测（tests/test_summary_queries.py）。

口径说明
--------
* 时间一律为本地 ISO（``YYYY-MM-DDTHH:MM:SS``），与 events / behavior_states /
  behavior_events 的 ts 列同口径；纯日期（``YYYY-MM-DD``）自动展开为当日全天。
* 设备「开启」判定复用洞察层同一套状态归一化（``normalize_state``：on/open/playing/
  heat/cool/门开 等计为开启；unavailable/unknown/纯数值归为 other，不参与开关边沿）。
* events 表只有状态变化事件、没有原始时长字段 —— 开启时长由「开→关」状态变化
  序列积分得到；窗口开始前已开启的片段从窗口左沿起算，窗口结束仍未关闭的片段
  截断到窗口右沿（返回值带 ``window_open_session`` 标记后者）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .insights.parser.entity import normalize_state
from .store import TELEMETRY_DOMAINS, now_local


def _parse_ts(value):
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _expand_bound(value: str, is_end: bool) -> str:
    s = str(value or "").strip()
    if len(s) == 10 and s[4] == "-":
        return s + ("T23:59:59" if is_end else "T00:00:00")
    return s


def _resolve_window(store, start: str, end: str, days: int):
    """start/end 优先；都为空时取最近 ``days`` 天。返回 (start, end) 本地 ISO。"""
    start = _expand_bound(start, False)
    end = _expand_bound(end, True)
    if start or end:
        if not end:
            end = now_local(store.tz_offset_hours).isoformat(timespec="seconds")
        if not start:
            d = _parse_ts(end) or now_local(store.tz_offset_hours)
            start = (d - timedelta(days=max(1, int(days or 7)))).isoformat(
                timespec="seconds")
        return start, end
    now = now_local(store.tz_offset_hours)
    d = max(1, int(days or 7))
    return ((now - timedelta(days=d)).isoformat(timespec="seconds"),
            now.isoformat(timespec="seconds"))


def _round_minutes(seconds: float) -> float:
    return round(seconds / 60.0, 1)


# ── 1. 设备用量汇总 ──────────────────────────────────────────────────────────


def _entity_sessions(store, entity_id: str, win_start: str, win_end: str,
                     debounce_seconds: int):
    """单设备：把窗口内状态变化序列切成 on 片段，返回 (总秒数, 片段数, 事件数, 未闭合)。"""
    rows = store.query_events(entities=[entity_id], start=win_start, end=win_end,
                              limit=5000, order="asc")
    prior = store.query_events(entities=[entity_id], end=win_start, limit=1,
                              order="desc")
    cur_on = None
    total_seconds = 0.0
    sessions = 0
    open_at_end = False

    if prior and prior[0]["ts"] < win_start and normalize_state(prior[0]["new_state"]) == "on":
        cur_on = _parse_ts(win_start)
    if cur_on is None and rows and normalize_state(rows[0]["new_state"]) == "on":
        # 窗口内第一条就是开启边沿（或此前无任何记录）：从该条起算
        cur_on = _parse_ts(rows[0]["ts"]) or _parse_ts(win_start)

    for r in rows:
        ts = _parse_ts(r["ts"])
        if ts is None:
            continue
        kind = normalize_state(r["new_state"])
        if kind == "other":
            # unavailable/unknown/纯数值：不是开关边沿，跳过（避免把离线当"关"）
            continue
        if kind == "on":
            if cur_on is None:
                cur_on = ts
        elif cur_on is not None:
            seconds = (ts - cur_on).total_seconds()
            if seconds >= max(0, int(debounce_seconds or 0)):
                total_seconds += seconds
                sessions += 1
            cur_on = None

    if cur_on is not None:
        end_dt = _parse_ts(win_end) or datetime.now()
        seconds = max(0.0, (end_dt - cur_on).total_seconds())
        open_at_end = True
        if seconds >= max(0, int(debounce_seconds or 0)):
            total_seconds += seconds
            sessions += 1

    return total_seconds, sessions, len(rows), open_at_end


def device_usage_summary(store, entity_id: str, start: str = "", end: str = "",
                         days: int = 7, debounce_seconds: int = 5) -> dict:
    """设备用量汇总：总开启时长 / 开关次数（on 片段数）/ 平均单次时长。

    数据源是 events 表的状态变化序列（原始时长不存在，按时长积分口径计算）。
    窗口内没有任何事件时三个指标返回 None（``no_data=true``）。
    """
    entities = [e.strip() for e in str(entity_id or "").split(",") if e.strip()]
    if not entities:
        return {"ok": False, "error": "INVALID_PARAM: entity_id 不能为空（逗号分隔可传多个）"}
    win_start, win_end = _resolve_window(store, start, end, days)

    per_entity = []
    total_seconds = 0.0
    total_sessions = 0
    total_events = 0
    any_open = False
    for ent in entities:
        seconds, sessions, n_events, open_at_end = _entity_sessions(
            store, ent, win_start, win_end, debounce_seconds)
        per_entity.append({
            "entity_id": ent,
            "total_on_minutes": _round_minutes(seconds),
            "on_off_count": sessions,
            "average_session_minutes": (
                _round_minutes(seconds / sessions) if sessions else None),
            "events_in_window": n_events,
            "window_open_session": open_at_end,
        })
        total_seconds += seconds
        total_sessions += sessions
        total_events += n_events
        any_open = any_open or open_at_end

    has_window_data = any(p["events_in_window"] or p["on_off_count"] for p in per_entity)
    return {
        "ok": True,
        "entity_id": ",".join(entities),
        "window": {"start": win_start, "end": win_end,
                   "tz_offset_hours": store.tz_offset_hours},
        "total_on_minutes": _round_minutes(total_seconds) if has_window_data else None,
        "on_off_count": total_sessions if has_window_data else None,
        "average_session_minutes": (
            _round_minutes(total_seconds / total_sessions)
            if has_window_data and total_sessions else None),
        "events_in_window": total_events,
        "window_open_session": any_open,
        "per_entity": per_entity,
        "computed_from": "events.new_state 状态变化序列积分（开→关为一个片段；"
                         "窗口前已开启从左沿起算，窗口末未闭合截断到右沿）",
        "no_data": not has_window_data,
    }


# ── 2. 房间行为汇总 ──────────────────────────────────────────────────────────


def room_behavior_summary(store, room: str, start: str = "", end: str = "",
                          days: int = 7) -> dict:
    """房间行为汇总：活动标签分布（推断活动 + 视觉动作计数）+ 每时段（24 小时）活跃度。

    活动分布来自 behavior_states（canonical 推断活动）与 behavior_events（视觉动作，
    仅 status=ok）；每时段活跃度为剔除遥测域后的设备事件小时直方图。
    """
    room = str(room or "").strip()
    if not room:
        return {"ok": False, "error": "INVALID_PARAM: room 不能为空"}
    win_start, win_end = _resolve_window(store, start, end, days)
    conn = store.connect()
    with store._lock:
        state_rows = conn.execute(
            "SELECT activity, COUNT(*) c FROM behavior_states "
            "WHERE room = ? AND ts >= ? AND ts <= ? GROUP BY activity ORDER BY c DESC",
            (room, win_start, win_end)).fetchall()
        vision_rows = conn.execute(
            "SELECT action, COUNT(*) c FROM behavior_events "
            "WHERE room = ? AND server_ts >= ? AND server_ts <= ? "
            "AND status = 'ok' AND action IS NOT NULL AND action != '' "
            "GROUP BY action ORDER BY c DESC",
            (room, win_start, win_end)).fetchall()
        hour_rows = conn.execute(
            "SELECT CAST(strftime('%H', ts) AS INTEGER) h, COUNT(*) c FROM events "
            f"WHERE room = ? AND ts >= ? AND ts <= ? AND domain NOT IN "
            f"({','.join('?' * len(TELEMETRY_DOMAINS))}) GROUP BY h",
            (room, win_start, win_end, *TELEMETRY_DOMAINS)).fetchall()

    activity_distribution = (
        [{"activity": r["activity"], "count": int(r["c"]), "source": "inferred_state"}
         for r in state_rows]
        + [{"activity": r["action"], "count": int(r["c"]), "source": "vision_action"}
           for r in vision_rows]
    )
    hourly = [0] * 24
    for r in hour_rows:
        h = r["h"]
        if h is not None and 0 <= int(h) < 24:
            hourly[int(h)] = int(r["c"])

    return {
        "ok": True,
        "room": room,
        "window": {"start": win_start, "end": win_end,
                   "tz_offset_hours": store.tz_offset_hours},
        "activity_distribution": activity_distribution,
        "hourly_activity": {str(h): hourly[h] for h in range(24)},
        "state_count": sum(int(r["c"]) for r in state_rows),
        "vision_event_count": sum(int(r["c"]) for r in vision_rows),
        "device_event_count": sum(hourly),
        "no_data": not activity_distribution and not any(hourly),
        "computed_from": "behavior_states(推断活动) + behavior_events(视觉动作, status=ok)"
                         " + events(小时直方图, 剔除遥测域)",
    }


# ── 3. 成员当日行为序列 ──────────────────────────────────────────────────────


def member_daily_pattern(store, member_id: str, date: str = "") -> dict:
    """成员当日行为序列：推断活动状态 + 视觉动作 + 具名设备事件，按时间升序合并。

    成员隔离 fail-closed：``member_id`` 必填（普通调用面不存在「全成员日序列」），
    成员不存在返回 NOT_FOUND。数据里的人员归属按成员**姓名**匹配
    （behavior_states.member / behavior_events.persons 存的都是姓名口径）。
    """
    member_id = str(member_id or "").strip()
    if not member_id:
        return {"ok": False,
                "error": "INVALID_PARAM/DENIED: member_id 缺失，日行为序列仅按成员维度开放"}
    member = store.get_member(member_id)
    if not member:
        return {"ok": False, "error": f"NOT_FOUND: 成员 {member_id} 不存在"}
    name = str(member.get("name") or "").strip()
    date = str(date or "").strip() or now_local(store.tz_offset_hours).strftime("%Y-%m-%d")
    if len(date) != 10:
        return {"ok": False, "error": f"INVALID_PARAM: date 需为 YYYY-MM-DD，收到 {date!r}"}
    day_start, day_end = f"{date}T00:00:00", f"{date}T23:59:59"

    timeline = []
    conn = store.connect()
    with store._lock:
        state_rows = conn.execute(
            "SELECT ts, activity, room, confidence, source FROM behavior_states "
            "WHERE member = ? AND ts >= ? AND ts <= ? ORDER BY ts ASC",
            (name, day_start, day_end)).fetchall()
    for r in state_rows:
        timeline.append({
            "ts": r["ts"], "time": str(r["ts"])[11:16], "kind": "activity_state",
            "activity": r["activity"], "room": r["room"],
            "confidence": r["confidence"], "source": r["source"],
        })

    for ev in store.list_behavior_events(day_from=date, day_to=date, member=name,
                                         limit=500):
        timeline.append({
            "ts": ev.get("server_ts"), "time": str(ev.get("server_ts") or "")[11:16],
            "kind": "vision_action", "activity": ev.get("action") or "",
            "room": ev.get("room") or "", "confidence": ev.get("confidence"),
            "source": "vision",
        })

    for ev in store.query_events(person=name, start=day_start, end=day_end,
                                 limit=500, order="asc"):
        timeline.append({
            "ts": ev.get("ts"), "time": str(ev.get("ts") or "")[11:16],
            "kind": "device_event", "activity": ev.get("action") or "",
            "room": ev.get("room") or "",
            "entity_id": ev.get("entity_id"),
            "new_state": ev.get("new_state"), "source": "events",
        })

    timeline.sort(key=lambda t: str(t.get("ts") or ""))

    hours = [0] * 24
    rooms = []
    for t in timeline:
        hh = str(t.get("time") or "")[:2]
        if hh.isdigit() and 0 <= int(hh) < 24:
            hours[int(hh)] += 1
        room = t.get("room")
        if room and room not in rooms:
            rooms.append(room)

    return {
        "ok": True,
        "member_id": member_id,
        "member_name": name,
        "date": date,
        "count": len(timeline),
        "timeline": timeline,
        "summary": {
            "first_seen": timeline[0]["ts"] if timeline else None,
            "last_seen": timeline[-1]["ts"] if timeline else None,
            "rooms_visited": rooms,
            "hourly_counts": {str(h): hours[h] for h in range(24) if hours[h]},
        },
        "no_data": not timeline,
        "computed_from": "behavior_states(member=姓名) + behavior_events(persons 含姓名) "
                         "+ events(person=姓名, 通常为空)",
    }
