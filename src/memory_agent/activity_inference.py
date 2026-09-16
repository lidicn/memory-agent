"""v0.9.5 主动感知·行为推断层：从事件流产出 canonical 行为状态。

三层分工（见 docs/主动感知分工建议书_20260912.md）：
* 实时反应层（毫秒级规则/推送/联动）归**管家**；
* 权威记忆 + 发现层归 **MA** —— 本模块从 ``behavior_events``（视觉在场）与
  HA ``events``（门磁/灯/空调/电脑等设备状态）推断分钟级「谁·在哪·做什么」的
  canonical 真相，写 ``behavior_states`` 供 ``GET /api/behaviors`` 消费；
* 身份层：``ma/presence``（ArcFace）是「谁」的唯一权威源，本模块只**消费**
  ``store.recent_presence``，不重造身份。

设计要点（对齐开发计划 §4）：
* **有序序列匹配**：内置 sequence rule（门开→门关→灯亮→空调开→电脑开），
  滑动窗口逐事件推进，支持 ``within_min``（相对序列首步）与 ``after_prev_min``
  （相对上一步）时间约束；
* **PIR 去抖**：同一实体在 ``pir_debounce_sec`` 内的连续触发合并为一次；
* **多传感器融合**：门磁+灯+空调+电脑多源命中得高置信；单源命中低置信；
* **置信阈值**：低于 ``activity_conf_threshold`` 的识别**只进 candidate_rules**，
  不污染权威 ``behavior_states``（开发计划 §4.4）。

MA 不做实时流（批量采集 + 人脸推送）；本模块由 runtime 周期触发（非 websocket）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Optional

from .store import now_local


# ── 内置有序序列规则（开发计划 §4.2；room 为关键词列表，空=任意房间）──────────
DEFAULT_SEQUENCE_RULES: list[dict] = [
    {
        "name": "书房工作",
        "order_sensitive": True,
        "room": ["书房", "study"],
        "steps": [
            {"tag": "door", "state": "on", "within_min": 5},
            {"tag": "door", "state": "off", "within_min": 10, "after_prev_min": 2},
            {"tag": "light", "state": "on", "within_min": 12},
            {"tag": "climate", "state": "on", "within_min": 15},
            {"tag": "computer", "state": "on", "within_min": 15},
        ],
        "time_window": "",
        "infer": "study_work",
        "confidence": 0.85,
    },
    {
        "name": "就寝",
        "order_sensitive": True,
        "room": ["主卧室", "卧室", "bedroom"],
        "steps": [
            {"tag": "door", "state": "off", "within_min": 10},
            {"tag": "light", "state": "on", "within_min": 10, "after_prev_min": 2},
            {"tag": "climate", "state": "on", "within_min": 15},
            {"tag": "light", "state": "off", "within_min": 25},
        ],
        "time_window": "21:00-02:00",
        "infer": "user_asleep",
        "confidence": 0.8,
    },
    {
        "name": "房间移动",
        "order_sensitive": True,
        "room": [],
        "steps": [
            {"tag": "door", "state": "on", "within_min": 5},
            {"tag": "door", "state": "off", "within_min": 10, "after_prev_min": 2},
        ],
        "time_window": "",
        "infer": "transit",
        "confidence": 0.4,  # 单门磁证据 → 低置信，只进候选
    },
]

_ON_STATES = {"on", "open", "playing", "heat", "cool", "auto", "detected",
              "home", "unlocked", "1", "true", "yes"}
_OFF_STATES = {"off", "closed", "idle", "unavailable", "unknown", "none",
               "0", "false", "no", "not_home", "locked"}


def _is_on(state: Any) -> bool:
    s = str(state or "").strip().lower()
    if s in _ON_STATES:
        return True
    if s in _OFF_STATES:
        return False
    return bool(state)  # 未知值以真值判定


def _parse_ts(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _in_time_window(ts: str, window: str) -> bool:
    """判断时刻是否落在 ``HH:MM-HH:MM`` 窗口内（支持跨午夜 21:00-02:00）。"""
    if not window:
        return True
    try:
        s, e = window.split("-")
        sm = int(s[:2]) * 60 + int(s[3:5])
        em = int(e[:2]) * 60 + int(e[3:5])
        dt = _parse_ts(ts)
        if dt is None:
            return True
        tm = dt.hour * 60 + dt.minute
        if sm <= em:
            return sm <= tm <= em
        return tm >= sm or tm <= em  # 跨午夜
    except Exception:
        return True


class ActivityInferenceService:
    """行为推断引擎（无 LLM，纯确定性规则；可在单测里用 fake runtime 驱动）。"""

    def __init__(self, runtime: Any) -> None:
        self.rt = runtime
        self.config = runtime.config
        self.store = runtime.store
        self.insights = getattr(runtime, "insights", None)
        self.rules = list(DEFAULT_SEQUENCE_RULES)

    # ── 内部工具 ─────────────────────────────────────────────────────────
    def _friendly_names(self) -> dict[str, str]:
        """从 config.rooms 取 entity_id → friendly_name 映射（用于标签推断）。"""
        out: dict[str, str] = {}
        rooms = getattr(self.config, "rooms", None) or {}
        for payload in rooms.values():
            ents = (payload or {}).get("entities") or {}
            for eid, info in ents.items():
                if isinstance(info, dict):
                    out[eid] = info.get("friendly_name") or ""
        return out

    def _tags_of(self, eid: str, name: str) -> set:
        if self.insights is not None and hasattr(self.insights, "_tags_of"):
            try:
                return self.insights._tags_of(eid, name)
            except Exception:
                pass
        return set()

    def _debounce(self, events: list[dict], sec: int) -> list[dict]:
        """同一实体在 ``sec`` 秒内的连续事件合并为一次（PIR 去抖）。"""
        if sec <= 0:
            return events
        out: list[dict] = []
        last: dict[str, datetime] = {}
        for e in events:
            eid = e.get("entity_id") or ""
            dt = _parse_ts(e.get("ts") or "")
            prev = last.get(eid)
            if dt is not None and prev is not None and (dt - prev).total_seconds() < sec:
                continue  # 抖动，丢弃
            if dt is not None:
                last[eid] = dt
            out.append(e)
        return out

    def _state_ok(self, state: Any, want: str) -> bool:
        if want in ("", "any", None):
            return True
        if want == "on":
            return _is_on(state)
        if want == "off":
            return not _is_on(state)
        return str(state or "").strip().lower() == str(want).lower()

    def _room_ok(self, room: str, rule: dict) -> bool:
        keys = rule.get("room") or []
        if not keys:
            return True
        r = (room or "").lower()
        return any(str(k).lower() in r for k in keys)

    # ── 序列匹配 ─────────────────────────────────────────────────────────
    def _match_rule(self, events: list[dict], rule: dict) -> list[dict]:
        """在（已按 ts 升序、带 tags 的）事件序列中匹配一条有序规则，返回命中列表。

        每个命中含 ``{ts, events:[...]}``。命中后从末步之后继续找下一段序列。
        """
        steps = rule.get("steps") or []
        if not steps:
            return []
        hits: list[dict] = []
        matched: list[dict] = []
        t0: Optional[datetime] = None
        prev: Optional[datetime] = None
        for e in events:
            step = steps[len(matched)]
            if not (step.get("tag") in (e.get("tags") or set())
                    and self._state_ok(e.get("state"), step.get("state", "any"))):
                continue
            dt = _parse_ts(e.get("ts") or "")
            if len(matched) == 0:
                matched = [e]
                t0 = prev = dt
            else:
                after = float(step.get("after_prev_min") or 0)
                within = float(step.get("within_min") or 0)
                if (dt is not None and prev is not None and after > 0
                        and (dt - prev).total_seconds() < after * 60):
                    continue  # 间隔未达 after_prev_min，不推进（等待后续事件）
                if (dt is not None and t0 is not None and within > 0
                        and (dt - t0).total_seconds() > within * 60):
                    # 超出相对首步的时间窗 → 以当前事件重置为新序列起点
                    matched = [e]
                    t0 = prev = dt
                    continue
                matched.append(e)
                prev = dt
            if len(matched) == len(steps):
                if _in_time_window(matched[-1].get("ts") or "", rule.get("time_window", "")):
                    hits.append({"ts": matched[-1].get("ts"), "events": list(matched)})
                matched = []
                t0 = prev = None
        return hits

    # ── 主流程 ───────────────────────────────────────────────────────────
    def run(self, start: str | None = None, end: str | None = None,
            window_minutes: int | None = None) -> dict:
        """对窗口内事件跑推断，产出 canonical 状态；低置信进候选规则。

        返回 ``{ok, window, scanned, states, persisted, candidates, detail}``。
        """
        wmin = int(window_minutes or getattr(self.config, "activity_window_minutes", 15) or 15)
        deb = int(getattr(self.config, "pir_debounce_sec", 30) or 0)
        thr = float(getattr(self.config, "activity_conf_threshold", 0.6) or 0.6)
        now = now_local(self.config.tz_offset_hours)
        end = end or now.isoformat(sep="T")
        if not start:
            start = (now.replace(microsecond=0)
                     - timedelta(minutes=wmin)).isoformat(sep="T")

        try:
            events = self.store.query_events(start=start, end=end, order="asc", limit=5000)
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "error": f"事件查询失败: {exc}"}

        names = self._friendly_names()
        for e in events:
            e["tags"] = self._tags_of(e.get("entity_id") or "", names.get(e.get("entity_id"), ""))
            # events 表用 new_state 表示「变更后状态」，统一成 state 供规则匹配
            e["state"] = e.get("new_state")
        events = self._debounce(events, deb)

        # 按房间分组（事件 room 由采集端由 config.rooms 推出）
        by_room: dict[str, list[dict]] = {}
        for e in events:
            rm = (e.get("room") or "").strip() or "未知"
            by_room.setdefault(rm, []).append(e)

        # 身份：该窗口内各成员最近被识别到的房间（ma/presence 权威）
        try:
            presence = self.store.recent_presence(start)
        except Exception:
            presence = []
        room_member: dict[str, str] = {}
        for p in presence:
            room_member.setdefault(p.get("room") or "", p.get("name") or "")

        states: list[dict] = []
        for room, evs in by_room.items():
            for rule in self.rules:
                if not self._room_ok(room, rule):
                    continue
                for hit in self._match_rule(evs, rule):
                    conf = float(rule.get("confidence") or 0.0)
                    states.append({
                        "member": room_member.get(room, ""),
                        "room": room,
                        "activity": rule.get("infer") or rule.get("name") or "unknown",
                        "confidence": conf,
                        "ts": hit["ts"],
                        "rule": rule.get("name") or "",
                        "evidence": [e.get("entity_id") for e in hit["events"]],
                    })

        persisted = candidates = 0
        detail: list[dict] = []
        for st in states:
            if st["confidence"] >= thr:
                self.store.add_behavior_state(
                    member=st["member"], room=st["room"], activity=st["activity"],
                    confidence=st["confidence"], ts=st["ts"], source="inference",
                    evidence=st["evidence"],
                )
                persisted += 1
                detail.append({"activity": st["activity"], "room": st["room"],
                               "member": st["member"], "confidence": st["confidence"],
                               "ts": st["ts"], "kind": "state"})
            else:
                self.store.upsert_candidate_rule(
                    name=f"{st['room']}/{st['rule']}", steps=st["evidence"],
                    time_window="", infer=st["activity"], confidence=st["confidence"],
                    source="inference",
                    evidence=[{"ts": st["ts"], "room": st["room"], "member": st["member"]}],
                )
                candidates += 1
                detail.append({"activity": st["activity"], "room": st["room"],
                               "member": st["member"], "confidence": st["confidence"],
                               "ts": st["ts"], "kind": "candidate"})

        return {
            "ok": True,
            "window": {"start": start, "end": end},
            "scanned": len(events),
            "states": len(states),
            "persisted": persisted,
            "candidates": candidates,
            "threshold": thr,
            "detail": detail,
        }

    # ── 对外读接口（GET /api/behaviors 数据源）───────────────────────────
    def current_behaviors(self, minutes: int = 30) -> list[dict]:
        """融合「身份+房间」（ma/presence）与「最近 canonical 活动」（behavior_states）。

        返回 ``[{member, location, activity, confidence, ts, via}]``。
        """
        now = now_local(self.config.tz_offset_hours)
        since = (now - timedelta(minutes=max(1, minutes))).isoformat(sep="T")
        try:
            presence = self.store.recent_presence(since)
        except Exception:
            presence = []
        try:
            states = self.store.list_behavior_states(since=since, limit=300)
        except Exception:
            states = []

        out: list[dict] = []
        for p in presence:
            member = p.get("name") or ""
            room = p.get("room") or ""
            st = next((s for s in states if s.get("member") == member), None)
            if st is None:
                st = next((s for s in states if s.get("room") == room), None)
            out.append({
                "member": member,
                "location": room,
                "activity": (st or {}).get("activity", ""),
                "confidence": float((st or {}).get("confidence", 0.0)),
                "ts": (st or {}).get("ts") or p.get("last_seen"),
                "via": p.get("via"),
            })
        return out

    # ── 序列模式挖掘（任务 C：7 天事件 → 候选规则，无 LLM）────────────────
    _TAG_PRIORITY = ("door", "light", "climate", "computer", "media",
                     "appliance", "presence", "cover")

    # 序列 → 语义名（轻量映射，非 LLM）
    _INFER_HINTS = (
        (("door", "light", "climate"), "room_occupy"),
        (("door", "light"), "room_enter"),
        (("computer", "light"), "work_session"),
        (("media", "light"), "media_session"),
        (("light", "climate"), "comfort_on"),
    )

    def _primary_tag(self, tags: set) -> str:
        for t in self._TAG_PRIORITY:
            if t in tags:
                return t
        return sorted(tags)[0] if tags else ""

    def _infer_name(self, tags: list) -> str:
        tset = set(tags)
        for combo, name in self._INFER_HINTS:
            if set(combo).issubset(tset):
                return name
        return "sequence:" + "+".join(tags)

    def mine_sequences(self, start: str | None = None, end: str | None = None,
                       days: int = 7, min_support: int = 2, top_n: int = 12,
                       rooms: list[str] | None = None, max_len: int = 4) -> dict:
        """从事件流自动发现高频**有序 tag 序列**，生成候选规则写 ``candidate_rules``。

        与预定义规则（``DEFAULT_SEQUENCE_RULES``）互补：本方法不做 LLM，纯频次挖掘
        （按房间统计长度 2..max_len 的连续 tag 序列，支持度 ≥ ``min_support``），
        产出进 staging 供人工审核，采纳后经导出接口交付管家规则库（任务 C）。
        """
        now = now_local(self.config.tz_offset_hours)
        end = end or now.isoformat(sep="T")
        if not start:
            start = (now.replace(microsecond=0)
                     - timedelta(days=max(1, days))).isoformat(sep="T")
        try:
            events = self.store.query_events(
                start=start, end=end, rooms=rooms, order="asc", limit=20000
            )
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "error": f"事件查询失败: {exc}"}

        names = self._friendly_names()
        by_room: dict[str, list[tuple]] = {}
        for e in events:
            tags = self._tags_of(e.get("entity_id") or "", names.get(e.get("entity_id"), ""))
            if not tags:
                continue
            rm = (e.get("room") or "").strip() or "未知"
            tag = self._primary_tag(tags)
            state = "on" if _is_on(e.get("new_state")) else "off"
            by_room.setdefault(rm, []).append((tag, state, e.get("ts")))

        mined: list[dict] = []
        for rm, seq in by_room.items():
            if len(seq) < min_support:
                continue
            # 压缩连续重复 (tag,state)
            compact: list[tuple] = []
            for item in seq:
                if not compact or compact[-1][:2] != item[:2]:
                    compact.append(item)
            counts: dict[tuple, int] = {}
            for n in range(2, max_len + 1):
                for i in range(len(compact) - n + 1):
                    key = tuple((t, s) for (t, s, _) in compact[i:i + n])
                    counts[key] = counts.get(key, 0) + 1
            for key, cnt in counts.items():
                if cnt >= min_support:
                    mined.append({"room": rm, "steps": key, "support": cnt})

        mined.sort(key=lambda x: -x["support"])
        mined = mined[:top_n]
        rules: list[dict] = []
        for m in mined:
            steps = [{"tag": t, "state": s} for (t, s) in m["steps"]]
            name = f"{m['room']}序列:" + "→".join(
                f"{t}({s})" for (t, s) in m["steps"]
            )
            conf = round(min(0.9, 0.5 + 0.1 * m["support"]), 2)
            self.store.upsert_candidate_rule(
                name=name, steps=steps, time_window="",
                infer=self._infer_name([t for (t, _) in m["steps"]]),
                confidence=conf, source="researcher",
                evidence=[{"room": m["room"], "support": m["support"]}],
            )
            rules.append({"name": name, "steps": steps,
                          "support": m["support"], "confidence": conf})

        return {"ok": True, "start": start, "end": end,
                "candidates": len(rules), "rules": rules}

    # ── 意图/习惯层（任务 D：重复活动 → 长期意图）───────────────────────
    def infer_habits(self, days: int = 14, min_days: int = 3) -> dict:
        """从 ``behavior_states`` 聚合重复活动为长期习惯，写 ``agent_memories``（staging）。

        作息/偏好类习惯沉淀为「成员 + 典型时段 + 活动 + 房间」，经 v0.8 记忆合并管线
        写回 staging 供审阅晋升，作为管家实时融合的先验置信（任务 D）。
        仅统计**已绑定身份**（member 非空）的状态；观测天数 < ``min_days`` 的不沉淀。
        """
        now = now_local(self.config.tz_offset_hours)
        since = (now.replace(microsecond=0)
                 - timedelta(days=max(1, days))).isoformat(sep="T")
        try:
            states = self.store.list_behavior_states(since=since, limit=3000)
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "error": f"状态查询失败: {exc}"}

        agg: dict[tuple, dict] = {}
        for s in states:
            member = s.get("member") or ""
            if not member:
                continue  # 无身份的活动不沉淀为「某人的习惯」
            key = (member, s.get("activity") or "")
            a = agg.setdefault(key, {"days": set(), "hours": [], "rooms": {}})
            ts = s.get("ts") or ""
            a["days"].add(str(ts)[:10])
            dt = _parse_ts(ts)
            if dt is not None:
                a["hours"].append(dt.hour)
            rm = s.get("room") or ""
            if rm:
                a["rooms"][rm] = a["rooms"].get(rm, 0) + 1

        saved = 0
        habits: list[dict] = []
        for (member, activity), a in agg.items():
            if len(a["days"]) < min_days:
                continue
            hours = a["hours"]
            typical_hour = max(set(hours), key=hours.count) if hours else None
            room = max(a["rooms"], key=a["rooms"].get) if a["rooms"] else ""
            hh = f"{typical_hour:02d}:00" if typical_hour is not None else "未知时段"
            text = (f"{member} 通常在 {hh} 左右于{room or '家中'}进行「{activity}」"
                    f"（近 {len(a['days'])} 天观测）")
            try:
                self.rt.agent_memory.merge_semantic_memory(
                    session_id="habits", text=text,
                    topic_key=f"habit:{member}:{activity}",
                    tags=["habit", f"member:{member}", f"activity:{activity}"],
                    source_refs=[f"activity:{activity}"],
                    ttl_days=90, source="ma",
                )
                saved += 1
                habits.append({"member": member, "activity": activity,
                               "typical_hour": typical_hour, "room": room,
                               "days": len(a["days"])})
            except Exception as exc:  # noqa: BLE001
                print(f"[Activity] 习惯写回失败: {exc}")
        return {"ok": True, "habits": habits, "saved": saved}
