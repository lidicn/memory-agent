"""Insights 框架 · 数据访问层（StoreRepository）

严格对齐生产库真实 schema（docs/insights_schema_contract.py）：

events(id, ts, day, room, entity_id, domain, action, person,
       old_state, new_state, attrs_json)
behavior_events(server_ts, device_ts, day, room, camera_src, persons_json, count,
                action, scene, confidence, appearance_json, trigger,
                vlm_latency_ms, snapshot_path, raw_response, status)
perception_events(event_id, server_ts, day, source, kind, room, entity_id,
                  confidence, payload_json, raw_event_json)

必须遵守的生产事实：
1. 不存在 entities / entity_catalog 表 —— 实体清单只能来自 events 表聚合；
2. events 没有 state / attributes 列 —— 只能用 new_state / attrs_json；
3. ts / server_ts 是 ISO8601 字符串，day 是 "YYYY-MM-DD"，比较走字符串序；
4. 全部 SQL 经 self.store.db_query(sql, params)（qmark 占位符）执行，不碰 conn/cursor。

失败语义：除 health() 外一律 fail-closed（异常上抛），由 service 层决定如何兜底。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Sequence, Tuple

from .models import EventRecord, house_tz

__all__ = ["StoreRepository"]

_LOG = logging.getLogger("insights.repository")

#: events 表真实列（顺序与 SELECT 一致）
EVENT_COLUMNS = ("id", "ts", "day", "room", "entity_id", "domain",
                 "action", "person", "old_state", "new_state", "attrs_json")


# --------------------------------------------------------------------- 工具
def _to_epoch(value: Any) -> float:
    """ISO8601 字符串 -> epoch float（生产数据为家庭墙钟 naive 字符串）。

    容器跑在 UTC，naive ISO 会被当 UTC 解析导致整体平移数小时。
    这里显式按 ``house_tz()``（Config.tz_offset_hours 注入）解释再转 epoch。
    解析失败 fail-open 返回 0.0。
    """
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=house_tz())
        return dt.timestamp()
    except (TypeError, ValueError):
        try:
            return float(text)
        except (TypeError, ValueError):
            _LOG.warning("无法解析时间戳: %r", value)
            return 0.0


def _to_iso(value: float) -> str:
    """epoch float -> ISO8601 字符串（秒级，家庭墙钟口径，与生产数据对齐）。"""
    return datetime.fromtimestamp(float(value), tz=house_tz()).replace(tzinfo=None).isoformat(timespec="seconds")


def _load_attrs(raw: Any) -> Dict[str, Any]:
    """attrs_json -> dict；NULL/空 -> {}；解析失败 fail-open 返回 {} 并留日志。"""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        _LOG.warning("attrs_json 解析失败: %r", raw)
        return {}
    return parsed if isinstance(parsed, dict) else {}


class StoreRepository:
    """events / behavior_events / perception_events 的只读访问层。

    所有查询只经 _execute() -> store.db_query()。除 health() 外失败即抛（fail-closed）。
    """

    #: load_events(behavior_only=True) 追加的过滤条件（交付说明 §一/B）
    BEHAVIOR_ONLY_SQL = "COALESCE(action, '') != ''"
    DEFAULT_MAX_SCAN = 30000

    def __init__(self, store: Any, config: Any) -> None:
        self.store = store
        self.config = config
        self.log = _LOG

    # ------------------------------------------------------------ 基础出口
    def _execute(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        """唯一 SQL 出口，强制走 store.db_query（qmark 占位符）。"""
        rows = self.store.db_query(sql, tuple(params))
        return list(rows) if rows else []

    def health(self) -> bool:
        """SELECT 1 能跑通即 True；任何异常都 fail-closed 成 False。"""
        try:
            rows = self._execute("SELECT 1 AS ok")
        except Exception as exc:
            self.log.warning("health 检查失败: %s", exc)
            return False
        return bool(rows) and rows[0].get("ok") == 1

    # ------------------------------------------------------------ 内部工具
    @staticmethod
    def _as_tuple(values: Any) -> Tuple[Any, ...]:
        """None/""/[] -> ()（表示不过滤）；字符串按逗号分割；其余按元素收集。"""
        if values is None:
            return ()
        if isinstance(values, str):
            return tuple(p.strip() for p in values.split(",") if p.strip())
        if isinstance(values, (list, tuple, set, frozenset)):
            return tuple(v for v in values if v not in (None, ""))
        return (values,)

    @classmethod
    def _add_in(cls, where: List[str], params: List[Any], column: str, values: Any) -> None:
        vals = cls._as_tuple(values)
        if not vals:
            return
        where.append(column + " IN (" + ",".join(["?"] * len(vals)) + ")")
        params.extend(vals)

    @classmethod
    def _add_not_in(cls, where: List[str], params: List[Any], column: str,
                    values: Any) -> None:
        """硬排除：`column NOT IN (...)`，空排除集不加任何条件（等于不过滤）。

        不补 `OR column IS NULL`：events 的 entity_id/room/domain 都是
        `NOT NULL DEFAULT ''`（store.py 的建表语句），缺值时入库已被折叠成 `''`，
        NULL 行不存在，加了是掩盖问题而不是防御。排除集同理由 `_as_tuple` 滤掉
        `None`/`""`，不会写出 `NOT IN (NULL)` 这种恒为 UNKNOWN 的条件。
        """
        vals = cls._as_tuple(values)
        if not vals:
            return
        where.append(column + " NOT IN (" + ",".join(["?"] * len(vals)) + ")")
        params.extend(vals)

    @staticmethod
    def _bounds(tr: Any) -> Tuple[str, str, str, str]:
        """(start_iso, end_iso, start_day, end_day)；tr 只要求 TimeRange 形状。"""
        start_iso = getattr(tr, "start_iso", None) or ""
        end_iso = getattr(tr, "end_iso", None) or ""
        if not start_iso:
            if not hasattr(tr, "start_ts"):
                raise TypeError("tr 缺少 start_iso / start_ts")
            start_iso = _to_iso(tr.start_ts)
        if not end_iso:
            if not hasattr(tr, "end_ts"):
                raise TypeError("tr 缺少 end_iso / end_ts")
            end_iso = _to_iso(tr.end_ts)
        return start_iso, end_iso, start_iso[:10], end_iso[:10]

    def _scan_limit(self) -> int:
        return int(getattr(self.config, "max_scan", 0) or self.DEFAULT_MAX_SCAN)

    @property
    def scan_limit(self) -> int:
        """对外暴露的扫描上限（DCD 20261004 MA-裁5 Q4=A）。

        调用方需要它才能把「`total` 是扫描上限还是全量」说清楚；上限本身仍是
        `load_events` 的 `LIMIT`，裁定明确**不提高**。"""
        return self._scan_limit()

    def _top_n(self, limit: Any, default: int = 50) -> int:
        ceiling = int(getattr(self.config, "max_limit", 0) or 5000)
        try:
            n = int(limit) if limit else int(default)
        except (TypeError, ValueError):
            n = int(default)
        return max(1, min(n, ceiling))

    def _event_where(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                     domains: Any = None, behavior_only: bool = False,
                     exclude_entity_ids: Any = None,
                     exclude_domains: Any = None) -> Tuple[List[str], List[Any]]:
        start_iso, end_iso, start_day, end_day = self._bounds(tr)
        where = ["day BETWEEN ? AND ?", "ts BETWEEN ? AND ?"]
        params: List[Any] = [start_day, end_day, start_iso, end_iso]
        self._add_in(where, params, "entity_id", entity_ids)
        self._add_in(where, params, "room", rooms)
        self._add_in(where, params, "domain", domains)
        # 硬排除（DCD 20261004 MA-裁6 Q3=A）：signal_exclusions 里的实体必须从
        # 事件流和 activity_matrix 两侧同时剔除，只剔一侧会让「已排除」成为空话。
        self._add_not_in(where, params, "entity_id", exclude_entity_ids)
        self._add_not_in(where, params, "domain", exclude_domains)
        if behavior_only:
            where.append(self.BEHAVIOR_ONLY_SQL)
        return where, params

    def _behavior_where(self, tr: Any, rooms: Any = None) -> Tuple[List[str], List[Any]]:
        start_iso, end_iso, start_day, end_day = self._bounds(tr)
        where = ["day BETWEEN ? AND ?", "server_ts BETWEEN ? AND ?"]
        params: List[Any] = [start_day, end_day, start_iso, end_iso]
        self._add_in(where, params, "room", rooms)
        return where, params

    def _to_record(self, row: Dict[str, Any]) -> EventRecord:
        """events 行 -> EventRecord（契约 §1 的转换规则逐条对应）。"""
        attrs = _load_attrs(row.get("attrs_json"))
        entity_id = row.get("entity_id") or ""
        friendly = attrs.get("friendly_name") or entity_id
        unit = attrs.get("unit_of_measurement") or attrs.get("unit") or ""
        return EventRecord(
            ts=_to_epoch(row.get("ts")),
            entity_id=entity_id,
            state=row.get("new_state") or "",
            attributes=attrs,
            friendly_name=str(friendly),
            room=row.get("room") or "",
            domain=row.get("domain") or "",
            unit=str(unit),
        )

    # ------------------------------------------------------------ 事件读取
    def load_events(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                    domains: Any = None, behavior_only: bool = False,
                    exclude_entity_ids: Any = None,
                    exclude_domains: Any = None) -> List[EventRecord]:
        """查 events 表，ts BETWEEN start_iso AND end_iso，返回 EventRecord 列表。

        上限 config.max_scan（默认 30000），命中截断会记 warning。
        """
        where, params = self._event_where(tr, entity_ids, rooms, domains, behavior_only,
                                          exclude_entity_ids, exclude_domains)
        limit = self._scan_limit()
        sql = ("SELECT id, ts, day, room, entity_id, domain, action, person, old_state, new_state, attrs_json "
               "FROM events WHERE " + " AND ".join(where) + " ORDER BY ts ASC, id ASC LIMIT ?")
        params.append(limit)
        rows = self._execute(sql, params)
        if len(rows) >= limit:
            self.log.warning("load_events 命中扫描上限 %s，结果可能被截断", limit)
        return [self._to_record(row) for row in rows]

    def count_events(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                     domains: Any = None, behavior_only: bool = False,
                     exclude_entity_ids: Any = None,
                     exclude_domains: Any = None) -> int:
        where, params = self._event_where(tr, entity_ids, rooms, domains, behavior_only,
                                          exclude_entity_ids, exclude_domains)
        rows = self._execute("SELECT COUNT(*) AS c FROM events WHERE " + " AND ".join(where), params)
        return int((rows[0].get("c") if rows else 0) or 0)

    def day_counts(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                   domains: Any = None, behavior_only: bool = False,
                   exclude_entity_ids: Any = None,
                   exclude_domains: Any = None) -> Dict[str, int]:
        where, params = self._event_where(tr, entity_ids, rooms, domains, behavior_only,
                                          exclude_entity_ids, exclude_domains)
        sql = "SELECT day, COUNT(*) AS c FROM events WHERE " + " AND ".join(where) + " GROUP BY day ORDER BY day"
        return {str(r.get("day") or ""): int(r.get("c") or 0) for r in self._execute(sql, params)}

    def activity_matrix(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                        domains: Any = None, behavior_only: bool = False,
                        exclude_entity_ids: Any = None,
                        exclude_domains: Any = None) -> List[Dict[str, Any]]:
        """(day, hour, domain) -> count；hour 由 substr(ts,12,2) 提取（0-23）。

        `exclude_entity_ids` 是裁6 Q3 的硬排除落点：时段启发式（兜底输出）就由这张
        表算出，不排除被点名的实体，「已排除 N 个实体」只会出现在返回体里而不生效。
        """
        where, params = self._event_where(tr, entity_ids, rooms, domains, behavior_only,
                                          exclude_entity_ids, exclude_domains)
        sql = ("SELECT day, CAST(substr(ts, 12, 2) AS INTEGER) AS hour_value, domain, COUNT(*) AS c "
               "FROM events WHERE " + " AND ".join(where) + " GROUP BY day, hour_value, domain")
        out: List[Dict[str, Any]] = []
        for r in self._execute(sql, params):
            out.append({
                "day": str(r.get("day") or ""),
                "hour": int(r.get("hour_value") or 0),
                "domain": str(r.get("domain") or ""),
                "count": int(r.get("c") or 0),
            })
        return out

    def entity_stats(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                     domains: Any = None, behavior_only: bool = False) -> List[Dict[str, Any]]:
        """窗口内每个实体一行：count / first_ts / last_ts / active_days。

        room/domain 理论上实体恒定，用 MAX() 保证「一实体一行」。
        """
        where, params = self._event_where(tr, entity_ids, rooms, domains, behavior_only)
        sql = ("SELECT entity_id, MAX(room) AS room, MAX(domain) AS domain, COUNT(*) AS c, "
               "MIN(ts) AS first_ts, MAX(ts) AS last_ts, COUNT(DISTINCT day) AS active_days "
               "FROM events WHERE " + " AND ".join(where) +
               " GROUP BY entity_id ORDER BY c DESC, entity_id")
        out: List[Dict[str, Any]] = []
        for r in self._execute(sql, params):
            out.append({
                "entity_id": str(r.get("entity_id") or ""),
                "room": str(r.get("room") or ""),
                "domain": str(r.get("domain") or ""),
                "count": int(r.get("c") or 0),
                "first_ts": str(r.get("first_ts") or ""),
                "last_ts": str(r.get("last_ts") or ""),
                "active_days": int(r.get("active_days") or 0),
            })
        return out

    def entity_catalog(self, rooms: Any = None, domains: Any = None,
                       limit: Any = None) -> List[Dict[str, Any]]:
        """全量已知实体清单（契约 §五：只能从 events 聚合，不能查 entities 表）。"""
        where: List[str] = []
        params: List[Any] = []
        self._add_in(where, params, "room", rooms)
        self._add_in(where, params, "domain", domains)
        sql = ("SELECT entity_id, MAX(room) AS room, MAX(domain) AS domain, "
               "MAX(ts) AS last_ts, COUNT(*) AS total FROM events")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY entity_id ORDER BY entity_id LIMIT ?"
        params.append(self._top_n(limit, 5000))
        out: List[Dict[str, Any]] = []
        for r in self._execute(sql, params):
            out.append({
                "entity_id": str(r.get("entity_id") or ""),
                "room": str(r.get("room") or ""),
                "domain": str(r.get("domain") or ""),
                "last_ts": str(r.get("last_ts") or ""),
                "total": int(r.get("total") or 0),
            })
        return out

    def last_seen(self, tr: Any = None, entity_ids: Any = None,
                  rooms: Any = None) -> Dict[str, str]:
        """entity_id -> 最后一次出现的 ISO 时间（tr=None 表示全量）。"""
        where: List[str] = []
        params: List[Any] = []
        if tr is not None:
            _s, _e, start_day, end_day = self._bounds(tr)
            where += ["day BETWEEN ? AND ?", "ts BETWEEN ? AND ?"]
            params += [start_day, end_day, _s, _e]
        self._add_in(where, params, "entity_id", entity_ids)
        self._add_in(where, params, "room", rooms)
        sql = "SELECT entity_id, MAX(ts) AS last_ts FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY entity_id"
        return {str(r.get("entity_id") or ""): str(r.get("last_ts") or "")
                for r in self._execute(sql, params)}

    def state_counts(self, tr: Any, entity_id: str = None, rooms: Any = None,
                     domains: Any = None, limit: Any = None) -> List[Dict[str, Any]]:
        """new_state 的分布（注意：events 没有 state 列）。"""
        where, params = self._event_where(tr, entity_id, rooms, domains, False)
        sql = ("SELECT COALESCE(new_state, '') AS state_value, COUNT(*) AS c FROM events WHERE "
               + " AND ".join(where) + " GROUP BY state_value ORDER BY c DESC, state_value LIMIT ?")
        params.append(self._top_n(limit, 50))
        return [{"state": str(r.get("state_value") or ""), "count": int(r.get("c") or 0)}
                for r in self._execute(sql, params)]

    def action_counts(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                      domains: Any = None, limit: Any = None) -> List[Dict[str, Any]]:
        where, params = self._event_where(tr, entity_ids, rooms, domains, False)
        sql = ("SELECT COALESCE(action, '') AS action_value, COUNT(*) AS c FROM events WHERE "
               + " AND ".join(where) + " GROUP BY action_value ORDER BY c DESC, action_value LIMIT ?")
        params.append(self._top_n(limit, 50))
        return [{"action": str(r.get("action_value") or ""), "count": int(r.get("c") or 0)}
                for r in self._execute(sql, params)]

    def entity_action_counts(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                             domains: Any = None, limit: Any = None) -> List[Dict[str, Any]]:
        where, params = self._event_where(tr, entity_ids, rooms, domains, False)
        sql = ("SELECT entity_id, COALESCE(action, '') AS action_value, COUNT(*) AS c FROM events WHERE "
               + " AND ".join(where) + " GROUP BY entity_id, action_value LIMIT ?")
        params.append(self._top_n(limit, 5000))
        return [{"entity_id": str(r.get("entity_id") or ""),
                 "action": str(r.get("action_value") or ""),
                 "count": int(r.get("c") or 0)} for r in self._execute(sql, params)]

    def quality_counts(self, tr: Any, rooms: Any = None) -> Dict[str, int]:
        """字段完整性计数（一次聚合拿全）。"""
        where, params = self._event_where(tr, None, rooms, None, False)
        sql = ("SELECT COUNT(*) AS total, COUNT(DISTINCT day) AS active_days, "
               "SUM(CASE WHEN room IS NULL OR room = '' THEN 1 ELSE 0 END) AS empty_room, "
               "SUM(CASE WHEN domain IS NULL OR domain = '' THEN 1 ELSE 0 END) AS empty_domain, "
               "SUM(CASE WHEN entity_id IS NULL OR entity_id = '' THEN 1 ELSE 0 END) AS empty_entity, "
               "SUM(CASE WHEN new_state IS NULL OR new_state = '' THEN 1 ELSE 0 END) AS empty_state, "
               "SUM(CASE WHEN attrs_json IS NULL OR attrs_json = '' THEN 1 ELSE 0 END) AS empty_attrs, "
               "SUM(CASE WHEN action IS NULL OR action = '' THEN 1 ELSE 0 END) AS empty_action "
               "FROM events WHERE " + " AND ".join(where))
        rows = self._execute(sql, params)
        row = rows[0] if rows else {}
        keys = ("total", "active_days", "empty_room", "empty_domain",
                "empty_entity", "empty_state", "empty_attrs", "empty_action")
        return {k: int(row.get(k) or 0) for k in keys}

    def sample_rows(self, tr: Any, limit: Any = 500, rooms: Any = None) -> List[Dict[str, Any]]:
        """抽样原始行（供 attrs_json 可解析性检查）。"""
        where, params = self._event_where(tr, None, rooms, None, False)
        sql = ("SELECT ts, day, room, entity_id, domain, new_state, attrs_json FROM events WHERE "
               + " AND ".join(where) + " ORDER BY ts DESC LIMIT ?")
        params.append(self._top_n(limit, 500))
        return self._execute(sql, params)

    # ------------------------------------------------- 旁挂依赖（规则表/排除表）
    def activity_rules(self, enabled_only: bool = True) -> List[Dict[str, Any]]:
        """`activity_rules` 表（define_activity 的落库处，裁6 Q2=A 要求引擎真的读它）。

        表不存在/查询失败一律上抛（本层 fail-closed），由 service 层降级并在返回体的
        `rule_sources.activity_rules_error` 里留痕——静默回退成"没有规则"就是又一次
        把失败换成空结果。
        """
        sql = ("SELECT rule_id, name, room, tags_json, start_hour, end_hour, "
               "min_events, confidence, note, enabled FROM activity_rules")
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY name"
        out: List[Dict[str, Any]] = []
        for r in self._execute(sql):
            tags = r.get("tags_json")
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags or "[]")
                except (TypeError, ValueError):
                    tags = []
            out.append({
                "rule_id": str(r.get("rule_id") or ""),
                "name": str(r.get("name") or ""),
                "room": str(r.get("room") or ""),
                "tags": [str(t) for t in (tags or []) if str(t).strip()],
                "start_hour": int(r.get("start_hour") or 0),
                "end_hour": int(r.get("end_hour") or 23),
                "min_events": int(r.get("min_events") or 1),
                "confidence": float(r.get("confidence") or 0.0),
                "note": str(r.get("note") or ""),
                "enabled": bool(r.get("enabled")),
            })
        return out

    def signal_exclusions(self, include_revoked: bool = False) -> List[Dict[str, Any]]:
        """`signal_exclusions` 表（学习策略 teach_signal kind='hard' 的落库处）。"""
        sql = ("SELECT exclusion_id, entity_id, scope, exclusion_type, reason, revoked "
               "FROM signal_exclusions")
        if not include_revoked:
            sql += " WHERE revoked=0"
        sql += " ORDER BY entity_id, scope"
        return [{
            "exclusion_id": str(r.get("exclusion_id") or ""),
            "entity_id": str(r.get("entity_id") or ""),
            "scope": str(r.get("scope") or "all"),
            "exclusion_type": str(r.get("exclusion_type") or "exclude"),
            "reason": str(r.get("reason") or ""),
            "revoked": bool(r.get("revoked")),
        } for r in self._execute(sql)]

    def excluded_entity_ids(self) -> Dict[str, Any]:
        """活动推断要硬排除的实体清单 + 来源计数（裁6 Q3 的「已排除 N 个实体」）。

        两个来源：
        1. `signal_exclusions` 里生效且 `exclusion_type='exclude'` 的行——
           `is_automation` / `not_automation` 是**分类标注**（告诉 agent 这实体是不是自动化），
           不是排除，误当排除会把正常设备从活动里抹掉；
        2. `config.excluded_entities`（采集侧的显式排除；注入的是原始 app Config 时可见）。
        """
        ids: List[str] = []
        sources: Dict[str, int] = {"signal_exclusions": 0, "config": 0}
        scopes: Dict[str, int] = {}
        try:
            for row in self.signal_exclusions():
                if row["exclusion_type"] != "exclude":
                    continue
                eid = row["entity_id"]
                if not eid or eid in ids:
                    continue
                ids.append(eid)
                sources["signal_exclusions"] += 1
                scopes[row["scope"]] = scopes.get(row["scope"], 0) + 1
        except Exception as exc:  # noqa: BLE001 - 排除表读不到不能让整条查询外抛
            self.log.warning("signal_exclusions 读取失败，本轮按无硬排除处理: %s", exc)
            sources["signal_exclusions_error"] = "%s: %s" % (type(exc).__name__, exc)
        for eid in (getattr(self.config, "excluded_entities", None) or []):
            text = str(eid or "").strip()
            if text and text not in ids:
                ids.append(text)
                sources["config"] += 1
        return {"entity_ids": sorted(ids), "count": len(ids),
                "sources": sources, "scopes": scopes}

    # ------------------------------------------------- behavior / perception
    def behavior_summary(self, tr: Any, rooms: Any = None) -> List[Dict[str, Any]]:
        """behavior_events 按 (room, trigger) 聚合。"""
        where, params = self._behavior_where(tr, rooms)
        sql = ("SELECT room, COALESCE(\"trigger\", '') AS trigger_value, COUNT(*) AS c, "
               "SUM(COALESCE(\"count\", 0)) AS person_total, "
               "AVG(confidence) AS avg_confidence, AVG(vlm_latency_ms) AS avg_latency, "
               "SUM(CASE WHEN status IS NULL OR status = 'ok' THEN 0 ELSE 1 END) AS error_count "
               "FROM behavior_events WHERE " + " AND ".join(where) +
               " GROUP BY room, trigger_value ORDER BY c DESC")
        out: List[Dict[str, Any]] = []
        for r in self._execute(sql, params):
            out.append({
                "room": str(r.get("room") or ""),
                "trigger": str(r.get("trigger_value") or ""),
                "count": int(r.get("c") or 0),
                "person_total": int(r.get("person_total") or 0),
                "avg_confidence": float(r.get("avg_confidence") or 0.0),
                "avg_latency": float(r.get("avg_latency") or 0.0),
                "error_count": int(r.get("error_count") or 0),
            })
        return out

    def behavior_actions(self, tr: Any, rooms: Any = None, limit: Any = 20) -> List[Dict[str, Any]]:
        where, params = self._behavior_where(tr, rooms)
        sql = ("SELECT COALESCE(action, '') AS action_value, COUNT(*) AS c FROM behavior_events WHERE "
               + " AND ".join(where) + " GROUP BY action_value ORDER BY c DESC, action_value LIMIT ?")
        params.append(self._top_n(limit, 20))
        return [{"action": str(r.get("action_value") or ""), "count": int(r.get("c") or 0)}
                for r in self._execute(sql, params)]

    def behavior_hourly(self, tr: Any, rooms: Any = None) -> Dict[int, int]:
        where, params = self._behavior_where(tr, rooms)
        sql = ("SELECT CAST(substr(server_ts, 12, 2) AS INTEGER) AS hour_value, COUNT(*) AS c "
               "FROM behavior_events WHERE " + " AND ".join(where) + " GROUP BY hour_value")
        return {int(r.get("hour_value") or 0): int(r.get("c") or 0) for r in self._execute(sql, params)}

    def behavior_days(self, tr: Any, rooms: Any = None) -> Dict[str, int]:
        where, params = self._behavior_where(tr, rooms)
        sql = "SELECT day, COUNT(*) AS c FROM behavior_events WHERE " + " AND ".join(where) + " GROUP BY day ORDER BY day"
        return {str(r.get("day") or ""): int(r.get("c") or 0) for r in self._execute(sql, params)}

    def perception_summary(self, tr: Any, rooms: Any = None, kinds: Any = None,
                           limit: Any = 50) -> List[Dict[str, Any]]:
        """perception_events 按 (source, kind, room) 聚合。"""
        where, params = self._behavior_where(tr, rooms)
        self._add_in(where, params, "kind", kinds)
        sql = ("SELECT source, kind, COALESCE(room, '') AS room_value, COUNT(*) AS c, "
               "AVG(confidence) AS avg_confidence FROM perception_events WHERE "
               + " AND ".join(where) + " GROUP BY source, kind, room_value ORDER BY c DESC LIMIT ?")
        params.append(self._top_n(limit, 50))
        out: List[Dict[str, Any]] = []
        for r in self._execute(sql, params):
            out.append({
                "source": str(r.get("source") or ""),
                "kind": str(r.get("kind") or ""),
                "room": str(r.get("room_value") or ""),
                "count": int(r.get("c") or 0),
                "avg_confidence": float(r.get("avg_confidence") or 0.0),
            })
        return out
    # ------------------------------------------------------------------
    # api.py 兼容接口
    # ------------------------------------------------------------------
    def list_entities(self) -> List[Any]:
        """api.py 用此方法构建 EntityResolver。

        P2：之前直接复用 entity_catalog()，但它返回的字段只有
        entity_id/room/domain/last_ts/total，根本没有 friendly_name/category/unit，
        导致 resolver 里所有实体的友好名和单位全是空串。
        改为取每个实体最新一条事件的 attrs_json 解析 friendly_name / unit。

        性能修复：原写法用相关子查询（每行两次全表扫描），events 表达百万行时
        启动需几十分钟。改为 JOIN + GROUP BY，一次扫描完成分组聚合。
        """
        from .models import EntityInfo
        sql = ("SELECT e.entity_id, e.room, e.domain, e.attrs_json, c.total "
               "FROM events e "
               "JOIN (SELECT entity_id, MAX(rowid) AS max_rowid, COUNT(*) AS total "
               "      FROM events GROUP BY entity_id) c "
               "ON e.rowid = c.max_rowid "
               "ORDER BY e.entity_id LIMIT ?")
        rows = self._execute(sql, (self._top_n(None, 5000),))
        out = []
        for r in rows:
            attrs = _load_attrs(r.get("attrs_json"))
            entity_id = str(r.get("entity_id") or "")
            friendly = attrs.get("friendly_name") or entity_id
            unit = attrs.get("unit_of_measurement") or attrs.get("unit") or ""
            try:
                out.append(EntityInfo(
                    entity_id=entity_id,
                    friendly_name=str(friendly),
                    room=str(r.get("room") or ""),
                    domain=str(r.get("domain") or ""),
                    category="",  # category 由 resolver.resolve 按 domain 推，不在此落库
                    unit=str(unit),
                ))
            except Exception:
                continue
        return out

    def invalidate(self) -> None:
        """api.py 调用此方法使缓存失效。StoreRepository 无缓存，空操作。"""
        pass


# ----------------------------------------------------------------------
# 工厂与基类（api.py 兼容）
# ----------------------------------------------------------------------
class BaseRepository:
    """仓储基类（api.py 类型标注用）。"""
    pass


def build_repository(store: Any, config: Any) -> StoreRepository:
    """从生产 Store 构建 StoreRepository（api.py 工厂函数）。"""
    return StoreRepository(store, config)
