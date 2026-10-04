"""Insights 框架 · 业务服务层（BehaviorService）

把 StoreRepository 的行数据加工成 api.py 需要的 dict（api.py 只做分页信封包装）。

失败语义：
  * repository 层抛异常（fail-closed）；
  * 本层在方法边界兜住，返回 {"ok": False, "method", "error", ...默认骨架}（fail-open），
    保证 api.py 永远拿得到 dict、且关键键不缺位。

本层不依赖 models.TimeRange 的构造器（签名未在契约给出），
内部用 _Window 自建窗口；对外只按 start_iso/end_iso/start_ts/end_ts 形状取值，
因此真实的 TimeRange 传入同样工作。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

from .activity import ActivityEngine, ActivityRule, BUILTIN_ACTIVITIES, Signal
from .models import fmt_ts, house_dt, house_ts
from .repository import _to_epoch, _to_iso

__all__ = ["BehaviorService", "CATEGORY_DOMAINS", "WEEKDAY_NAMES", "compute_sessions"]

_LOG = logging.getLogger("insights.service")


def compute_sessions(events: Any, tr: Any = None,
                     min_session_seconds: float = 1.0) -> Dict[str, list]:
    """把事件流切成 on→off 使用会话，返回 {entity_id: [Session, ...]}。

    - 用 parser.entity.normalize_state 判定开关口径（与新框架一致）；
    - 窗口结束仍处于 on 的会话 open=True，end 取 tr.end_ts；
    - 短于 min_session_seconds 的会话丢弃（去抖）；
    - anomaly.py / activity.py 共用此函数（之前 import 缺失，调用即 ImportError）。
    """
    from .models import Session
    from .parser.entity import normalize_state

    grouped: Dict[str, list] = {}
    for ev in events or []:
        grouped.setdefault(ev.entity_id, []).append(ev)

    out: Dict[str, list] = {}
    threshold = float(min_session_seconds or 0.0)
    for eid, evs in grouped.items():
        evs = sorted(evs, key=lambda e: e.ts)
        sessions: List[Session] = []
        open_start: Optional[float] = None
        open_info: Any = None
        for ev in evs:
            kind = normalize_state(ev.state)
            if kind == "on" and open_start is None:
                open_start = ev.ts
                open_info = ev
            elif kind == "off" and open_start is not None:
                dur = ev.ts - open_start
                if dur >= threshold:
                    sessions.append(Session(
                        entity_id=eid, start=open_start, end=ev.ts,
                        friendly_name=ev.friendly_name, room=ev.room,
                        domain=ev.domain, open=False))
                open_start = None
                open_info = None
        if open_start is not None:
            end_ts = tr.end_ts if (tr is not None and hasattr(tr, "end_ts")) else evs[-1].ts
            if end_ts - open_start >= threshold:
                sessions.append(Session(
                    entity_id=eid, start=open_start, end=end_ts,
                    friendly_name=open_info.friendly_name if open_info else "",
                    room=open_info.room if open_info else "",
                    domain=open_info.domain if open_info else "", open=True))
        if sessions:
            out[eid] = sessions
    return out

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

#: category -> domain 集合（events 无 category 列，只能从 entity_id/domain 推断）
CATEGORY_DOMAINS = {
    "light": {"light"},
    "lighting": {"light", "switch"},
    "lights": {"light", "switch"},
    "switch": {"switch", "input_boolean"},
    "switches": {"switch", "input_boolean"},
    "climate": {"climate", "humidifier", "water_heater", "fan"},
    "media": {"media_player", "remote"},
    "security": {"lock", "alarm_control_panel", "camera"},
    "sensor": {"sensor", "binary_sensor"},
    "sensors": {"sensor", "binary_sensor"},
    "cover": {"cover"},
}


# ------------------------------------------------------------------ 工具函数
def _split(value: Any) -> List[str]:
    """None/"" -> []；字符串按逗号分割；可迭代按元素收集。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def _pct(part: float, whole: float) -> float:
    return round(part / whole, 4) if whole else 0.0


def _iter_days(start_day: str, end_day: str):
    try:
        cur = datetime.strptime(start_day, "%Y-%m-%d")
        end = datetime.strptime(end_day, "%Y-%m-%d")
    except (TypeError, ValueError):
        return
    while cur <= end:
        yield cur.strftime("%Y-%m-%d")
        cur = cur + timedelta(days=1)


def _weekday_index(day: str) -> Optional[int]:
    try:
        return datetime.strptime(day, "%Y-%m-%d").weekday()
    except (TypeError, ValueError):
        return None


def _label_activity(start_hour: int, end_hour: int, dominant: str) -> str:
    """把一个连续活跃时段贴上人类可读标签（规则透明、可核对）。"""
    if end_hour < 6:
        return "夜间活动"
    if start_hour < 9:
        return "晨间活动"
    if start_hour < 18:
        return "日间活动"
    if dominant == "media_player":
        return "晚间娱乐"
    if dominant in ("light", "switch"):
        return "晚间照明/开关调整"
    return "晚间活动"


def _longest_quiet(hourly: Dict[int, int], from_hour: int, to_hour: int) -> Tuple[int, int]:
    """[from_hour, to_hour] 内最长连续 0 事件时段，返回 (长度, 起始小时)。"""
    best = 0
    best_at = from_hour
    run = 0
    start = from_hour
    for h in range(from_hour, to_hour + 1):
        if int(hourly.get(h, 0)) == 0:
            if run == 0:
                start = h
            run += 1
            if run > best:
                best = run
                best_at = start
        else:
            run = 0
    return best, best_at


def _safe_hour(value: Any, default: int) -> int:
    """规则表里的小时列可能是 None/字符串/越界值，取整并夹到 [0, 23]。"""
    try:
        hour = int(value)
    except (TypeError, ValueError):
        return int(default)
    return hour if 0 <= hour <= 23 else int(default)


def _day_hour_ts(day: str, hour: int) -> float:
    """'YYYY-MM-DD' + 小时 -> epoch（家庭墙钟口径，与 `day_key` 一致）。"""
    try:
        return house_ts(datetime.strptime("%s %02d:00:00" % (day, int(hour)), "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return 0.0


def _allow_hit(name: Any, activity: Any, allow: List[str]) -> bool:
    """`activities` 白名单命中判据：中文名 / 英文键 / 标签任一相等（大小写不敏感）。"""
    wanted = {str(a).strip().lower() for a in allow if str(a).strip()}
    if not wanted:
        return True
    return any(str(v or "").strip().lower() in wanted for v in (name, activity))


def _semantic_evidence(match: Any) -> str:
    """把规则命中写成可核对的证据串（legacy 每条活动都带 evidence，这里补同一口径）。"""
    parts: List[str] = []
    for sig in getattr(match, "signals", []) or []:
        label = sig.get("room") or sig.get("category") or sig.get("query") or "-"
        detail = str(sig.get("query") or sig.get("category") or "").strip()
        parts.append("%s%s %.1f 分钟/%d 段%s" % (
            label, ("「%s」" % detail) if detail else "",
            float(sig.get("minutes") or 0.0), int(sig.get("count") or 0),
            "" if sig.get("satisfied") else "（未达标）"))
    origin = "自定义规则" if getattr(match, "source", "") == "activity_rules" else "内置语义规则"
    return "%s「%s」命中：%s" % (origin, getattr(match, "name", ""), "；".join(parts) or "无信号")


def _window_meta(tr: Any) -> Dict[str, Any]:
    """窗口回显：优先用 TimeRange 自己的 to_dict，退化到 ISO 串（legacy 的 `window` 键）。"""
    to_dict = getattr(tr, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict() or {})
    return {"start": str(getattr(tr, "start_iso", "") or ""),
            "end": str(getattr(tr, "end_iso", "") or "")}


class _Window:
    """TimeRange 的最小替身（duck-typing），供 compare_insights / user_persona 自建窗口。"""

    __slots__ = ("start_ts", "end_ts", "start_iso", "end_iso", "days")

    def __init__(self, start_ts: float, end_ts: float) -> None:
        self.start_ts = float(start_ts)
        self.end_ts = float(end_ts)
        self.start_iso = _to_iso(self.start_ts)
        self.end_iso = _to_iso(self.end_ts)
        self.days = max(1, int(round((self.end_ts - self.start_ts) / 86400.0)))

    def split_days(self):
        for day in _iter_days(self.start_iso[:10], self.end_iso[:10]):
            day_start = datetime.strptime(day, "%Y-%m-%d")
            day_end = day_start + timedelta(days=1)
            yield (day, max(house_ts(day_start), self.start_ts),
                   min(house_ts(day_end), self.end_ts))


class _ResolverAdapter:
    """对 resolver 的最小假设 + 防御性探测。

    支持形态：
      1) resolver.resolve / lookup / describe / get / entity(entity_id) -> dict 或对象
      2) Mapping 形态：resolver[entity_id]
    全都取不到就退回 entity_id 推断（domain = entity_id.split('.')[0]）。
    """

    _PROBE = ("resolve", "lookup", "describe", "get", "entity")
    _CAT_PROBE = ("domains_for_category", "domains_for", "category_domains", "domains_of")

    def __init__(self, resolver: Any) -> None:
        self.resolver = resolver

    def meta(self, entity_id: str) -> Dict[str, str]:
        out = {"entity_id": entity_id, "friendly_name": "", "unit": "",
               "category": "", "room": ""}
        res = self.resolver
        if res is None:
            return out
        obj = None
        for name in self._PROBE:
            fn = getattr(res, name, None)
            if not callable(fn):
                continue
            try:
                obj = fn(entity_id)
            except Exception:
                obj = None
            if obj:
                break
        if obj is None:
            try:
                if hasattr(res, "__contains__") and entity_id in res:
                    obj = res[entity_id]
            except Exception:
                obj = None
        if obj is None:
            return out
        if isinstance(obj, dict):
            data = obj
        else:
            data = {}
            for key in ("friendly_name", "name", "unit", "unit_of_measurement",
                        "category", "domain", "room"):
                if hasattr(obj, key):
                    data[key] = getattr(obj, key)
        out["friendly_name"] = str(data.get("friendly_name") or data.get("name") or "")
        out["unit"] = str(data.get("unit") or data.get("unit_of_measurement") or "")
        out["category"] = str(data.get("category") or data.get("domain") or "")
        out["room"] = str(data.get("room") or "")
        return out

    def category_domains(self, category: str) -> set:
        text = (category or "").strip()
        if not text:
            return set()
        res = self.resolver
        for name in self._CAT_PROBE:
            fn = getattr(res, name, None) if res is not None else None
            if not callable(fn):
                continue
            try:
                got = fn(text)
            except Exception:
                got = None
            if got:
                return {got} if isinstance(got, str) else set(got)
        mapped = CATEGORY_DOMAINS.get(text.lower())
        if mapped:
            return set(mapped)
        return {text}


class BehaviorService:
    """业务服务层：api.py 持有 self.core = BehaviorService(repo, resolver, config)。"""

    def __init__(self, repo: Any, resolver: Any, config: Any) -> None:
        self.repo = repo
        self.resolver = resolver
        self.config = config
        self.log = _LOG
        self._adapter = _ResolverAdapter(resolver)

    # ------------------------------------------------------------- 公共骨架
    def _now(self) -> float:
        """可被测试替换的时钟接缝（不改 __init__ 签名）。"""
        return datetime.now().timestamp()

    def _fail(self, method: str, exc: Exception, defaults: Dict[str, Any]) -> Dict[str, Any]:
        self.log.exception("%s 执行失败", method)
        out = dict(defaults)
        out["ok"] = False
        out["method"] = method
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
        return out

    def _tr_days(self, tr: Any) -> Tuple[str, str]:
        start_iso = getattr(tr, "start_iso", "") or ""
        end_iso = getattr(tr, "end_iso", "") or ""
        if not start_iso and hasattr(tr, "start_ts"):
            start_iso = _to_iso(tr.start_ts)
        if not end_iso and hasattr(tr, "end_ts"):
            end_iso = _to_iso(tr.end_ts)
        if not start_iso or not end_iso:
            raise TypeError("tr 缺少 start_iso/end_iso（或 start_ts/end_ts）")
        return start_iso[:10], end_iso[:10]

    def _filters(self, room: Any = "", category: str = "", entity_id: Any = ""):
        return _split(room), sorted(self._adapter.category_domains(category)), _split(entity_id)

    def _filter_echo(self, room, category, entity_id, rooms, domains, entity_ids):
        return {"room": room, "category": category, "entity_id": entity_id,
                "rooms": rooms, "domains": domains, "entity_ids": entity_ids}

    # ------------------------------------------------------------- coverage
    def coverage(self, tr: Any) -> Dict[str, Any]:
        try:
            out = self._coverage(tr)
            out["ok"] = True
            return out
        except Exception as exc:
            return self._fail("coverage", exc, {
                "days": [], "total_events": 0, "day_coverage": 0.0, "hour_coverage": 0.0})

    def _coverage(self, tr: Any) -> Dict[str, Any]:
        start_day, end_day = self._tr_days(tr)
        all_days = list(_iter_days(start_day, end_day))
        total_days = max(1, len(all_days))
        day_counts = self.repo.day_counts(tr)
        matrix = self.repo.activity_matrix(tr)

        hours_by_day: Dict[str, set] = {}
        hour_totals = [0] * 24
        for row in matrix:
            day = str(row.get("day") or "")
            hour = int(row.get("hour") or 0)
            cnt = int(row.get("count") or 0)
            if 0 <= hour < 24:
                hours_by_day.setdefault(day, set()).add(hour)
                hour_totals[hour] += cnt

        total_events = int(sum(day_counts.values()))
        day_list = []
        for day in all_days:
            ev = int(day_counts.get(day, 0))
            hours = sorted(hours_by_day.get(day, ()))
            day_list.append({"day": day, "events": ev, "active_hours": len(hours),
                             "hours": hours, "empty": ev == 0})
        active_days = sum(1 for d in all_days if day_counts.get(d))
        covered_hours = sum(len(v) for v in hours_by_day.values())
        peak = sorted(range(24), key=lambda h: hour_totals[h], reverse=True)[:3]
        return {
            "days": day_list,
            "total_events": total_events,
            "day_coverage": _pct(active_days, total_days),
            "hour_coverage": _pct(covered_hours, total_days * 24),
            "active_days": active_days,
            "total_days": total_days,
            "start_day": start_day,
            "end_day": end_day,
            "start_iso": getattr(tr, "start_iso", "") or "",
            "end_iso": getattr(tr, "end_iso", "") or "",
            "missing_days": [d["day"] for d in day_list if d["empty"]],
            "hourly": [{"hour": h, "count": hour_totals[h]} for h in range(24)],
            "peak_hours": peak,
        }

    # ----------------------------------------------------------------- usage
    def usage(self, tr: Any, room: str = "", category: str = "", entity_id: str = "") -> Dict[str, Any]:
        try:
            out = self._usage(tr, room, category, entity_id)
            out["ok"] = True
            return out
        except Exception as exc:
            return self._fail("usage", exc, {
                "items": [], "total_events": 0, "total_entities": 0,
                "by_room": [], "by_domain": [], "filters": {}})

    def _usage(self, tr: Any, room: str, category: str, entity_id: str) -> Dict[str, Any]:
        rooms, domains, entity_ids = self._filters(room, category, entity_id)
        stats = self.repo.entity_stats(tr, entity_ids=entity_ids, rooms=rooms, domains=domains)
        total_events = int(sum(int(s.get("count", 0)) for s in stats))
        span_days = max(1, len(list(_iter_days(*self._tr_days(tr)))))

        by_room: Dict[str, int] = {}
        by_domain: Dict[str, int] = {}
        items = []
        for s in stats:
            cnt = int(s.get("count", 0))
            room_key = s.get("room") or "(未标注)"
            domain_key = s.get("domain") or "(未标注)"
            by_room[room_key] = by_room.get(room_key, 0) + cnt
            by_domain[domain_key] = by_domain.get(domain_key, 0) + cnt
            meta = self._adapter.meta(s.get("entity_id") or "")
            items.append({
                "entity_id": s.get("entity_id") or "",
                "friendly_name": meta["friendly_name"] or s.get("entity_id") or "",
                "room": s.get("room") or "",
                "domain": s.get("domain") or "",
                "category": meta["category"] or s.get("domain") or "",
                "unit": meta["unit"],
                "count": cnt,
                "share": _pct(cnt, total_events),
                "active_days": int(s.get("active_days", 0)),
                "avg_per_day": round(cnt / max(1, int(s.get("active_days", 0))), 2),
                "first_ts": s.get("first_ts") or "",
                "last_ts": s.get("last_ts") or "",
            })
        items.sort(key=lambda x: (-x["count"], x["entity_id"]))
        limit = int(getattr(self.config, "default_limit", 100) or 100)
        out: Dict[str, Any] = {
            "items": items[:limit],
            "total_entities": len(stats),
            "total_events": total_events,
            "span_days": span_days,
            "truncated": len(items) > limit,
            "by_room": [{"room": k, "count": v, "share": _pct(v, total_events)}
                        for k, v in sorted(by_room.items(), key=lambda kv: (-kv[1], kv[0]))],
            "by_domain": [{"domain": k, "count": v, "share": _pct(v, total_events)}
                          for k, v in sorted(by_domain.items(), key=lambda kv: (-kv[1], kv[0]))],
            "filters": self._filter_echo(room, category, entity_id, rooms, domains, entity_ids),
        }
        if len(entity_ids) == 1:
            out["states"] = self.repo.state_counts(tr, entity_id=entity_ids[0])
            out["actions"] = self.repo.action_counts(tr, entity_ids=entity_ids)
        return out

    # --------------------------------------------------------- device_health
    def device_health(self, tr: Any, room: str = "", category: str = "") -> Dict[str, Any]:
        try:
            out = self._device_health(tr, room, category)
            out["ok"] = True
            return out
        except Exception as exc:
            return self._fail("device_health", exc, {
                "items": [], "summary": {"active": 0, "idle": 0, "offline": 0, "total": 0},
                "filters": {}})

    def _device_health(self, tr: Any, room: str, category: str) -> Dict[str, Any]:
        rooms, domains, entity_ids = self._filters(room, category)
        stats = self.repo.entity_stats(tr, entity_ids=entity_ids, rooms=rooms, domains=domains)
        catalog = self.repo.entity_catalog(rooms=rooms, domains=domains)
        offline_days = int(getattr(self.config, "offline_days", 3) or 3)
        ref = self._now()

        rows: Dict[str, Dict[str, Any]] = {}
        for c in catalog:
            eid = c.get("entity_id") or ""
            rows[eid] = {"entity_id": eid, "room": c.get("room") or "",
                         "domain": c.get("domain") or "", "total_events": int(c.get("total", 0)),
                         "last_ts": c.get("last_ts") or "", "count": 0, "active_days": 0,
                         "first_ts": "", "in_window": False}
        for s in stats:
            eid = s.get("entity_id") or ""
            row = rows.setdefault(eid, {"entity_id": eid, "room": s.get("room") or "",
                                        "domain": s.get("domain") or "", "total_events": 0,
                                        "last_ts": "", "count": 0, "active_days": 0,
                                        "first_ts": "", "in_window": False})
            row.update({"room": s.get("room") or row["room"],
                        "domain": s.get("domain") or row["domain"],
                        "count": int(s.get("count", 0)),
                        "active_days": int(s.get("active_days", 0)),
                        "first_ts": s.get("first_ts") or "", "in_window": True})
            if (s.get("last_ts") or "") > (row.get("last_ts") or ""):
                row["last_ts"] = s.get("last_ts") or ""

        items = []
        for row in rows.values():
            last_iso = row.get("last_ts") or ""
            last_epoch = _to_epoch(last_iso) if last_iso else 0.0
            idle_hours = round((ref - last_epoch) / 3600.0, 2) if last_epoch else None
            if row["count"] > 0:
                status = "active"
            elif idle_hours is not None and idle_hours <= offline_days * 24:
                status = "idle"
            else:
                status = "offline"
            meta = self._adapter.meta(row["entity_id"])
            items.append({
                "entity_id": row["entity_id"],
                "friendly_name": meta["friendly_name"] or row["entity_id"],
                "room": row["room"], "domain": row["domain"],
                "category": meta["category"] or row["domain"],
                "count": row["count"], "active_days": row["active_days"],
                "first_ts": row["first_ts"], "last_ts": row["last_ts"],
                "total_events": row["total_events"],
                "in_window": row["in_window"],
                "idle_hours": idle_hours,
                "idle_days": round(idle_hours / 24.0, 2) if idle_hours is not None else None,
                "status": status,
            })
        items.sort(key=lambda x: (x["status"] != "active", -x["count"], x["entity_id"]))
        summary = {"active": 0, "idle": 0, "offline": 0, "total": len(items)}
        for it in items:
            summary[it["status"]] = summary.get(it["status"], 0) + 1
        return {
            "items": items,
            "summary": summary,
            "offline_days": offline_days,
            "reference_ts": _to_iso(ref),
            "filters": self._filter_echo(room, category, "", rooms, domains, entity_ids),
        }

    # -------------------------------------------------------- anomaly_report
    def anomaly_report(self, tr: Any, room: str = "", category: str = "") -> Dict[str, Any]:
        try:
            out = self._anomaly_report(tr, room, category)
            out["ok"] = True
            return out
        except Exception as exc:
            return self._fail("anomaly_report", exc, {
                "anomalies": [], "summary": {"count": 0, "by_type": {}, "by_severity": {}},
                "filters": {}})

    def _anomaly_report(self, tr: Any, room: str, category: str) -> Dict[str, Any]:
        rooms, domains, entity_ids = self._filters(room, category)
        start_day, end_day = self._tr_days(tr)
        all_days = list(_iter_days(start_day, end_day))
        day_counts = self.repo.day_counts(tr, entity_ids=entity_ids, rooms=rooms, domains=domains)
        matrix = self.repo.activity_matrix(tr, entity_ids=entity_ids, rooms=rooms, domains=domains)
        stats = self.repo.entity_stats(tr, entity_ids=entity_ids, rooms=rooms, domains=domains)
        quality = self.repo.quality_counts(tr, rooms=rooms)

        anomalies: List[Dict[str, Any]] = []
        counts = [int(day_counts.get(d, 0)) for d in all_days]
        total_events = int(sum(counts))
        med = float(median(counts)) if counts else 0.0
        threshold = max(3.0 * med, med + 10.0)

        # 规则 1：单日事件量尖峰（中位数稳健阈值）
        for d in all_days:
            v = int(day_counts.get(d, 0))
            if v > threshold:
                anomalies.append({
                    "type": "event_spike", "severity": "high" if v >= 5 * max(1.0, med) else "medium",
                    "day": d, "entity_id": "", "value": v, "baseline": round(threshold, 2),
                    "message": "%s 事件量 %d 显著高于中位数 %.1f（阈值 %.1f）" % (d, v, med, threshold),
                    "suggestion": "检查设备抖动 / 传感器噪声 / 批量导入",
                })
        # 规则 2：数据缺口
        for d in all_days:
            if int(day_counts.get(d, 0)) == 0:
                anomalies.append({
                    "type": "data_gap", "severity": "medium", "day": d, "entity_id": "",
                    "value": 0, "baseline": round(med, 2),
                    "message": "%s 无任何事件" % d,
                    "suggestion": "确认采集链路当天是否停摆",
                })
        # 规则 3：深夜（00-06 点）活动占比过高
        matrix_total = sum(int(r.get("count", 0)) for r in matrix)
        night = sum(int(r.get("count", 0)) for r in matrix if int(r.get("hour", 0)) < 6)
        if matrix_total > 0 and night / matrix_total > 0.15:
            anomalies.append({
                "type": "night_activity", "severity": "medium", "day": "", "entity_id": "",
                "value": night, "baseline": round(0.15 * matrix_total, 2),
                "message": "深夜时段事件 %d 条，占比 %.1f%%" % (night, 100.0 * night / matrix_total),
                "suggestion": "核对夜间是否有异常触发或定时任务",
            })
        # 规则 4：噪声实体（单实体占比超过 noise_ratio_cap）
        cap = float(getattr(self.config, "noise_ratio_cap", 0.6) or 0.6)
        if stats and matrix_total > 0:
            top = max(stats, key=lambda s: int(s.get("count", 0)))
            ratio = int(top.get("count", 0)) / matrix_total
            if ratio > cap:
                anomalies.append({
                    "type": "noise_entity", "severity": "high", "day": "",
                    "entity_id": top.get("entity_id") or "",
                    "value": round(ratio, 4), "baseline": cap,
                    "message": "%s 占全部事件 %.1f%%，超过噪声上限 %.2f"
                               % (top.get("entity_id"), 100.0 * ratio, cap),
                    "suggestion": "降采样或从行为分析中排除该实体",
                })
        # 规则 5：开关状态抖动
        flap: Dict[str, int] = {}
        for row in self.repo.entity_action_counts(tr, entity_ids=entity_ids, rooms=rooms, domains=domains):
            act = (row.get("action") or "").lower()
            if act in ("turned_on", "turned_off", "on", "off"):
                eid = row.get("entity_id") or ""
                flap[eid] = flap.get(eid, 0) + int(row.get("count", 0))
        if flap:
            avg_flap = sum(flap.values()) / float(len(flap))
            for eid, v in flap.items():
                if v >= 20 and v >= 3 * max(1.0, avg_flap):
                    anomalies.append({
                        "type": "state_flapping", "severity": "medium", "day": "", "entity_id": eid,
                        "value": v, "baseline": round(avg_flap, 2),
                        "message": "%s 开关动作 %d 次，远高于均值 %.1f" % (eid, v, avg_flap),
                        "suggestion": "检查自动化循环触发或硬件故障",
                    })
        # 规则 6：字段缺失
        for field, key in (("room", "empty_room"), ("domain", "empty_domain"),
                           ("new_state", "empty_state")):
            v = int(quality.get(key, 0))
            if v > 0:
                anomalies.append({
                    "type": "missing_fields", "severity": "low", "day": "", "entity_id": "",
                    "value": v, "baseline": 0,
                    "message": "%d 条事件缺 %s" % (v, field), "field": field,
                    "suggestion": "回填元数据或修正采集端",
                })

        by_type: Dict[str, int] = {}
        by_sev: Dict[str, int] = {}
        for a in anomalies:
            by_type[a["type"]] = by_type.get(a["type"], 0) + 1
            by_sev[a["severity"]] = by_sev.get(a["severity"], 0) + 1
        return {
            "anomalies": anomalies,
            "summary": {"count": len(anomalies), "by_type": by_type, "by_severity": by_sev,
                        "total_events": total_events, "days": len(all_days),
                        "median_daily": round(med, 2), "threshold": round(threshold, 2)},
            "filters": self._filter_echo(room, category, "", rooms, domains, entity_ids),
        }

    # ----------------------------------------------------- behavior_insights
    def behavior_insights(self, tr: Any, room: str = "", category: str = "") -> Dict[str, Any]:
        try:
            out = self._behavior_insights(tr, room, category)
            out["ok"] = True
            return out
        except Exception as exc:
            return self._fail("behavior_insights", exc, {
                "summary": {"behavior_events": 0}, "by_trigger": [], "by_room": [],
                "top_actions": [], "hourly": [], "days": [], "perception": {"total": 0},
                "filters": {}})

    def _behavior_insights(self, tr: Any, room: str, category: str) -> Dict[str, Any]:
        rooms = _split(room)
        summary = self.repo.behavior_summary(tr, rooms=rooms)
        actions = self.repo.behavior_actions(tr, rooms=rooms, limit=20)
        hourly_map = self.repo.behavior_hourly(tr, rooms=rooms)
        days_map = self.repo.behavior_days(tr, rooms=rooms)
        perception = self.repo.perception_summary(tr, rooms=rooms)

        total = sum(int(r.get("count", 0)) for r in summary)
        person_total = sum(int(r.get("person_total", 0)) for r in summary)
        error_count = sum(int(r.get("error_count", 0)) for r in summary)
        by_trigger: Dict[str, int] = {}
        by_room: Dict[str, int] = {}
        conf_sum = 0.0
        conf_n = 0
        for r in summary:
            cnt = int(r.get("count", 0))
            tkey = r.get("trigger") or "(未标注)"
            rkey = r.get("room") or "(未标注)"
            by_trigger[tkey] = by_trigger.get(tkey, 0) + cnt
            by_room[rkey] = by_room.get(rkey, 0) + cnt
            conf_sum += float(r.get("avg_confidence") or 0.0) * cnt
            conf_n += cnt

        perc_total = sum(int(r.get("count", 0)) for r in perception)
        by_kind: Dict[str, int] = {}
        by_source: Dict[str, int] = {}
        for r in perception:
            by_kind[r.get("kind") or "(未标注)"] = by_kind.get(r.get("kind") or "(未标注)", 0) + int(r.get("count", 0))
            by_source[r.get("source") or "(未标注)"] = by_source.get(r.get("source") or "(未标注)", 0) + int(r.get("count", 0))

        return {
            "summary": {
                "behavior_events": total,
                "person_total": person_total,
                "error_count": error_count,
                "error_rate": _pct(error_count, total),
                "avg_confidence": round(conf_sum / conf_n, 4) if conf_n else 0.0,
                "rooms": sorted(by_room.keys()),
            },
            "by_trigger": [{"trigger": k, "count": v, "share": _pct(v, total)}
                           for k, v in sorted(by_trigger.items(), key=lambda kv: (-kv[1], kv[0]))],
            "by_room": [{"room": k, "count": v, "share": _pct(v, total)}
                        for k, v in sorted(by_room.items(), key=lambda kv: (-kv[1], kv[0]))],
            "top_actions": actions,
            "hourly": [{"hour": h, "count": int(hourly_map.get(h, 0))} for h in range(24)],
            "days": [{"day": d, "count": c} for d, c in sorted(days_map.items())],
            "perception": {
                "total": perc_total,
                "by_kind": [{"kind": k, "count": v} for k, v in sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0]))],
                "by_source": [{"source": k, "count": v} for k, v in sorted(by_source.items(), key=lambda kv: (-kv[1], kv[0]))],
            },
            "filters": self._filter_echo(room, category, "", rooms, [], []),
        }

    # ------------------------------------------------------- compare_insights
    def compare_insights(self, compare_days: int = 7, room: str = "", category: str = "") -> Dict[str, Any]:
        defaults = {"days": 0, "current": {}, "previous": {}, "delta": {}, "trend": "flat",
                    "filters": {}}
        try:
            out = self._compare_insights(compare_days, room, category)
            out["ok"] = True
            return out
        except Exception as exc:
            return self._fail("compare_insights", exc, defaults)

    def _compare_insights(self, compare_days: int, room: str, category: str) -> Dict[str, Any]:
        days = int(compare_days or getattr(self.config, "default_days", 7) or 7)
        if days <= 0:
            raise ValueError("compare_days 必须为正整数，得到 %r" % (compare_days,))
        now = self._now()
        cur = _Window(now - days * 86400.0, now)
        prev = _Window(now - 2 * days * 86400.0, now - days * 86400.0)
        rooms, domains, entity_ids = self._filters(room, category)

        def snap(window: _Window) -> Dict[str, Any]:
            counts = self.repo.day_counts(window, entity_ids=entity_ids, rooms=rooms, domains=domains)
            stats = self.repo.entity_stats(window, entity_ids=entity_ids, rooms=rooms, domains=domains)
            total = int(sum(counts.values()))
            return {
                "total_events": total,
                "active_entities": len(stats),
                "active_days": sum(1 for v in counts.values() if v),
                "avg_per_day": round(total / float(days), 2),
                "start_iso": window.start_iso, "end_iso": window.end_iso,
            }

        current = snap(cur)
        previous = snap(prev)
        delta = {}
        for key in ("total_events", "active_entities", "active_days", "avg_per_day"):
            diff = current[key] - previous[key]
            delta[key] = {"current": current[key], "previous": previous[key],
                          "delta": diff, "pct": _pct(diff, previous[key]) if previous[key] else None}
        pct_change = delta["total_events"]["pct"]
        if pct_change is None:
            trend = "up" if current["total_events"] > 0 else "flat"
        elif pct_change >= 0.05:
            trend = "up"
        elif pct_change <= -0.05:
            trend = "down"
        else:
            trend = "flat"
        return {
            "days": days, "current": current, "previous": previous,
            "delta": delta, "trend": trend,
            "filters": self._filter_echo(room, category, "", rooms, domains, entity_ids),
        }

    # ---------------------------------------------------------------- rhythm
    def rhythm(self, tr: Any, room: str = "") -> Dict[str, Any]:
        try:
            out = self._rhythm(tr, room)
            out["ok"] = True
            return out
        except Exception as exc:
            return self._fail("rhythm", exc, {"hourly": [], "weekday": [], "peaks": {},
                                              "total_events": 0, "filters": {}})

    def _rhythm(self, tr: Any, room: str) -> Dict[str, Any]:
        rooms = _split(room)
        matrix = self.repo.activity_matrix(tr, rooms=rooms)
        hourly = [0] * 24
        weekday = [0] * 7
        days_seen = set()
        total = 0
        for r in matrix:
            cnt = int(r.get("count") or 0)
            hour = int(r.get("hour") or 0)
            total += cnt
            if 0 <= hour < 24:
                hourly[hour] += cnt
            day = str(r.get("day") or "")
            if day:
                days_seen.add(day)
                idx = _weekday_index(day)
                if idx is not None:
                    weekday[idx] += cnt

        order = sorted(range(24), key=lambda h: (-hourly[h], h))
        top_hours = order[:3]
        quiet_hours = sorted(range(24), key=lambda h: (hourly[h], h))[:3]
        weekend = weekday[5] + weekday[6]
        top3_sum = sum(hourly[h] for h in top_hours)
        return {
            "hourly": [{"hour": h, "count": hourly[h], "share": _pct(hourly[h], total)}
                       for h in range(24)],
            "weekday": [{"weekday": i, "name": WEEKDAY_NAMES[i], "count": weekday[i],
                         "share": _pct(weekday[i], total)} for i in range(7)],
            "peaks": {
                "top_hours": [{"hour": h, "count": hourly[h]} for h in top_hours],
                "quiet_hours": [{"hour": h, "count": hourly[h]} for h in quiet_hours],
                "top3_concentration": _pct(top3_sum, total),
                "weekend_share": _pct(weekend, total),
            },
            "total_events": total,
            "days": len(days_seen),
            "room": room,
            "filters": {"rooms": rooms},
        }

    # ------------------------------------------------------- infer_activities
    def infer_activities(self, tr: Any, rooms: str = "",
                         activities: Any = None) -> Dict[str, Any]:
        """活动推断：语义规则判定为主，时段启发式为兜底（DCD 20261004 MA-裁6 Q1=A）。

        `activities` 是 ToolSpec/MCP 一直声明、门面原先收下就丢的那个入参：这里真正
        下发给规则筛选（键名 / 中文名 / 标签都可以），并同时过滤兜底时段的标签名。
        """
        try:
            out = self._infer_activities(tr, rooms, activities)
            out["ok"] = True
            return out
        except Exception as exc:
            return self._fail("infer_activities", exc, {"activities": [], "summary": {},
                                                        "filters": {}, "total_activities": 0,
                                                        "activity_types": [],
                                                        "excluded_entities": {}})

    def _infer_activities(self, tr: Any, rooms: str,
                          activities: Any = None) -> Dict[str, Any]:
        room_list = _split(rooms)
        allow = _split(activities)
        exclusions = self._activity_exclusions()
        exclude_ids = list(exclusions["entity_ids"])

        semantic, rules_meta = self._semantic_activities(tr, room_list, allow, exclude_ids)
        heuristic, seg_meta = self._heuristic_segments(tr, room_list, allow, exclude_ids)
        rows = semantic + heuristic
        rows.sort(key=lambda a: (str(a.get("day") or ""), float(a.get("start_ts") or 0.0),
                                 str(a.get("activity") or ""), str(a.get("name") or "")))

        by_name: Dict[str, int] = {}
        for a in rows:
            by_name[str(a.get("name") or a.get("activity") or "")] = \
                by_name.get(str(a.get("name") or a.get("activity") or ""), 0) + 1
        return {
            "activities": rows,
            "summary": {"count": len(rows), "by_name": by_name,
                        "days": seg_meta["days"],
                        "semantic": len(semantic), "heuristic": len(heuristic)},
            "filters": {"rooms": room_list, "activities": allow},
            # 与 legacy 同名键（裁5 Q2=A「并存为正式口径」）：总数 + 命中的活动类型集合
            "total_activities": len(rows),
            "activity_types": sorted({str(a.get("activity")) for a in rows
                                      if a.get("activity")}),
            "window": _window_meta(tr),
            # 裁6 Q3=A：被硬排除的实体必须可见，且这里的 count 就是真正生效的排除数
            "excluded_entities": exclusions,
            # 裁6 Q4 三项对比读数的第三项（旁挂依赖：规则表）
            "rule_sources": rules_meta,
        }

    # -------------------------------------------------------- 语义规则判定
    def _activity_exclusions(self) -> Dict[str, Any]:
        """排除表读数（读不到也要如实报数，不能静默当成"没有排除"）。"""
        try:
            data = self.repo.excluded_entity_ids()
        except Exception as exc:  # noqa: BLE001
            self.log.exception("excluded_entity_ids 读取失败")
            return {"entity_ids": [], "count": 0,
                    "sources": {"error": "%s: %s" % (type(exc).__name__, exc)},
                    "scopes": {}}
        ids = [str(e) for e in (data.get("entity_ids") or []) if str(e)]
        return {"entity_ids": sorted(set(ids)), "count": len(set(ids)),
                "sources": dict(data.get("sources") or {}),
                "scopes": dict(data.get("scopes") or {})}

    def _activity_rule_rows(self) -> Tuple[List[Dict[str, Any]], str]:
        try:
            return list(self.repo.activity_rules(enabled_only=True) or []), ""
        except Exception as exc:  # noqa: BLE001
            # 规则表读不到 ≠ 没有规则：把原因带回返回体，否则"注册了却没生效"无从分辨。
            self.log.warning("activity_rules 读取失败: %s", exc)
            return [], "%s: %s" % (type(exc).__name__, exc)

    @staticmethod
    def _rule_from_row(row: Dict[str, Any]) -> Optional[ActivityRule]:
        """`activity_rules` 行 -> ActivityRule（legacy 自定义规则的口径搬运）。

        口径差异要说清楚：legacy 数的是**事件条数**，本引擎的 `min_count` 数的是
        **会话段数**（on→off 配对后）；`min_minutes=0` 表示规则只声明频次、不声明时长，
        与 legacy 一致。窗口语义取新引擎口径（结束 <= 起始视为跨天）。
        """
        name = str(row.get("name") or "").strip()
        if not name:
            return None
        try:
            min_events = max(1, int(row.get("min_events") or 1))
        except (TypeError, ValueError):
            min_events = 1
        try:
            declared = float(row.get("confidence") or 0.0)
        except (TypeError, ValueError):
            declared = 0.0
        room = str(row.get("room") or "")
        tags = [str(t) for t in (row.get("tags") or []) if str(t).strip()]
        return ActivityRule(
            key=name, name=name, room=room,
            tags=tags,
            # tags_json 在 legacy 里是**判定条件**（设备标签任一命中才计事件），
            # 不是展示标签：不给 require_tags 就会退化成「全屋 + 频次」的假命中。
            require_tags=tags,
            window=(_safe_hour(row.get("start_hour"), 0), _safe_hour(row.get("end_hour"), 23)),
            min_minutes=0.0,
            requires=[Signal(room=room, min_minutes=0.0, min_count=min_events)],
            confidence=min(1.0, max(0.0, declared)),
            source="activity_rules")

    def _semantic_activities(self, tr: Any, room_list: List[str], allow: List[str],
                             exclude_ids: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """跑 ActivityEngine：内置 5 条语义规则 + `activity_rules` 表里的自定义规则。"""
        rules, rules_error = self._activity_rule_rows()
        engine = ActivityEngine(self.resolver, self.config)
        custom: Dict[str, ActivityRule] = {}
        for row in rules:
            rule = self._rule_from_row(row)
            if rule is not None:
                custom[rule.key] = rule
        engine.custom = custom

        events = self.repo.load_events(tr, rooms=room_list, exclude_entity_ids=exclude_ids)
        # 语义判定吃的是 load_events 的**截断切片**（`max_scan` 上限），兜底一侧走
        # `activity_matrix`（SQL 聚合，全窗口）。生产库 30 天窗实测：切片只覆盖窗口前 20 小时。
        # 不把「扫了多少 / 全窗口多少」写进返回体，"语义比兜底少"就说不清是规则不匹配还是被上限饿死。
        try:
            events_total = self.repo.count_events(
                tr, rooms=room_list, exclude_entity_ids=exclude_ids)
        except Exception as exc:  # noqa: BLE001
            self.log.warning("count_events（活动窗口真值）读取失败: %s", exc)
            events_total = -1
        scan_limit = int(getattr(self.repo, "scan_limit", 0) or 0)
        matches = engine.infer(events, tr, rooms=",".join(room_list),
                               activities=allow or None)
        rows: List[Dict[str, Any]] = []
        for m in matches:
            item = m.to_dict()
            item["start_hour"] = house_dt(m.start).hour if m.start else 0
            item["end_hour"] = house_dt(m.end).hour if m.end else 0
            item["typical_window"] = "%02d:00-%02d:00" % (int(m.window[0]), int(m.window[1])) \
                if getattr(m, "window", None) else ""
            item["evidence"] = _semantic_evidence(m)
            item["source"] = "semantic"
            item["custom"] = m.source == "activity_rules"
            item["rule"] = m.name if m.source == "activity_rules" else ""
            rows.append(item)
        meta = {
            "builtin": len(BUILTIN_ACTIVITIES),
            "activity_rules_table": len(rules),
            "custom_applied": len(custom),
            "activity_rules_error": rules_error,
            "selected": len(engine.select(activities=allow or None, rooms=",".join(room_list))),
            "events_scanned": len(events),
            "events_total": events_total,
            "scan_limit": scan_limit,
            # 与裁5 Q4=A 同口径：等于上限时无法与"刚好这么多"区分，所以说"可能被截"
            "scan_truncated": bool(scan_limit and len(events) >= scan_limit),
            "excluded_entity_ids": len(exclude_ids),
        }
        return rows, meta

    # -------------------------------------------------------- 时段启发式兜底
    def _heuristic_segments(self, tr: Any, room_list: List[str], allow: List[str],
                            exclude_ids: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """无标签设备的兜底输出：连续活跃时段贴时段标签（裁6 Q1=A 保留为补充输出）。

        与语义判定的区别写在每条的 `source` 上：这里只证明「这段时间有设备在动」，
        不证明人在做什么。
        """
        matrix = self.repo.activity_matrix(tr, rooms=room_list, exclude_entity_ids=exclude_ids)
        per_day: Dict[str, Dict[int, Dict[str, int]]] = {}
        for r in matrix:
            day = str(r.get("day") or "")
            hour = int(r.get("hour") or 0)
            domain = str(r.get("domain") or "")
            if not (0 <= hour < 24):
                continue
            bucket = per_day.setdefault(day, {}).setdefault(hour, {})
            bucket[domain] = bucket.get(domain, 0) + int(r.get("count") or 0)

        rows: List[Dict[str, Any]] = []
        for day in sorted(per_day.keys()):
            hours = per_day[day]
            hourly_total = {h: sum(dom.values()) for h, dom in hours.items()}
            segments: List[List[int]] = []
            run: List[int] = []
            for h in range(24):
                if hourly_total.get(h, 0) > 0:
                    run.append(h)
                else:
                    if run:
                        segments.append(run)
                        run = []
            if run:
                segments.append(run)
            for seg in segments:
                ev = sum(hourly_total.get(h, 0) for h in seg)
                dom_counts: Dict[str, int] = {}
                for h in seg:
                    for d, c in hours.get(h, {}).items():
                        dom_counts[d] = dom_counts.get(d, 0) + c
                dominant = max(dom_counts, key=lambda d: dom_counts[d]) if dom_counts else ""
                name = _label_activity(seg[0], seg[-1], dominant)
                if allow and not _allow_hit(name, "", allow):
                    continue
                start_ts = _day_hour_ts(day, seg[0])
                end_ts = _day_hour_ts(day, seg[-1])
                rows.append({
                    "activity": name,
                    "name": name,
                    "day": day,
                    "room": ",".join(room_list),
                    "start_hour": seg[0],
                    "end_hour": seg[-1],
                    "start_ts": start_ts,
                    "end_ts": end_ts + 3599.0,
                    "start": fmt_ts(start_ts),
                    "end": fmt_ts(end_ts + 3599.0),
                    "events": ev,
                    "dominant_domain": dominant,
                    "domains": dom_counts,
                    "confidence": round(min(0.95, 0.40 + ev / 200.0 + (0.10 if len(seg) >= 2 else 0.0)), 3),
                    "typical_window": "%02d:00-%02d:00" % (seg[0], seg[-1] + 1),
                    "evidence": "时段启发式：%s %02d:00-%02d:59 有 %d 条事件（主导域 %s），"
                                "无设备标签可判定具体活动" % (day, seg[0], seg[-1], ev, dominant or "-"),
                    "source": "heuristic",
                    "custom": False,
                    "tags": [],
                    "signals": [],
                })
            gap_len, gap_at = _longest_quiet(hourly_total, 9, 18)
            if gap_len >= 3:
                name = "可能离家"
                if not allow or _allow_hit(name, "away", allow):
                    start_ts = _day_hour_ts(day, gap_at)
                    rows.append({
                        "activity": "away",
                        "name": name, "day": day, "room": ",".join(room_list),
                        "start_hour": gap_at, "end_hour": gap_at + gap_len - 1,
                        "start_ts": start_ts,
                        "end_ts": _day_hour_ts(day, gap_at + gap_len - 1) + 3599.0,
                        "start": fmt_ts(start_ts),
                        "end": fmt_ts(_day_hour_ts(day, gap_at + gap_len - 1) + 3599.0),
                        "events": 0, "dominant_domain": "", "domains": {},
                        "confidence": round(min(0.9, 0.45 + gap_len / 24.0), 3),
                        "typical_window": "%02d:00-%02d:00" % (gap_at, gap_at + gap_len),
                        "evidence": "时段启发式：%s %02d:00 起连续 %d 小时无事件" % (day, gap_at, gap_len),
                        "source": "heuristic",
                        "custom": False,
                        "tags": ["away"],
                        "signals": [],
                    })
        return rows, {"days": len(per_day)}


    # ---------------------------------------------------------- user_persona
    def user_persona(self, days: int = 14) -> Dict[str, Any]:
        defaults = {"days": 0, "traits": [], "top_rooms": [], "top_entities": [],
                    "rhythm": {}, "total_events": 0, "window": {}}
        try:
            out = self._user_persona(days)
            out["ok"] = True
            return out
        except Exception as exc:
            return self._fail("user_persona", exc, defaults)

    def _user_persona(self, days: int) -> Dict[str, Any]:
        n = int(days or getattr(self.config, "default_days", 14) or 14)
        if n <= 0:
            raise ValueError("days 必须为正整数，得到 %r" % (days,))
        now = self._now()
        window = _Window(now - n * 86400.0, now)

        matrix = self.repo.activity_matrix(window)
        stats = self.repo.entity_stats(window)
        behavior = self.repo.behavior_summary(window)

        hourly = [0] * 24
        total = 0
        for r in matrix:
            cnt = int(r.get("count") or 0)
            total += cnt
            h = int(r.get("hour") or 0)
            if 0 <= h < 24:
                hourly[h] += cnt
        peak_hour = max(range(24), key=lambda h: hourly[h]) if total else 0
        if total == 0:
            rhythm_label = "无数据"
        elif peak_hour < 9:
            rhythm_label = "早起型"
        elif peak_hour >= 21:
            rhythm_label = "夜猫子型"
        else:
            rhythm_label = "日间活跃型"

        room_counts: Dict[str, int] = {}
        for s in stats:
            key = s.get("room") or "(未标注)"
            room_counts[key] = room_counts.get(key, 0) + int(s.get("count", 0))
        top_rooms = sorted(room_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        top_entities = sorted(stats, key=lambda s: (-int(s.get("count", 0)), s.get("entity_id") or ""))

        order = sorted(range(24), key=lambda h: (-hourly[h], h))
        concentration = _pct(sum(hourly[h] for h in order[:3]), total)
        if concentration >= 0.6:
            regularity = "高"
        elif concentration >= 0.35:
            regularity = "中"
        else:
            regularity = "低"

        behavior_total = sum(int(r.get("count", 0)) for r in behavior)
        person_total = sum(int(r.get("person_total", 0)) for r in behavior)

        traits = [
            {"name": "作息类型", "value": rhythm_label,
             "evidence": {"peak_hour": peak_hour, "peak_count": hourly[peak_hour]}},
            {"name": "最常活动房间", "value": top_rooms[0][0] if top_rooms else "(无)",
             "evidence": {"count": top_rooms[0][1] if top_rooms else 0}},
            {"name": "设备交互强度", "value": "%.1f 次/天" % (total / float(n)),
             "evidence": {"total_events": total, "days": n}},
            {"name": "作息规律度", "value": regularity,
             "evidence": {"top3_concentration": concentration}},
        ]
        if behavior_total:
            traits.append({"name": "视觉行为事件", "value": "%d 条 / 人次 %d" % (behavior_total, person_total),
                           "evidence": {"behavior_events": behavior_total, "person_total": person_total}})

        return {
            "days": n,
            "traits": traits,
            "top_rooms": [{"room": k, "count": v, "share": _pct(v, total)} for k, v in top_rooms[:10]],
            "top_entities": [{"entity_id": s.get("entity_id") or "", "room": s.get("room") or "",
                              "domain": s.get("domain") or "", "count": int(s.get("count", 0)),
                              "share": _pct(int(s.get("count", 0)), total)} for s in top_entities[:10]],
            "rhythm": {"peak_hour": peak_hour,
                       "hourly": [{"hour": h, "count": hourly[h]} for h in range(24)],
                       "top3_concentration": concentration},
            "total_events": total,
            "window": {"start_iso": window.start_iso, "end_iso": window.end_iso},
        }

    # ---------------------------------------------------------- data_quality
    def data_quality(self, tr: Any) -> Dict[str, Any]:
        try:
            out = self._data_quality(tr)
            out["ok"] = True
            return out
        except Exception as exc:
            return self._fail("data_quality", exc, {"checks": [], "issues": [], "score": 0.0,
                                                    "total_events": 0, "filters": {}})

    def _data_quality(self, tr: Any) -> Dict[str, Any]:
        qc = self.repo.quality_counts(tr)
        total = int(qc.get("total", 0))
        sample_limit = int(getattr(self.config, "default_limit", 100) or 100) * 5
        sample = self.repo.sample_rows(tr, limit=sample_limit)
        bad_attrs = 0
        for row in sample:
            raw = row.get("attrs_json")
            if raw:
                try:
                    json.loads(raw)
                except Exception:
                    bad_attrs += 1

        start_day, end_day = self._tr_days(tr)
        all_days = list(_iter_days(start_day, end_day))
        day_counts = self.repo.day_counts(tr)
        missing = [d for d in all_days if not day_counts.get(d)]

        stats = self.repo.entity_stats(tr)
        cap = float(getattr(self.config, "noise_ratio_cap", 0.6) or 0.6)
        top = max(stats, key=lambda s: int(s.get("count", 0))) if stats else None
        noise_ratio = round(int(top.get("count", 0)) / total, 4) if (top and total) else 0.0

        def check(name: str, ok: bool, value: int, ratio: float, detail: str) -> Dict[str, Any]:
            return {"name": name, "ok": bool(ok), "value": value,
                    "ratio": round(ratio, 4), "detail": detail}

        checks = [
            check("room 完整性", qc.get("empty_room", 0) == 0, qc.get("empty_room", 0),
                  _pct(qc.get("empty_room", 0), total), "缺 room 的事件数"),
            check("domain 完整性", qc.get("empty_domain", 0) == 0, qc.get("empty_domain", 0),
                  _pct(qc.get("empty_domain", 0), total), "缺 domain 的事件数"),
            check("entity_id 完整性", qc.get("empty_entity", 0) == 0, qc.get("empty_entity", 0),
                  _pct(qc.get("empty_entity", 0), total), "缺 entity_id 的事件数"),
            check("new_state 完整性", qc.get("empty_state", 0) == 0, qc.get("empty_state", 0),
                  _pct(qc.get("empty_state", 0), total), "缺 new_state 的事件数"),
            check("attrs_json 完整性", qc.get("empty_attrs", 0) == 0, qc.get("empty_attrs", 0),
                  _pct(qc.get("empty_attrs", 0), total), "缺 attrs_json 的事件数"),
            check("action 完整性", qc.get("empty_action", 0) == 0, qc.get("empty_action", 0),
                  _pct(qc.get("empty_action", 0), total), "缺 action 的事件数"),
            check("attrs_json 可解析", bad_attrs == 0, bad_attrs,
                  _pct(bad_attrs, len(sample)), "抽样 %d 行" % len(sample)),
            check("日期连续性", not missing, len(missing),
                  _pct(len(missing), len(all_days)), "缺 days: %s" % (missing[:10],)),
            check("噪声占比", noise_ratio <= cap, round(noise_ratio, 4), noise_ratio,
                  "最高实体 %s 占比 %.2f，上限 %.2f"
                  % (top.get("entity_id") if top else "-", noise_ratio, cap)),
        ]
        issues = [c["name"] for c in checks if not c["ok"]]
        score = round(sum(1 for c in checks if c["ok"]) / float(len(checks)), 3) if checks else 0.0
        return {
            "checks": checks,
            "issues": issues,
            "score": score,
            "total_events": total,
            "sample_size": len(sample),
            "missing_days": missing,
            "noise_ratio": noise_ratio,
            "summary": {"empty_room": qc.get("empty_room", 0),
                        "empty_domain": qc.get("empty_domain", 0),
                        "empty_state": qc.get("empty_state", 0),
                        "bad_attrs": bad_attrs,
                        "active_days": qc.get("active_days", 0)},
            "filters": {"room": "", "category": "", "entity_id": ""},
        }