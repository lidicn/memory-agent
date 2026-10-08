# -*- coding: utf-8 -*-
"""MA 多模态统一数据模型：``unified_events`` 表 + 双写 + 统一查询。

职责
----
1. 在现有 sqlite 库里幂等创建 ``unified_events``（含索引）。
2. 双写写入口 ``write_event()``：**fail-open**，任何异常只记日志并返回
   ``{"ok": False, ...}``，绝不影响调用方主流程。
3. 统一查询 ``query_events(person, room, start, end) -> List[dict]``。

硬约束（交付单 §2.2）
--------------------
* 只用 sqlite3 + 标准库，不引入第三方依赖；
* 不迁移旧数据、不删旧表、不改 events / perception_events 结构；
* 本模块不 import store.py / agent_memory.py，避免与现有实现耦合。

数据规范
--------
* 时间戳：ISO8601、UTC+8，``2026-09-28T23:53:09+08:00``；亚秒非零时带 6 位小数
  ``2026-09-28T23:53:09.123456+08:00``。输入允许 None / datetime / Unix 时间戳 /
  ISO8601 字符串（Z、±HH:MM、±HHMM、空格分隔均接受）；naive 值按 UTC+8 解释。
* person：空字符串表示"未识别"，永不写 NULL；room 同理，``''`` 表示未知。
* source  ∈ device | vision | perception | activity | llm（大小写不敏感，落库小写）
* modality ∈ sensor | image | audio | text（同上）
* payload：JSON 对象（dict），落库为紧凑、键排序文本，保证可复现。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "TZ_UTC8",
    "SOURCES",
    "MODALITIES",
    "SCHEMA_SQL",
    "INDEX_SQL",
    "DB_PATH_ENV",
    "UnifiedEventStore",
    "coerce_datetime",
    "format_ts",
    "normalize_event",
    "resolve_db_path",
    "get_store",
    "reset_stores",
    "init_schema",
    "write_event",
    "query_events",
]

logger = logging.getLogger("memory_agent.unified_store")

TZ_UTC8 = timezone(timedelta(hours=8), "+08:00")

SOURCES: Tuple[str, ...] = ("device", "vision", "perception", "activity", "llm")
MODALITIES: Tuple[str, ...] = ("sensor", "image", "audio", "text")

DB_PATH_ENV = "MEMORY_AGENT_DB_PATH"
DEFAULT_DB_NAME = "memory_agent.db"

SCHEMA_SQL = (
    "CREATE TABLE IF NOT EXISTS unified_events (\n"
    "    id           INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    ts           TEXT    NOT NULL,\n"
    "    source       TEXT    NOT NULL CHECK (source IN "
    "('device','vision','perception','activity','llm')),\n"
    "    modality     TEXT    NOT NULL CHECK (modality IN "
    "('sensor','image','audio','text')),\n"
    "    person       TEXT    NOT NULL DEFAULT '',\n"
    "    room         TEXT    NOT NULL DEFAULT '',\n"
    "    event_type   TEXT    NOT NULL DEFAULT '',\n"
    "    payload      TEXT    NOT NULL DEFAULT '{}',\n"
    "    created_at   TEXT    NOT NULL\n"
    ")"
)

INDEX_SQL: Tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_unified_events_ts ON unified_events (ts)",
    "CREATE INDEX IF NOT EXISTS idx_unified_events_person_ts ON unified_events (person, ts)",
    "CREATE INDEX IF NOT EXISTS idx_unified_events_room_ts ON unified_events (room, ts)",
    "CREATE INDEX IF NOT EXISTS idx_unified_events_source_ts ON unified_events (source, ts)",
    "CREATE INDEX IF NOT EXISTS idx_unified_events_modality_ts ON unified_events (modality, ts)",
)

_ACCEPTED_KEYS = frozenset(
    ("ts", "timestamp", "source", "modality", "person", "room", "event_type", "payload")
)


# --------------------------------------------------------------------------
# 时间戳
# --------------------------------------------------------------------------
def coerce_datetime(value: Any) -> datetime:
    """把 None / datetime / Unix 时间戳 / ISO8601 字符串转成带时区的 datetime。

    - ``None``         -> 现在（UTC+8）
    - naive datetime   -> 按 UTC+8 墙上时间解释
    - aware datetime   -> 保留时区，由调用方 ``astimezone`` 归一
    - int / float      -> Unix 时间戳（UTC 语义）
    - str              -> ISO8601；naive 字符串按 UTC+8 解释
    """
    if value is None:
        return datetime.now(TZ_UTC8)

    if isinstance(value, datetime):
        return value.replace(tzinfo=TZ_UTC8) if value.tzinfo is None else value

    if isinstance(value, bool):
        raise TypeError("ts 不接受 bool")

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, TZ_UTC8)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("ts 数字超出可表示范围: %r" % (value,)) from exc

    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("ts 不能是空字符串（需要\"此刻\"请传 None）")
        if text[-1] in ("Z", "z"):
            text = text[:-1] + "+00:00"
        text = _insert_offset_colon(text)
        if "T" not in text and " " in text:
            text = text.replace(" ", "T", 1)
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("ts 不是合法 ISO8601 字符串: %r" % (value,)) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TZ_UTC8)
        return parsed

    raise TypeError("ts 类型不支持: %s" % type(value).__name__)


def _insert_offset_colon(text: str) -> str:
    """``+0800`` -> ``+08:00``，兼容 Python < 3.11 的 fromisoformat。"""
    for sign in ("+", "-"):
        idx = text.rfind(sign)
        if idx > 0:
            tail = text[idx + 1:]
            if tail.isdigit() and len(tail) == 4:
                return text[:idx + 1] + tail[:2] + ":" + tail[2:]
    return text


def format_ts(value: Any = None) -> str:
    """归一化为 ISO8601 UTC+8 字符串（本模块唯一认可的时间格式）。"""
    dt = coerce_datetime(value).astimezone(TZ_UTC8)
    head = dt.strftime("%Y-%m-%dT%H:%M:%S")
    if dt.microsecond:
        return "%s.%06d+08:00" % (head, dt.microsecond)
    return head + "+08:00"


# --------------------------------------------------------------------------
# 字段归一化
# --------------------------------------------------------------------------
def _norm_text(value: Any, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError("%s 必须是 str 或 None，收到 %s" % (field, type(value).__name__))
    return value.strip()


def _norm_enum(value: Any, field: str, allowed: Tuple[str, ...]) -> str:
    if value is None:
        raise ValueError("%s 必填，允许值: %s" % (field, "/".join(allowed)))
    if not isinstance(value, str):
        raise TypeError("%s 必须是 str，收到 %s" % (field, type(value).__name__))
    text = value.strip().lower()
    if text not in allowed:
        raise ValueError("%s 非法: %r，允许值: %s" % (field, value, "/".join(allowed)))
    return text


def _norm_payload(value: Any) -> Tuple[Dict[str, Any], str]:
    if value is None:
        return {}, "{}"
    if not isinstance(value, Mapping):
        raise TypeError("payload 必须是 dict/mapping，收到 %s" % type(value).__name__)
    try:
        text = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("payload 无法序列化为 JSON: %s" % exc) from exc
    return dict(value), text


def normalize_event(event: Mapping[str, Any]) -> Dict[str, Any]:
    """校验 + 归一化一条事件，返回统一结构（非法输入抛 TypeError/ValueError）。"""
    if not isinstance(event, Mapping):
        raise TypeError("event 必须是 mapping/dict，收到 %s" % type(event).__name__)

    data = dict(event)
    unknown = sorted(str(k) for k in data if k not in _ACCEPTED_KEYS)
    if unknown:
        logger.warning("unified_store: 忽略未识别字段（不写入 unified_events）: %s", ", ".join(unknown))

    raw_ts = data["ts"] if data.get("ts") is not None else data.get("timestamp")
    payload, payload_json = _norm_payload(data.get("payload"))

    return {
        "ts": format_ts(raw_ts),
        "source": _norm_enum(data.get("source"), "source", SOURCES),
        "modality": _norm_enum(data.get("modality"), "modality", MODALITIES),
        "person": _norm_text(data.get("person"), "person"),
        "room": _norm_text(data.get("room"), "room"),
        "event_type": _norm_text(data.get("event_type"), "event_type"),
        "payload": payload,
        "payload_json": payload_json,
        "created_at": format_ts(None),
    }


def _row_to_event(row: sqlite3.Row) -> Dict[str, Any]:
    raw = row["payload"]
    payload: Dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
        else:
            logger.warning("unified_events.id=%s 的 payload 不是 JSON 对象，降级为 _raw", row["id"])
            payload = {"_raw": raw}
    return {
        "id": row["id"],
        "ts": row["ts"],
        "source": row["source"],
        "modality": row["modality"],
        "person": row["person"],
        "room": row["room"],
        "event_type": row["event_type"],
        "payload": payload,
        "created_at": row["created_at"],
    }


def _norm_order(order: Any) -> str:
    if not isinstance(order, str):
        raise TypeError("order 必须是 str，收到 %s" % type(order).__name__)
    text = order.strip().lower()
    if text in ("asc", "ascending"):
        return "ASC"
    if text in ("desc", "descending"):
        return "DESC"
    raise ValueError("order 只能是 asc/desc，收到 %r" % (order,))


# --------------------------------------------------------------------------
# 存储
# --------------------------------------------------------------------------
class UnifiedEventStore:
    """unified_events 的写入与查询。写路径永不抛异常，查询参数非法会抛。"""

    def __init__(self, db_path: Any, connection_timeout: float = 5.0) -> None:
        self.db_path = str(db_path)
        self.connection_timeout = float(connection_timeout)
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=self.connection_timeout)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        """幂等建表 + 建索引。只针对 unified_events，不触碰任何旧表。"""
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            conn = self._connect()
            try:
                conn.execute(SCHEMA_SQL)
                for stmt in INDEX_SQL:
                    conn.execute(stmt)
                conn.commit()
            finally:
                conn.close()
            self._schema_ready = True

    def write_event(self, event: Mapping[str, Any]) -> Dict[str, Any]:
        """双写入口。失败只记日志，返回 ``{"ok": False, "stage": ..., "error": ...}``。"""
        try:
            normalized = normalize_event(event)
        except (TypeError, ValueError) as exc:
            logger.warning("unified_store.write_event 参数非法，双写已跳过: %s", exc)
            return {"ok": False, "error": str(exc), "stage": "validation"}

        try:
            self.init_schema()
            conn = self._connect()
            try:
                cur = conn.execute(
                    "INSERT INTO unified_events "
                    "(ts, source, modality, person, room, event_type, payload, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        normalized["ts"],
                        normalized["source"],
                        normalized["modality"],
                        normalized["person"],
                        normalized["room"],
                        normalized["event_type"],
                        normalized["payload_json"],
                        normalized["created_at"],
                    ),
                )
                conn.commit()
                row_id = cur.lastrowid
            finally:
                conn.close()
        except Exception as exc:  # 双写 fail-open：吞掉一切异常
            logger.exception("unified_store.write_event 双写失败（已忽略，不影响主流程）: %s", exc)
            return {"ok": False, "error": str(exc), "stage": "storage"}

        return {
            "ok": True,
            "id": row_id,
            "event": {
                "id": row_id,
                "ts": normalized["ts"],
                "source": normalized["source"],
                "modality": normalized["modality"],
                "person": normalized["person"],
                "room": normalized["room"],
                "event_type": normalized["event_type"],
                "payload": normalized["payload"],
                "created_at": normalized["created_at"],
            },
        }

    def query_events(
        self,
        person: Optional[str] = None,
        room: Optional[str] = None,
        start: Any = None,
        end: Any = None,
        source: Optional[str] = None,
        modality: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: Optional[int] = None,
        order: str = "asc",
    ) -> List[Dict[str, Any]]:
        """统一查询。``person=None``/``room=None`` 不过滤；``''`` 只查未识别/未知。

        时间区间为双闭区间（``ts >= start AND ts <= end``）。
        参数非法抛 TypeError/ValueError；存储异常返回 [] 并记日志。
        """
        person_f = None if person is None else _norm_text(person, "person")
        room_f = None if room is None else _norm_text(room, "room")
        start_f = None if start is None else format_ts(start)
        end_f = None if end is None else format_ts(end)
        source_f = None if source is None else _norm_enum(source, "source", SOURCES)
        modality_f = None if modality is None else _norm_enum(modality, "modality", MODALITIES)
        type_f = None if event_type is None else _norm_text(event_type, "event_type")

        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise TypeError("limit 必须是 int 或 None，收到 %s" % type(limit).__name__)
            if limit < 0:
                raise ValueError("limit 不能为负数: %r" % (limit,))
        order_f = _norm_order(order)

        if start_f is not None and end_f is not None and start_f > end_f:
            logger.debug("unified_store.query_events: start > end，返回空列表")
            return []

        where = ["1=1"]
        params: List[Any] = []
        if person_f is not None:
            where.append("person = ?")
            params.append(person_f)
        if room_f is not None:
            where.append("room = ?")
            params.append(room_f)
        if start_f is not None:
            where.append("ts >= ?")
            params.append(start_f)
        if end_f is not None:
            where.append("ts <= ?")
            params.append(end_f)
        if source_f is not None:
            where.append("source = ?")
            params.append(source_f)
        if modality_f is not None:
            where.append("modality = ?")
            params.append(modality_f)
        if type_f is not None:
            where.append("event_type = ?")
            params.append(type_f)

        sql = (
            "SELECT id, ts, source, modality, person, room, event_type, payload, created_at "
            "FROM unified_events WHERE " + " AND ".join(where) +
            " ORDER BY ts " + order_f + ", id " + order_f
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        try:
            self.init_schema()
            conn = self._connect()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        except Exception as exc:  # 查询 fail-open
            logger.exception("unified_store.query_events 查询失败，返回空列表: %s", exc)
            return []
        return [_row_to_event(r) for r in rows]


# --------------------------------------------------------------------------
# 模块级便捷入口（MCP 工具体里直接用这一层）
# --------------------------------------------------------------------------
_store_cache: Dict[str, UnifiedEventStore] = {}
_store_lock = threading.Lock()


def resolve_db_path(db_path: Optional[str] = None) -> str:
    if db_path:
        return str(db_path)
    env = os.environ.get(DB_PATH_ENV)
    if env:
        return env
    return DEFAULT_DB_NAME


def get_store(db_path: Optional[str] = None) -> UnifiedEventStore:
    path = resolve_db_path(db_path)
    with _store_lock:
        store = _store_cache.get(path)
        if store is None:
            store = UnifiedEventStore(path)
            _store_cache[path] = store
        return store


def reset_stores() -> None:
    """清空缓存的 store 实例（测试用）。"""
    with _store_lock:
        _store_cache.clear()


def init_schema(db_path: Optional[str] = None) -> None:
    get_store(db_path).init_schema()


def _merge_event(event: Any, fields: Dict[str, Any]) -> Dict[str, Any]:
    if event is None:
        merged: Dict[str, Any] = {}
    elif isinstance(event, Mapping):
        merged = dict(event)
    else:
        raise TypeError("event 必须是 mapping/dict，收到 %s" % type(event).__name__)
    if fields:
        merged.update(fields)
    return merged


def write_event(event: Any = None, *, db_path: Optional[str] = None, **fields: Any) -> Dict[str, Any]:
    """双写（支持字典或关键字两种调用方式）；任何情况下都不抛异常。"""
    try:
        merged = _merge_event(event, fields)
    except (TypeError, ValueError) as exc:
        logger.warning("unified_store.write_event 参数非法，双写已跳过: %s", exc)
        return {"ok": False, "error": str(exc), "stage": "validation"}
    try:
        return get_store(db_path).write_event(merged)
    except Exception as exc:  # 兜底：绝不让双写影响主流程
        logger.exception("unified_store.write_event 双写失败（已忽略，不影响主流程）: %s", exc)
        return {"ok": False, "error": str(exc), "stage": "storage"}


def query_events(
    person: Optional[str] = None,
    room: Optional[str] = None,
    start: Any = None,
    end: Any = None,
    source: Optional[str] = None,
    modality: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: Optional[int] = None,
    order: str = "asc",
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return get_store(db_path).query_events(
        person=person,
        room=room,
        start=start,
        end=end,
        source=source,
        modality=modality,
        event_type=event_type,
        limit=limit,
        order=order,
    )