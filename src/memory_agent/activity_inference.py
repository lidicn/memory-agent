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

from . import algo_kernel
from .store import TELEMETRY_DOMAINS, now_local


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

    def _prepare_events(self, events: list[dict], deb: int) -> list[dict]:
        """给事件补 ``tags`` / ``state`` 并做 PIR 去抖——规则匹配的统一前置口径。

        ``run`` 与 ``audit_rule_recall`` 必须共用它，否则两处的"规则是否命中"
        会因输入口径不同而对不上（审计就失去意义）。
        """
        names = self._friendly_names()
        for e in events:
            e["tags"] = self._tags_of(
                e.get("entity_id") or "", names.get(e.get("entity_id"), ""))
            # events 表用 new_state 表示「变更后状态」，统一成 state 供规则匹配
            e["state"] = e.get("new_state")
        return self._debounce(events, deb)

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

        events = self._prepare_events(events, deb)

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
                rid, action = self.store.upsert_candidate_rule(
                    name=f"{st['room']}/{st['rule']}", steps=st["evidence"],
                    time_window="", infer=st["activity"], confidence=st["confidence"],
                    source="inference",
                    evidence=[{"ts": st["ts"], "room": st["room"], "member": st["member"]}],
                )
                if not action.startswith("rejected"):
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

    # ── P1.4 规则召回审计（用统计补召回，而不是替换规则）────────────────────
    def _rule_prefix_blocker(self, day_events: list[dict], rule: dict) -> Optional[int]:
        """诊断规则卡在第几步（1-based 步号）。

        做法：用规则的**前 k 步**去匹配，找到第一个匹配不上的 k。复用同一个
        ``_match_rule``，保证"卡住点"的判定与真实命中判定完全同口径。
        返回 ``None`` 表示各前缀都能匹配 → 卡住的是 ``time_window`` 或末步之后的约束。
        """
        steps = rule.get("steps") or []
        for k in range(1, len(steps) + 1):
            if not self._match_rule(day_events, {**rule, "steps": steps[:k]}):
                return k
        return None

    def audit_rule_recall(self, start: str | None = None, end: str | None = None,
                          days: int = 14, rooms: list | None = None, *,
                          persist: bool = True, min_near_miss: int = 2,
                          max_gaps: int = 10,
                          extra_rules: list | None = None) -> dict:
        """审计序列规则的**召回缺口**（P1.4）。

        背景：spike 实测手写规则是**高精确 / 低召回**（sleeping P=0.988 / R=0.585）
        ——规则触发时几乎总是对的，但大量该判的时段它没判。所以正确方向不是
        "换成 HMM"，而是**补召回**：找出"原材料齐备却没命中"的场景，诊断卡在哪一步。

        口径（与 P1.1 一致的「房间·天」粒度，便于交叉核对）：

        * **eligible（应命中）**：该房间当天出现了规则所有步骤所需的标签
        * **matched**：当天规则确实命中
        * **near_miss**：eligible 但未命中 → 召回缺口
        * **estimated_recall** = matched / eligible（规则自身口径下的召回上界估计）
        * **blocker**：对每个 near_miss 天诊断第一个匹配不上的步骤

        ``persist=True`` 且某缺口出现次数 ≥ ``min_near_miss`` 时，产出**放宽建议**
        候选规则（去掉卡住那一步，``source="recall_gap"``）供人工审核——
        这是"补召回"的落地形式，不自动改线上规则。
        """
        cfg = self.config
        now = now_local(cfg.tz_offset_hours)
        end = end or now.isoformat(sep="T")
        if not start:
            start = (now.replace(microsecond=0)
                     - timedelta(days=max(1, days))).isoformat(sep="T")
        try:
            events = self._iter_events(start, end, rooms=rooms)
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "error": f"事件查询失败: {exc}"}
        deb = int(getattr(cfg, "pir_debounce_sec", 30) or 0)
        events = self._prepare_events(events, deb)

        by_room_day: dict[tuple, list[dict]] = {}
        for e in events:
            ts = _parse_ts(e.get("ts") or "")
            if ts is None:
                continue
            rm = (e.get("room") or "").strip() or "未知"
            by_room_day.setdefault((rm, ts.date().isoformat()), []).append(e)

        rules = list(self.rules) + list(extra_rules or [])
        audit: list[dict] = []
        gaps: list[dict] = []
        # Phase 4.2 建议语义去重：已生成建议名称列表（用于 is_duplicate 检查）
        generated_suggestion_names: list[str] = []
        dedup_suppressed: list[dict] = []
        dedup_enabled = bool(getattr(self.config, "semantic_dedup_enabled", True))
        dedup_threshold = float(getattr(self.config, "semantic_dedup_threshold", 0.85))
        deduper = getattr(self.rt, "semantic_dedup", None)
        for rule in rules:
            steps = rule.get("steps") or []
            anchors = {s.get("tag") for s in steps if s.get("tag")}
            if not anchors:
                continue
            eligible = matched = 0
            near_miss: list[str] = []
            blockers: dict[str, dict] = {}
            for (rm, day), evs in by_room_day.items():
                if not self._room_ok(rm, rule):
                    continue
                present = {t for e in evs for t in (e.get("tags") or set())}
                if not anchors.issubset(present):
                    continue  # 原材料不全 → 不该要求规则命中
                eligible += 1
                if self._match_rule(evs, rule):
                    matched += 1
                    continue
                near_miss.append(f"{rm}|{day}")
                idx = self._rule_prefix_blocker(evs, rule)
                if idx is None:
                    key, reason = "time_window", "time_window"
                else:
                    st = steps[idx - 1]
                    key = f"{st.get('tag')}({st.get('state', 'any')})@step{idx}"
                    # 区分两种完全不同的病因：**事件当天根本没出现**（设备/采集缺口，
                    # 规则再放宽也没用）vs **出现了但不满足时序/时间窗**（规则可放宽）。
                    seen = any(
                        (st.get("tag") in (e.get("tags") or set()))
                        and self._state_ok(e.get("state"), st.get("state", "any"))
                        for e in evs)
                    reason = "timing_or_order" if seen else "missing_event"
                b = blockers.setdefault(key, {"count": 0, "reasons": {}})
                b["count"] += 1
                b["reasons"][reason] = b["reasons"].get(reason, 0) + 1

            top = sorted(blockers.items(), key=lambda x: -x[1]["count"])
            audit.append({
                "rule": rule.get("name"), "infer": rule.get("infer"),
                "anchors": sorted(anchors),
                "eligible_days": eligible, "matched_days": matched,
                "near_miss_days": len(near_miss),
                "estimated_recall": (round(matched / eligible, 3) if eligible else None),
                "top_blockers": [{"blocker": k, "count": v["count"],
                                  "reasons": v["reasons"]} for k, v in top[:3]],
                "examples": near_miss[:5],
            })

            if not (persist and top and top[0][1]["count"] >= max(1, int(min_near_miss))):
                continue
            if len(gaps) >= max_gaps:
                continue
            blocker, binfo = top[0]
            count = binfo["count"]
            if blocker == "time_window" or "@step" not in blocker:
                continue
            reasons = binfo.get("reasons") or {}
            # 按**主导病因**判定（真实数据常常是混合的：如 33 例时序 + 8 例缺失，
            # 若要求"全部同一病因"就会一条建议都不出，反而丢失了主要可改的方向）
            dominant = max(reasons.items(), key=lambda x: x[1])[0] if reasons else ""
            # 事件根本没出现（设备/采集缺口）→ 放宽规则无意义，交人工先查数据源
            if dominant == "missing_event":
                continue
            try:
                idx = int(blocker.split("@step")[1])
            except (IndexError, ValueError):
                continue
            st = steps[idx - 1]
            relaxed = [dict(s) for s in steps]
            if dominant == "timing_or_order":
                # 事件出现过、只是时间约束不满足 → **放宽该步时间窗**（保留步骤，
                # 避免"删步骤"把规则退化成无意义）。注意 order 与 timing 在此
                # 合并统计，建议仍进 staging 由人工结合 evidence 判断。
                changed = False
                if st.get("within_min"):
                    relaxed[idx - 1]["within_min"] = round(
                        float(st["within_min"]) * 2, 1)
                    changed = True
                if st.get("after_prev_min"):
                    relaxed[idx - 1]["after_prev_min"] = 0
                    changed = True
                if not changed:
                    continue
                label = f"第{idx}步时间约束放宽"
                gap_kind = "relax_timing"
            else:
                del relaxed[idx - 1]
                if len(relaxed) < 2:
                    continue
                label = f"去掉第{idx}步"
                gap_kind = "drop_step"
            name = f"召回放宽[{rule.get('name')}]:{label}"
            # Phase 4.2 建议语义去重：检查是否与已生成建议语义重复
            if dedup_enabled and deduper is not None and generated_suggestion_names:
                dup_check = deduper.is_duplicate(name, generated_suggestion_names, threshold=dedup_threshold)
                if dup_check.get("duplicate"):
                    dedup_suppressed.append({
                        "name": name, "duplicate_of": dup_check.get("duplicate_of"),
                        "similarity": dup_check.get("similarity"), "method": dup_check.get("method"),
                        "rule": rule.get("name"), "blocker": blocker, "count": count,
                    })
                    continue
            try:
                rid, action = self.store.upsert_candidate_rule(
                    name=name, steps=relaxed,
                    time_window=rule.get("time_window", ""),
                    infer=rule.get("infer", ""),
                    # 宽松变体：置信度按原规则打 9 折，交人工复核后再决定是否启用
                    confidence=round(float(rule.get("confidence") or 0.5) * 0.9, 2),
                    source="recall_gap",
                    evidence=[{"rule": rule.get("name"), "blocker": blocker,
                               "kind": gap_kind,
                               "reasons": reasons,
                               "near_miss_days": len(near_miss),
                               "eligible_days": eligible, "matched_days": matched}],
                )
                gaps.append({"rule_id": rid, "action": action, "name": name,
                             "kind": gap_kind, "blocker": blocker, "count": count,
                             "reasons": reasons,
                             "near_miss_days": len(near_miss)})
                generated_suggestion_names.append(name)
            except Exception as exc:  # noqa: BLE001
                print(f"[Activity] 召回放宽建议写库失败: {exc}")

        return {
            "ok": True, "start": start, "end": end, "days": max(1, days),
            "events": len(events), "room_days": len(by_room_day),
            "rules": len(rules), "audit": audit,
            "gap_count": len(gaps), "gaps": gaps,
            "dedup_suppressed_count": len(dedup_suppressed),
            "dedup_suppressed": dedup_suppressed,
            "dedup_enabled": dedup_enabled,
            "dedup_threshold": dedup_threshold,
        }

    # ── P1.1 过程挖掘：行为过程模型 + 一致性检验（行为异常）────────────────
    def _iter_events(self, start: str, end: str, rooms: list | None = None,
                     max_rows: int = 200000) -> list[dict]:
        """取窗口内事件（分页全量，**排除纯遥测域**）。

        ``store.query_events`` 单次 LIMIT 硬上限 5000 会**静默截断**，直接传大
        limit 只会拿到最早一段；优先复用 ``insights._iter_all_events`` 分页。
        同时排除 ``TELEMETRY_DOMAINS``（功率/温湿度等固定周期上报）：它们没有行为
        标签、却占绝大多数行——不排除会让分页上限被遥测吃满，窗口只覆盖到最早几天
        （线上实测：30 天窗口反而只看到 3 个 case）。
        """
        kw = {"rooms": rooms, "order": "asc", "exclude_domains": list(TELEMETRY_DOMAINS)}
        ins = self.insights
        if ins is not None and hasattr(ins, "_iter_all_events"):
            try:
                return ins._iter_all_events(start, end, max_rows=max_rows, **kw)
            except TypeError as _e:
                # E-MA-01: 不要静默丢掉过滤条件，打警告
                import logging
                logging.getLogger(__name__).warning("_iter_all_events 不支持 exclude_domains，过滤条件已丢弃: %s", _e)
                kw.pop("exclude_domains", None)
                return ins._iter_all_events(start, end, max_rows=max_rows, **kw)
        # E-MA-01: fallback 到 query_events 时不要静默截断，提高 limit 并打警告
        import logging
        logging.getLogger(__name__).warning("_iter_all_events 不可用，fallback 到 query_events 可能截断数据")
        return self.store.query_events(start=start, end=end, limit=min(max_rows, 50000), **kw)

    def mine_process(self, start: str | None = None, end: str | None = None,
                     days: int = 7, rooms: list | None = None, *,
                     min_edge_support: float | None = None,
                     min_activity_support: float | None = None,
                     persist: bool = True, emit_rules: bool = True,
                     min_variant_support: int | None = None, max_rules: int = 8,
                     bucket_sec: int | None = None,
                     min_cases_per_room: int | None = None,
                     min_case_events: int | None = None,
                     max_rows: int = 200000) -> dict:
        """挖行为**过程模型**并检出偏离常态的 case（P1.1；异常 = 一致性检验）。

        与 ``mine_sequences``（朴素 n-gram 频次）互补：本方法把 ``(房间·天)`` 当
        case、``tag_on`` 当活动，构建直接跟随图（DFG），用**稀有边/稀有活动**判据
        检出"平时不这么走"的一天。实证（见 docs/调研_P1算法内核spike_结论_20260917.md）
        该判据已覆盖全部检出，故核心为纯 Python（无 AGPL / 无重依赖）；
        装了 pm4py 会自动叠加 Petri 网 token 回放增强。

        阈值缺省取 config 的 ``process_mining_min_*``（默认 0.1 / 0.1 / 3）。

        - ``persist``：异常写 ``behavior_anomalies``（按 case 幂等，保留人工复核 status）
        - ``emit_rules``：房间高频变体（支持度 ≥ ``min_variant_support``，长度 ≥3）
          写 ``candidate_rules``（source=``process``），作为**规则缺口提示**（P1.4）
        """
        cfg = self.config
        if min_edge_support is None:
            min_edge_support = float(
                getattr(cfg, "process_mining_min_edge_support", 0.1) or 0.1)
        if min_activity_support is None:
            min_activity_support = float(
                getattr(cfg, "process_mining_min_activity_support", 0.1) or 0.1)
        if min_variant_support is None:
            min_variant_support = int(
                getattr(cfg, "process_mining_min_variant_support", 3) or 3)
        if bucket_sec is None:
            bucket_sec = int(getattr(cfg, "process_mining_bucket_sec", 600) or 0)
        if min_cases_per_room is None:
            min_cases_per_room = int(
                getattr(cfg, "process_mining_min_cases_per_room", 4) or 1)
        if min_case_events is None:
            min_case_events = int(
                getattr(cfg, "process_mining_min_case_events", 2) or 2)
        now = now_local(cfg.tz_offset_hours)
        end = end or now.isoformat(sep="T")
        if not start:
            start = (now.replace(microsecond=0)
                     - timedelta(days=max(1, days))).isoformat(sep="T")
        try:
            events = self._iter_events(start, end, rooms=rooms, max_rows=max_rows)
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "error": f"事件查询失败: {exc}"}

        try:
            res = algo_kernel.mine_process_model(
                events, self._tags_of, primary_tag=self._primary_tag,
                min_edge_support=min_edge_support,
                min_activity_support=min_activity_support,
                bucket_sec=bucket_sec,
                min_cases_per_room=min_cases_per_room,
                min_case_events=min_case_events,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"过程挖掘失败: {exc}"}
        if not res.get("ok"):
            return res

        anomalies = res.get("anomalies") or []
        persisted = 0
        if persist and anomalies:
            for a in anomalies[:200]:
                try:
                    self.store.upsert_behavior_anomaly(
                        case_key=a.get("case") or "", day=a.get("day") or "",
                        room=a.get("room") or "", reasons=a.get("reasons"),
                        activities=a.get("activities"), rare_edges=a.get("rare_edges"),
                        rare_activities=a.get("rare_activities"),
                        severity=a.get("severity") or 0.0,
                        engine=res.get("engine") or "pure",
                    )
                    persisted += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"[Activity] 行为异常写库失败: {exc}")

        rules: list[dict] = []
        if emit_rules:
            for room, variants in (res.get("variants_by_room") or {}).items():
                for v in variants:
                    if len(rules) >= max_rules:
                        break
                    acts = v.get("activities") or []
                    if len(acts) < 3 or (v.get("count") or 0) < min_variant_support:
                        continue
                    tags = [t[:-3] if t.endswith("_on") else t for t in acts]
                    name = f"过程变体[{room}]:" + "→".join(tags)
                    conf = round(min(0.9, 0.5 + 0.1 * int(v["count"])), 2)
                    try:
                        rid, action = self.store.upsert_candidate_rule(
                            name=name,
                            steps=[{"tag": t, "state": "on"} for t in tags],
                            time_window="", infer=self._infer_name(tags),
                            confidence=conf, source="process",
                            evidence=[{"room": room, "support": v.get("count"),
                                       "source": "process_mining"}],
                        )
                        rules.append({"rule_id": rid, "action": action, "name": name,
                                      "support": v.get("count"), "confidence": conf})
                    except Exception as exc:  # noqa: BLE001
                        print(f"[Activity] 过程变体写候选规则失败: {exc}")

        return {
            "ok": True,
            "engine": res.get("engine"),
            "start": start, "end": end,
            "cases": res.get("cases"), "cases_all": res.get("cases_all"),
            "events": len(events),
            "reviewed_rooms": res.get("reviewed_rooms"),
            "skipped_rooms": res.get("skipped_rooms"),
            "min_cases_per_room": res.get("min_cases_per_room"),
            "min_case_events": min_case_events,
            "bucket_sec": res.get("bucket_sec"),
            "fitness_rate": res.get("fitness_rate"),
            "dfg_edges": res.get("dfg_edges"),
            "top_variants": (res.get("top_variants") or [])[:5],
            "anomaly_count": len(anomalies),
            "persisted": persisted,
            "anomalies": anomalies[:20],
            "candidates": len(rules),
            "rules": rules,
        }

    # ── P1.2 在线异常 + 概念漂移（river：HST 异常分 + ADWIN 漂移）────────────
    def mine_drift(self, start: str | None = None, end: str | None = None,
                   days: int = 14, rooms: list | None = None, *,
                   bucket_sec: int | None = None, window_size: int | None = None,
                   min_score: float | None = None,
                   persist: bool = True, max_rows: int = 200000) -> dict:
        """行为活跃度的**在线异常**与**概念漂移**检测（P1.2）。

        与 P1.1 互补：P1.1 是"事后按天做一致性检验"（这天像不像平时的样子），
        本方法把「每桶行为活跃度（标签组合 + 事件密度）」当**时间序列**在线喂给
        river —— Half-Space Trees 给无监督异常分（哪些时段不像平时），
        ADWIN 监测活跃度分布的**突变**（"最近作息/活跃度变了"）。

        默认按 **1 小时**分桶（作息量级；日以下抖动无意义），14 天 ≈ 336 个点。
        HST 需要 ≥ ``window_size`` 个样本才出分，样本不足时会自动下调窗口并注明。

        :param persist: 漂移点/异常时段写 ``behavior_drifts``（按 (桶,类型) 幂等）
        """
        cfg = self.config
        if bucket_sec is None:
            bucket_sec = int(getattr(cfg, "drift_bucket_sec", 3600) or 3600)
        now = now_local(cfg.tz_offset_hours)
        end = end or now.isoformat(sep="T")
        if not start:
            start = (now.replace(microsecond=0)
                     - timedelta(days=max(1, days))).isoformat(sep="T")
        try:
            events = self._iter_events(start, end, rooms=rooms, max_rows=max_rows)
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "error": f"事件查询失败: {exc}"}

        series = algo_kernel.extract_observation_series(
            events, self._tags_of, bucket_sec=bucket_sec)
        if len(series) < 10:
            return {"ok": True, "points": len(series), "drift_count": 0,
                    "anomaly_count": 0, "persisted": 0,
                    "note": "样本点不足（<10），暂不做漂移/异常检测"}

        # HST 需要 window_size 个样本才建立正常区间；样本少时下调窗口，避免全程冷启动
        ws = int(window_size or getattr(cfg, "drift_window_size", 0) or 0)
        if ws <= 0:
            ws = max(30, min(250, len(series) // 2))
        if min_score is None:
            min_score = float(getattr(cfg, "drift_min_score", 0.9) or 0.9)
        det = algo_kernel.OnlineAnomalyDetector(window_size=ws)
        res = det.score_stream(series, min_score=min_score)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error"), "points": len(series)}

        drifts = (res.get("drift") or {}).get("points") or []
        # 主判据 = 「同一小时的历史分布偏离」：可解释、可核对（给出 expected/z）；
        # HST 分数在本项目数据上区分度饱和（p90≈0.99），只作**相对排名**参考。
        anomalies = algo_kernel.hour_of_day_deviation(
            series, k=float(getattr(cfg, "drift_k", 3.0) or 3.0),
            min_delta=float(getattr(cfg, "drift_min_delta", 10.0) or 10.0))
        relative_top = sorted(res.get("anomalies") or [],
                              key=lambda x: -(x.get("score") or 0))[:10]

        persisted = 0
        if persist:
            by_bucket = {s["bucket"]: s for s in series}
            for d in drifts:
                item = by_bucket.get(d.get("bucket")) or {}
                try:
                    self.store.upsert_behavior_drift(
                        bucket_ts=d.get("bucket") or "", kind="drift",
                        day=str(d.get("bucket") or "")[:10],
                        density=d.get("density") or 0.0, score=d.get("score") or 0.0,
                        tags=item.get("tags"), detail={"reason": "adwin_mean_shift"})
                    persisted += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"[Activity] 漂移点写库失败: {exc}")
            for a in anomalies:
                # score 存"显著度"：有 z 用 z（历史恒定时为 None → 退化为绝对偏离量）
                score = a.get("z")
                if score is None:
                    score = a.get("delta") or 0.0
                try:
                    self.store.upsert_behavior_drift(
                        bucket_ts=a.get("bucket") or "", kind="anomaly",
                        day=str(a.get("bucket") or "")[:10],
                        density=a.get("density") or 0.0, score=score,
                        tags=a.get("tags"),
                        detail={"reason": "hour_of_day_deviation",
                                "expected": a.get("expected"),
                                "scale": a.get("scale"),
                                "delta": a.get("delta"),
                                "z": a.get("z"), "hour": a.get("hour")})
                    persisted += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"[Activity] 异常时段写库失败: {exc}")

        return {
            "ok": True,
            "start": start, "end": end,
            "points": len(series), "bucket_sec": bucket_sec, "window_size": ws,
            "events": len(events),
            "score_mean": res.get("score_mean"), "score_max": res.get("score_max"),
            "drift_count": len(drifts),
            "anomaly_count": len(anomalies),
            "persisted": persisted,
            "drifts": drifts[:10],
            "anomalies": anomalies[:10],
            # HST 相对排名（区分度饱和，仅供参考，不作为异常判据）
            "relative_top": relative_top,
        }

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
