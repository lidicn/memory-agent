"""SQLite 数据访问层 —— 行为事件主存储 / 采集日历 / 采集任务

设计要点
--------
* 仅使用标准库 ``sqlite3``，零额外依赖；数据库落在 ``/data/memory_agent.db``。
* WAL 模式，读写不互斥，采集期间 WebUI 仍可正常查询。
* **所有方法均为同步实现**，调用方负责用 ``asyncio.to_thread`` 包裹，
  避免阻塞事件循环（这是采集不把 WebUI 卡死的前提）。
* 单连接 + ``RLock`` 保护，``check_same_thread=False`` 以适配线程池。
* 事件主键为 ``sha1(entity_id|原始时间戳)``，配合 ``INSERT OR REPLACE``
  保证重复回填同一区间完全幂等。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

SCHEMA_VERSION = 1

# 纯遥测域：这些域的实体按固定周期上报（功率、温湿度、电量……），
# 混进「行为分布」会把小时热力图污染成均匀的「电表节拍」，
# 因此所有行为类聚合默认排除它们（behavior_only=True）。
TELEMETRY_DOMAINS: tuple[str, ...] = (
    "sensor",
    "number",
    "weather",
    "sun",
    "update",
    "button",
    "select",
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    ts          TEXT NOT NULL,
    day         TEXT NOT NULL,
    room        TEXT NOT NULL DEFAULT '',
    entity_id   TEXT NOT NULL,
    domain      TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL DEFAULT '',
    person      TEXT NOT NULL DEFAULT '',
    old_state   TEXT,
    new_state   TEXT,
    attrs_json  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_day       ON events(day);
CREATE INDEX IF NOT EXISTS idx_events_room_ts   ON events(room, ts);
CREATE INDEX IF NOT EXISTS idx_events_entity_ts ON events(entity_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_ts        ON events(ts);

-- Agent 记忆（参与式写回向量库）：元数据权威源，chroma 仅存标量镜像
CREATE TABLE IF NOT EXISTS agent_memories (
    memory_id            TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL,
    text                 TEXT NOT NULL,
    topic_key            TEXT NOT NULL DEFAULT '',
    tags_json            TEXT NOT NULL DEFAULT '[]',
    source_refs_json     TEXT NOT NULL DEFAULT '[]',
    state                TEXT NOT NULL DEFAULT 'staging',
    trust                REAL NOT NULL DEFAULT 0.0,
    ttl_days             INTEGER NOT NULL DEFAULT 30,
    auto_promote_blocked INTEGER NOT NULL DEFAULT 0,
    mirror_dirty         INTEGER NOT NULL DEFAULT 0,
    feedback_up          INTEGER NOT NULL DEFAULT 0,
    feedback_down        INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    expires_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_mem_state   ON agent_memories(state);
CREATE INDEX IF NOT EXISTS idx_agent_mem_session ON agent_memories(session_id);
CREATE INDEX IF NOT EXISTS idx_agent_mem_topic   ON agent_memories(topic_key);

-- 推断活动落地（供 source_refs 真溯源 / promote(a) 联动解析）
CREATE TABLE IF NOT EXISTS detected_activities (
    activity_id  TEXT PRIMARY KEY,
    day          TEXT,
    activity     TEXT,
    confidence   REAL,
    evidence     TEXT,
    room         TEXT,
    session_id   TEXT,
    created_at   TEXT,
    entities_json TEXT NOT NULL DEFAULT '[]',
    events_json   TEXT NOT NULL DEFAULT '[]'
);

-- Agent 自定义活动/模式识别规则（Phase 2-B：define_activity 注册，infer_activities 套用）
CREATE TABLE IF NOT EXISTS activity_rules (
    rule_id    TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    room       TEXT NOT NULL DEFAULT '',
    tags_json  TEXT NOT NULL DEFAULT '[]',
    start_hour INTEGER NOT NULL DEFAULT 0,
    end_hour   INTEGER NOT NULL DEFAULT 23,
    min_events INTEGER NOT NULL DEFAULT 1,
    confidence REAL NOT NULL DEFAULT 0.6,
    note       TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_activity_rules_name ON activity_rules(name);

CREATE TABLE IF NOT EXISTS collect_days (
    day        TEXT PRIMARY KEY,
    events     INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collect_jobs (
    id            TEXT PRIMARY KEY,
    type          TEXT NOT NULL,
    status        TEXT NOT NULL,
    params_json   TEXT,
    progress_json TEXT,
    result_json   TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON collect_jobs(created_at DESC);

-- 语音问答答案缓存：高频同类问题（同一设备 + 时间窗口）秒级复用，跳过 LLM
CREATE TABLE IF NOT EXISTS voice_answer_cache (
    cache_key   TEXT PRIMARY KEY,
    intent      TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0,
    last_used   TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voice_cache_intent ON voice_answer_cache(intent);

-- 家庭成员（生活习惯档案）--
CREATE TABLE IF NOT EXISTS members (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    avatar_emoji  TEXT DEFAULT '',
    avatar_bg     TEXT DEFAULT '#0EA5E9',
    avatar_url    TEXT DEFAULT '',
    note          TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS member_rooms (
    member_id TEXT NOT NULL,
    room_name TEXT NOT NULL,
    PRIMARY KEY (member_id, room_name)
);
CREATE TABLE IF NOT EXISTS member_devices (
    member_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    PRIMARY KEY (member_id, entity_id)
);
CREATE TABLE IF NOT EXISTS member_tags (
    id            TEXT PRIMARY KEY,
    member_id     TEXT NOT NULL,
    tag           TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT 'other',
    emoji         TEXT DEFAULT '',
    confidence    REAL DEFAULT 0.0,
    evidence_json TEXT DEFAULT '[]',
    source        TEXT NOT NULL DEFAULT 'agent',
    confirmed_at  TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_member_tags_member ON member_tags(member_id);
"""


# ── 时间处理 ───────────────────────────────────────────────────────────────

def parse_ts(raw: Any, tz_offset_hours: float = 8.0) -> datetime | None:
    """把 HA 返回的各种时间格式统一解析为「本地时区的 naive datetime」。

    容器内通常没有设置 TZ，直接用 UTC 会让「按天聚合」整体偏移 8 小时，
    日历热力图与作息分析都会错位，因此这里显式做偏移换算。
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        text = str(raw).strip()
        if not text:
            return None
        dt = None
        normalized = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            return None

    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone(timedelta(hours=tz_offset_hours))).replace(tzinfo=None)
    # 无时区信息时视为已经是本地时间，不做二次换算
    return dt.replace(microsecond=0)


def make_event_id(entity_id: str, raw_ts: Any) -> str:
    """事件幂等主键。用原始高精度时间戳做哈希，避免同秒事件相互覆盖。"""
    payload = f"{entity_id}|{raw_ts}".encode("utf-8", errors="replace")
    return hashlib.sha1(payload).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat(sep="T")


def now_local(tz_offset_hours: float = 8.0) -> datetime:
    return datetime.now(timezone(timedelta(hours=tz_offset_hours))).replace(
        tzinfo=None, microsecond=0
    )


# ── 主类 ───────────────────────────────────────────────────────────────────

class Store:
    """行为事件与采集任务的统一存储。"""

    def __init__(self, db_path: str = "/data/memory_agent.db", tz_offset_hours: float = 8.0):
        self.db_path = db_path
        self.tz_offset_hours = tz_offset_hours
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # -- 连接与建表 --------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        with self._lock:
            if self._conn is not None:
                return self._conn
            parent = os.path.dirname(self.db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._conn = conn
            return conn

    def init_schema(self) -> None:
        conn = self.connect()
        with self._lock:
            conn.executescript(_SCHEMA_SQL)
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            # 增量迁移：已存在库补列（CREATE TABLE IF NOT EXISTS 不会改旧表）
            for col in ("entities_json", "events_json"):
                try:
                    conn.execute(
                        f"ALTER TABLE detected_activities ADD COLUMN {col} TEXT NOT NULL DEFAULT '[]'"
                    )
                except Exception:
                    pass  # 列已存在
            # members 表 avatar_url 列迁移
            try:
                conn.execute("ALTER TABLE members ADD COLUMN avatar_url TEXT DEFAULT ''")
            except Exception:
                pass  # 列已存在
            conn.commit()
        print(f"[Store] SQLite 就绪: {self.db_path} (schema v{SCHEMA_VERSION})")

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.commit()
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    # -- 事件写入 ----------------------------------------------------------

    def insert_events(self, events: list[dict]) -> int:
        """批量写入事件，返回实际处理条数。

        单次事务 + ``executemany``，500 条一批由调用方控制。
        逐条 insert 在上万条事件时会拖垮整个采集，禁止退化。
        """
        if not events:
            return 0
        rows = []
        for e in events:
            entity_id = e.get("entity_id") or ""
            if not entity_id:
                continue
            ts = e.get("ts")
            if isinstance(ts, datetime):
                ts = _iso(ts)
            if not ts:
                continue
            day = e.get("day") or str(ts)[:10]
            attrs = e.get("attrs")
            attrs_json = e.get("attrs_json")
            if attrs_json is None and attrs is not None:
                try:
                    attrs_json = json.dumps(attrs, ensure_ascii=False)
                except (TypeError, ValueError):
                    attrs_json = None
            rows.append(
                (
                    e.get("id") or make_event_id(entity_id, e.get("raw_ts") or ts),
                    ts,
                    day,
                    e.get("room") or "",
                    entity_id,
                    e.get("domain") or entity_id.split(".")[0],
                    e.get("action") or "",
                    e.get("person") or "",
                    e.get("old_state"),
                    e.get("new_state"),
                    attrs_json,
                )
            )
        if not rows:
            return 0
        conn = self.connect()
        with self._lock:
            conn.executemany(
                """INSERT OR REPLACE INTO events
                   (id, ts, day, room, entity_id, domain, action, person,
                    old_state, new_state, attrs_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            conn.commit()
        return len(rows)

    def recount_days(self, days: list[str]) -> None:
        """按天重新统计事件量写入 ``collect_days``。

        用「重算」而非「累加」，重复回填同一天不会把计数撑爆。
        """
        if not days:
            return
        conn = self.connect()
        stamp = now_local(self.tz_offset_hours).isoformat()
        with self._lock:
            for day in sorted(set(days)):
                cur = conn.execute("SELECT COUNT(*) FROM events WHERE day = ?", (day,))
                count = int(cur.fetchone()[0])
                conn.execute(
                    """INSERT INTO collect_days(day, events, updated_at) VALUES(?,?,?)
                       ON CONFLICT(day) DO UPDATE SET events=excluded.events,
                                                      updated_at=excluded.updated_at""",
                    (day, count, stamp),
                )
            conn.commit()

    # -- 语音问答答案缓存 ---------------------------------------------------

    def save_answer_cache(
        self, cache_key: str, payload: dict, intent: str = ""
    ) -> None:
        """写入/更新一个语音问答答案。payload 必须是可 JSON 序列化的 dict。"""
        conn = self.connect()
        stamp = now_local(self.tz_offset_hours).isoformat()
        with self._lock:
            conn.execute(
                """INSERT INTO voice_answer_cache(cache_key, intent, payload, hits, last_used, created_at)
                   VALUES(?,?,?,1,?,?)
                   ON CONFLICT(cache_key) DO UPDATE SET
                       payload=excluded.payload,
                       intent=excluded.intent,
                       hits=voice_answer_cache.hits+1,
                       last_used=excluded.last_used""",
                (cache_key, intent, json.dumps(payload, ensure_ascii=False), stamp, stamp),
            )
            conn.commit()

    def get_answer_cache(self, cache_key: str) -> dict | None:
        """命中返回行 dict（含 payload 字段，调用方自行 json.loads），未命中返回 None。"""
        conn = self.connect()
        with self._lock:
            row = conn.execute(
                "SELECT cache_key, intent, payload, hits, last_used, created_at "
                "FROM voice_answer_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        cols = ("cache_key", "intent", "payload", "hits", "last_used", "created_at")
        return dict(zip(cols, row))

    def list_answer_cache(self, limit: int = 100) -> list[dict]:
        conn = self.connect()
        with self._lock:
            rows = conn.execute(
                "SELECT cache_key, intent, payload, hits, last_used, created_at "
                "FROM voice_answer_cache ORDER BY hits DESC, last_used DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        cols = ("cache_key", "intent", "payload", "hits", "last_used", "created_at")
        return [dict(zip(cols, r)) for r in rows]

    def clear_answer_cache(self, cache_key: str | None = None) -> int:
        """清空缓存。传 cache_key 只删单条，否则全清。返回删除行数。"""
        conn = self.connect()
        with self._lock:
            if cache_key:
                cur = conn.execute(
                    "DELETE FROM voice_answer_cache WHERE cache_key = ?", (cache_key,)
                )
            else:
                cur = conn.execute("DELETE FROM voice_answer_cache")
            conn.commit()
            return cur.rowcount

    # -- 家庭成员 / 生活习惯档案 --------------------------------------------

    def create_member(
        self, name: str, avatar_emoji: str = "", avatar_bg: str = "#0EA5E9",
        avatar_url: str = "", note: str = ""
    ) -> dict:
        """创建家庭成员。返回新成员行 dict（含 rooms/devices/tags 空集合）。"""
        conn = self.connect()
        mid = uuid.uuid4().hex
        stamp = now_local(self.tz_offset_hours).isoformat()
        with self._lock:
            conn.execute(
                """INSERT INTO members(id, name, avatar_emoji, avatar_bg, avatar_url, note, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (mid, name, avatar_emoji, avatar_bg, avatar_url, note, stamp, stamp),
            )
            conn.commit()
        m = self.get_member(mid) or {}
        m["rooms"] = []
        m["devices"] = []
        m["tags"] = []
        return m

    def get_member(self, member_id: str) -> dict | None:
        conn = self.connect()
        with self._lock:
            row = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
        if row is None:
            return None
        m = dict(row)
        m["rooms"] = self._member_rooms(conn, member_id)
        m["devices"] = self._member_devices(conn, member_id)
        m["tags"] = self.list_member_tags(member_id)
        return m

    def list_members(self) -> list[dict]:
        """列出全部成员，并附带其关联房间/设备与标签。"""
        conn = self.connect()
        with self._lock:
            rows = conn.execute("SELECT * FROM members ORDER BY created_at").fetchall()
            members = []
            for r in rows:
                m = dict(r)
                m["rooms"] = self._member_rooms(conn, m["id"])
                m["devices"] = self._member_devices(conn, m["id"])
                m["tags"] = self.list_member_tags(m["id"])
                members.append(m)
        return members

    @staticmethod
    def _member_rooms(conn, member_id: str) -> list[str]:
        return [
            r["room_name"]
            for r in conn.execute(
                "SELECT room_name FROM member_rooms WHERE member_id = ? ORDER BY room_name",
                (member_id,),
            ).fetchall()
        ]

    @staticmethod
    def _member_devices(conn, member_id: str) -> list[str]:
        return [
            r["entity_id"]
            for r in conn.execute(
                "SELECT entity_id FROM member_devices WHERE member_id = ? ORDER BY entity_id",
                (member_id,),
            ).fetchall()
        ]

    def update_member(self, member_id: str, **fields) -> dict | None:
        allowed = {"name", "avatar_emoji", "avatar_bg", "avatar_url", "note"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return self.get_member(member_id)
        sets["updated_at"] = now_local(self.tz_offset_hours).isoformat()
        cols = ", ".join(f"{k}=?" for k in sets)
        args = [*sets.values(), member_id]
        conn = self.connect()
        with self._lock:
            conn.execute(f"UPDATE members SET {cols} WHERE id = ?", args)
            conn.commit()
        return self.get_member(member_id)

    def delete_member(self, member_id: str) -> None:
        conn = self.connect()
        with self._lock:
            conn.execute("DELETE FROM members WHERE id = ?", (member_id,))
            conn.execute("DELETE FROM member_rooms WHERE member_id = ?", (member_id,))
            conn.execute("DELETE FROM member_devices WHERE member_id = ?", (member_id,))
            conn.execute("DELETE FROM member_tags WHERE member_id = ?", (member_id,))
            conn.commit()

    def set_member_rooms(self, member_id: str, rooms: list[str]) -> None:
        conn = self.connect()
        with self._lock:
            conn.execute("DELETE FROM member_rooms WHERE member_id = ?", (member_id,))
            for room in rooms or []:
                conn.execute(
                    "INSERT OR IGNORE INTO member_rooms(member_id, room_name) VALUES(?,?)",
                    (member_id, room),
                )
            conn.commit()

    def set_member_devices(self, member_id: str, entity_ids: list[str]) -> None:
        conn = self.connect()
        with self._lock:
            conn.execute("DELETE FROM member_devices WHERE member_id = ?", (member_id,))
            for eid in entity_ids or []:
                conn.execute(
                    "INSERT OR IGNORE INTO member_devices(member_id, entity_id) VALUES(?,?)",
                    (member_id, eid),
                )
            conn.commit()

    def add_member_tag(
        self,
        member_id: str,
        tag: str,
        category: str = "other",
        emoji: str = "",
        confidence: float = 0.0,
        evidence: list | None = None,
        source: str = "agent",
    ) -> dict:
        """写入一条生活习惯标签（同名标签覆盖式更新，保留最新一次证据/置信度）。"""
        conn = self.connect()
        stamp = now_local(self.tz_offset_hours).isoformat()
        ev = json.dumps(evidence or [], ensure_ascii=False)
        with self._lock:
            existing = conn.execute(
                "SELECT id FROM member_tags WHERE member_id = ? AND tag = ?",
                (member_id, tag),
            ).fetchone()
            if existing:
                tid = existing["id"]
                conn.execute(
                    """UPDATE member_tags SET category=?, emoji=?, confidence=?,
                          evidence_json=?, source=?, confirmed_at=?, created_at=?
                       WHERE id=?""",
                    (category, emoji, confidence, ev, source, stamp, stamp, tid),
                )
            else:
                tid = uuid.uuid4().hex
                conn.execute(
                    """INSERT INTO member_tags(id, member_id, tag, category, emoji,
                          confidence, evidence_json, source, confirmed_at, created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (tid, member_id, tag, category, emoji, confidence, ev, source, stamp, stamp),
                )
            conn.commit()
            row = conn.execute("SELECT * FROM member_tags WHERE id = ?", (tid,)).fetchone()
        return self._tag_from_row(row)

    @staticmethod
    def _tag_from_row(row) -> dict:
        if row is None:
            return {}
        d = dict(row)
        try:
            d["evidence"] = json.loads(d.get("evidence_json") or "[]")
        except Exception:
            d["evidence"] = []
        return d

    def list_member_tags(self, member_id: str) -> list[dict]:
        conn = self.connect()
        with self._lock:
            rows = conn.execute(
                "SELECT * FROM member_tags WHERE member_id = ? ORDER BY category, confidence DESC",
                (member_id,),
            ).fetchall()
        return [self._tag_from_row(r) for r in rows]

    def delete_member_tag(self, member_id: str, tag: str) -> None:
        conn = self.connect()
        with self._lock:
            conn.execute(
                "DELETE FROM member_tags WHERE member_id = ? AND tag = ?", (member_id, tag)
            )
            conn.commit()

    # -- 事件查询 ----------------------------------------------------------

    @staticmethod
    def _filter_sql(
        start: str | None = None,
        end: str | None = None,
        rooms: list[str] | None = None,
        entities: list[str] | None = None,
        domains: list[str] | None = None,
        person: str | None = None,
        states: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> tuple[str, list[Any]]:
        """构造统一的 WHERE 片段。所有查询/聚合共用，保证过滤语义一致。"""
        sql: list[str] = []
        args: list[Any] = []
        if start:
            sql.append("AND ts >= ?")
            args.append(start)
        if end:
            sql.append("AND ts <= ?")
            args.append(end)
        if rooms:
            sql.append(f"AND room IN ({','.join('?' * len(rooms))})")
            args.extend(rooms)
        if entities:
            sql.append(f"AND entity_id IN ({','.join('?' * len(entities))})")
            args.extend(entities)
        if domains:
            sql.append(f"AND domain IN ({','.join('?' * len(domains))})")
            args.extend(domains)
        if exclude_domains:
            sql.append(f"AND domain NOT IN ({','.join('?' * len(exclude_domains))})")
            args.extend(exclude_domains)
        if person:
            sql.append("AND person = ?")
            args.append(person)
        if states:
            sql.append(f"AND new_state IN ({','.join('?' * len(states))})")
            args.extend(states)
        return " ".join(sql), args

    def query_events(
        self,
        start: str | None = None,
        end: str | None = None,
        rooms: list[str] | None = None,
        entities: list[str] | None = None,
        domains: list[str] | None = None,
        person: str | None = None,
        limit: int = 500,
        offset: int = 0,
        order: str = "asc",
        states: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> list[dict]:
        where, args = self._filter_sql(
            start, end, rooms, entities, domains, person, states, exclude_domains
        )
        sql = f"SELECT * FROM events WHERE 1=1 {where}"
        sql += f" ORDER BY ts {'DESC' if str(order).lower() == 'desc' else 'ASC'}"
        sql += " LIMIT ? OFFSET ?"
        args = [*args, max(1, min(int(limit), 5000)), max(0, int(offset))]

        conn = self.connect()
        with self._lock:
            cur = conn.execute(sql, args)
            return [dict(r) for r in cur.fetchall()]

    def count_events(
        self,
        start: str | None = None,
        end: str | None = None,
        rooms: list[str] | None = None,
        entities: list[str] | None = None,
        domains: list[str] | None = None,
        person: str | None = None,
        states: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> int:
        where, args = self._filter_sql(
            start, end, rooms, entities, domains, person, states, exclude_domains
        )
        conn = self.connect()
        with self._lock:
            return int(
                conn.execute(
                    f"SELECT COUNT(*) FROM events WHERE 1=1 {where}", args
                ).fetchone()[0]
            )

    def entity_last_seen(self, entities: list[str] | None = None) -> dict[str, str]:
        """实体 → 最后一次出现的时间戳。设备目录用（判断「最后在线」）。"""
        sql = "SELECT entity_id, MAX(ts) AS last_ts FROM events"
        args: list[Any] = []
        if entities:
            sql += f" WHERE entity_id IN ({','.join('?' * len(entities))})"
            args.extend(entities)
        sql += " GROUP BY entity_id"
        conn = self.connect()
        with self._lock:
            cur = conn.execute(sql, args)
            return {r["entity_id"]: (r["last_ts"] or "") for r in cur.fetchall()}

    def entity_event_counts(
        self, start: str | None = None, end: str | None = None
    ) -> dict[str, int]:
        """实体 → 区间内事件数。设备目录用（判断活跃度）。"""
        where, args = self._filter_sql(start, end)
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                f"SELECT entity_id, COUNT(*) c FROM events WHERE 1=1 {where} GROUP BY entity_id",
                args,
            )
            return {r["entity_id"]: int(r["c"]) for r in cur.fetchall()}

    def day_counts(self, start_day: str, end_day: str) -> dict[str, int]:
        """日历热力图数据源。走 ``collect_days`` 索引；若该表为空则 fallback 从 events 聚合（旧数据兼容）。"""
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                "SELECT day, events FROM collect_days WHERE day >= ? AND day <= ? ORDER BY day",
                (start_day, end_day),
            )
            counts = {r["day"]: int(r["events"]) for r in cur.fetchall()}
            if counts:
                return counts
            # fallback：collect_days 尚未回填（旧库迁移 / 手工导入）时直接从 events 聚合，
            # 顺手把结果写回 collect_days，下次即可走索引。
            rows = conn.execute(
                "SELECT day, COUNT(*) c FROM events WHERE day >= ? AND day <= ? "
                "GROUP BY day ORDER BY day",
                (start_day, end_day),
            ).fetchall()
            counts = {r["day"]: int(r["c"]) for r in rows}
        if counts:
            self.recount_days(list(counts))
        return counts

    def collected_days(self) -> list[str]:
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                "SELECT day FROM collect_days WHERE events > 0 ORDER BY day"
            )
            return [r["day"] for r in cur.fetchall()]

    def distinct_rooms(self) -> list[str]:
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                "SELECT room, COUNT(*) c FROM events WHERE room != '' GROUP BY room ORDER BY c DESC"
            )
            return [r["room"] for r in cur.fetchall()]

    def distinct_persons(self) -> list[str]:
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                "SELECT DISTINCT person FROM events WHERE person != '' ORDER BY person"
            )
            return [r["person"] for r in cur.fetchall()]

    def stats(self) -> dict:
        conn = self.connect()
        with self._lock:
            total = int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            rng = conn.execute("SELECT MIN(day), MAX(day) FROM events").fetchone()
            rooms = int(
                conn.execute(
                    "SELECT COUNT(DISTINCT room) FROM events WHERE room != ''"
                ).fetchone()[0]
            )
            entities = int(
                conn.execute("SELECT COUNT(DISTINCT entity_id) FROM events").fetchone()[0]
            )
            days = int(
                conn.execute(
                    "SELECT COUNT(*) FROM collect_days WHERE events > 0"
                ).fetchone()[0]
            )
        return {
            "total_events": total,
            "first_day": rng[0] or "",
            "last_day": rng[1] or "",
            "rooms": rooms,
            "entities": entities,
            "days_covered": days,
        }

    # -- 聚合分析（供 LLM 上下文压缩使用）-----------------------------------

    def hour_histogram(
        self,
        start: str,
        end: str,
        rooms: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        entities: list[str] | None = None,
    ) -> dict[str, list[int]]:
        """房间 × 24 小时活跃直方图。

        ``exclude_domains`` 用于剔除功率/温湿度等周期性遥测，
        否则直方图会被「每分钟一条」的传感器拍平成均匀噪声。
        """
        where, wargs = self._filter_sql(
            start, end, rooms, entities, None, None, None, exclude_domains
        )
        sql = [
            "SELECT room, CAST(strftime('%H', ts) AS INTEGER) h, COUNT(*) c",
            f"FROM events WHERE 1=1 {where}",
            "GROUP BY room, h",
        ]
        args: list[Any] = list(wargs)
        conn = self.connect()
        with self._lock:
            cur = conn.execute(" ".join(sql), args)
            out: dict[str, list[int]] = {}
            for r in cur.fetchall():
                room = r["room"] or "未分组"
                bucket = out.setdefault(room, [0] * 24)
                hour = r["h"]
                if hour is not None and 0 <= hour < 24:
                    bucket[hour] = int(r["c"])
            return out

    def top_entities(
        self,
        start: str,
        end: str,
        rooms: list[str] | None = None,
        limit: int = 25,
        exclude_domains: list[str] | None = None,
    ) -> list[dict]:
        where, wargs = self._filter_sql(
            start, end, rooms, None, None, None, None, exclude_domains
        )
        sql = [
            "SELECT entity_id, room, domain, COUNT(*) c",
            f"FROM events WHERE 1=1 {where}",
            "GROUP BY entity_id ORDER BY c DESC LIMIT ?",
        ]
        args: list[Any] = [*wargs, int(limit)]
        conn = self.connect()
        with self._lock:
            cur = conn.execute(" ".join(sql), args)
            return [
                {
                    "entity_id": r["entity_id"],
                    "room": r["room"],
                    "domain": r["domain"],
                    "count": int(r["c"]),
                }
                for r in cur.fetchall()
            ]

    def transitions(
        self,
        start: str,
        end: str,
        rooms: list[str] | None = None,
        max_gap_seconds: int = 1800,
        limit: int = 25,
        exclude_domains: list[str] | None = None,
    ) -> list[dict]:
        """相邻状态转移对 A→B 及平均间隔，用于识别行为链路。

        ``exclude_domains`` 必须支持：功率传感器成对上报会刷出成千上万条
        「power=0.0 → power=0.0」的伪链路，把真正的行为序列彻底淹没。
        """
        where = ["ts >= ?", "ts <= ?"]
        args: list[Any] = [start, end]
        if rooms:
            where.append(f"room IN ({','.join('?' * len(rooms))})")
            args.extend(rooms)
        if exclude_domains:
            where.append(f"domain NOT IN ({','.join('?' * len(exclude_domains))})")
            args.extend(exclude_domains)
        inner = f"""
            SELECT room,
                   entity_id || '=' || COALESCE(new_state,'') AS cur_key,
                   LAG(entity_id || '=' || COALESCE(new_state,'')) OVER
                       (PARTITION BY room ORDER BY ts) AS prev_key,
                   (julianday(ts) - julianday(LAG(ts) OVER
                       (PARTITION BY room ORDER BY ts))) * 86400.0 AS gap
            FROM events WHERE {' AND '.join(where)}
        """
        sql = f"""
            SELECT room, prev_key, cur_key, COUNT(*) c, AVG(gap) avg_gap
            FROM ({inner})
            WHERE prev_key IS NOT NULL AND prev_key != cur_key
              AND gap IS NOT NULL AND gap >= 0 AND gap <= ?
            GROUP BY room, prev_key, cur_key
            ORDER BY c DESC LIMIT ?
        """
        args.extend([int(max_gap_seconds), int(limit)])
        conn = self.connect()
        try:
            with self._lock:
                cur = conn.execute(sql, args)
                return [
                    {
                        "room": r["room"],
                        "from": r["prev_key"],
                        "to": r["cur_key"],
                        "count": int(r["c"]),
                        "avg_gap_s": round(float(r["avg_gap"] or 0), 1),
                    }
                    for r in cur.fetchall()
                ]
        except sqlite3.OperationalError as exc:  # 老版本 SQLite 无窗口函数
            print(f"[Store] transitions 降级（{exc}）")
            return []

    def daily_counts_by_room(
        self, start: str, end: str, rooms: list[str] | None = None
    ) -> list[dict]:
        sql = [
            "SELECT day, room, COUNT(*) c FROM events WHERE ts >= ? AND ts <= ?"
        ]
        args: list[Any] = [start, end]
        if rooms:
            sql.append(f"AND room IN ({','.join('?' * len(rooms))})")
            args.extend(rooms)
        sql.append("GROUP BY day, room ORDER BY day")
        conn = self.connect()
        with self._lock:
            cur = conn.execute(" ".join(sql), args)
            return [
                {"day": r["day"], "room": r["room"], "count": int(r["c"])}
                for r in cur.fetchall()
            ]

    def sample_events(
        self, start: str, end: str, rooms: list[str] | None = None, limit: int = 50
    ) -> list[dict]:
        """均匀采样代表性事件，避免把上万条原始流水塞进 prompt。"""
        total = self.count_events(start, end, rooms)
        if total <= 0:
            return []
        step = max(1, total // max(1, limit))
        sql = [
            "SELECT ts, room, entity_id, action, old_state, new_state,",
            "ROW_NUMBER() OVER (ORDER BY ts) rn FROM events",
            "WHERE ts >= ? AND ts <= ?",
        ]
        args: list[Any] = [start, end]
        if rooms:
            sql.append(f"AND room IN ({','.join('?' * len(rooms))})")
            args.extend(rooms)
        outer = f"SELECT * FROM ({' '.join(sql)}) WHERE rn % ? = 0 LIMIT ?"
        args.extend([step, int(limit)])
        conn = self.connect()
        try:
            with self._lock:
                cur = conn.execute(outer, args)
                return [
                    {
                        "ts": r["ts"],
                        "room": r["room"],
                        "entity_id": r["entity_id"],
                        "action": r["action"],
                        "old_state": r["old_state"],
                        "new_state": r["new_state"],
                    }
                    for r in cur.fetchall()
                ]
        except sqlite3.OperationalError:
            return self.query_events(start=start, end=end, rooms=rooms, limit=limit)

    # -- 采集任务 ----------------------------------------------------------

    def create_job(self, job_type: str, params: dict | None = None) -> str:
        job_id = uuid.uuid4().hex[:16]
        conn = self.connect()
        with self._lock:
            conn.execute(
                """INSERT INTO collect_jobs
                   (id, type, status, params_json, progress_json, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    job_id,
                    job_type,
                    "pending",
                    json.dumps(params or {}, ensure_ascii=False),
                    "{}",
                    now_local(self.tz_offset_hours).isoformat(),
                ),
            )
            conn.commit()
        return job_id

    def update_job(
        self,
        job_id: str,
        status: str | None = None,
        progress: dict | None = None,
        result: dict | None = None,
        error: str | None = None,
        finished: bool = False,
    ) -> None:
        sets: list[str] = []
        args: list[Any] = []
        if status is not None:
            sets.append("status = ?")
            args.append(status)
        if progress is not None:
            sets.append("progress_json = ?")
            args.append(json.dumps(progress, ensure_ascii=False))
        if result is not None:
            sets.append("result_json = ?")
            args.append(json.dumps(result, ensure_ascii=False))
        if error is not None:
            sets.append("error = ?")
            args.append(error[:2000])
        if finished:
            sets.append("finished_at = ?")
            args.append(now_local(self.tz_offset_hours).isoformat())
        if not sets:
            return
        args.append(job_id)
        conn = self.connect()
        with self._lock:
            conn.execute(f"UPDATE collect_jobs SET {', '.join(sets)} WHERE id = ?", args)
            conn.commit()

    def get_job(self, job_id: str) -> dict | None:
        conn = self.connect()
        with self._lock:
            cur = conn.execute("SELECT * FROM collect_jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
        return self._job_row(row) if row else None

    def list_jobs(self, limit: int = 20) -> list[dict]:
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                "SELECT * FROM collect_jobs ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            )
            return [self._job_row(r) for r in cur.fetchall()]

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict:
        def _loads(text: str | None) -> Any:
            if not text:
                return {}
            try:
                return json.loads(text)
            except (TypeError, ValueError):
                return {}

        return {
            "id": row["id"],
            "type": row["type"],
            "status": row["status"],
            "params": _loads(row["params_json"]),
            "progress": _loads(row["progress_json"]),
            "result": _loads(row["result_json"]),
            "error": row["error"] or "",
            "created_at": row["created_at"],
            "finished_at": row["finished_at"] or "",
        }

    def mark_stale_jobs(self) -> int:
        """进程重启后，把残留的 running/pending 任务标记为中断。"""
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                """UPDATE collect_jobs SET status='error', error='进程重启导致任务中断',
                   finished_at=? WHERE status IN ('pending','running')""",
                (now_local(self.tz_offset_hours).isoformat(),),
            )
            conn.commit()
            return cur.rowcount or 0

    # -- 保留策略 ----------------------------------------------------------

    def purge_old(self, retention_days: int) -> int:
        if retention_days <= 0:
            return 0
        cutoff = (
            now_local(self.tz_offset_hours) - timedelta(days=retention_days)
        ).strftime("%Y-%m-%d")
        conn = self.connect()
        with self._lock:
            cur = conn.execute("DELETE FROM events WHERE day < ?", (cutoff,))
            removed = cur.rowcount or 0
            conn.execute("DELETE FROM collect_days WHERE day < ?", (cutoff,))
            conn.commit()
        if removed:
            print(f"[Store] 按保留策略清理 {removed} 条事件（早于 {cutoff}）")
        return removed

    # ── Agent 记忆（参与式写回向量库）────────────────────────────────────────

    def get_event(self, event_id: str):
        """按 id 取单条事件（供 source_refs 真溯源校验）。"""
        conn = self.connect()
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None

    def get_events_by_entity(self, entity_id: str, day: str = "") -> list:
        """按实体 id（可选限定日期）取事件列表，供 explain_insight 由 entity_id 重建底层证据。"""
        conn = self.connect()
        if day:
            rows = conn.execute(
                "SELECT * FROM events WHERE entity_id=? AND day=? ORDER BY ts",
                (entity_id, day),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events WHERE entity_id=? ORDER BY ts", (entity_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def add_agent_memory(
        self,
        session_id: str,
        text: str,
        topic_key: str,
        tags_json: str,
        source_refs_json: str,
        ttl_days: int,
        state: str = "staging",
        auto_promote_blocked: int = 0,
        memory_id: str | None = None,
        created_at: str | None = None,
    ) -> str:
        now = now_local(self.tz_offset_hours)
        created_at = created_at or now.isoformat(timespec="seconds")
        expires_at = (now + timedelta(days=ttl_days)).strftime("%Y-%m-%d")
        memory_id = memory_id or hashlib.sha1(
            f"{session_id}|{text}|{created_at}".encode("utf-8")
        ).hexdigest()[:16]
        conn = self.connect()
        with self._lock:
            conn.execute(
                """INSERT OR REPLACE INTO agent_memories
                   (memory_id, session_id, text, topic_key, tags_json,
                    source_refs_json, state, trust, ttl_days,
                    auto_promote_blocked, mirror_dirty, feedback_up,
                    feedback_down, created_at, updated_at, expires_at)
                   VALUES (?,?,?,?,?,?,?,0,?,?,0,0,0,?,?,?)""",
                (
                    memory_id, session_id, text, topic_key, tags_json,
                    source_refs_json, state, ttl_days, auto_promote_blocked,
                    created_at, created_at, expires_at,
                ),
            )
            conn.commit()
        return memory_id

    def get_agent_memory(self, memory_id: str):
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM agent_memories WHERE memory_id = ?", (memory_id,)
        ).fetchone()
        return dict(row) if row else None

    def set_agent_memory_state(self, memory_id: str, state: str, mirror_dirty: int = 0) -> None:
        conn = self.connect()
        with self._lock:
            conn.execute(
                """UPDATE agent_memories SET state=?, updated_at=?,
                   mirror_dirty=? WHERE memory_id=?""",
                (state, now_local(self.tz_offset_hours).isoformat(timespec="seconds"),
                 mirror_dirty, memory_id),
            )
            conn.commit()

    def set_agent_auto_promote_blocked(self, memory_id: str, blocked: int) -> None:
        conn = self.connect()
        with self._lock:
            conn.execute(
                "UPDATE agent_memories SET auto_promote_blocked=? WHERE memory_id=?",
                (blocked, memory_id),
            )
            conn.commit()

    def mark_mirror_dirty(self, memory_id: str, dirty: int = 1) -> None:
        conn = self.connect()
        with self._lock:
            conn.execute(
                "UPDATE agent_memories SET mirror_dirty=? WHERE memory_id=?",
                (dirty, memory_id),
            )
            conn.commit()

    def list_agent_memories(self, state: str = "all", limit: int = 500) -> list:
        conn = self.connect()
        if state == "all":
            rows = conn.execute(
                "SELECT * FROM agent_memories ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_memories WHERE state=? ORDER BY updated_at DESC LIMIT ?",
                (state, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_dirty_agent_mirrors(self) -> list:
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM agent_memories WHERE mirror_dirty=1"
        ).fetchall()
        return [dict(r) for r in rows]

    def expire_overdue_agent_memories(self) -> int:
        today = now_local(self.tz_offset_hours).strftime("%Y-%m-%d")
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                """UPDATE agent_memories SET state='revoked', updated_at=?
                   WHERE state IN ('staging','live','pending_review')
                   AND expires_at < ?""",
                (now_local(self.tz_offset_hours).isoformat(timespec="seconds"), today),
            )
            conn.commit()
            return cur.rowcount or 0

    def record_agent_feedback(self, memory_id: str, useful: bool, trust_step: float = 0.2) -> dict | None:
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM agent_memories WHERE memory_id=?", (memory_id,)
        ).fetchone()
        if row is None:
            return None
        up = row["feedback_up"]
        down = row["feedback_down"]
        trust = float(row["trust"])
        ttl = int(row["ttl_days"])
        if useful:
            up += 1
            trust = min(1.0, trust + trust_step)
        else:
            down += 1
            trust = max(-1.0, trust - trust_step)
        now = now_local(self.tz_offset_hours)
        if useful:
            expires_at = (now + timedelta(days=ttl)).strftime("%Y-%m-%d")
        else:
            expires_at = (now + timedelta(days=max(1, ttl // 2))).strftime("%Y-%m-%d")
        with self._lock:
            conn.execute(
                """UPDATE agent_memories SET feedback_up=?, feedback_down=?,
                   trust=?, expires_at=?, updated_at=? WHERE memory_id=?""",
                (up, down, trust, expires_at, now.isoformat(timespec="seconds"), memory_id),
            )
            conn.commit()
        return {"feedback_up": up, "feedback_down": down, "trust": trust, "expires_at": expires_at}

    def get_session_agent_trust(self, session_id: str, strict_threshold: float = -0.3) -> dict:
        conn = self.connect()
        rows = conn.execute(
            "SELECT trust, state FROM agent_memories WHERE session_id=?", (session_id,)
        ).fetchall()
        if not rows:
            return {"session_id": session_id, "count": 0, "avg_trust": 0.0,
                    "live": 0, "revoked": 0, "strict": False}
        trusts = [float(r["trust"]) for r in rows]
        avg = sum(trusts) / len(trusts)
        live = sum(1 for r in rows if r["state"] == "live")
        revoked = sum(1 for r in rows if r["state"] == "revoked")
        return {
            "session_id": session_id,
            "count": len(rows),
            "avg_trust": round(avg, 3),
            "live": live,
            "revoked": revoked,
            "strict": avg < strict_threshold,
        }

    # ── detected_activities（source_refs / promote(a) 联动）────────────────

    def upsert_detected_activities(self, rows: list) -> None:
        if not rows:
            return
        conn = self.connect()
        with self._lock:
            conn.executemany(
                """INSERT OR REPLACE INTO detected_activities
                   (activity_id, day, activity, confidence, evidence, room, session_id, created_at,
                    entities_json, events_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        r.get("activity_id"), r.get("day"), r.get("activity"),
                        r.get("confidence"), json.dumps(r.get("evidence"), ensure_ascii=False),
                        r.get("room"), r.get("session_id", ""), r.get("created_at", ""),
                        r.get("entities_json", "[]"), r.get("events_json", "[]"),
                    )
                    for r in rows
                ],
            )
            conn.commit()

    def get_detected_activity(self, activity_id: str):
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM detected_activities WHERE activity_id=?", (activity_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["evidence"] = json.loads(d["evidence"]) if d["evidence"] else []
        except Exception:
            d["evidence"] = []
        try:
            d["entities_json"] = json.loads(d["entities_json"]) if d.get("entities_json") else []
        except Exception:
            d["entities_json"] = []
        return d

    # ── 自定义活动规则（Phase 2-B：define_activity / infer_activities 套用）──
    def upsert_activity_rule(self, rule: dict) -> str:
        name = (rule.get("name") or "").strip()
        if not name:
            raise ValueError("rule.name 不能为空")
        rule_id = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
        now = _iso(datetime.now(timezone.utc))
        tags = rule.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        conn = self.connect()
        with self._lock:
            conn.execute(
                """INSERT OR REPLACE INTO activity_rules
                   (rule_id, name, room, tags_json, start_hour, end_hour, min_events,
                    confidence, note, enabled, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?, COALESCE((SELECT created_at FROM activity_rules WHERE rule_id=?),?),?)""",
                (
                    rule_id, name, (rule.get("room") or ""),
                    json.dumps(tags, ensure_ascii=False),
                    int(rule.get("start_hour", 0)), int(rule.get("end_hour", 23)),
                    int(rule.get("min_events", 1)), float(rule.get("confidence", 0.6)),
                    (rule.get("note") or ""), 1 if rule.get("enabled", True) else 0,
                    rule_id, now, now,
                ),
            )
            conn.commit()
        return rule_id

    def list_activity_rules(self, enabled_only: bool = True) -> list:
        conn = self.connect()
        sql = (
            "SELECT rule_id, name, room, tags_json, start_hour, end_hour, "
            "min_events, confidence, note, enabled FROM activity_rules"
        )
        if enabled_only:
            sql += " WHERE enabled=1"
        rows = conn.execute(sql).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "rule_id": r["rule_id"],
                    "name": r["name"],
                    "room": r["room"],
                    "tags": json.loads(r["tags_json"] or "[]"),
                    "start_hour": r["start_hour"],
                    "end_hour": r["end_hour"],
                    "min_events": r["min_events"],
                    "confidence": r["confidence"],
                    "note": r["note"],
                    "enabled": bool(r["enabled"]),
                }
            )
        return out

    def delete_activity_rule(self, name: str) -> bool:
        conn = self.connect()
        cur = conn.execute("DELETE FROM activity_rules WHERE name=?", (name,))
        conn.commit()
        return cur.rowcount > 0
