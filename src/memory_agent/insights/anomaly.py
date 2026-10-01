"""异常检测模块：设备异常 + 数据质量 + 噪声源识别。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .models import (
    Anomaly, AnomalyType, EntityInfo, EventRecord, InsightConfig, Page,
    Session, Severity, TimeRange, fmt_ts,
)
from .parser.entity import EntityResolver, normalize_state

__all__ = ["AnomalyDetector"]


class AnomalyDetector:
    """设备健康 / 数据质量 / 噪声源检测。"""

    def __init__(self, resolver: EntityResolver,
                 config: Optional[InsightConfig] = None) -> None:
        self.resolver = resolver
        self.config = config or InsightConfig()

    # ------------------------------------------------------------------
    # 设备健康
    # ------------------------------------------------------------------
    def device_health(self, events: Sequence[EventRecord], tr: TimeRange,
                      entities: Optional[Sequence[EntityInfo]] = None,
                      last_seen_map: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
        """设备健康检查：离线 / 状态反复 / 时长异常 / 正常。"""
        entities = self._entities(events, entities)
        last_seen_map = last_seen_map or {}
        per_entity = self._group(events)
        rows: List[Dict[str, Any]] = []
        for info in entities:
            evs = per_entity.get(info.entity_id, [])
            last_ts = max([e.ts for e in evs] + [last_seen_map.get(info.entity_id, 0.0)])
            # P2：last_ts 可能来自窗口外更晚的事件，截断为非负，避免 days_silent 出现负数
            days_silent = max(0.0, round((tr.end_ts - last_ts) / 86400.0, 2)) if last_ts else None
            changes = sum(1 for i in range(1, len(evs))
                          if normalize_state(evs[i].state) != normalize_state(evs[i - 1].state))
            flap = len(self._flapping_windows(evs))
            units = sorted({(e.unit or str(e.attributes.get("unit_of_measurement", "")))
                            for e in evs if (e.unit or e.attributes.get("unit_of_measurement"))})
            checks: List[str] = []
            if last_ts and days_silent is not None and days_silent > self.config.offline_days:
                status, text = "offline", "超过 %s 天无数据" % days_silent
                checks.append(AnomalyType.OFFLINE.value)
            elif flap:
                status, text = "flapping", "%d 个时间窗内状态反复" % flap
                checks.append(AnomalyType.FLAPPING.value)
            elif len(units) > 1:
                status, text = "unusual", "单位冲突：%s" % "/".join(units)
                checks.append(AnomalyType.UNIT_CONFLICT.value)
            elif not evs:
                status, text = "no_data", "窗口内无数据"
                checks.append(AnomalyType.MISSING_DATA.value)
            else:
                status, text = "ok", "正常"
            rows.append({
                "entity_id": info.entity_id, "friendly_name": info.label,
                "label": info.label, "room": info.room, "domain": info.domain,
                "category": info.category, "unit": info.unit,
                "event_count": len(evs), "state_changes": changes,
                "last_seen": fmt_ts(last_ts) if last_ts else "",
                "last_seen_ts": last_ts or None, "days_silent": days_silent,
                "active_minutes": self._active_minutes(evs, tr),
                "flapping_count": flap, "units": units, "checks": checks,
                "status": status, "status_text": text,
            })
        rows.sort(key=lambda r: (r["status"] == "ok", r["entity_id"]))
        return rows

    # ------------------------------------------------------------------
    # 数据质量
    # ------------------------------------------------------------------
    def data_quality(self, events: Sequence[EventRecord], tr: TimeRange,
                     entities: Optional[Sequence[EntityInfo]] = None) -> List[Anomaly]:
        """数据质量检测：缺失、单位冲突、噪声源。"""
        issues: List[Anomaly] = []
        per_entity = self._group(events)
        issues.extend(self._missing_data(per_entity))
        issues.extend(self._unit_conflict(per_entity))
        issues.extend(self.noise_sources(events))
        return issues

    def noise_sources(self, events: Sequence[EventRecord]) -> List[Anomaly]:
        """噪声源识别：单一设备事件占比 > noise_ratio_cap。"""
        total = len(events)
        if not total:
            return []
        per_entity = self._group(events)
        out: List[Anomaly] = []
        for entity_id, evs in per_entity.items():
            ratio = len(evs) / float(total)
            if ratio > self.config.noise_ratio_cap:
                info = self.resolver.meta(entity_id)
                out.append(Anomaly(
                    type=AnomalyType.NOISE_SOURCE.value,
                    title="噪声源：%s" % info.label,
                    detail="占全部事件 %.1f%%（阈值 %.0f%%），会拍平小时分布，建议降采样" % (
                        ratio * 100, self.config.noise_ratio_cap * 100),
                    entity_id=entity_id, friendly_name=info.label, room=info.room,
                    severity=Severity.MEDIUM.value,
                    value=round(ratio, 4), expected=self.config.noise_ratio_cap,
                    data={"event_count": len(evs), "total": total,
                          "ratio": round(ratio, 4)}))
        return out

    def flapping(self, events: Sequence[EventRecord]) -> List[Anomaly]:
        """状态反复：短时间频繁切换。"""
        out: List[Anomaly] = []
        for entity_id, evs in self._group(events).items():
            windows = self._flapping_windows(evs)
            if not windows:
                continue
            info = self.resolver.meta(entity_id)
            worst = max(windows, key=lambda w: w["count"])
            out.append(Anomaly(
                type=AnomalyType.FLAPPING.value,
                title="状态反复：%s" % info.label,
                detail="%d 分钟内切换 %d 次" % (
                    self.config.flapping_window // 60, worst["count"]),
                entity_id=entity_id, friendly_name=info.label, room=info.room,
                severity=Severity.MEDIUM.value, value=float(worst["count"]),
                expected=float(self.config.flapping_count), ts=worst["ts"],
                data={"windows": windows[:5]}))
        return out

    def duration_spikes(self, sessions: Dict[str, List[Session]]) -> List[Anomaly]:
        """使用时长异常：远超历史中位数。"""
        out: List[Anomaly] = []
        for entity_id, sess in sessions.items():
            durations = [s.minutes for s in sess if s.minutes > 0]
            if len(durations) < 2:
                continue
            base = _median(durations)
            if base <= 0:
                continue
            for ses in sess:
                if (ses.minutes > base * self.config.spike_factor
                        and ses.minutes > self.config.spike_min_minutes):
                    info = self.resolver.meta(entity_id)
                    out.append(Anomaly(
                        type=AnomalyType.DURATION_SPIKE.value,
                        title="使用时长异常：%s" % info.label,
                        detail="单次 %.1f 分钟，是中位数 %.1f 分钟的 %.1f 倍" % (
                            ses.minutes, base, ses.minutes / base),
                        entity_id=entity_id, friendly_name=info.label, room=info.room,
                        severity=Severity.LOW.value, value=ses.minutes,
                        expected=base, ts=ses.start,
                        data={"start": fmt_ts(ses.start), "end": fmt_ts(ses.end)}))
        return out

    # ------------------------------------------------------------------
    # 综合报告
    # ------------------------------------------------------------------
    def report(self, events: Sequence[EventRecord], tr: TimeRange,
               sessions: Optional[Dict[str, List[Session]]] = None,
               entities: Optional[Sequence[EntityInfo]] = None,
               last_seen: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """异常报告：设备异常 + 数据质量 + 噪声源。"""
        from .service import compute_sessions
        sessions = sessions or compute_sessions(events, tr, self.config.min_session_seconds)
        health = self.device_health(events, tr, entities=entities, last_seen_map=last_seen)
        anomalies: List[Anomaly] = []
        for row in health:
            if row["status"] == "offline":
                anomalies.append(Anomaly(
                    type=AnomalyType.OFFLINE.value,
                    title="设备离线：%s" % row["label"], detail=row["status_text"],
                    entity_id=row["entity_id"], friendly_name=row["label"],
                    room=row["room"], severity=Severity.HIGH.value,
                    value=row["days_silent"], expected=float(self.config.offline_days),
                    ts=row["last_seen_ts"]))
        anomalies.extend(self.flapping(events))
        anomalies.extend(self.duration_spikes(sessions))
        anomalies.extend(self.data_quality(events, tr, entities=entities))
        counts: Dict[str, int] = {}
        for item in anomalies:
            counts[item.type] = counts.get(item.type, 0) + 1
        noise = [i for i in anomalies if i.type == AnomalyType.NOISE_SOURCE.value]
        summary = "发现 %d 条异常（离线 %d / 反复 %d / 时长 %d / 缺失 %d / 单位 %d / 噪声 %d）" % (
            len(anomalies), counts.get("offline", 0), counts.get("flapping", 0),
            counts.get("duration_spike", 0), counts.get("missing_data", 0),
            counts.get("unit_conflict", 0), counts.get("noise_source", 0))
        return Page.build([a.to_dict() for a in anomalies], counts=counts,
                          noise_sources=[n.to_dict() for n in noise],
                          devices=health, summary=summary,
                          time_range=tr.to_dict()).to_dict("anomalies")

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _group(events: Sequence[EventRecord]) -> Dict[str, List[EventRecord]]:
        out: Dict[str, List[EventRecord]] = {}
        for ev in sorted(events, key=lambda e: e.ts):
            out.setdefault(ev.entity_id, []).append(ev)
        return out

    def _entities(self, events: Sequence[EventRecord],
                  entities: Optional[Sequence[EntityInfo]]) -> List[EntityInfo]:
        if entities is not None:
            return list(entities)
        seen: Dict[str, EntityInfo] = {}
        for ev in events:
            seen.setdefault(ev.entity_id, self.resolver.meta(ev.entity_id))
        if not seen:
            return self.resolver.all()
        return list(seen.values())

    def _active_minutes(self, events: Sequence[EventRecord], tr: TimeRange) -> float:
        # P2：与 report() 口径一致，必须传 min_session_seconds，否则短会话被计入活跃分钟
        from .service import compute_sessions
        sessions = compute_sessions(events, tr, self.config.min_session_seconds)
        return round(sum(s.minutes for sess in sessions.values() for s in sess), 1)

    def _flapping_windows(self, events: Sequence[EventRecord]) -> List[Dict[str, Any]]:
        """滑动窗口内切换次数 >= flapping_count 视为状态反复。"""
        out: List[Dict[str, Any]] = []
        states = [(e.ts, normalize_state(e.state)) for e in events]
        changes = [states[i][0] for i in range(1, len(states))
                   if states[i][1] != states[i - 1][1]]
        head = 0
        for tail in range(len(changes)):
            while changes[tail] - changes[head] > self.config.flapping_window:
                head += 1
            count = tail - head + 1
            if count >= self.config.flapping_count:
                ts = changes[head]
                # P2：时间上重叠的窗口合并为一个（取最大切换次数），
                # 否则同一轮反复会被按 tail 逐次重复计数，flapping_count 虚高
                if out and ts - out[-1]["ts"] < self.config.flapping_window:
                    if count > out[-1]["count"]:
                        out[-1]["count"] = count
                else:
                    out.append({"ts": ts, "count": count,
                                "window": self.config.flapping_window})
        return out

    def _missing_data(self, per_entity: Dict[str, List[EventRecord]]) -> List[Anomaly]:
        """数据缺失：采集节奏被打断（空洞 > 中位间隔 * 系数）。"""
        out: List[Anomaly] = []
        for entity_id, evs in per_entity.items():
            if len(evs) < 5:
                continue
            gaps = [evs[i].ts - evs[i - 1].ts for i in range(1, len(evs))]
            base = _median(gaps)
            if base <= 0 or base > 6 * 3600:
                continue
            threshold = max(base * self.config.missing_gap_factor,
                            self.config.missing_gap_min)
            # P2：之前遇到第一个超阈空洞就 break，报的是"最早"而非"最严重"；
            # 改为遍历全部空洞，只报最长的那一次
            worst_idx, worst_gap = -1, 0.0
            for index, gap in enumerate(gaps, start=1):
                if gap > threshold and gap > worst_gap:
                    worst_idx, worst_gap = index, gap
            if worst_idx > 0:
                info = self.resolver.meta(entity_id)
                out.append(Anomaly(
                    type=AnomalyType.MISSING_DATA.value,
                    title="数据缺失：%s" % info.label,
                    detail="%s 起空洞 %.1f 小时（正常间隔 %.1f 分钟）" % (
                        fmt_ts(evs[worst_idx - 1].ts), worst_gap / 3600.0, base / 60.0),
                    entity_id=entity_id, friendly_name=info.label, room=info.room,
                    severity=Severity.MEDIUM.value, value=round(worst_gap / 3600.0, 2),
                    expected=round(base / 60.0, 2), ts=evs[worst_idx].ts))
        return out

    def _unit_conflict(self, per_entity: Dict[str, List[EventRecord]]) -> List[Anomaly]:
        """单位冲突：同一设备上报了多种单位。"""
        out: List[Anomaly] = []
        for entity_id, evs in per_entity.items():
            units = {(e.unit or str(e.attributes.get("unit_of_measurement", ""))).strip()
                     for e in evs}
            units = {u for u in units if u}
            if len(units) > 1:
                info = self.resolver.meta(entity_id)
                out.append(Anomaly(
                    type=AnomalyType.UNIT_CONFLICT.value,
                    title="单位冲突：%s" % info.label,
                    detail="同一设备出现 %d 种单位：%s" % (len(units), "、".join(sorted(units))),
                    entity_id=entity_id, friendly_name=info.label, room=info.room,
                    severity=Severity.LOW.value, value=float(len(units)), expected=1.0,
                    data={"units": sorted(units)}))
        return out


def _median(values: Sequence[float]) -> float:
    data = sorted(values)
    size = len(data)
    if not size:
        return 0.0
    mid = size // 2
    return float(data[mid]) if size % 2 else (data[mid - 1] + data[mid]) / 2.0
