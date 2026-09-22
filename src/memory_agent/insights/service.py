"""业务逻辑层：设备使用统计、行为洞察、作息分析，并调度各专项模块。

服务端算好再给：时长、开关次数、作息、异常都在这里算完，
不把上万条原始事件 dump 给调用方硬算。
"""

from __future__ import annotations

import logging
import statistics
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .activity import ActivityEngine
from .anomaly import AnomalyDetector
from .models import (
    EventRecord, Insight, InsightConfig, Page, Session, StateKind, TimeRange,
    UsageStat, day_key, fmt_ts, hour_of,
)
from .parser.entity import EntityResolver, normalize_state
from .parser.timeframe import resolve_range
from .persona import PersonaBuilder
from .repository import BaseRepository
from .report import ReportBuilder

LOG = logging.getLogger(__name__)

__all__ = ["BehaviorService", "compute_sessions", "hour_histogram", "median"]


def median(values: Sequence[float]) -> float:
    """中位数（空序列返回 0）。"""
    return float(statistics.median(values)) if values else 0.0


def compute_sessions(events: Iterable[EventRecord], tr: Optional[TimeRange] = None,
                     min_seconds: float = 1.0) -> Dict[str, List[Session]]:
    """把事件流切分为「开启 -> 关闭」会话。

    算法：按实体排序扫描，遇到 on 记起点，遇到 off 生成一段会话；
    窗口结束仍未关闭的会话按窗口右边界截断并标记 ``open=True``（时间范围裁剪）。
    """
    by_entity: Dict[str, List[EventRecord]] = {}
    for ev in sorted(events, key=lambda e: (e.ts, e.entity_id)):
        by_entity.setdefault(ev.entity_id, []).append(ev)

    out: Dict[str, List[Session]] = {}
    for entity_id, evs in by_entity.items():
        meta = evs[0]
        open_start: Optional[EventRecord] = None
        raw: List[Session] = []
        for ev in evs:
            kind = normalize_state(ev.state)
            if kind == StateKind.ON.value and open_start is None:
                open_start = ev
            elif kind == StateKind.OFF.value and open_start is not None:
                raw.append(_session(open_start, ev.ts, meta, False))
                open_start = None
        if open_start is not None:
            end_ts = tr.end_ts if tr else evs[-1].ts
            raw.append(_session(open_start, end_ts, meta, True))

        sessions: List[Session] = []
        for ses in raw:
            start, end = (tr.clip(ses.start, ses.end) if tr else (ses.start, ses.end))
            if end - start >= min_seconds:
                ses.start, ses.end = start, end
                sessions.append(ses)
        out[entity_id] = sessions
    return out


def _session(open_event: EventRecord, end_ts: float, meta: EventRecord,
             is_open: bool) -> Session:
    return Session(
        entity_id=open_event.entity_id,
        start=open_event.ts,
        end=end_ts,
        friendly_name=open_event.friendly_name or meta.friendly_name,
        room=open_event.room or meta.room,
        domain=open_event.domain or meta.domain,
        open=is_open,
    )


def hour_histogram(events: Iterable[EventRecord]) -> List[Dict[str, Any]]:
    """小时分布（0-23），用于作息/高峰分析。"""
    counts = [0] * 24
    for ev in events:
        counts[hour_of(ev.ts)] += 1
    return [{"hour": h, "count": counts[h]} for h in range(24)]


class BehaviorService:
    """行为洞察业务层。"""

    def __init__(self, repo: BaseRepository, resolver: EntityResolver,
                 config: Optional[InsightConfig] = None,
                 activities: Optional[ActivityEngine] = None,
                 anomalies: Optional[AnomalyDetector] = None,
                 persona: Optional[PersonaBuilder] = None,
                 reports: Optional[ReportBuilder] = None) -> None:
        self.repo = repo
        self.resolver = resolver
        self.config = config or InsightConfig()
        self.activities = activities or ActivityEngine(resolver, self.config)
        self.anomalies = anomalies or AnomalyDetector(resolver, self.config)
        self.persona = persona or PersonaBuilder(self.config)
        self.reports = reports or ReportBuilder(self.config)
        self.insight_index: Dict[str, Insight] = {}

    # ------------------------------------------------------------------
    # 数据装载
    # ------------------------------------------------------------------
    def load_events(self, tr: TimeRange, entity_ids: Optional[Sequence[str]] = None,
                    room: str = "", category: str = "", query: str = "",
                    behavior_only: bool = True,
                    limit: Optional[int] = None) -> List[EventRecord]:
        """装载窗口内事件；默认排除纯遥测（默认干净）。"""
        ids = list(entity_ids) if entity_ids is not None else None
        if ids is None and (room or category or query):
            ids = self.resolver.resolve_ids(room=room, category=category, query=query)
            if not ids:
                return []
        if ids is None and behavior_only and self.config.exclude_telemetry:
            ids = [e.entity_id for e in self.resolver.behavior_entities()]
            if not ids:
                ids = None  # 目录为空时退回全量 + 启发式过滤
        rows = self.repo.fetch_events(tr.start_ts, tr.end_ts, entity_ids=ids,
                                      limit=limit or self.config.max_scan)
        if behavior_only and self.config.exclude_telemetry:
            rows = [r for r in rows
                    if not self.resolver.is_telemetry(r.entity_id)
                    and normalize_state(r.state) != StateKind.OTHER.value]
        for row in rows:
            self.resolver.enrich(row)
        return rows

    # ------------------------------------------------------------------
    # 使用统计
    # ------------------------------------------------------------------
    def entity_rows(self, events: Sequence[EventRecord],
                    sessions: Dict[str, List[Session]]) -> List[Dict[str, Any]]:
        """按实体聚合：时长、开关次数、活跃天数等（服务端算好再给）。"""
        rows: Dict[str, UsageStat] = {}
        for ev in events:
            stat = rows.get(ev.entity_id)
            if stat is None:
                info = self.resolver.meta(ev.entity_id)
                stat = UsageStat(
                    key=ev.entity_id, label=info.label, room=info.room,
                    entity_id=ev.entity_id, domain=info.domain,
                    category=info.category, unit=info.unit,
                    first=fmt_ts(ev.ts), last=fmt_ts(ev.ts))
                rows[ev.entity_id] = stat
            stat.event_count += 1
            stat.last = fmt_ts(max(as_ts_text(stat.last), ev.ts))
        day_sets: Dict[str, set] = {}
        for ev in events:
            day_sets.setdefault(ev.entity_id, set()).add(ev.day)
        for entity_id, sess in sessions.items():
            stat = rows.get(entity_id)
            if stat is None:
                continue
            stat.sessions = len(sess)
            stat.on_count = len(sess)
            stat.active_minutes = round(sum(s.minutes for s in sess), 1)
            stat.avg_session_minutes = round(
                stat.active_minutes / len(sess), 1) if sess else 0.0
        for entity_id, days in day_sets.items():
            if entity_id in rows:
                rows[entity_id].days_active = len(days)
        return [s.to_dict() for s in
                sorted(rows.values(), key=lambda s: (-s.active_minutes, s.label))]

    def usage(self, tr: TimeRange, room: str = "", category: str = "",
              query: str = "", group_by: str = "entity") -> Dict[str, Any]:
        """设备使用统计（支持 entity / room / domain / category / day 聚合）。"""
        events = self.load_events(tr, room=room, category=category, query=query)
        sessions = compute_sessions(events, tr, self.config.min_session_seconds)
        entity_rows = self.entity_rows(events, sessions)
        rows = self._group(entity_rows, sessions, group_by, tr)
        summary = "窗口 %s 至 %s 共 %d 条记录、%d 个实体，累计活跃 %.1f 分钟" % (
            fmt_ts(tr.start_ts), fmt_ts(tr.end_ts), len(events),
            len(entity_rows), sum(r["active_minutes"] for r in entity_rows))
        return Page.build(rows, group_by=group_by, time_range=tr.to_dict(),
                          summary=summary).to_dict("items")

    def _group(self, entity_rows: List[Dict[str, Any]],
               sessions: Dict[str, List[Session]], group_by: str,
               tr: TimeRange) -> List[Dict[str, Any]]:
        key = (group_by or "entity").lower()
        if key in ("entity", "entities", ""):
            return entity_rows
        if key == "day":
            return self._day_rows(sessions, tr)
        field = {"room": "room", "domain": "domain", "category": "category"}.get(key)
        if not field:
            return entity_rows
        buckets: Dict[str, Dict[str, Any]] = {}
        for row in entity_rows:
            name = row.get(field) or "(未分配)"
            bucket = buckets.setdefault(name, {
                "key": name, "label": name, "friendly_name": name,
                "room": name if field == "room" else row.get("room", ""),
                "entity_id": "", "domain": row.get("domain", ""),
                "category": row.get("category", ""), "sessions": 0, "on_count": 0,
                "active_minutes": 0.0, "avg_session_minutes": 0.0, "days_active": 0,
                "event_count": 0, "entity_count": 0, "first": row.get("first", ""),
                "last": row.get("last", ""), "unit": ""})
            bucket["sessions"] += row["sessions"]
            bucket["on_count"] += row["on_count"]
            bucket["active_minutes"] = round(
                bucket["active_minutes"] + row["active_minutes"], 1)
            bucket["days_active"] = max(bucket["days_active"], row["days_active"])
            bucket["event_count"] += row["event_count"]
            bucket["entity_count"] += 1
        for bucket in buckets.values():
            bucket["avg_session_minutes"] = round(
                bucket["active_minutes"] / bucket["sessions"], 1) if bucket["sessions"] else 0.0
        return sorted(buckets.values(), key=lambda r: (-r["active_minutes"], r["label"]))

    @staticmethod
    def _day_rows(sessions: Dict[str, List[Session]], tr: TimeRange) -> List[Dict[str, Any]]:
        buckets: Dict[str, Dict[str, Any]] = {}
        for day, _s, _e in tr.split_days():
            buckets[day] = {"key": day, "label": day, "friendly_name": day, "room": "",
                            "entity_id": "", "domain": "", "category": "", "day": day,
                            "sessions": 0, "on_count": 0, "active_minutes": 0.0,
                            "avg_session_minutes": 0.0, "days_active": 1,
                            "event_count": 0, "entity_count": 0, "first": "", "last": "",
                            "unit": ""}
        for sess in sessions.values():
            for ses in sess:
                bucket = buckets.setdefault(ses.day, {
                    "key": ses.day, "label": ses.day, "friendly_name": ses.day,
                    "day": ses.day, "sessions": 0, "on_count": 0,
                    "active_minutes": 0.0, "avg_session_minutes": 0.0,
                    "days_active": 1, "event_count": 0, "entity_count": 0})
                bucket["sessions"] += 1
                bucket["on_count"] += 1
                bucket["active_minutes"] = round(bucket["active_minutes"] + ses.minutes, 1)
        for bucket in buckets.values():
            bucket["avg_session_minutes"] = round(
                bucket["active_minutes"] / bucket["sessions"], 1) if bucket["sessions"] else 0.0
        return [buckets[k] for k in sorted(buckets)]

    # ------------------------------------------------------------------
    # 空调 / 净水器
    # ------------------------------------------------------------------
    def climate_sessions(self, query: str = "", room: str = "", tr: Optional[TimeRange] = None,
                         days: int = 7) -> Dict[str, Any]:
        """空调会话统计。"""
        tr = tr or resolve_range(days=days, default_days=self.config.default_days)
        target = query or "空调"
        ids = self.resolver.resolve_ids(room=room, category="climate", query=target) or \
            self.resolver.resolve_ids(room=room, query=target)
        events = self.load_events(tr, entity_ids=ids or [], behavior_only=True)
        sessions = compute_sessions(events, tr, self.config.min_session_seconds)
        rows: List[Dict[str, Any]] = []
        for entity_id, sess in sessions.items():
            for ses in sess:
                rows.append(ses.to_dict())
        rows.sort(key=lambda r: (r["day"], r["start_ts"]))
        total_minutes = round(sum(r["minutes"] for r in rows), 1)
        summary = "共 %d 段空调会话，累计 %.1f 分钟" % (len(rows), total_minutes)
        return Page.build(rows, total_minutes=total_minutes, summary=summary,
                          time_range=tr.to_dict()).to_dict("sessions")

    def water_purifier_usage(self, start: Any, end: Any) -> Dict[str, Any]:
        """净水器使用统计（按天聚合开关次数与时长）。"""
        tr = resolve_range(start, end, default_days=self.config.default_days)
        ids: List[str] = []
        for kw in ("净水", "纯水", "饮水", "purifier", "water"):
            for eid in self.resolver.resolve_ids(query=kw):
                if eid not in ids:
                    ids.append(eid)
        events = self.load_events(tr, entity_ids=ids or [], behavior_only=True)
        sessions = compute_sessions(events, tr, self.config.min_session_seconds)
        buckets: Dict[str, Dict[str, Any]] = {}
        for day, _s, _e in tr.split_days():
            buckets[day] = {"key": day, "label": day, "friendly_name": day,
                            "room": "", "day": day, "uses": 0, "minutes": 0.0}
        for sess in sessions.values():
            for ses in sess:
                bucket = buckets.setdefault(ses.day, {
                    "key": ses.day, "label": ses.day, "friendly_name": ses.day,
                    "day": ses.day, "uses": 0, "minutes": 0.0})
                bucket["uses"] += 1
                bucket["minutes"] = round(bucket["minutes"] + ses.minutes, 1)
        items = [buckets[k] for k in sorted(buckets)]
        total_uses = sum(i["uses"] for i in items)
        summary = "净水器共使用 %d 次，累计 %.1f 分钟" % (
            total_uses, sum(i["minutes"] for i in items))
        return Page.build(items, total_uses=total_uses,
                          total_minutes=round(sum(i["minutes"] for i in items), 1),
                          entities=ids, summary=summary,
                          time_range=tr.to_dict()).to_dict("items")

    # ------------------------------------------------------------------
    # 行为洞察
    # ------------------------------------------------------------------
    def build_insights(self, tr: TimeRange, room: str = "", category: str = "",
                       query: str = "") -> List[Insight]:
        """生成洞察列表（含 explain 所需的 evidence）。"""
        events = self.load_events(tr, room=room, category=category, query=query)
        sessions = compute_sessions(events, tr, self.config.min_session_seconds)
        rows = self.entity_rows(events, sessions)
        insights: List[Insight] = []

        for row in [r for r in rows if r["active_minutes"] > 0][:5]:
            insights.append(Insight(
                insight_id="top_device:%s" % row["entity_id"], type="usage",
                title="最常用设备：%s" % row["label"],
                detail="活跃 %.1f 分钟 / %d 段会话 / %d 天" % (
                    row["active_minutes"], row["sessions"], row["days_active"]),
                room=row["room"], entity_id=row["entity_id"],
                friendly_name=row["label"], score=row["active_minutes"],
                data=dict(row),
                explanation="统计窗口内该设备的「开启->关闭」会话总时长，"
                            "会话由状态归一化后切分得到。"))

        hist = [h for h in hour_histogram(events) if h["count"] > 0]
        for item in sorted(hist, key=lambda h: -h["count"])[:3]:
            insights.append(Insight(
                insight_id="peak_hour:%d" % item["hour"], type="peak_hour",
                title="活动高峰：%02d:00 时段" % item["hour"],
                detail="该时段产生 %d 条行为事件" % item["count"],
                score=float(item["count"]), data=dict(item),
                explanation="按小时统计行为事件数量（已排除功率/温湿度遥测），"
                            "取最高的若干时段。"))

        room_rows = self._group(rows, sessions, "room", tr)
        for row in room_rows[:3]:
            insights.append(Insight(
                insight_id="room_rank:%s" % row["label"], type="room",
                title="最活跃房间：%s" % row["label"],
                detail="活跃 %.1f 分钟，%d 个设备" % (
                    row["active_minutes"], row["entity_count"]),
                room=row["room"] or row["label"], friendly_name=row["label"],
                score=row["active_minutes"], data=dict(row),
                explanation="按房间聚合设备活跃时长后排序。"))

        rhythm = self.rhythm(tr, room=room)
        if rhythm.get("samples"):
            insights.append(Insight(
                insight_id="rhythm:sleep", type="rhythm",
                title="作息：约 %s 入睡、%s 起床" % (rhythm["sleep"], rhythm["wake"]),
                detail="基于 %d 天样本" % rhythm["samples"], score=float(rhythm["samples"]),
                data=rhythm,
                explanation="每天取最后一条行为事件作为入睡参考、第一条作为起床参考，"
                            "再取中位数。"))
        for row in [r for r in rows if r["sessions"] <= 1][:3]:
            insights.append(Insight(
                insight_id="rare:%s" % row["entity_id"], type="rarity",
                title="低频设备：%s" % row["label"],
                detail="窗口内仅 %d 次使用" % row["sessions"],
                room=row["room"], entity_id=row["entity_id"],
                friendly_name=row["label"], score=1.0, data=dict(row),
                explanation="会话次数 <= 1，属于低频设备。"))

        self.insight_index = {i.insight_id: i for i in insights}
        return insights

    def behavior_insights(self, tr: TimeRange, room: str = "", category: str = "",
                          query: str = "") -> Dict[str, Any]:
        """行为洞察（单窗口）。"""
        insights = self.build_insights(tr, room=room, category=category, query=query)
        summary = "窗口内共生成 %d 条洞察" % len(insights)
        return Page.build([i.to_dict() for i in insights], summary=summary,
                          time_range=tr.to_dict()).to_dict("insights")

    def compare_insights(self, compare_days: int = 7,
                         now: Optional[Any] = None) -> Dict[str, Any]:
        """行为洞察（当前窗口 vs 前一个等长窗口）。"""
        days = max(1, int(compare_days or 7))
        current = resolve_range(days=days, now=now,
                                default_days=self.config.default_days, label="本期")
        previous = current.shift(days)
        cur_rows = self.entity_rows(
            self.load_events(current),
            compute_sessions(self.load_events(current), current,
                             self.config.min_session_seconds))
        prev_rows = self.entity_rows(
            self.load_events(previous),
            compute_sessions(self.load_events(previous), previous,
                             self.config.min_session_seconds))
        prev_map = {r["entity_id"]: r for r in prev_rows}
        insights = self.build_insights(current)
        comparisons: List[Dict[str, Any]] = []
        for row in cur_rows[:5]:
            before = prev_map.get(row["entity_id"], {}).get("active_minutes", 0.0)
            delta = round(row["active_minutes"] - before, 1)
            rate = (round(delta / before * 100, 1) if before else None)
            comparisons.append({
                "entity_id": row["entity_id"], "label": row["label"],
                "room": row["room"], "friendly_name": row["label"],
                "current_minutes": row["active_minutes"], "previous_minutes": before,
                "delta_minutes": delta, "delta_rate": rate,
                "trend": "up" if delta > 0 else ("down" if delta < 0 else "flat"),
            })
        summary = "对比最近 %d 天与前 %d 天：共 %d 个设备发生变化" % (
            days, days, sum(1 for c in comparisons if c["trend"] != "flat"))
        return Page.build([i.to_dict() for i in insights], compare_days=days,
                          summary=summary, current=current.to_dict(),
                          previous=previous.to_dict(),
                          comparisons=comparisons).to_dict("insights")

    # ------------------------------------------------------------------
    # 作息 / 覆盖率
    # ------------------------------------------------------------------
    def rhythm(self, tr: TimeRange, room: str = "",
               entity_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """作息规律：起床 / 入睡时间（中位数）。"""
        events = self.load_events(tr, entity_ids=entity_ids, room=room,
                                  behavior_only=True)
        per_day: Dict[str, List[EventRecord]] = {}
        for ev in events:
            per_day.setdefault(ev.day, []).append(ev)
        wakes: List[float] = []
        sleeps: List[float] = []
        details: List[Dict[str, Any]] = []
        for day, evs in sorted(per_day.items()):
            evs.sort(key=lambda e: e.ts)
            wake = next((e for e in evs
                         if self.config.wake_from <= e.hour <= self.config.wake_to), None)
            sleep_candidates = [e for e in evs if e.hour >= self.config.sleep_from]
            sleep = sleep_candidates[-1] if sleep_candidates else evs[-1]
            if wake:
                wakes.append(wake.ts - _day_start_ts(wake.ts))
            sleeps.append(sleep.ts - _day_start_ts(sleep.ts))
            details.append({"day": day,
                            "wake": fmt_ts(wake.ts) if wake else "",
                            "sleep": fmt_ts(sleep.ts)})
        return {
            "wake": _fmt_seconds_of_day(median(wakes)) if wakes else "",
            "sleep": _fmt_seconds_of_day(median(sleeps)) if sleeps else "",
            "wake_seconds": median(wakes) if wakes else None,
            "sleep_seconds": median(sleeps) if sleeps else None,
            "samples": len(details),
            "days": details,
            "time_range": tr.to_dict(),
        }

    def coverage(self, tr: TimeRange) -> Dict[str, Any]:
        """数据覆盖率（按天 / 按小时）。"""
        events = self.load_events(tr, behavior_only=False)
        per_day: Dict[str, Dict[str, Any]] = {}
        for day, _s, _e in tr.split_days():
            per_day[day] = {"day": day, "label": day, "friendly_name": day,
                            "events": 0, "behavior_events": 0, "telemetry_events": 0,
                            "entities": set(), "hours": set()}
        behavior_entities = {e.entity_id for e in self.resolver.behavior_entities()}
        for ev in events:
            bucket = per_day.setdefault(ev.day, {
                "day": ev.day, "label": ev.day, "events": 0, "behavior_events": 0,
                "telemetry_events": 0, "entities": set(), "hours": set()})
            bucket["events"] += 1
            is_behavior = (not behavior_entities) or ev.entity_id in behavior_entities
            bucket["behavior_events" if is_behavior else "telemetry_events"] += 1
            bucket["entities"].add(ev.entity_id)
            bucket["hours"].add(ev.hour)
        items = []
        total_hours = 0
        for day in sorted(per_day):
            bucket = per_day[day]
            total_hours += len(bucket["hours"])
            items.append({"day": day, "label": day, "friendly_name": day,
                          "events": bucket["events"],
                          "behavior_events": bucket["behavior_events"],
                          "telemetry_events": bucket["telemetry_events"],
                          "entities": len(bucket["entities"]),
                          "hours": len(bucket["hours"]),
                          "hour_coverage": round(len(bucket["hours"]) / 24.0, 3)})
        day_count = max(1, len(items))
        hour_cov = round(total_hours / float(24 * day_count), 3)
        day_cov = round(sum(1 for i in items if i["events"] > 0) / float(day_count), 3)
        return Page.build(items, hour_coverage=hour_cov, day_coverage=day_cov,
                          total_hours=total_hours, expected_hours=24 * day_count,
                          total_events=sum(i["events"] for i in items),
                          behavior_events=sum(i["behavior_events"] for i in items),
                          telemetry_events=sum(i["telemetry_events"] for i in items),
                          entities_seen=len({e.entity_id for e in events}),
                          time_range=tr.to_dict()).to_dict("days")

    # ------------------------------------------------------------------
    # 专项模块调度
    # ------------------------------------------------------------------
    def infer_activities(self, tr: TimeRange, rooms: str = "",
                         activities: Any = None) -> Dict[str, Any]:
        events = self.load_events(tr, behavior_only=True)
        matches = self.activities.infer(events, tr, rooms=rooms, activities=activities)
        summary = "推断到 %d 次活动" % len(matches)
        return Page.build([m.to_dict() for m in matches], summary=summary,
                          time_range=tr.to_dict()).to_dict("activities")

    def anomaly_report(self, tr: TimeRange, room: str = "", category: str = "",
                       query: str = "") -> Dict[str, Any]:
        ids = self.resolver.resolve_ids(room=room, category=category, query=query) \
            if (room or category or query) else None
        events = self.load_events(tr, entity_ids=ids, behavior_only=False)
        sessions = compute_sessions(events, tr, self.config.min_session_seconds)
        return self.anomalies.report(events, tr, sessions=sessions,
                                     entities=self._entities_for(ids),
                                     last_seen=self.repo.last_seen(ids))

    def device_health(self, tr: TimeRange, room: str = "", category: str = "",
                      query: str = "") -> Dict[str, Any]:
        ids = self.resolver.resolve_ids(room=room, category=category, query=query) \
            if (room or category or query) else None
        events = self.load_events(tr, entity_ids=ids, behavior_only=False)
        rows = self.anomalies.device_health(
            events, tr, entities=self._entities_for(ids),
            last_seen_map=self.repo.last_seen(ids))
        return Page.build(rows, time_range=tr.to_dict()).to_dict("devices")

    def data_quality_issues(self, tr: TimeRange, limit: int = 30000) -> Dict[str, Any]:
        events = self.load_events(tr, behavior_only=False, limit=limit)
        issues = self.anomalies.data_quality(events, tr)
        return Page.build([i.to_dict() for i in issues], limit=limit,
                          scanned=len(events), time_range=tr.to_dict()
                          ).to_dict("issues")

    def data_quality(self, tr: TimeRange) -> Dict[str, Any]:
        """数据质量综合评分。"""
        coverage = self.coverage(tr)
        events = self.load_events(tr, behavior_only=False)
        issues = self.anomalies.data_quality(events, tr)
        weights = {"high": 15, "medium": 8, "low": 3, "info": 1}
        penalty = sum(weights.get(i.severity, 3) for i in issues)
        penalty += int(max(0.0, 1.0 - coverage["hour_coverage"]) * 30)
        score = max(0, min(100, 100 - penalty))
        grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
        summary = "数据质量 %d 分（%s），共 %d 个问题，小时覆盖率 %.0f%%" % (
            score, grade, len(issues), coverage["hour_coverage"] * 100)
        return Page.build([i.to_dict() for i in issues], score=score, grade=grade,
                          summary=summary, coverage=coverage).to_dict("issues")

    def user_persona(self, days: int = 14) -> Dict[str, Any]:
        """用户画像（综合使用统计、作息、活动、异常）。"""
        tr = resolve_range(days=days, default_days=days)
        usage = self.usage(tr, group_by="entity")["items"]
        rooms = self.usage(tr, group_by="room")["items"]
        rhythm = self.rhythm(tr)
        activity_data = self.infer_activities(tr)
        anomalies = self.anomaly_report(tr)
        coverage = self.coverage(tr)
        persona = self.persona.build(days=days, usage_rows=usage, room_rows=rooms,
                                     rhythm=rhythm, activities=activity_data["activities"],
                                     anomalies=anomalies["anomalies"], coverage=coverage)
        return Page.build([persona], persona=persona,
                          summary=persona.get("summary", ""),
                          days=days).to_dict("persona")

    def explain_insight(self, insight_id: str) -> Dict[str, Any]:
        """解释一条洞察（含证据链）。"""
        return self.persona.explain(insight_id, self.insight_index)

    def _entities_for(self, ids: Optional[Sequence[str]]) -> Optional[List[Any]]:
        if ids is None:
            return None
        return [self.resolver.meta(eid) for eid in ids]


def as_ts_text(text: str) -> float:
    """'YYYY-MM-DD HH:MM:SS' -> 时间戳（解析失败返回 0）。"""
    from .parser.timeframe import as_ts as _as_ts
    value = _as_ts(text)
    return float(value or 0.0)


def _day_start_ts(ts: float) -> float:
    from datetime import datetime
    dt = datetime.fromtimestamp(ts).replace(hour=0, minute=0, second=0, microsecond=0)
    return dt.timestamp()


def _fmt_seconds_of_day(seconds: float) -> str:
    seconds = int(seconds) % 86400
    return "%02d:%02d" % (seconds // 3600, (seconds % 3600) // 60)
