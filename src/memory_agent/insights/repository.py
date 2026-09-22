"""数据访问层：SQL 查询封装 + 缓存 + 降级。

- ``StoreRepository``：对接现有项目 Store 类（真实数据库）；
- ``MemoryRepository``：内存实现，用于单测与数据库故障时的降级；
- 所有查询都返回 (records, total)，由上层组装分页信封。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .models import EntityInfo, EventRecord, InsightConfig, fmt_ts
from .parser.entity import category_of_domain, domain_of
from .parser.timeframe import as_ts, parse_time

LOG = logging.getLogger(__name__)

__all__ = [
    "BaseRepository", "MemoryRepository", "StoreRepository", "TTLCache",
    "build_repository", "SQL_EVENTS", "SQL_EVENTS_COUNT", "SQL_ENTITIES",
    "SQL_LAST_SEEN",
]

# --------------------------------------------------------------------------
# SQL（假设表结构：events(ts, entity_id, state, attributes)，
#       实体元信息可选表 entities(entity_id, friendly_name, room, ...)）
# 行解析是宽容的：列名/时间列/attributes 列都做了别名兼容。
# --------------------------------------------------------------------------
SQL_EVENTS = (
    "SELECT entity_id, state, attributes, ts FROM events "
    "WHERE ts >= ? AND ts <= ? {entity_filter} ORDER BY ts ASC LIMIT ? OFFSET ?"
)
SQL_EVENTS_COUNT = (
    "SELECT COUNT(*) AS cnt FROM events "
    "WHERE ts >= ? AND ts <= ? {entity_filter}"
)
SQL_ENTITIES = (
    "SELECT entity_id, friendly_name, room, domain, category, unit, enabled "
    "FROM entities"
)
SQL_RECENT = (
    "SELECT entity_id, state, attributes, ts FROM events ORDER BY ts DESC LIMIT ?"
)
SQL_LAST_SEEN = "SELECT entity_id, MAX(ts) AS ts FROM events GROUP BY entity_id"

_ROW_ALIASES: Dict[str, Tuple[str, ...]] = {
    "ts": ("ts", "timestamp", "time", "created_at", "occurred_at", "when", "last_seen"),
    "entity_id": ("entity_id", "entity", "entityId", "object_id"),
    "state": ("state", "value", "new_state"),
    "attributes": ("attributes", "attrs", "data", "extra"),
    "friendly_name": ("friendly_name", "name", "title"),
    "room": ("room", "area", "area_name", "room_name"),
    "unit": ("unit", "unit_of_measurement"),
    "enabled": ("enabled", "is_enabled"),
}


class TTLCache:
    """简单 TTL 缓存（时间过期 + 手动失效）。"""

    def __init__(self, ttl: float = 300.0) -> None:
        self.ttl = float(ttl)
        self._data: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            hit = self._data.get(key)
            if not hit:
                return None
            expire_at, value = hit
            if time.time() >= expire_at:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> Any:
        with self._lock:
            self._data[key] = (time.time() + (self.ttl if ttl is None else ttl), value)
        return value

    def invalidate(self, prefix: str = "") -> None:
        """数据变更时调用：清空全部或指定前缀的缓存。"""
        with self._lock:
            if not prefix:
                self._data.clear()
                return
            for key in [k for k in self._data if k.startswith(prefix)]:
                self._data.pop(key, None)

    def __len__(self) -> int:
        return len(self._data)


def _pick(row: Mapping[str, Any], kind: str, default: Any = None) -> Any:
    for key in _ROW_ALIASES.get(kind, (kind,)):
        if key in row and row[key] is not None:
            return row[key]
    return default


def _as_attrs(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "ignore")
    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
            return loaded if isinstance(loaded, Mapping) else {}
        except ValueError:
            return {}
    return {}


class BaseRepository(ABC):
    """仓储接口：所有上层只依赖这层，方便 mock 测试。"""

    def __init__(self, config: Optional[InsightConfig] = None) -> None:
        self.config = config or InsightConfig()

    @abstractmethod
    def list_entities(self) -> List[EntityInfo]:
        """实体目录。"""

    @abstractmethod
    def fetch_events(self, start_ts: float, end_ts: float,
                     entity_ids: Optional[Sequence[str]] = None,
                     limit: Optional[int] = None,
                     offset: int = 0) -> List[EventRecord]:
        """按时间范围取事件（升序，已裁剪）。"""

    @abstractmethod
    def count_events(self, start_ts: float, end_ts: float,
                     entity_ids: Optional[Sequence[str]] = None) -> int:
        """统计总数（透明性：total / offset / has_more）。"""

    @abstractmethod
    def last_seen(self, entity_ids: Optional[Sequence[str]] = None) -> Dict[str, float]:
        """每个实体最后一次出现的时间戳。"""

    def query_events(self, start_ts: float, end_ts: float,
                     entity_ids: Optional[Sequence[str]] = None,
                     limit: int = 100, offset: int = 0
                     ) -> Tuple[List[EventRecord], int]:
        total = self.count_events(start_ts, end_ts, entity_ids)
        rows = self.fetch_events(start_ts, end_ts, entity_ids,
                                 limit=limit, offset=offset)
        return rows, total

    def invalidate(self) -> None:
        """数据变更后调用（缓存失效策略：时间过期 + 数据变更）。"""

    def health(self) -> bool:
        return True


class MemoryRepository(BaseRepository):
    """内存仓储：单测与降级模式使用。"""

    def __init__(self, records: Optional[Iterable[EventRecord]] = None,
                 entities: Optional[Iterable[EntityInfo]] = None,
                 config: Optional[InsightConfig] = None) -> None:
        super().__init__(config)
        self.records: List[EventRecord] = sorted(
            list(records or []), key=lambda r: (r.ts, r.entity_id))
        self.entities: List[EntityInfo] = list(entities or [])

    def add_events(self, records: Iterable[EventRecord]) -> None:
        self.records.extend(records)
        self.records.sort(key=lambda r: (r.ts, r.entity_id))
        self.invalidate()

    def list_entities(self) -> List[EntityInfo]:
        return list(self.entities)

    def _filter(self, start_ts: float, end_ts: float,
                entity_ids: Optional[Sequence[str]]) -> List[EventRecord]:
        wanted = set(entity_ids) if entity_ids is not None else None
        return [r for r in self.records
                if start_ts <= r.ts <= r.end_ts if True] if False else [
            r for r in self.records
            if start_ts <= r.ts <= r.end_ts
            and (wanted is None or r.entity_id in wanted)
        ]

    def fetch_events(self, start_ts: float, end_ts: float,
                     entity_ids: Optional[Sequence[str]] = None,
                     limit: Optional[int] = None,
                     offset: int = 0) -> List[EventRecord]:
        rows = self._filter(start_ts, end_ts, entity_ids)
        if limit is None:
            return rows[offset:]
        return rows[offset:offset + max(0, int(limit))]

    def count_events(self, start_ts: float, end_ts: float,
                     entity_ids: Optional[Sequence[str]] = None) -> int:
        return len(self._filter(start_ts, end_ts, entity_ids))

    def last_seen(self, entity_ids: Optional[Sequence[str]] = None) -> Dict[str, float]:
        wanted = set(entity_ids) if entity_ids is not None else None
        out: Dict[str, float] = {}
        for row in self.records:
            if wanted is not None and row.entity_id not in wanted:
                continue
            if row.entity_id not in out or row.ts > out[row.entity_id]:
                out[row.entity_id] = row.ts
        return out


class StoreRepository(BaseRepository):
    """基于现有项目 ``Store`` 类的数据访问层。

    适配策略（不修改数据库 schema）：
    1. 依次探测 ``query`` / ``db_query`` / ``execute`` / ``conn``；
    2. 行字段名做别名兼容，attributes 支持 JSON 文本或 dict；
    3. 任何数据库异常都只记日志并返回空结果（降级，不抛异常）。
    """

    def __init__(self, store: Any, config: Optional[InsightConfig] = None) -> None:
        super().__init__(config)
        self.store = store
        self.cache = TTLCache(self.config.cache_ttl)

    # ---------------- 底层执行 ----------------
    def _run(self, sql: str, params: Sequence[Any] = ()) -> List[Mapping[str, Any]]:
        try:
            rows = self._execute(sql, params)
        except Exception as exc:  # noqa: BLE001 - 数据库错误必须降级
            LOG.warning("SQL 执行失败，返回降级空结果: %s (%s)", sql[:80], exc)
            return []
        return list(rows or [])

    def _execute(self, sql: str, params: Sequence[Any]) -> Any:
        paramstyle = getattr(self.store, "paramstyle", "qmark")
        if paramstyle == "format":
            sql = sql.replace("?", "%s")
        for name in ("query", "db_query", "execute", "fetchall"):
            fn = getattr(self.store, name, None)
            if callable(fn):
                return fn(sql, tuple(params))
        conn = getattr(self.store, "conn", None) or getattr(self.store, "connection", None)
        if conn is not None:
            cursor = conn.execute(sql, tuple(params))
            return cursor.fetchall()
        raise RuntimeError("Store 不支持查询接口")

    @staticmethod
    def _to_event(row: Mapping[str, Any]) -> Optional[EventRecord]:
        entity_id = _pick(row, "entity_id", "")
        ts = as_ts(_pick(row, "ts", None))
        if not entity_id or ts is None:
            return None
        attrs = _as_attrs(_pick(row, "attributes", {}))
        return EventRecord(
            ts=float(ts),
            entity_id=str(entity_id),
            state=str(_pick(row, "state", "")),
            attributes=attrs,
            friendly_name=str(_pick(row, "friendly_name", "")
                              or attrs.get("friendly_name", "")),
            room=str(_pick(row, "room", "") or attrs.get("room", "")
                     or attrs.get("area", "")),
            domain=domain_of(str(entity_id)),
            unit=str(_pick(row, "unit", "") or attrs.get("unit_of_measurement", "")),
        )

    @staticmethod
    def _to_entity(row: Mapping[str, Any]) -> Optional[EntityInfo]:
        entity_id = _pick(row, "entity_id", "")
        if not entity_id:
            return None
        attrs = _as_attrs(_pick(row, "attributes", {}))
        domain = str(row.get("domain") or "") or domain_of(str(entity_id))
        category = str(row.get("category") or "") or category_of_domain(domain)
        enabled = _pick(row, "enabled", True)
        return EntityInfo(
            entity_id=str(entity_id),
            friendly_name=str(_pick(row, "friendly_name", "")
                              or attrs.get("friendly_name", "")),
            room=str(_pick(row, "room", "") or attrs.get("room", "")
                     or attrs.get("area", "")),
            domain=domain,
            category=category,
            unit=str(_pick(row, "unit", "") or attrs.get("unit_of_measurement", "")),
            enabled=bool(enabled) if enabled is not None else True,
        )

    # ---------------- 接口实现 ----------------
    def list_entities(self) -> List[EntityInfo]:
        cached = self.cache.get("entities")
        if cached is not None:
            return cached
        rows = self._run(SQL_ENTITIES)
        infos: List[EntityInfo] = []
        for row in rows:
            item = self._to_entity(row)
            if item:
                infos.append(item)
        if not infos:  # 没有实体表时，从最近事件推断目录
            for row in self._run(SQL_RECENT, (self.config.max_scan,)):
                item = self._to_entity(row)
                if item and all(i.entity_id != item.entity_id for i in infos):
                    infos.append(item)
        return self.cache.set("entities", infos)

    def fetch_events(self, start_ts: float, end_ts: float,
                     entity_ids: Optional[Sequence[str]] = None,
                     limit: Optional[int] = None,
                     offset: int = 0) -> List[EventRecord]:
        key = "ev:%s:%s:%s:%s:%s" % (start_ts, end_ts,
                                     tuple(entity_ids) if entity_ids else None,
                                     limit, offset)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        clause, params = self._entity_clause(entity_ids)
        lim = int(limit if limit is not None else self.config.max_scan)
        sql = SQL_EVENTS.format(entity_filter=clause)
        rows = self._run(sql, (start_ts, end_ts) + params + (lim, int(offset)))
        events = [e for e in (self._to_event(r) for r in rows) if e]
        return self.cache.set(key, events)

    def count_events(self, start_ts: float, end_ts: float,
                     entity_ids: Optional[Sequence[str]] = None) -> int:
        key = "cnt:%s:%s:%s" % (start_ts, end_ts,
                                tuple(entity_ids) if entity_ids else None)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        clause, params = self._entity_clause(entity_ids)
        rows = self._run(SQL_EVENTS_COUNT.format(entity_filter=clause),
                         (start_ts, end_ts) + params)
        count = int(_pick(rows[0], "state", 0)) if rows else 0
        if rows and count == 0:
            count = int(list(rows[0].values())[0] or 0)
        return int(self.cache.set(key, count))

    def last_seen(self, entity_ids: Optional[Sequence[str]] = None) -> Dict[str, float]:
        cached = self.cache.get("last_seen")
        if cached is not None:
            result = dict(cached)
        else:
            result = {}
            for row in self._run(SQL_LAST_SEEN):
                entity_id = _pick(row, "entity_id", "")
                ts = as_ts(_pick(row, "ts", None))
                if entity_id and ts is not None:
                    result[str(entity_id)] = float(ts)
            self.cache.set("last_seen", result)
        if entity_ids is not None:
            wanted = set(entity_ids)
            result = {k: v for k, v in result.items() if k in wanted}
        return result

    @staticmethod
    def _entity_clause(entity_ids: Optional[Sequence[str]]
                       ) -> Tuple[str, Tuple[Any, ...]]:
        if not entity_ids:
            return "", ()
        marks = ",".join("?" for _ in entity_ids)
        return " AND entity_id IN (%s)" % marks, tuple(entity_ids)

    def invalidate(self) -> None:
        self.cache.invalidate()

    def health(self) -> bool:
        try:
            self._execute("SELECT 1", ())
            return True
        except Exception:  # noqa: BLE001
            return False


def build_repository(store: Any = None,
                     config: Optional[InsightConfig] = None) -> BaseRepository:
    """工厂：有 Store 用 SQL 仓储，否则/异常时降级为内存仓储。"""
    if store is None:
        return MemoryRepository(config=config)
    try:
        repo = StoreRepository(store, config)
        if not repo.health():
            LOG.warning("Store 不可用，降级为内存仓储")
            return MemoryRepository(config=config)
        return repo
    except Exception as exc:  # noqa: BLE001
        LOG.warning("初始化 StoreRepository 失败，降级: %s", exc)
        return MemoryRepository(config=config)
