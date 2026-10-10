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
import logging
import os
import sqlite3
import sys
import threading
from contextlib import contextmanager
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from . import house_time
from .day_bounds import (LONG_WINDOW_MAX, _clamped, clamp_days, clamp_minutes)

SCHEMA_VERSION = 1

# candidate_rules 的"人已确认"状态字（DCD 裁定 7 红线位 user_confirmed 由它推导）。
# HTTP 与 MCP 两条入口必须写同一个字面量，否则确认结果互不可见。
CANDIDATE_ACCEPTED = "accepted"
# DCD R3：已进引擎的候选规则改此状态，避免同一候选被重复晋升
CANDIDATE_PROMOTED = "promoted"
# 候选词表的另两档。HTTP 审核、手动录入的放行判定、挖掘链路的 TTL 归零都用同一套字
# 面量——此前只有 accepted/promoted 有名字，staging/rejected 在各调用点各写一遍，
# 少写一个字母不会报错，只会让那一行状态既进不了列表也删不掉。
CANDIDATE_STAGING = "staging"
CANDIDATE_REJECTED = "rejected"
# 「还没进引擎」的那三档：可以改内容、可以真删。**不含 promoted**——晋升之后
# 候选行是 ``active_rules`` 那一行的上游依据，改它或删它都会让链路两头分叉。
CANDIDATE_PRE_ENGINE = (CANDIDATE_STAGING, CANDIDATE_ACCEPTED, CANDIDATE_REJECTED)
# 候选来源。'inference' 是挖掘链路产的建议（默认值），'manual' 是人工录入
# （DCD 20261005 §二.2 Q1=乙+丁：写 CRUD 挂进 R3 通道，add 落 candidate_rules）。
CANDIDATE_SOURCE_INFERENCE = "inference"
CANDIDATE_SOURCE_MANUAL = "manual"

# 审计 S8：语音问答缓存软上限，超过则按创建时间淘汰最旧 10% 防止无限增长
_VOICE_CACHE_MAX = int(os.getenv("MA_VOICE_CACHE_MAX", "2000"))

# DCD R1：反馈携带的问题/评论文本入库前截断上限（防止用户粘贴整段对话撑爆元数据行）
_FEEDBACK_TEXT_MAX = int(os.getenv("MA_FEEDBACK_TEXT_MAX", "500"))

# 反馈置信度调整只对这三个枚举值生效；其余入参（含空串与用户自由文本）一律不动置信度。
# 第七轮审计 P7：调用点曾把自由文本当 outcome 传，形参错位后反馈静默丢失、接口仍回成功。
_FEEDBACK_OUTCOMES = ("success", "override", "failed")

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
-- 审计 P0-6：实体聚合（entity_catalog / entity_stats / entity_last_seen）要在锁内
-- GROUP BY entity_id 再取 MAX(room)/MAX(domain)。只含 (entity_id, ts) 的索引命中后
-- 仍要为 room/domain 回表 30 万次，实测 12.0s；把两列并进索引让它自成覆盖，
-- 同一个库实测降到 0.45s（26×），写入侧 5000 行前后差值在噪声内。
-- 取代旧的 idx_events_entity_ts：它的两列是本索引的严格前缀，同时保留即纯写放大。
CREATE INDEX IF NOT EXISTS idx_events_entity_cover ON events(entity_id, ts, room, domain);
CREATE INDEX IF NOT EXISTS idx_events_ts        ON events(ts);

-- Agent 记忆（参与式写回向量库）：元数据权威源，chroma 仅存标量镜像
CREATE TABLE IF NOT EXISTS agent_memories (
    memory_id            TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL,
    text                 TEXT NOT NULL,
    topic_key            TEXT NOT NULL DEFAULT '',
    source               TEXT NOT NULL DEFAULT 'ma',
    tags_json            TEXT NOT NULL DEFAULT '[]',
    source_refs_json     TEXT NOT NULL DEFAULT '[]',
    state                TEXT NOT NULL DEFAULT 'staging',
    trust                REAL NOT NULL DEFAULT 0.0,
    ttl_days             INTEGER NOT NULL DEFAULT 30,
    auto_promote_blocked INTEGER NOT NULL DEFAULT 0,
    mirror_dirty         INTEGER NOT NULL DEFAULT 0,
    feedback_up          INTEGER NOT NULL DEFAULT 0,
    feedback_down        INTEGER NOT NULL DEFAULT 0,
    feedback_question    TEXT NOT NULL DEFAULT '',
    feedback_comment     TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    expires_at           TEXT NOT NULL,
    prev_id             TEXT NOT NULL DEFAULT '',
    valid_from          TEXT NOT NULL DEFAULT '',
    valid_to            TEXT NOT NULL DEFAULT '',
    observed_at         TEXT NOT NULL DEFAULT ''
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
    appearance_json TEXT DEFAULT '',   -- 成员外观档案：{approx_age,gender,clothing,hair,note}
    profile_json  TEXT DEFAULT '',     -- 成员档案（作息/兴趣/课程等），schema 由豆包管家定义，本服务透明存取
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

-- 信号硬排除层（学习策略）：记录「某实体在某检测维度不应被采信」的纠正
CREATE TABLE IF NOT EXISTS signal_exclusions (
    exclusion_id TEXT PRIMARY KEY,
    entity_id    TEXT NOT NULL,
    scope        TEXT NOT NULL DEFAULT 'all',   -- all|wake_anchor|presence|working|watching_tv
    exclusion_type TEXT NOT NULL DEFAULT 'exclude', -- exclude|is_automation|not_automation
    reason       TEXT NOT NULL DEFAULT '',
    created_by   TEXT NOT NULL DEFAULT 'user',
    created_at   TEXT NOT NULL,
    revoked      INTEGER NOT NULL DEFAULT 0,
    revoked_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sig_excl_entity ON signal_exclusions(entity_id, scope);

-- MCP 调用审计（v0.7.5-2：谁能动、动过什么）
-- 与进程内 MCP_CALL_STATS 分工：统计是「效率快照」（内存、重启清零），
-- 本表是「可追溯链路」（落库、带身份与成败，保留 MCP_AUDIT_KEEP_DAYS 天）。
CREATE TABLE IF NOT EXISTS mcp_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,              -- 本地时区 ISO8601
  token_name TEXT NOT NULL,      -- 调用方令牌名（未知为 ''）
  origin TEXT NOT NULL DEFAULT '',  -- Origin/Host，便于定位来源客户端
  tool TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'read',  -- 该工具所需权限 read|write
  duration_ms REAL NOT NULL DEFAULT 0,
  ok INTEGER NOT NULL DEFAULT 1,       -- 1 成功 / 0 失败
  error TEXT                           -- 失败摘要（截断 500 字）
);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_ts ON mcp_audit(ts);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_token ON mcp_audit(token_name);

-- 视觉行为事件（多模态识别产出，vision-behavior-spec §7.1）
-- 「人+动作」导向，与设备/实体导向的 events 表语义不同，独立建表
CREATE TABLE IF NOT EXISTS behavior_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  server_ts TEXT NOT NULL,        -- ISO8601 本地时区（服务端接收时间，查询主键）
  device_ts INTEGER,              -- 设备（TV）上报的 ms 时间戳，可空
  day TEXT NOT NULL,              -- 'YYYY-MM-DD'，冗余列方便按天聚合/清理
  room TEXT NOT NULL,
  camera_src TEXT,                -- go2rtc 流名
  persons_json TEXT NOT NULL DEFAULT '[]',
  count INTEGER NOT NULL DEFAULT 0,
  action TEXT,                    -- '坐在电竞沙发看书'
  scene TEXT,                     -- 一句话场景概括
  confidence REAL,
  appearance_json TEXT,           -- 无TV房间的外观描述（穿搭标注原料）
  trigger TEXT,                   -- count_change/identity_change/heartbeat/patrol/manual/face
  vlm_latency_ms INTEGER,
  snapshot_path TEXT,
  raw_response TEXT,
  status TEXT NOT NULL DEFAULT 'ok'  -- ok | vlm_failed | low_confidence | skipped
);
CREATE INDEX IF NOT EXISTS idx_be_day_room ON behavior_events(day, room);
-- P1-7: 热路径查询索引 - 在场查询按时间范围/房间+时间范围过滤
CREATE INDEX IF NOT EXISTS idx_be_server_ts ON behavior_events(server_ts);
CREATE INDEX IF NOT EXISTS idx_be_room_server_ts ON behavior_events(room, server_ts);

-- 统一感知总线（主动感知 v2.0）：边缘 AI / VLM / sensor 三类来源归一化收口
CREATE TABLE IF NOT EXISTS perception_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT UNIQUE,       -- 幂等键 make_event_id(entity_id, server_ts)
  server_ts TEXT NOT NULL,
  day TEXT NOT NULL,
  source TEXT NOT NULL,       -- edge_ai | vlm | sensor
  kind TEXT NOT NULL,         -- face_known | face_unknown | human | pet | cry | gesture | day_night | fav_area | no_human | motion | object
  room TEXT,
  entity_id TEXT,
  confidence REAL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  raw_event_json TEXT
);
-- 审计 P2-1：event_id 的索引由 init_schema 里的**唯一**索引负责（idx_perception_events_event_id），
-- 这里不再建同名非唯一索引——两份索引等于每行写两遍。
CREATE INDEX IF NOT EXISTS idx_pe_source_day ON perception_events(source, day);
CREATE INDEX IF NOT EXISTS idx_pe_kind_ts ON perception_events(kind, server_ts);

-- Phase 5.3 持久意图 + 周期归档
CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    period       TEXT NOT NULL DEFAULT 'daily',
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_records (
    record_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL,
    period_key   TEXT NOT NULL,
    data_json    TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    UNIQUE(task_id, period_key)
);

-- vMA-1.3 多模态统一数据模型（DCD 20260929 放行，VIEW 方案）
-- 统一只读视图：events + behavior_events + perception_events 三表 UNION ALL
-- 字段映射：
--   source: device(events) | vision(behavior_events) | perception(perception_events)
--   event_type: device=entity_id:action | vision=action | perception=kind
--   person: events.person | vision=persons_json[0].name | perception=payload_json.person
--   entity_id: device=events.entity_id | vision=camera_src | perception=pe.entity_id
--   payload: device=attrs_json | vision=scene/count/camera_src JSON | perception=payload_json
-- 过滤：vision 源仅 status='ok' 行（失败/低置信行不入视图）
-- 注意：本定义与 init_schema() 迁移块中的 canonical 定义必须逐列一致
CREATE VIEW IF NOT EXISTS unified_events AS
    SELECT
        e.id AS event_id,
        e.ts AS server_ts,
        e.day AS day,
        e.room,
        'device' AS source,
        e.entity_id || ':' || e.action AS event_type,
        e.person,
        e.entity_id,
        CAST(NULL AS REAL) AS confidence,
        COALESCE(e.attrs_json, '{}') AS payload
    FROM events e
    UNION ALL
    SELECT
        CAST(be.id AS TEXT) AS event_id,
        be.server_ts,
        be.day AS day,
        be.room,
        'vision' AS source,
        COALESCE(be.action, '') AS event_type,
        COALESCE(json_extract(be.persons_json, '$[0].name'), '') AS person,
        COALESCE(be.camera_src, '') AS entity_id,
        be.confidence,
        json_object(
            'scene', COALESCE(be.scene, ''),
            'count', COALESCE(be.count, 0),
            'camera_src', COALESCE(be.camera_src, '')
        ) AS payload
    FROM behavior_events be
    WHERE be.status = 'ok'
    UNION ALL
    SELECT
        COALESCE(pe.event_id, CAST(pe.id AS TEXT)) AS event_id,
        pe.server_ts,
        pe.day AS day,
        COALESCE(pe.room, '') AS room,
        'perception' AS source,
        pe.kind AS event_type,
        COALESCE(json_extract(pe.payload_json, '$.person'), '') AS person,
        COALESCE(pe.entity_id, '') AS entity_id,
        pe.confidence,
        pe.payload_json AS payload
    FROM perception_events pe;

CREATE VIEW IF NOT EXISTS unified_events_daily AS
SELECT
    day,
    source,
    room,
    person,
    COUNT(*) AS event_count,
    MIN(server_ts) AS first_event,
    MAX(server_ts) AS last_event
FROM unified_events
GROUP BY day, source, room, person;
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
        # 与 now_local 同一口径：装了 homesdk 就按 IANA 家庭时区换算，未装才用小时偏移。
        dt = dt.astimezone(house_time.house_tz()).replace(tzinfo=None)
    # 无时区信息时视为已经是本地时间，不做二次换算
    return dt.replace(microsecond=0)


def make_event_id(entity_id: str, raw_ts: Any) -> str:
    """事件幂等主键。用原始高精度时间戳做哈希，避免同秒事件相互覆盖。"""
    payload = f"{entity_id}|{raw_ts}".encode("utf-8", errors="replace")
    return hashlib.sha1(payload).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat(sep="T")


def now_local(tz_offset_hours: float = 8.0) -> datetime:
    """家庭墙钟的 naive datetime。主路径是 `homesdk.time`（契约 §四），
    `tz_offset_hours` 在未装 homesdk 的部署里才是换算依据。见 `house_time.now_local`。
    """
    return house_time.now_local(tz_offset_hours)


# ── 在场查询辅助（豆包管家对接）────────────────────────────────────────────

# 这些是「没认出是谁」的占位名，不参与在场判定（管家只问候认出的人）
_UNKNOWN_IDENTITIES = {"未识别", "未识别成员", "陌生人", "unknown", "stranger", "none"}

# TV 端上报的 via 与服务端补认的 via 语义等价，统一别名便于消费方少写分支
_VIA_ALIAS = {"face": "arcface"}


def _as_float(value: Any) -> float | None:
    try:
        if isinstance(value, bool) or value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _median_hhmm(values: list[str]) -> str | None:
    """取一组 HH:MM 的中位数（跨零点场景用「分钟数」排序会失真，样本量小够用）。"""
    items = sorted(v for v in values if v and len(v) == 5)
    if not items:
        return None
    return items[len(items) // 2]


# ── 保留清理（审计 P0-7/P0-8）───────────────────────────────────────────────
# 每批删除行数：锁只在批内持有，批与批之间释放。5000 行实测单批 <10ms，
# 再小会让 VACUUM 前的 WAL 提交次数失控。
PURGE_BATCH_ROWS = 5000
# 达到这个删除量才付一次 VACUUM 的代价：VACUUM 是排他的（100MB 级约几百 ms），
# 小清理不值得。
PURGE_VACUUM_MIN_ROWS = 50_000

# ── 统一视图的分支下推表（审计 P0-6 / P2-4）────────────────────────────────
#: unified_events 的三个分支：投影 + 视图级过滤列在该分支上的真实表达式。
#: ``extra`` 是该分支在视图里自带的条件（vision 只放行 status='ok'）。
#: ⚠ 这里的投影必须与 _SCHEMA_SQL 中 unified_events 的定义逐列等价。
#:    防漂移的是 tests/test_vma13_unified_events_contract.py 的
#:    TestPagePushdown / TestCountPushdown——它们拿视图本身当参照逐组合比对行与
#:    总数，视图列一改这两组必红，而不是等到线上查出列对不上才发现。
_UNIFIED_COLS = ("event_id", "server_ts", "day", "room", "source", "event_type",
                 "person", "entity_id", "confidence", "payload")

_UNIFIED_BRANCHES = (
    {
        "source": "device",
        "ts": "e.ts",
        "room": "e.room",
        "person": "e.person",
        "extra": "",
        "select": (
            "SELECT e.id AS event_id, e.ts AS server_ts, e.day AS day, "
            "e.room AS room, 'device' AS source, "
            "e.entity_id || ':' || e.action AS event_type, e.person AS person, "
            "e.entity_id AS entity_id, CAST(NULL AS REAL) AS confidence, "
            "COALESCE(e.attrs_json, '{}') AS payload FROM events e"
        ),
    },
    {
        "source": "vision",
        "ts": "be.server_ts",
        "room": "be.room",
        "person": "COALESCE(json_extract(be.persons_json, '$[0].name'), '')",
        "extra": "be.status = 'ok'",
        "select": (
            "SELECT CAST(be.id AS TEXT) AS event_id, be.server_ts AS server_ts, "
            "be.day AS day, be.room AS room, 'vision' AS source, "
            "COALESCE(be.action, '') AS event_type, "
            "COALESCE(json_extract(be.persons_json, '$[0].name'), '') AS person, "
            "COALESCE(be.camera_src, '') AS entity_id, be.confidence AS confidence, "
            "json_object('scene', COALESCE(be.scene, ''), 'count', COALESCE(be.count, 0), "
            "'camera_src', COALESCE(be.camera_src, '')) AS payload "
            "FROM behavior_events be"
        ),
    },
    {
        "source": "perception",
        "ts": "pe.server_ts",
        "room": "COALESCE(pe.room, '')",
        "person": "COALESCE(json_extract(pe.payload_json, '$.person'), '')",
        "extra": "",
        "select": (
            "SELECT COALESCE(pe.event_id, CAST(pe.id AS TEXT)) AS event_id, "
            "pe.server_ts AS server_ts, pe.day AS day, COALESCE(pe.room, '') AS room, "
            "'perception' AS source, pe.kind AS event_type, "
            "COALESCE(json_extract(pe.payload_json, '$.person'), '') AS person, "
            "COALESCE(pe.entity_id, '') AS entity_id, pe.confidence AS confidence, "
            "pe.payload_json AS payload FROM perception_events pe"
        ),
    },
)

#: 启动自检力度（审计 P0-11）。``PRAGMA integrity_check`` 逐页扫描整个文件，
#: 耗时随体积近似线性（104 MB / 30 万行实测 2.1s），是唯一一项**只会越来越慢**的
#: 启动开销。``quick_check`` 覆盖同样的"还能不能用"判定且快一个数量级。
#: 环境变量 ``MA_DB_INTEGRITY_CHECK`` = quick（默认）| full | off。
#: quick 判红时会自动升级到 full 复核，确认损坏才走"从备份恢复"——
#: 快速自检的误判不允许直接导致数据回滚。
INTEGRITY_CHECK_MODES = ("quick", "full", "off")

# 幂等键 TTL 的比较口径（第六轮审计 M-1：原来按 %Y-%m-%d 存，小时级 TTL 全塌成同一天）
IDEMPOTENCY_TS_FMT = "%Y-%m-%d %H:%M:%S"


def _integrity_check_mode() -> str:
    mode = (os.getenv("MA_DB_INTEGRITY_CHECK") or "quick").strip().lower()
    if mode not in INTEGRITY_CHECK_MODES:
        print(f"[Store] MA_DB_INTEGRITY_CHECK={mode!r} 不是合法值，按 quick 处理")
        mode = "quick"
    return mode


# ── 主类 ───────────────────────────────────────────────────────────────────

class Store:
    """行为事件与采集任务的统一存储。"""

    @staticmethod
    def _escape_like(s: str) -> str:
        """P1-5: 转义 LIKE 通配符，防止 name/member_id 含 % 或 _ 时跨成员检索。
        用法：WHERE col LIKE ? ESCAPE '\\'，参数用 f"%{_escape_like(name)}%"。"""
        return (s or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _serialize_persons(persons) -> str:
        """P1-4: 统一 persons 序列化格式。
        字符串元素（旧格式 ["alice"]）转为 dict（{"name": "alice", "confidence": 0.0}），
        确保写侧永远是 dict 数组，读侧不会因 isinstance(p, dict) 为 False 而静默丢弃。"""
        if not persons:
            return "[]"
        if isinstance(persons, str):
            try:
                persons = json.loads(persons)
            except Exception:
                return "[]"
        if not isinstance(persons, list):
            return "[]"
        normalized = []
        for p in persons:
            if isinstance(p, dict):
                normalized.append(p)
            elif isinstance(p, str):
                # 旧格式：字符串数组 → 转为 dict
                normalized.append({"name": p, "confidence": 0.0})
            else:
                # 其他类型跳过
                continue
        return json.dumps(normalized, ensure_ascii=False)

    @staticmethod
    def _deserialize_persons(raw) -> list:
        """P1-4: 统一 persons 反序列化。
        解析 JSON，字符串元素转为 dict，遇到格式错误记 ERROR（不静默丢弃）。
        返回 dict 数组。"""
        if not raw:
            return []
        if isinstance(raw, list):
            persons = raw
        else:
            try:
                persons = json.loads(raw)
            except Exception as e:
                print(f"[Store] persons_json 解析失败: {e}", file=sys.stderr)
                return []
        if not isinstance(persons, list):
            print(f"[Store] persons_json 不是数组: {type(persons).__name__}", file=sys.stderr)
            return []
        result = []
        for p in persons:
            if isinstance(p, dict):
                result.append(p)
            elif isinstance(p, str):
                # 旧格式兼容：字符串 → dict
                result.append({"name": p, "confidence": 0.0})
            else:
                print(f"[Store] persons 元素格式异常（非dict非str）: {type(p).__name__}", file=sys.stderr)
        return result


    def __init__(self, db_path: str = "/data/memory_agent.db", tz_offset_hours: float = 8.0,
                 backup_dir: str = ""):
        self.db_path = db_path
        self.tz_offset_hours = tz_offset_hours
        # 自查发现：BackupManager 把快照写到 backup_dir/ma-<date>.db，而恢复逻辑原先只找
        # db_path + ".bak*"，两边永不相交 → 损坏时"自动恢复"永远空跑。现在两处都找。
        self.backup_dir = backup_dir or ""
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
            # FULL：提交时 fsync WAL，崩溃时不丢事务（审计：反复损坏根因，NORMAL 模式下
            # WAL 写入后若被 SIGKILL 可能导致 WAL 帧不完整 → 数据库损坏）。
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._conn = conn
            return conn

    @contextmanager
    def _db(self):
        """P1-3: Acquire lock and yield connection, protecting execute/commit."""
        with self._lock:
            yield self.connect()

    @contextmanager
    def transaction(self):
        """持锁的事务区：正常退出提交、异常回滚。

        第六轮审计 CRITICAL-2 的统一入口。连接是单例共享的，「execute 与 commit 之间
        没有锁」意味着别的线程可能把自己的半截事务一并提交（实测复现），也可能在自己的
        语句中途读到本线程尚未写入完成的行。事务区把「加锁 + 提交 + 失败回滚」绑成一步，
        调用方不再需要手写 try/except/rollback——也不容易漏。
        """
        conn = self.connect()
        with self._lock:
            try:
                yield conn
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception as rb_exc:
                    # 回滚都失败 = 连接本身已经断了（进程级故障，不是本次事务的问题）；
                    # 记下来但让原始异常继续上抛，调用方看到的仍是自己那步失败。
                    logging.getLogger(__name__).warning(
                        "[Store] 事务回滚失败: %s", rb_exc)
                raise

    def db_query(self, sql: str, params: tuple = ()) -> list:
        """Raw SQL query (read-only) for new insights framework compatibility.

        Returns rows as list of dicts. Used by insights.StoreRepository.
        """
        with self._db() as conn:
            cur = conn.execute(sql, params)
            cols = [d[0] for d in cur.description] if cur.description else []
            return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

    def check_and_recover(self, mode: str | None = None) -> dict:
        """启动时自检数据库完整性；损坏则从最近备份自动恢复。

        审计 P0-11：默认只做 ``quick_check``。``integrity_check`` 要逐页扫全文件，
        启动阻塞随数据量线性增长（104 MB 实测 4.0s）。quick 判红时升级 full 复核，
        只有复核也判红才真的回滚到备份。``MA_DB_INTEGRITY_CHECK=full`` 留给手动运维。

        快照来源两处：``<db>.bak*``（历史约定）与 ``backup_dir/ma-*.db``
        （``BackupManager`` 每日 ``VACUUM INTO`` 的实际落点），按 mtime 取最新。
        """
        import glob
        import logging as _logging
        logger = _logging.getLogger("memory_agent.store")
        mode = (mode or _integrity_check_mode()).strip().lower()
        if mode not in INTEGRITY_CHECK_MODES:
            mode = "quick"
        result = {"checked": mode != "off", "mode": mode, "pragma": None,
                  "recovered": False, "backup_used": None, "error": None}
        if mode == "off":
            logger.info("DB integrity check: skipped (MA_DB_INTEGRITY_CHECK=off)")
            return result

        def _run(pragma: str) -> str | None:
            """返回 None 表示 ok，否则返回问题描述。"""
            label = pragma.split()[-1]
            try:
                # 共享连接的读也要落在锁区内（第六轮审计 CRITICAL-2 静态门）
                with self._db() as conn:
                    r = conn.execute(pragma).fetchone()
            except Exception as exc:  # noqa: BLE001
                return f"{label} exception: {exc}"
            if r and r[0] == "ok":
                return None
            return f"{label} failed: {r[0] if r else 'unknown'}"

        primary = "PRAGMA integrity_check" if mode == "full" else "PRAGMA quick_check"
        problem = _run(primary)
        if problem is None:
            result["pragma"] = primary.split()[-1]
            logger.info(f"DB integrity check ({result['pragma']}): OK")
            return result
        if mode == "quick":
            # 快速自检判红 → 逐页复核。宁可慢这 4 秒，也不凭快判就覆盖数据。
            full = _run("PRAGMA integrity_check")
            if full is None:
                result["pragma"] = "quick_check+integrity_check"
                result["error"] = (
                    f"quick_check 判红但 integrity_check 通过，未做回滚；原件保留待排查：{problem}")
                logger.error(result["error"])
                return result
            problem = full
        result["pragma"] = "integrity_check"
        result["error"] = problem
        logger.error(problem)

        candidates = [self.db_path + ".bak*"]
        if self.backup_dir:
            candidates.append(os.path.join(self.backup_dir, "ma-*.db"))
        backups: list[str] = []
        for pat in candidates:
            backups.extend(p for p in glob.glob(pat) if os.path.isfile(p))
        backups.sort(key=os.path.getmtime, reverse=True)
        if not backups:
            logger.error(
                f"No backup found for recovery (查找位置: {', '.join(candidates)})")
            return result

        latest_bak = backups[0]
        logger.warning(f"Recovering from backup: {latest_bak}")

        conn = self.connect()
        conn.close()
        self._conn = None
        import shutil
        shutil.copy2(latest_bak, self.db_path)
        # 快照是 VACUUM INTO 出来的独立库。留下旧的 -wal/-shm 等于把上一份日志
        # 叠到新文件上；未干净关闭过的库正是"要恢复"的那些，风险最高的就是这里。
        for suffix in ("-wal", "-shm"):
            try:
                os.remove(self.db_path + suffix)
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.error(f"清理残留 {self.db_path}{suffix} 失败: {exc}")

        try:
            new_conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
            r = new_conn.execute("PRAGMA integrity_check").fetchone()
            new_conn.close()
            if r and r[0] == "ok":
                result["recovered"] = True
                result["backup_used"] = os.path.basename(latest_bak)
                logger.info(f"DB recovered from {os.path.basename(latest_bak)}")
            else:
                result["error"] += f"; recovery failed: {r[0] if r else 'unknown'}"
                logger.error(result["error"])
        except Exception as e:
            result["error"] += f"; recovery exception: {e}"
            logger.error(result["error"])

        return result

    # ── FTS5 关键词索引：自检 + 彻底重建 ────────────────────────────────────

    _FTS_TRIGGER_SQL = {
        "agent_memories_fts_ai": """
            CREATE TRIGGER agent_memories_fts_ai
            AFTER INSERT ON agent_memories BEGIN
                INSERT INTO agent_memories_fts(rowid, text, topic_key, tags_json)
                VALUES (new.rowid, new.text, new.topic_key, new.tags_json);
            END""",
        "agent_memories_fts_ad": """
            CREATE TRIGGER agent_memories_fts_ad
            AFTER DELETE ON agent_memories BEGIN
                INSERT INTO agent_memories_fts(agent_memories_fts, rowid, text, topic_key, tags_json)
                VALUES ('delete', old.rowid, old.text, old.topic_key, old.tags_json);
            END""",
        "agent_memories_fts_au": """
            CREATE TRIGGER agent_memories_fts_au
            AFTER UPDATE ON agent_memories BEGIN
                INSERT INTO agent_memories_fts(agent_memories_fts, rowid, text, topic_key, tags_json)
                VALUES ('delete', old.rowid, old.text, old.topic_key, old.tags_json);
                INSERT INTO agent_memories_fts(rowid, text, topic_key, tags_json)
                VALUES (new.rowid, new.text, new.topic_key, new.tags_json);
            END""",
    }

    @staticmethod
    def _fts_index_healthy(conn) -> bool:
        """FTS5 自己的 integrity-check：索引与内容表一致才算健康（缺表也算不健康）。

        判据不许换成 ``SELECT COUNT(*) FROM agent_memories_fts``：外部内容表的 COUNT(*)
        走的是内容表，空索引也返回主表行数——旧守卫正是被这一点骗死的。
        """
        conn.commit()  # 先落地已执行的迁移：检查失败要回滚，不能连带丢别的语句
        try:
            conn.execute(
                "INSERT INTO agent_memories_fts(agent_memories_fts, rank) "
                "VALUES('integrity-check', 1)")
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False

    def _rebuild_agent_memory_fts(self, conn) -> str:
        """彻底重建外部内容 FTS5 索引并回填，返回生效分词器；全失败返回 ``""``。

        rebuild 必须无条件执行：新建的 external-content 表索引是空的，不回填就会让写
        触发器的 'delete' 分支在每一条 UPDATE/DELETE 上抛 malformed。
        """
        last_exc: Exception | None = None
        for _tok in ("trigram", "unicode61"):  # trigram 中文子串友好，不支持则退 unicode61
            try:
                for _t in self._FTS_TRIGGER_SQL:
                    conn.execute(f"DROP TRIGGER IF EXISTS {_t}")
                conn.execute("DROP TABLE IF EXISTS agent_memories_fts")
                conn.execute(
                    f"""CREATE VIRTUAL TABLE agent_memories_fts USING fts5(
                          text, topic_key, tags_json,
                          content='agent_memories', content_rowid='rowid',
                          tokenize='{_tok}')"""
                )
                for _sql in self._FTS_TRIGGER_SQL.values():
                    conn.execute(_sql)
                conn.execute(
                    "INSERT INTO agent_memories_fts(agent_memories_fts) VALUES('rebuild')")
                conn.commit()
                if not self._fts_index_healthy(conn):
                    raise RuntimeError("rebuild 之后 integrity-check 仍不通过")
                return _tok
            except Exception as exc:
                last_exc = exc
                conn.rollback()
                print(f"[Store] FTS5({_tok}) 重建失败: {exc}")
        print(f"[Store] ⚠️ FTS5 关键词索引修不起来（{last_exc}）：agent_memories 的 "
              "UPDATE/DELETE 会持续报 database disk image is malformed，需人工重建库")
        return ""

    def init_schema(self) -> None:
        conn = self.connect()
        with self._lock:
            conn.executescript(_SCHEMA_SQL)
            # Phase 5.3 持久意图 + 周期归档：tasks / task_records 表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id      TEXT PRIMARY KEY,
                    name         TEXT NOT NULL,
                    description  TEXT NOT NULL DEFAULT '',
                    period       TEXT NOT NULL DEFAULT 'daily',
                    enabled      INTEGER NOT NULL DEFAULT 1,
                    created_at   TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_records (
                    record_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id      TEXT NOT NULL,
                    period_key   TEXT NOT NULL,
                    data_json    TEXT NOT NULL DEFAULT '{}',
                    created_at   TEXT NOT NULL,
                    UNIQUE(task_id, period_key)
                )
            """)
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            # 增量迁移：已存在库补列（CREATE TABLE IF NOT EXISTS 不会改旧表）
            # v0.5 记忆统一入库：agent_memories 增加来源字段 source（缺省 'ma' 视为本服务原生）
            try:
                conn.execute(
                    "ALTER TABLE agent_memories ADD COLUMN source TEXT NOT NULL DEFAULT 'ma'"
                )
            except Exception as _exc:
                if "duplicate column" not in str(_exc).lower():
                    print(f"[Store] agent_memories.source 列迁移异常: {_exc}")
            # v0.8-2 记忆演变链：prev_id 指向被合并/失效的上一个版本
            try:
                conn.execute(
                    "ALTER TABLE agent_memories ADD COLUMN prev_id TEXT NOT NULL DEFAULT ''"
                )
            except Exception as _exc:
                if "duplicate column" not in str(_exc).lower():
                    print(f"[Store] agent_memories.prev_id 列迁移异常: {_exc}")
            # v0.9 时间有效性（Graphiti 时间模型）：事实有效区间 + 观测时间
            for _col in ("valid_from", "valid_to", "observed_at"):
                try:
                    conn.execute(
                        f"ALTER TABLE agent_memories ADD COLUMN {_col} TEXT NOT NULL DEFAULT ''"
                    )
                except Exception as _exc:
                    if "duplicate column" not in str(_exc).lower():
                        print(f"[Store] agent_memories.{_col} 列迁移异常: {_exc}")
            # WO-MA-005: 成员归属维度（隐私面），默认空=未归属
            try:
                conn.execute(
                    "ALTER TABLE agent_memories ADD COLUMN member_id TEXT NOT NULL DEFAULT ''"
                )
            except Exception as _exc:
                if "duplicate column" not in str(_exc).lower():
                    print(f"[Store] agent_memories.member_id 列迁移异常: {_exc}")
            # DCD 2026-10-01 R1：👎 必须能还原"当初问了什么"，否则 badcase 门永远攒不出来。
            # 同窗补上 comment 的落点列——此前 record_agent_feedback 脱敏完 comment 却没有
            # 对应列可写（审计债 D1），脱敏纯属空转。
            for _col in ("feedback_question", "feedback_comment"):
                try:
                    conn.execute(
                        f"ALTER TABLE agent_memories ADD COLUMN {_col} TEXT NOT NULL DEFAULT ''"
                    )
                except Exception as _exc:
                    if "duplicate column" not in str(_exc).lower():
                        print(f"[Store] agent_memories.{_col} 列迁移异常: {_exc}")
            # 主动感知 v2.0：perception_events 幂等键（event_id）
            # 旧表可能无 event_id 列，补齐后保证重复事件被 IGNORE
            # 修复（审计 P1-1）：SQLite 不支持在 ADD COLUMN 上加 UNIQUE 约束，
            # 原语句恒失败且被 except:pass 静默吞掉，导致 event_id 列在旧库不存在。
            # 改为两步：ADD COLUMN 无约束 + CREATE UNIQUE INDEX。
            try:
                conn.execute(
                    "ALTER TABLE perception_events ADD COLUMN event_id TEXT"
                )
            except Exception as _exc:
                # "duplicate column name" 是列已存在的正常情况，其他错误应可见
                if "duplicate column" not in str(_exc).lower():
                    print(f"[Store] perception_events.event_id 列迁移异常: {_exc}")
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_perception_events_event_id "
                    "ON perception_events(event_id)"
                )
                # 审计 P2-1：唯一索引在位后，schema 里那条同名**非唯一**索引就是纯写放大
                # （每插一行维护两份索引）。只在确认唯一索引建起来之后才删，
                # 否则两条一起没了，event_id 查询会退化成全表扫。
                conn.execute("DROP INDEX IF EXISTS idx_pe_event_id")
            except Exception as _exc:
                print(f"[Store] perception_events.event_id 唯一索引创建失败: {_exc}")
            # 审计 P0-6：存量库的 (entity_id, ts) 是覆盖索引的严格前缀。
            # 覆盖索引确认在位后才删旧的——顺序反了会让实体聚合退化成全表扫，
            # 而 CREATE 失败会直接跳到 except，旧的因此保留。
            try:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_events_entity_cover "
                    "ON events(entity_id, ts, room, domain)"
                )
                conn.execute("DROP INDEX IF EXISTS idx_events_entity_ts")
            except Exception as _exc:
                print(f"[Store] events 实体覆盖索引迁移失败: {_exc}")
            # TV端 FaceID 上报：client 字段标识调用方（xiaotiancai/mytv/...）
            try:
                conn.execute(
                    "ALTER TABLE behavior_events ADD COLUMN client TEXT NOT NULL DEFAULT ''"
                )
            except Exception as _exc:
                if "duplicate column" not in str(_exc).lower():
                    print(f"[Store] behavior_events.client 列迁移异常: {_exc}")
            # vMA-1.2.0 场景图：结构化场景描述 JSON（不建新表，只加一列）
            # PRAGMA 先检后加：生产库迁移会跑两遍，重复 ALTER 必须无副作用。
            try:
                _cols = {
                    r[1] for r in conn.execute(
                        "PRAGMA table_info(behavior_events)"
                    ).fetchall()
                }
                if "scene_graph_json" not in _cols:
                    conn.execute(
                        "ALTER TABLE behavior_events ADD COLUMN scene_graph_json TEXT DEFAULT ''"
                    )
            except Exception as _exc:
                if "duplicate column" not in str(_exc).lower():
                    print(f"[Store] behavior_events.scene_graph_json 列迁移异常: {_exc}")
            # v0.8-4 混合检索：FTS5 关键词索引（external content + trigger 自动同步）
            #
            # 这段原来每次启动都 DROP+CREATE 外部内容表，再靠「FTS 表行数为 0 且主表非空」
            # 决定要不要 rebuild。那道守卫**永不成立**：外部内容 FTS5 的 COUNT(*) 读的是内容表，
            # 索引整片丢了也照样返回主表行数（生产快照实测 _docsize 0 行、_data 只剩 2 行，
            # 而 COUNT=247）。于是启动删掉可用索引后从不回填，留下一具空索引配三个写触发器
            # ——此后每一条 UPDATE/DELETE 都会在触发器的 'delete' 分支上抛
            # "database disk image is malformed"，agent_memories 整表冻成只读（生产实测
            # 周期 sweep 连续 59 次必失败 / 48 小时 664 次；自动晋升、TTL 过期、镜像
            # reconcile 全停），关键词检索则静默返空。
            # 判据换成 FTS5 自己的 integrity-check，只在坏/缺时彻底重建并无条件 rebuild。
            if self._fts_index_healthy(conn):
                print("[Store] FTS5 关键词索引健康（integrity-check）")
            else:
                _tok = self._rebuild_agent_memory_fts(conn)
                if _tok:
                    print(f"[Store] FTS5 关键词索引已重建（tokenize={_tok}）")
            for col in ("entities_json", "events_json"):
                try:
                    conn.execute(
                        f"ALTER TABLE detected_activities ADD COLUMN {col} TEXT NOT NULL DEFAULT '[]'"
                    )
                except Exception as _exc:
                    if "duplicate column" not in str(_exc).lower():
                        print(f"[Store] detected_activities.{col} 列迁移异常: {_exc}")
            # members 表 avatar_url 列迁移
            try:
                conn.execute("ALTER TABLE members ADD COLUMN avatar_url TEXT DEFAULT ''")
            except Exception as _exc:
                if "duplicate column" not in str(_exc).lower():
                    print(f"[Store] members.avatar_url 列迁移异常: {_exc}")
            # members 表 appearance_json 列迁移
            try:
                conn.execute("ALTER TABLE members ADD COLUMN appearance_json TEXT DEFAULT ''")
            except Exception as _exc:
                if "duplicate column" not in str(_exc).lower():
                    print(f"[Store] members.appearance_json 列迁移异常: {_exc}")
            # 人脸库中央集权：成员 ArcSoft 特征（可移植性见交接单风险项）
            try:
                conn.execute("ALTER TABLE members ADD COLUMN face_feature TEXT DEFAULT ''")
            except Exception as _exc:
                if "duplicate column" not in str(_exc).lower():
                    print(f"[Store] members.face_feature 列迁移异常: {_exc}")
            # 成员档案（豆包管家对接：作息/兴趣/课程，本服务不解释内容）
            try:
                conn.execute("ALTER TABLE members ADD COLUMN profile_json TEXT DEFAULT ''")
            except Exception as _exc:
                if "duplicate column" not in str(_exc).lower():
                    print(f"[Store] members.profile_json 列迁移异常: {_exc}")
            # 竞技场快照（AutoFlow 竞技场对接：脱敏后的版本化固定数据集）
            conn.execute(
                """CREATE TABLE IF NOT EXISTS arena_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    arena_id TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    room TEXT,
                    devices TEXT,
                    history_days INTEGER,
                    snapshot_json TEXT,
                    created_at TEXT
                )"""
            )
            # 竞技场提交结果（闭环记录，沉淀洞察迭代数据）
            conn.execute(
                """CREATE TABLE IF NOT EXISTS arena_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    insight_id TEXT,
                    arena_id TEXT,
                    agent_id TEXT,
                    task_title TEXT,
                    task_description TEXT,
                    flow_dsl TEXT,
                    success INTEGER,
                    token_used INTEGER,
                    used_memory_tools TEXT,
                    created_at TEXT
                )"""
            )
            # 实体身份与健康层（v0.2：HA 实体动荡治理，见 docs/架构设计_MA对外接口与设备身份层.md）
            # logical_devices：物理设备 ↔ HA 实体的一对多稳定映射，模板只引用 stable_id
            conn.execute(
                """CREATE TABLE IF NOT EXISTS logical_devices (
                    stable_id       TEXT PRIMARY KEY,
                    display_name    TEXT NOT NULL DEFAULT '',
                    device_class    TEXT NOT NULL DEFAULT '',
                    primary_entity  TEXT NOT NULL DEFAULT '',
                    candidates_json TEXT NOT NULL DEFAULT '[]',
                    provenance      TEXT NOT NULL DEFAULT 'discovered',
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_logical_devices_class ON logical_devices(device_class)"
            )
            # device_health：实体级健康/墓碑，state ∈ active|unknown|stale|disabled
            conn.execute(
                """CREATE TABLE IF NOT EXISTS device_health (
                    entity_id    TEXT PRIMARY KEY,
                    stable_id    TEXT NOT NULL DEFAULT '',
                    state        TEXT NOT NULL DEFAULT 'active',
                    last_seen    TEXT NOT NULL DEFAULT '',
                    last_data_ts TEXT NOT NULL DEFAULT '',
                    referenced   INTEGER NOT NULL DEFAULT 0,
                    note         TEXT NOT NULL DEFAULT '',
                    updated_at   TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_device_health_state ON device_health(state)"
            )
            # 记忆研究员（v0.8）：定向洞察任务配置（Insight Job，四轴坐标系）
            conn.execute(
                """CREATE TABLE IF NOT EXISTS insight_jobs (
                    job_id        TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    direction     TEXT NOT NULL DEFAULT '',
                    area          TEXT NOT NULL DEFAULT '',
                    member        TEXT NOT NULL DEFAULT '',
                    window_mode   TEXT NOT NULL DEFAULT 'relative',
                    window_days   INTEGER NOT NULL DEFAULT 7,
                    window_start  TEXT NOT NULL DEFAULT '',
                    window_end    TEXT NOT NULL DEFAULT '',
                    schedule      TEXT NOT NULL DEFAULT '0 3 * * *',
                    budget_tokens INTEGER NOT NULL DEFAULT 20000,
                    enabled       INTEGER NOT NULL DEFAULT 0,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL,
                    last_run_at   TEXT NOT NULL DEFAULT ''
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_insight_jobs_enabled ON insight_jobs(enabled)"
            )
            # 记忆研究员（v0.8）：每次运行的成本/审计记录
            conn.execute(
                """CREATE TABLE IF NOT EXISTS researcher_runs (
                    run_id           TEXT PRIMARY KEY,
                    job_id           TEXT NOT NULL,
                    started_at       TEXT NOT NULL,
                    finished_at      TEXT NOT NULL DEFAULT '',
                    token_used       INTEGER NOT NULL DEFAULT 0,
                    units_total      INTEGER NOT NULL DEFAULT 0,
                    units_processed  INTEGER NOT NULL DEFAULT 0,
                    hits             INTEGER NOT NULL DEFAULT 0,
                    ok               INTEGER NOT NULL DEFAULT 0,
                    error            TEXT NOT NULL DEFAULT '',
                    detail_json      TEXT NOT NULL DEFAULT '{}'
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_researcher_runs_job ON researcher_runs(job_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_researcher_runs_started ON researcher_runs(started_at)"
            )
            # v0.9.5 主动感知：canonical 行为状态（权威，供 GET /api/behaviors）
            conn.execute(
                """CREATE TABLE IF NOT EXISTS behavior_states (
                    state_id      TEXT PRIMARY KEY,
                    member        TEXT NOT NULL DEFAULT '',
                    room          TEXT NOT NULL DEFAULT '',
                    activity      TEXT NOT NULL,
                    confidence    REAL NOT NULL DEFAULT 0.0,
                    ts            TEXT NOT NULL,
                    source        TEXT NOT NULL DEFAULT 'inference',
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    created_at    TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_behavior_states_member_ts "
                "ON behavior_states(member, ts)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_behavior_states_ts ON behavior_states(ts)"
            )
            # v0.9.5 主动感知：候选序列规则（低置信/待审核，采纳后回灌管家规则库）
            conn.execute(
                """CREATE TABLE IF NOT EXISTS candidate_rules (
                    rule_id       TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    steps_json    TEXT NOT NULL DEFAULT '[]',
                    time_window   TEXT NOT NULL DEFAULT '',
                    infer         TEXT NOT NULL DEFAULT '',
                    confidence    REAL NOT NULL DEFAULT 0.0,
                    source        TEXT NOT NULL DEFAULT 'inference',
                    status        TEXT NOT NULL DEFAULT 'staging',
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidate_rules_status ON candidate_rules(status)"
            )
            # vMA-1.2.1 DCD裁定7: user_confirmed 红线——自动建议的规则缺确认永不进引擎
            try:
                conn.execute(
                    "ALTER TABLE candidate_rules ADD COLUMN user_confirmed INTEGER NOT NULL DEFAULT 0"
                )
            except Exception as _exc:
                if "duplicate column" not in str(_exc).lower():
                    print(f"[Store] candidate_rules.user_confirmed 列迁移异常: {_exc}")
            # DCD 20261004 MA-裁1 Q1（显式化）：晋升规则的吵人上限来自候选自己这一列。
            # **可空、无默认值**是裁定的要点——默认 300 会让「缺省即拒」永远走不到，
            # 数值就又回到由 ``add_rule`` 的形参替 DCD 说话。未设定的候选读出来是 None，
            # 晋升端据此拒绝（见 ``rule_lifecycle.promote``）。
            try:
                conn.execute(
                    "ALTER TABLE candidate_rules ADD COLUMN cooldown_seconds INTEGER"
                )
            except Exception as _exc:
                if "duplicate column" not in str(_exc).lower():
                    print(f"[Store] candidate_rules.cooldown_seconds 列迁移异常: {_exc}")
            # Phase 3.1 / DCD R3：引擎唯一读取的规则表。
            # 这两张表历史上只在生产库里存在、src 里没有 DDL，全新库会把整条
            # 规则链（路线图 §5）直接打死，所以列形状必须与生产保持一致。
            conn.execute(
                """CREATE TABLE IF NOT EXISTS active_rules (
                    rule_id          TEXT PRIMARY KEY,
                    name             TEXT NOT NULL,
                    description      TEXT NOT NULL DEFAULT '',
                    condition_json   TEXT NOT NULL DEFAULT '{}',
                    action_json      TEXT NOT NULL DEFAULT '{}',
                    enabled          INTEGER NOT NULL DEFAULT 1,
                    cooldown_seconds INTEGER NOT NULL DEFAULT 300,
                    created_at       TEXT NOT NULL,
                    updated_at       TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS rule_trigger_history (
                    trigger_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id      TEXT NOT NULL,
                    event_json   TEXT NOT NULL DEFAULT '{}',
                    action_json  TEXT NOT NULL DEFAULT '{}',
                    triggered_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rules_enabled "
                "ON active_rules(enabled)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rule_trigger_rule "
                "ON rule_trigger_history(rule_id, triggered_at)"
            )
            # DCD R3 四红线的载体：来源/生效模式/证据数/生命周期时间戳 + 试运行与误报标记
            _rule_cols = [
                "rule_type TEXT NOT NULL DEFAULT 'static'",
                "origin TEXT NOT NULL DEFAULT 'manual'",
                "source_rule_id TEXT NOT NULL DEFAULT ''",
                "mode TEXT NOT NULL DEFAULT 'live'",
                "evidence_count INTEGER NOT NULL DEFAULT 0",
                "promoted_at TEXT NOT NULL DEFAULT ''",
                "activated_at TEXT NOT NULL DEFAULT ''",
                "revoked_at TEXT NOT NULL DEFAULT ''",
                # 触发策略（count/single/absence）。此前 match_event 读 rule["trigger"]
                # 但库里没有这一列，count 分支不可达 → DCD Q2 的 60 秒/3 次无处落地。
                "trigger_json TEXT NOT NULL DEFAULT '{}'",
            ]
            for _ddl in _rule_cols:
                try:
                    conn.execute(f"ALTER TABLE active_rules ADD COLUMN {_ddl}")
                except Exception as _exc:
                    if "duplicate column" not in str(_exc).lower():
                        print(f"[Store] active_rules.{_ddl.split()[0]} 列迁移异常: {_exc}")
            for _ddl in ("dry_run INTEGER NOT NULL DEFAULT 0",
                         "false_positive INTEGER NOT NULL DEFAULT 0"):
                try:
                    conn.execute(f"ALTER TABLE rule_trigger_history ADD COLUMN {_ddl}")
                except Exception as _exc:
                    if "duplicate column" not in str(_exc).lower():
                        print(f"[Store] rule_trigger_history.{_ddl.split()[0]} 列迁移异常: {_exc}")
            # 撤销要能定位"这条规则产生过哪些推断"
            try:
                conn.execute(
                    "ALTER TABLE detected_activities ADD COLUMN source_rule_id TEXT NOT NULL DEFAULT ''"
                )
            except Exception as _exc:
                if "duplicate column" not in str(_exc).lower():
                    print(f"[Store] detected_activities.source_rule_id 列迁移异常: {_exc}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_detected_source_rule "
                "ON detected_activities(source_rule_id)"
            )
            # 红线"审计"：机器建议 → 人工确认 → 生效 → 转正/撤销 全链路留痕
            conn.execute(
                """CREATE TABLE IF NOT EXISTS rule_lifecycle_audit (
                    audit_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id        TEXT NOT NULL,
                    source_rule_id TEXT NOT NULL DEFAULT '',
                    action         TEXT NOT NULL,
                    actor          TEXT NOT NULL DEFAULT '',
                    from_state     TEXT NOT NULL DEFAULT '',
                    to_state       TEXT NOT NULL DEFAULT '',
                    reason         TEXT NOT NULL DEFAULT '',
                    detail_json    TEXT NOT NULL DEFAULT '{}',
                    created_at     TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lifecycle_rule "
                "ON rule_lifecycle_audit(rule_id, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lifecycle_src "
                "ON rule_lifecycle_audit(source_rule_id)"
            )
            # vMA-1.2.2 bug 上报通道：agent 使用 MCP 时发现 bug 记录于此
            conn.execute(
                """CREATE TABLE IF NOT EXISTS bug_reports (
                    bug_id      TEXT PRIMARY KEY,
                    tool_name   TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL,
                    expected    TEXT NOT NULL DEFAULT '',
                    actual      TEXT NOT NULL DEFAULT '',
                    severity    TEXT NOT NULL DEFAULT 'minor',
                    status      TEXT NOT NULL DEFAULT 'open',
                    reporter    TEXT NOT NULL DEFAULT '',
                    created_at  TEXT NOT NULL,
                    resolved_at TEXT NOT NULL DEFAULT ''
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bug_reports_status ON bug_reports(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bug_reports_tool ON bug_reports(tool_name)"
            )
            # vMA-1.2.2 bug 上报通道：agent 使用 MCP 时发现 bug 记录于此
            conn.execute(
                """CREATE TABLE IF NOT EXISTS bug_reports (
                    bug_id      TEXT PRIMARY KEY,
                    tool_name   TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL,
                    expected    TEXT NOT NULL DEFAULT '',
                    actual      TEXT NOT NULL DEFAULT '',
                    severity    TEXT NOT NULL DEFAULT 'minor',
                    status      TEXT NOT NULL DEFAULT 'open',
                    reporter    TEXT NOT NULL DEFAULT '',
                    created_at  TEXT NOT NULL,
                    resolved_at TEXT NOT NULL DEFAULT ''
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bug_reports_status ON bug_reports(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bug_reports_tool ON bug_reports(tool_name)"
            )
            # P1.1 过程挖掘：行为异常（偏离已学过程模型的 case，一次性/复核用）
            conn.execute(
                """CREATE TABLE IF NOT EXISTS behavior_anomalies (
                    anomaly_id     TEXT PRIMARY KEY,
                    case_key       TEXT NOT NULL,
                    day            TEXT NOT NULL DEFAULT '',
                    room           TEXT NOT NULL DEFAULT '',
                    reasons_json   TEXT NOT NULL DEFAULT '[]',
                    activities_json TEXT NOT NULL DEFAULT '[]',
                    rare_edges_json TEXT NOT NULL DEFAULT '[]',
                    rare_acts_json TEXT NOT NULL DEFAULT '[]',
                    severity       REAL NOT NULL DEFAULT 0.0,
                    engine         TEXT NOT NULL DEFAULT 'pure',
                    status         TEXT NOT NULL DEFAULT 'new',
                    detected_at    TEXT NOT NULL,
                    updated_at     TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_behavior_anomalies_case "
                "ON behavior_anomalies(case_key)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_behavior_anomalies_day "
                "ON behavior_anomalies(day)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_behavior_anomalies_status "
                "ON behavior_anomalies(status)"
            )
            # P1.2 在线异常/概念漂移（river）：行为活跃度分布突变点 + 异常时段
            conn.execute(
                """CREATE TABLE IF NOT EXISTS behavior_drifts (
                    drift_id    TEXT PRIMARY KEY,
                    bucket_ts   TEXT NOT NULL,
                    day         TEXT NOT NULL DEFAULT '',
                    kind        TEXT NOT NULL DEFAULT 'drift',
                    density     REAL NOT NULL DEFAULT 0.0,
                    score       REAL NOT NULL DEFAULT 0.0,
                    tags_json   TEXT NOT NULL DEFAULT '[]',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    detected_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_behavior_drifts_key "
                "ON behavior_drifts(bucket_ts, kind)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_behavior_drifts_day "
                "ON behavior_drifts(day)"
            )
            # v0.9 MCP 契约：幂等键表（写工具防重复执行）
            conn.execute(
                """CREATE TABLE IF NOT EXISTS idempotency_keys (
                    idem_key    TEXT PRIMARY KEY,
                    tool        TEXT NOT NULL,
                    result_text TEXT NOT NULL,
                    is_error    INTEGER NOT NULL DEFAULT 0,
                    created_at  TEXT NOT NULL,
                    expires_at  TEXT NOT NULL,
                    state       TEXT NOT NULL DEFAULT 'done'
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_idempotency_expires ON idempotency_keys(expires_at)"
            )
            # 第六轮审计 CRITICAL-1：幂等要保证「同一 key 只执行一次」，检查与执行之间
            # 不能有窗口。做法是先把行占住（state='pending'）再执行，成功才写 done。
            # 旧库补列：默认 'done'——历史行都是已完成的缓存结果，语义不变。
            try:
                conn.execute(
                    "ALTER TABLE idempotency_keys ADD COLUMN state TEXT NOT NULL DEFAULT 'done'"
                )
            except Exception as _exc:
                if "duplicate column" not in str(_exc).lower():
                    print(f"[Store] idempotency_keys.state 列迁移异常: {_exc}")
            # M-1：expires_at 历史上按「天」存，TTL 却按小时承诺（实测承诺 24h、
            # 实际 24.5–48h；ttl 1/2/12 小时全塌成同一个日期串）。旧行补到当天
            # 23:59:59，比较口径从此统一为完整时间戳。
            try:
                conn.execute(
                    "UPDATE idempotency_keys SET expires_at = expires_at || ' 23:59:59'"
                    " WHERE length(expires_at) = 10"
                )
            except Exception as _exc:
                print(f"[Store] idempotency_keys.expires_at 时间戳归一失败: {_exc}")
            # vMA-1.3 多模态统一：三源只读 VIEW（events + behavior_events + perception_events）
            # VIEW 只读，不动写入路径；event_type 语义：device=entity:action, vision=action, perception=kind
            # 历史缺陷修复：本定义曾与 _SCHEMA_SQL 中的定义列不一致
            # （一版有 payload 无 event_id/entity_id，另一版有 raw_json），
            # CREATE VIEW IF NOT EXISTS 首建者胜，导致 mcp 工具查询报 no such column。
            # 现两处统一为同一 canonical 列集；旧库若视图列集不符（缺 event_id/entity_id/payload）
            # 则 DROP 后按 canonical 重建（仅视图定义，不触碰任何源表数据）。
            _ue_cols = [r[1] for r in conn.execute("PRAGMA table_info(unified_events)")]
            if _ue_cols and not {"event_id", "entity_id", "payload"}.issubset(set(_ue_cols)):
                conn.execute("DROP VIEW unified_events")
                _ue_cols = []
            if not _ue_cols:
                conn.execute("""
                    CREATE VIEW unified_events AS
                    SELECT
                        e.id AS event_id,
                        e.ts AS server_ts,
                        e.day AS day,
                        e.room,
                        'device' AS source,
                        e.entity_id || ':' || e.action AS event_type,
                        e.person,
                        e.entity_id,
                        CAST(NULL AS REAL) AS confidence,
                        COALESCE(e.attrs_json, '{}') AS payload
                    FROM events e
                    UNION ALL
                    SELECT
                        CAST(be.id AS TEXT) AS event_id,
                        be.server_ts,
                        be.day AS day,
                        be.room,
                        'vision' AS source,
                        COALESCE(be.action, '') AS event_type,
                        COALESCE(json_extract(be.persons_json, '$[0].name'), '') AS person,
                        COALESCE(be.camera_src, '') AS entity_id,
                        be.confidence,
                        json_object(
                            'scene', COALESCE(be.scene, ''),
                            'count', COALESCE(be.count, 0),
                            'camera_src', COALESCE(be.camera_src, '')
                        ) AS payload
                    FROM behavior_events be
                    WHERE be.status = 'ok'
                    UNION ALL
                    SELECT
                        COALESCE(pe.event_id, CAST(pe.id AS TEXT)) AS event_id,
                        pe.server_ts,
                        pe.day AS day,
                        COALESCE(pe.room, '') AS room,
                        'perception' AS source,
                        pe.kind AS event_type,
                        COALESCE(json_extract(pe.payload_json, '$.person'), '') AS person,
                        COALESCE(pe.entity_id, '') AS entity_id,
                        pe.confidence,
                        pe.payload_json AS payload
                    FROM perception_events pe
                """)
            conn.commit()
        print(f"[Store] SQLite 就绪: {self.db_path} (schema v{SCHEMA_VERSION})")
        self.ensure_default_insight_jobs()

    # -- 记忆研究员（v0.8：定向洞察 Job + 运行审计）--------------------------------
    def ensure_default_insight_jobs(self) -> int:
        """首次启动时种子化一个内置定向洞察方案（默认禁用，用户可手动启用/试跑）。"""
        try:
            with self._lock:
                conn = self.connect()
                cur = conn.execute("SELECT COUNT(*) AS c FROM insight_jobs")
                if cur.fetchone()["c"] > 0:
                    return 0
                now = now_local(self.tz_offset_hours).isoformat()
                conn.execute(
                    """INSERT INTO insight_jobs(
                        job_id,name,direction,area,member,window_mode,window_days,
                        window_start,window_end,schedule,budget_tokens,enabled,created_at,updated_at,last_run_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "builtin_rhythm_001",
                        "内置作息节律洞察",
                        "rhythm",
                        "",
                        "",
                        "relative",
                        7,
                        "",
                        "",
                        "0 3 * * *",
                        20000,
                        0,
                        now,
                        now,
                        "",
                    ),
                )
                conn.commit()
                print("[Store] 已种子化内置定向洞察任务: builtin_rhythm_001")
                return 1
        except Exception as exc:
            print(f"[Store] 种子化内置洞察任务失败（不影响启动）: {exc}")
            return 0

    def save_insight_job(self, job: dict) -> dict:
        """写入/更新一个定向洞察任务（Insight Job）。job 需含 name；job_id 缺省自动生成。"""
        now = now_local(self.tz_offset_hours).isoformat()
        job_id = (job.get("job_id") or "").strip() or f"job_{uuid.uuid4().hex[:12]}"
        exists = self.get_insight_job(job_id)
        payload = {
            "job_id": job_id,
            "name": job.get("name") or "未命名洞察任务",
            "direction": job.get("direction") or "",
            "area": job.get("area") or "",
            "member": job.get("member") or "",
            "window_mode": job.get("window_mode") or "relative",
            "window_days": int(job.get("window_days") or 7),
            "window_start": job.get("window_start") or "",
            "window_end": job.get("window_end") or "",
            "schedule": job.get("schedule") or "0 3 * * *",
            "budget_tokens": int(job.get("budget_tokens") or 20000),
            "enabled": 1 if job.get("enabled") else 0,
            "created_at": (exists or {}).get("created_at") or now,
            "updated_at": now,
            "last_run_at": (exists or {}).get("last_run_at") or "",
        }
        with self._lock:
            conn = self.connect()
            conn.execute(
                """INSERT INTO insight_jobs(
                    job_id,name,direction,area,member,window_mode,window_days,
                    window_start,window_end,schedule,budget_tokens,enabled,created_at,updated_at,last_run_at
                ) VALUES(:job_id,:name,:direction,:area,:member,:window_mode,:window_days,
                         :window_start,:window_end,:schedule,:budget_tokens,:enabled,:created_at,:updated_at,:last_run_at)
                ON CONFLICT(job_id) DO UPDATE SET
                    name=excluded.name, direction=excluded.direction, area=excluded.area,
                    member=excluded.member, window_mode=excluded.window_mode, window_days=excluded.window_days,
                    window_start=excluded.window_start, window_end=excluded.window_end,
                    schedule=excluded.schedule, budget_tokens=excluded.budget_tokens,
                    enabled=excluded.enabled, updated_at=excluded.updated_at, last_run_at=excluded.last_run_at""",
                payload,
            )
            conn.commit()
        return payload

    def get_insight_job(self, job_id: str) -> dict | None:
        with self._lock:
            row = self.connect().execute(
                "SELECT * FROM insight_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_insight_jobs(self, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM insight_jobs"
        args: list = []
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY updated_at DESC"
        with self._lock:
            rows = self.connect().execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def delete_insight_job(self, job_id: str) -> bool:
        with self._lock:
            conn = self.connect()
            conn.execute("DELETE FROM insight_jobs WHERE job_id=?", (job_id,))
            conn.commit()
        return True

    def set_insight_job_enabled(self, job_id: str, enabled: bool) -> bool:
        with self._lock:
            conn = self.connect()
            conn.execute(
                "UPDATE insight_jobs SET enabled=?, updated_at=? WHERE job_id=?",
                (1 if enabled else 0, now_local(self.tz_offset_hours).isoformat(), job_id),
            )
            conn.commit()
        return True

    def touch_insight_job_last_run(self, job_id: str) -> None:
        """更新 Job 的最近运行时间（用于 UI 展示与调试）。"""
        with self._lock:
            conn = self.connect()
            conn.execute(
                "UPDATE insight_jobs SET last_run_at=? WHERE job_id=?",
                (now_local(self.tz_offset_hours).isoformat(timespec="seconds"), job_id),
            )
            conn.commit()

    def record_researcher_run(self, run: dict) -> None:
        """写入一次 Job 运行的成本/审计记录。run 需含 run_id/job_id/started_at。"""
        with self._lock:
            conn = self.connect()
            conn.execute(
                """INSERT OR REPLACE INTO researcher_runs(
                    run_id,job_id,started_at,finished_at,token_used,units_total,
                    units_processed,hits,ok,error,detail_json
                ) VALUES(:run_id,:job_id,:started_at,:finished_at,:token_used,:units_total,
                         :units_processed,:hits,:ok,:error,:detail_json)""",
                {
                    "run_id": run.get("run_id"),
                    "job_id": run.get("job_id"),
                    "started_at": run.get("started_at") or now_local(self.tz_offset_hours).isoformat(),
                    "finished_at": run.get("finished_at") or "",
                    "token_used": int(run.get("token_used") or 0),
                    "units_total": int(run.get("units_total") or 0),
                    "units_processed": int(run.get("units_processed") or 0),
                    "hits": int(run.get("hits") or 0),
                    "ok": 1 if run.get("ok") else 0,
                    "error": run.get("error") or "",
                    "detail_json": run.get("detail_json") or "{}",
                },
            )
            conn.commit()

    def list_researcher_runs(self, job_id: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM researcher_runs"
        args: list = []
        if job_id:
            sql += " WHERE job_id=?"
            args.append(job_id)
        sql += " ORDER BY started_at DESC LIMIT ?"
        args.append(clamp_limit(limit, 50, 500, label="list_researcher_runs"))
        with self._lock:
            rows = self.connect().execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def researcher_daily_token_used(self, day: str | None = None) -> int:
        """某日（本地 YYYY-MM-DD，默认今天）researcher 已消耗的 token 总量。"""
        day = day or now_local(self.tz_offset_hours).strftime("%Y-%m-%d")
        with self._lock:
            row = self.connect().execute(
                "SELECT COALESCE(SUM(token_used),0) AS t FROM researcher_runs WHERE started_at >= ?",
                (day,),
            ).fetchone()
        return int(row["t"]) if row else 0

    # -- MCP 调用审计（v0.7.5-2）--------------------------------------------

    def log_mcp_audit(
        self,
        token_name: str,
        tool: str,
        scope: str = "read",
        duration_ms: float = 0.0,
        ok: bool = True,
        error: str = "",
        origin: str = "",
    ) -> None:
        """记录一次 MCP 工具调用。

        审计是旁路：任何异常都不得影响主调用链路（吞掉并记日志即可）。
        """
        try:
            ts = now_local(self.tz_offset_hours).isoformat(timespec="seconds")
            with self._lock:
                conn = self.connect()
                conn.execute(
                    "INSERT INTO mcp_audit(ts, token_name, origin, tool, scope, "
                    "duration_ms, ok, error) VALUES (?,?,?,?,?,?,?,?)",
                    (ts, token_name or "", (origin or "")[:200], tool or "",
                     scope or "read", float(duration_ms or 0.0),
                     1 if ok else 0, (error or "")[:500] or None),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001 —— 旁路审计不得影响主链路
            print(f"[Store] MCP 审计写入失败（忽略）: {exc}")

    def list_mcp_audit(
        self,
        limit: int = 100,
        token_name: str = "",
        tool: str = "",
        only_failed: bool = False,
    ) -> list[dict]:
        """审计查询（只读）。默认最近 100 条。"""
        sql = "SELECT * FROM mcp_audit"
        where: list[str] = []
        args: list = []
        if token_name:
            where.append("token_name = ?")
            args.append(token_name)
        if tool:
            where.append("tool = ?")
            args.append(tool)
        if only_failed:
            where.append("ok = 0")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(int(limit or 100), 1000)))
        with self._lock:
            rows = self.connect().execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def purge_mcp_audit(self, keep_days: int = 30) -> int:
        """清理过期审计（默认保留 30 天，路线图风险的「保留周期上限」要求）。

        这是**保留期**不是查询窗口：上界用 `LONG_WINDOW_MAX`，把 3650 套在这里等于
        让「留 100 年」变成「删掉 10 年前的审计」。下界保持原语义（`<=0` 也当 1 天，
        调用点 `runtime.py:145` 传的是字面量 30）。
        """
        cutoff = now_local(self.tz_offset_hours) - timedelta(
            days=clamp_days(keep_days, hi=LONG_WINDOW_MAX))
        with self._lock:
            conn = self.connect()
            cur = conn.execute("DELETE FROM mcp_audit WHERE ts < ?", (cutoff.isoformat(),))
            conn.commit()
        return int(cur.rowcount or 0)

    # -- 实体身份与健康层（v0.2：HA 实体动荡治理）--------------------------------

    @staticmethod
    def _logical_row_to_dict(row: Any) -> dict:
        d = dict(row)
        try:
            d["candidates"] = json.loads(d.pop("candidates_json") or "[]")
        except Exception:
            d.pop("candidates_json", None)
            d["candidates"] = []
        return d

    def list_logical_devices(self) -> list[dict]:
        """全部逻辑设备（candidates 已反序列化）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM logical_devices ORDER BY stable_id"
            ).fetchall()
        return [self._logical_row_to_dict(r) for r in rows]

    def get_logical_device(self, stable_id: str) -> dict | None:
        """按稳定主键取逻辑设备。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM logical_devices WHERE stable_id=?", (stable_id,)
            ).fetchone()
        return self._logical_row_to_dict(row) if row else None

    def upsert_logical_device(self, device: dict) -> dict:
        """整行写入/更新逻辑设备；candidates 以 JSON 落库。"""
        now = now_local(self.tz_offset_hours).isoformat()
        payload = {
            "stable_id": device.get("stable_id") or "",
            "display_name": device.get("display_name") or "",
            "device_class": device.get("device_class") or "",
            "primary_entity": device.get("primary_entity") or "",
            "candidates_json": json.dumps(
                device.get("candidates") or [], ensure_ascii=False
            ),
            "provenance": device.get("provenance") or "discovered",
            "created_at": device.get("created_at") or now,
            "updated_at": now,
        }
        with self._lock:
            self._conn.execute(
                """INSERT INTO logical_devices
                   (stable_id, display_name, device_class, primary_entity,
                    candidates_json, provenance, created_at, updated_at)
                   VALUES (:stable_id, :display_name, :device_class, :primary_entity,
                           :candidates_json, :provenance, :created_at, :updated_at)
                   ON CONFLICT(stable_id) DO UPDATE SET
                     display_name=excluded.display_name,
                     device_class=excluded.device_class,
                     primary_entity=excluded.primary_entity,
                     candidates_json=excluded.candidates_json,
                     provenance=excluded.provenance,
                     updated_at=excluded.updated_at""",
                payload,
            )
            self._conn.commit()
        return payload

    def delete_logical_device(self, stable_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM logical_devices WHERE stable_id=?", (stable_id,)
            )
            self._conn.commit()
        return bool(cur.rowcount)

    def list_merged_logical_devices(self) -> list[dict]:
        """合并审计：列出所有 A2 自动合并（provenance='auto-merged'）的逻辑设备。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM logical_devices WHERE provenance='auto-merged' ORDER BY stable_id"
            ).fetchall()
        return [self._logical_row_to_dict(r) for r in rows]

    def split_logical_device(self, stable_id: str) -> dict:
        """回滚 / 拆分一个 A2 自动合并设备：把每个候选实体还原为独立逻辑设备，
        并标记为 ``user-pinned``（对账时不会被 A2 再次自动合并）。

        返回拆出的子设备 stable_id 列表；原合并设备被删除。
        """
        dev = self.get_logical_device(stable_id)
        if dev is None:
            return {"ok": False, "error": "逻辑设备不存在", "code": 404}
        if dev.get("provenance") != "auto-merged":
            return {"ok": False, "error": "仅 auto-merged 设备可拆分回滚", "code": 400}
        candidates = dev.get("candidates") or []
        if not candidates:
            return {"ok": False, "error": "无候选实体可拆分", "code": 400}

        created = []
        for c in candidates:
            eid = (c.get("entity_id") or c.get("stable_id") or "").strip()
            if not eid:
                continue
            self.upsert_logical_device(
                {
                    "stable_id": eid,
                    "display_name": c.get("name") or eid,
                    "device_class": dev.get("device_class") or "",
                    "primary_entity": eid,
                    "candidates": [c],
                    "provenance": "user-pinned",
                }
            )
            with self._lock:
                self._conn.execute(
                    "UPDATE device_health SET stable_id=? WHERE entity_id=?", (eid, eid)
                )
                self._conn.commit()
            created.append(eid)

        self.delete_logical_device(stable_id)
        return {"ok": True, "split_into": created, "deleted": stable_id}

    def upsert_device_health(self, entity_id: str, **fields: Any) -> dict:
        """按 entity_id 局部更新健康/墓碑记录；未提供的字段保持原值。"""
        now = now_local(self.tz_offset_hours).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM device_health WHERE entity_id=?", (entity_id,)
            ).fetchone()
            row = (
                dict(cur)
                if cur
                else {
                    "entity_id": entity_id,
                    "stable_id": "",
                    "state": "active",
                    "last_seen": "",
                    "last_data_ts": "",
                    "referenced": 0,
                    "note": "",
                    "updated_at": now,
                }
            )
            for key in (
                "stable_id",
                "state",
                "last_seen",
                "last_data_ts",
                "referenced",
                "note",
            ):
                if fields.get(key) is not None:
                    row[key] = fields[key]
            row["updated_at"] = now
            self._conn.execute(
                """INSERT INTO device_health
                   (entity_id, stable_id, state, last_seen, last_data_ts,
                    referenced, note, updated_at)
                   VALUES (:entity_id, :stable_id, :state, :last_seen, :last_data_ts,
                           :referenced, :note, :updated_at)
                   ON CONFLICT(entity_id) DO UPDATE SET
                     stable_id=excluded.stable_id,
                     state=excluded.state,
                     last_seen=excluded.last_seen,
                     last_data_ts=excluded.last_data_ts,
                     referenced=excluded.referenced,
                     note=excluded.note,
                     updated_at=excluded.updated_at""",
                row,
            )
            self._conn.commit()
        return row

    def get_device_health(self, entity_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM device_health WHERE entity_id=?", (entity_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_device_health(self, state: str = "", limit: int | None = None,
                           offset: int = 0) -> list[dict]:
        """健康/墓碑清单；state 为空返回全部，否则按状态过滤。

        ``limit=None`` = **无界**，这是进程内既有调用点的语义（身份层重建、HTTP 面板
        都要整张表）。分页只在对外工具面发生（DCD 20261004 MA-裁4 Q1 由调用方传 500），
        所以这里不把默认值改成 500——改了会让上面两处静默少读。

        排序必须带 ``entity_id`` 兜底：批量 upsert 给整片行打的是同一个 ``updated_at``，
        只按时间排的话 LIMIT/OFFSET 相邻两页会重叠并漏行。
        """
        sql = "SELECT * FROM device_health"
        params: list = []
        if state:
            sql += " WHERE state=?"
            params.append(state)
        sql += " ORDER BY updated_at DESC, entity_id"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([max(0, int(limit)), max(0, int(offset))])
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count_device_health(self, state: str = "") -> int:
        """分页前的大小——``total`` 要的是全量条数，不是当前页的行数。"""
        sql = "SELECT COUNT(*) FROM device_health"
        params: list = []
        if state:
            sql += " WHERE state=?"
            params.append(state)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row[0] or 0)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.commit()
                    # 关闭前强制 WAL checkpoint(TRUNCATE)，把 WAL 合并回主库并截断 WAL 文件，
                    # 避免重启时大 WAL 恢复失败导致数据库损坏（审计：反复损坏根因）。
                    try:
                        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    except Exception as e:
                        logging.getLogger(__name__).warning(
                            "Store.close: WAL checkpoint(TRUNCATE) 失败: %s", e
                        )
                    self._conn.close()
                except Exception as e:
                    logging.getLogger(__name__).warning(
                        "Store.close: commit/close 异常: %s", e
                    )
                self._conn = None

    # -- 竞技场快照 / 结果（AutoFlow 竞技场对接）--------------------------------

    def save_arena_snapshot(self, arena_id, room, devices, history_days, snapshot_json) -> dict:
        """写入/递增一个竞技场分区的版本化快照；返回 {version, id}。"""
        with self._lock:
            conn = self._conn
            cur = conn.execute(
                "SELECT COALESCE(MAX(version),0) FROM arena_snapshots WHERE arena_id=?",
                (arena_id,),
            )
            version = (cur.fetchone()[0] or 0) + 1
            cur = conn.execute(
                """INSERT INTO arena_snapshots
                   (arena_id, version, room, devices, history_days, snapshot_json, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (arena_id, version, room, devices, history_days, snapshot_json,
                 now_local(self.tz_offset_hours)),
            )
            conn.commit()
            return {"version": version, "id": cur.lastrowid}

    def get_arena_snapshot(self, arena_id, version=None) -> dict | None:
        with self._lock:
            if version is None:
                row = self._conn.execute(
                    "SELECT * FROM arena_snapshots WHERE arena_id=? ORDER BY version DESC LIMIT 1",
                    (arena_id,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM arena_snapshots WHERE arena_id=? AND version=?",
                    (arena_id, version),
                ).fetchone()
            return dict(row) if row else None

    def list_arena_snapshots(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT arena_id, MAX(version) AS version, room, devices, history_days,
                          MAX(created_at) AS created_at
                   FROM arena_snapshots GROUP BY arena_id ORDER BY created_at DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    def add_arena_result(self, arena_id, agent_id, task_title, task_description,
                         flow_dsl, success, token_used, used_memory_tools) -> str:
        insight_id = "arena_" + uuid.uuid4().hex[:16]
        with self._lock:
            self._conn.execute(
                """INSERT INTO arena_results
                   (insight_id, arena_id, agent_id, task_title, task_description,
                    flow_dsl, success, token_used, used_memory_tools, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (insight_id, arena_id, agent_id, task_title, task_description,
                 flow_dsl, 1 if success else 0, int(token_used or 0),
                 json.dumps(used_memory_tools or [], ensure_ascii=False),
                 now_local(self.tz_offset_hours)),
            )
            self._conn.commit()
        return insight_id

    def get_arena_result(self, insight_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM arena_results WHERE insight_id=?", (insight_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_arena_analytics(self, arena_id=None, limit_recent=20) -> dict:
        """竞技场闭环分析：按「是否使用家庭记忆 / 洞察工具」分 cohort，对比成功率与 token。

        返回 {total, with_memory, without_memory, by_arena, by_agent, recent}。
        cohort 划分依据 arena_results.used_memory_tools 是否非空列表。
        """
        with self._lock:
            if arena_id:
                rows = self._conn.execute(
                    "SELECT insight_id, arena_id, agent_id, task_title, success, "
                    "token_used, used_memory_tools, created_at FROM arena_results "
                    "WHERE arena_id=? ORDER BY id DESC", (arena_id,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT insight_id, arena_id, agent_id, task_title, success, "
                    "token_used, used_memory_tools, created_at FROM arena_results "
                    "ORDER BY id DESC"
                ).fetchall()
        rows = [dict(r) for r in rows]

        def _used(r) -> int:
            try:
                return len(json.loads(r["used_memory_tools"] or "[]") or [])
            except Exception:
                return 0

        def _cohort(used: bool) -> dict:
            sub = [r for r in rows if (_used(r) > 0) == used]
            n = len(sub)
            ok = sum(1 for r in sub if r["success"])
            toks = [r["token_used"] for r in sub if r["token_used"]]
            return {
                "count": n,
                "success_count": ok,
                "success_rate": round(ok / n, 3) if n else 0.0,
                "avg_token": round(sum(toks) / len(toks), 1) if toks else 0,
                "total_token": sum(toks),
            }

        def _group(key):
            groups: dict = {}
            for r in rows:
                groups.setdefault(r[key], []).append(r)
            out = []
            for g, sub in groups.items():
                with_m = [r for r in sub if _used(r) > 0]
                no_m = [r for r in sub if _used(r) == 0]
                ok_m = sum(1 for r in with_m if r["success"])
                ok_n = sum(1 for r in no_m if r["success"])
                toks_m = [r["token_used"] for r in with_m if r["token_used"]]
                out.append({
                    key: g,
                    "total": len(sub),
                    "with_memory": {
                        "count": len(with_m),
                        "success_rate": round(ok_m / len(with_m), 3) if with_m else 0.0,
                        "avg_token": round(sum(toks_m) / len(toks_m), 1) if toks_m else 0,
                    },
                    "without_memory": {
                        "count": len(no_m),
                        "success_rate": round(ok_n / len(no_m), 3) if no_m else 0.0,
                    },
                })
            out.sort(key=lambda x: -x["total"])
            return out

        recent = [{
            "insight_id": r["insight_id"],
            "arena_id": r["arena_id"],
            "agent_id": r["agent_id"],
            "task_title": r["task_title"],
            "success": bool(r["success"]),
            "token_used": r["token_used"],
            "used_memory_tools": _used(r),
            "created_at": r["created_at"],
        } for r in rows[: int(limit_recent)]]

        return {
            "total": len(rows),
            "with_memory": _cohort(True),
            "without_memory": _cohort(False),
            "by_arena": _group("arena_id"),
            "by_agent": _group("agent_id"),
            "recent": recent,
        }

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
            # 审计 S8：软上限，超过则按创建时间淘汰最旧 10%，防止无限增长
            if conn.execute("SELECT COUNT(*) FROM voice_answer_cache").fetchone()[0] > _VOICE_CACHE_MAX:
                conn.execute(
                    "DELETE FROM voice_answer_cache WHERE cache_key IN ("
                    "SELECT cache_key FROM voice_answer_cache ORDER BY created_at ASC, rowid ASC LIMIT ?)",
                    (max(1, _VOICE_CACHE_MAX // 10),),
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
        return dict(zip(cols, row, strict=True))

    def list_answer_cache(self, limit: int = 100) -> list[dict]:
        conn = self.connect()
        with self._lock:
            rows = conn.execute(
                "SELECT cache_key, intent, payload, hits, last_used, created_at "
                "FROM voice_answer_cache ORDER BY hits DESC, last_used DESC LIMIT ?",
                (clamp_limit(limit, 100, 1000, label="list_answer_cache"),),
            ).fetchall()
        cols = ("cache_key", "intent", "payload", "hits", "last_used", "created_at")
        return [dict(zip(cols, r, strict=True)) for r in rows]

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

    # -- 视觉行为事件（多模态识别） -------------------------------------------

    def insert_behavior_event(self, payload: dict) -> int:
        """写入一条行为事件，返回自增 id。"""
        ts = payload.get("server_ts") or now_local(self.tz_offset_hours).isoformat(sep="T")
        appearance = payload.get("appearance")
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                """INSERT INTO behavior_events(
                     server_ts, device_ts, day, room, camera_src,
                     persons_json, count, action, scene, confidence,
                     appearance_json, trigger, vlm_latency_ms,
                     snapshot_path, raw_response, status, client,
                     scene_graph_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ts,
                    payload.get("device_ts"),
                    payload.get("day") or str(ts)[:10],
                    payload.get("room") or "",
                    payload.get("camera_src") or "",
                    Store._serialize_persons(payload.get("persons") or []),
                    int(payload.get("count") or 0),
                    payload.get("action"),
                    payload.get("scene"),
                    payload.get("confidence"),
                    json.dumps(appearance, ensure_ascii=False) if appearance is not None else None,
                    payload.get("trigger") or "manual",
                    payload.get("vlm_latency_ms"),
                    payload.get("snapshot_path"),
                    payload.get("raw_response"),
                    payload.get("status") or "ok",
                    payload.get("client") or "",
                    json.dumps(payload["scene_graph_json"], ensure_ascii=False) if payload.get("scene_graph_json") else None,
                ),
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def _behavior_event_where(
        self, room: str | None = None, member: str | None = None,
        day_from: str | None = None, day_to: str | None = None,
        status: str | None = None,
    ) -> tuple[list[str], list]:
        """`behavior_events` 的过滤条件——`list_*` 与 `count_*` 共用一份，防止两边口径漂移。

        `member` 必须下推进 SQL（裁5 追加 Q-A 授权的「不带 LIMIT 的计数查询」靠它）。
        原先它是在 `LIMIT` **之后**用 Python 过滤的：请求 `limit=100` 时先取最近 100 行
        再筛人，库里真有 800 条也在筛后归零，于是「匹配总数」在这个读取形状下根本不可信。

        判定口径与 `_deserialize_persons` 逐字对齐：非法 JSON / 非数组 ⇒ 无人员（不匹配）；
        dict 元素比 `name`，旧格式的**字符串元素本身即姓名**。字符串那条要注意 `json_each`
        给字符串元素的 `type` 是 `text` 而不是 `string`——生产库实测写成 `string` 会漏掉
        那 1 行旧格式记录（8 个姓名里唯一一个比对不一致，正是它）。
        """
        conds: list[str] = []
        args: list = []
        if room:
            conds.append("room = ?")
            args.append(room)
        if status:
            conds.append("status = ?")
            args.append(status)
        if day_from:
            conds.append("day >= ?")
            args.append(day_from)
        if day_to:
            conds.append("day <= ?")
            args.append(day_to)
        if member:
            conds.append(
                "EXISTS (SELECT 1 FROM json_each("
                "CASE WHEN json_valid(behavior_events.persons_json)=1 "
                "AND json_type(behavior_events.persons_json)='array' "
                "THEN behavior_events.persons_json ELSE '[]' END) j "
                "WHERE (j.type='object' AND json_extract(j.value,'$.name')=?)"
                " OR (j.type='text' AND j.value=?))"
            )
            args.extend([member, member])
        return conds, args

    def count_behavior_events(
        self, room: str | None = None, member: str | None = None,
        day_from: str | None = None, day_to: str | None = None,
        status: str | None = None,
    ) -> int:
        """匹配的事件总数（不带 LIMIT 的 COUNT）—— 裁5 追加 Q-A 授权的计数查询。

        与 `list_behavior_events` 用同一个 `_behavior_event_where`，所以「总数」与「这一页」
        是同一批行的两种投影，`total >= len(page)` 恒成立。它不受 `max_scan` 那类扫描上限
        约束（上限管的是把行读进内存的那条路径），也不参与 `status` 之外的默认过滤。
        """
        conds, args = self._behavior_event_where(room, member, day_from, day_to, status)
        sql = "SELECT COUNT(*) FROM behavior_events"
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        conn = self.connect()
        with self._lock:
            return int(conn.execute(sql, args).fetchone()[0] or 0)

    def list_behavior_events(
        self, room: str | None = None, member: str | None = None,
        day_from: str | None = None, day_to: str | None = None,
        limit: int = 100, status: str | None = None, offset: int = 0,
    ) -> list[dict]:
        """查询行为事件。过滤（含 `member`）全部在 SQL 里完成，`offset` 支持翻页。"""
        conds, args = self._behavior_event_where(room, member, day_from, day_to, status)
        sql = "SELECT * FROM behavior_events"
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY server_ts DESC LIMIT ? OFFSET ?"
        args.extend([int(limit), int(offset)])
        conn = self.connect()
        with self._lock:
            rows = conn.execute(sql, args).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            try:
                d["persons"] = Store._deserialize_persons(d.pop("persons_json"))
            except Exception:
                d["persons"] = []
            try:
                d["appearance"] = (
                    json.loads(d.pop("appearance_json")) if d.get("appearance_json") else None
                )
            except Exception:
                d["appearance"] = None
            d.pop("appearance_json", None)
            out.append(d)
        return out

    def list_scene_graphs(
        self, room: str | None = None, person: str | None = None,
        minutes: int | None = None, limit: int = 20,
    ) -> list[dict]:
        """vMA-1.2.0 场景图查询：只返回 scene_graph_json 非空的行为事件。

        room/时间窗走 SQL 过滤；person 场景图里有 persons 但没有独立列，
        故在解析后的 JSON 上按姓名/member_id 过滤（数据量受 limit 约束）。
        返回 ``[{event_id, server_ts, day, room, trigger, status, confidence, scene_graph}]``。
        """
        limit = clamp_limit(limit, 20, 200, label="list_scene_graphs")
        # minutes 在 0/None 都是"不加时间窗"的合法语义，所以下界收进 0 而不是 1；
        # 上界必须有（MA-11）：`?minutes=2000000000` 的 `timedelta(minutes=)` 会
        # OverflowError 打成 HTTP 500，路由侧的 `except (TypeError, ValueError)` 兜不住它。
        minutes = clamp_minutes(minutes, default=0, lo=0) if minutes else 0
        sql = (
            "SELECT id, server_ts, day, room, trigger, status, confidence, scene_graph_json "
            "FROM behavior_events "
            "WHERE scene_graph_json IS NOT NULL AND scene_graph_json != ''"
        )
        args: list = []
        if room:
            sql += " AND room = ?"
            args.append(room)
        if minutes > 0:
            since = (now_local(self.tz_offset_hours)
                     - timedelta(minutes=minutes)).isoformat(sep="T")
            sql += " AND server_ts >= ?"
            args.append(since)
        # person 过滤在 Python 侧，SQL 侧多取一些再截断，避免过滤后不足 limit
        sql += " ORDER BY server_ts DESC LIMIT ?"
        args.append(limit * 5 if person else limit)
        conn = self.connect()
        with self._lock:
            rows = conn.execute(sql, args).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            raw = d.pop("scene_graph_json", None)
            try:
                sg = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                sg = None
            if not isinstance(sg, dict):
                continue
            if person:
                persons = sg.get("persons") or []
                hit = any(
                    p.get("name") == person or p.get("member_id") == person
                    for p in persons if isinstance(p, dict)
                )
                if not hit:
                    continue
            d["scene_graph"] = sg
            out.append(d)
            if len(out) >= limit:
                break
        return out

    def count_room_action_days(self, room: str, action: str, days: int = 7) -> int:
        """统计同房间+同 action 在最近 N 天内出现的**不同天数**（Phase 2.1 候选晋升）。

        返回值是「跨天证据数」——≥3 天表示该行为模式已跨天重复出现，
        可晋升为候选记忆（habit:/vision/）。
        """
        sql = """
            SELECT COUNT(DISTINCT day) as n
            FROM behavior_events
            WHERE room = ? AND action = ?
              AND day >= date('now', 'localtime', ?)
        """
        conn = self.connect()
        with self._lock:
            row = conn.execute(sql, (room, action, f"-{int(days)} days")).fetchone()
        return int(dict(row).get("n") or 0)

    # -- 统一感知总线（主动感知 v2.0） -----------------------------------------

    def insert_perception_event(self, payload: dict) -> int:
        """写入一条归一化感知事件，返回自增 id（重复 event_id 被 IGNORE，返回 0）。

        payload 字段：event_id(可选) / server_ts / source / kind / room /
        entity_id / confidence / payload(dict) / raw_event(dict)。
        未显式给 event_id 时由 make_event_id(entity_id, server_ts) 派生，保证幂等。
        """
        ts = payload.get("server_ts") or now_local(self.tz_offset_hours).isoformat(sep="T")
        event_id = payload.get("event_id") or make_event_id(
            str(payload.get("entity_id") or ""), str(ts)
        )
        conn = self.connect()
        with self._lock:
            # 用 total_changes 差值判定是否真写入：INSERT OR IGNORE 命中唯一约束时
            # 不产生变更；而 cur.rowcount 在不同 sqlite3 版本/驱动下对 IGNORE 的
            # 取值不一致（线上容器实测为 0 但行已写入），不能作为判据。
            before = conn.total_changes
            cur = conn.execute(
                """INSERT OR IGNORE INTO perception_events(
                     event_id, server_ts, day, source, kind, room, entity_id, confidence,
                     payload_json, raw_event_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    ts,
                    payload.get("day") or str(ts)[:10],
                    payload.get("source") or "sensor",
                    payload.get("kind") or "unknown",
                    payload.get("room"),
                    payload.get("entity_id"),
                    payload.get("confidence"),
                    json.dumps(payload.get("payload") or {}, ensure_ascii=False),
                    json.dumps(payload["raw_event"], ensure_ascii=False)
                    if payload.get("raw_event") is not None else None,
                ),
            )
            conn.commit()
            if conn.total_changes <= before:
                return 0  # 幂等命中：事件已存在，未新增
            return int(cur.lastrowid or 0)

    def list_perception_events(
        self, source: str | None = None, kind: str | None = None,
        room: str | None = None, day_from: str | None = None,
        day_to: str | None = None, limit: int = 100,
    ) -> list[dict]:
        """查询统一感知总线事件，按时间倒序。"""
        sql = "SELECT * FROM perception_events"
        conds: list[str] = []
        args: list = []
        if source:
            conds.append("source = ?"); args.append(source)
        if kind:
            conds.append("kind = ?"); args.append(kind)
        if room:
            conds.append("room = ?"); args.append(room)
        if day_from:
            conds.append("day >= ?"); args.append(day_from)
        if day_to:
            conds.append("day <= ?"); args.append(day_to)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY server_ts DESC LIMIT ?"
        args.append(int(limit))
        conn = self.connect()
        with self._lock:
            rows = conn.execute(sql, args).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d.pop("payload_json") or "{}")
            except Exception:
                d["payload"] = {}
            d.pop("raw_event_json", None)
            out.append(d)
        return out

    def has_recent_edge_signal(self, room: str, since_iso: str) -> bool:
        """判断某房间在 since_iso 之后是否有 edge_ai 感知事件（VLM 取帧 Gate 用）。"""
        conn = self.connect()
        with self._lock:
            row = conn.execute(
                "SELECT 1 FROM perception_events "
                "WHERE source='edge_ai' AND room=? AND server_ts >= ? LIMIT 1",
                (room, since_iso),
            ).fetchone()
        return row is not None

    # ── 行为状态 / 候选规则（v0.9.5 主动感知·行为推断层）────────────────────

    def add_behavior_state(self, member: str, room: str, activity: str,
                           confidence: float, ts: str | None = None,
                           source: str = "inference", evidence: list | None = None) -> str:
        """写入一条 canonical 行为状态（权威），返回 state_id。

        ``state_id`` 由 (source, room, activity, ts) 派生而不是随机：推断任务每 5 分钟
        跑一次、窗口互相重叠，同一段序列会被连续几次运行各看见一遍。随机主键让
        每一次看见都变成一条新行（一条 ``就寝`` 落 3-5 份）；确定性主键让
        ``INSERT OR REPLACE`` 天然收敛成一条，member 归属以最新一次推断为准。
        """
        ts = ts or now_local(self.tz_offset_hours).isoformat(sep="T")
        sid = hashlib.sha1(
            f"{source}|{room or ''}|{activity}|{ts}".encode("utf-8")
        ).hexdigest()[:16]
        conn = self.connect()
        with self._lock:
            conn.execute(
                """INSERT OR REPLACE INTO behavior_states(
                     state_id, member, room, activity, confidence, ts, source,
                     evidence_json, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (sid, member or "", room or "", activity, float(confidence), ts,
                 source, json.dumps(evidence or [], ensure_ascii=False),
                 now_local(self.tz_offset_hours).isoformat(sep="T")),
            )
            conn.commit()
        return sid

    def list_behavior_states(self, member: str | None = None, room: str | None = None,
                             since: str | None = None, limit: int = 100) -> list[dict]:
        """查询 canonical 行为状态（按 ts 倒序）。"""
        sql = "SELECT * FROM behavior_states"
        conds: list[str] = []
        args: list = []
        if member:
            conds.append("member = ?")
            args.append(member)
        if room:
            conds.append("room = ?")
            args.append(room)
        if since:
            conds.append("ts >= ?")
            args.append(since)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(int(limit))
        conn = self.connect()
        with self._lock:
            rows = conn.execute(sql, args).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            try:
                d["evidence"] = json.loads(d.pop("evidence_json") or "[]")
            except Exception:
                d["evidence"] = []
                d.pop("evidence_json", None)
            out.append(d)
        return out

    def latest_behavior_state(self, member: str = "", room: str = "") -> dict | None:
        """取某成员（或某房间）最近一条行为状态。"""
        sql = "SELECT * FROM behavior_states"
        conds: list[str] = []
        args: list = []
        if member:
            conds.append("member = ?")
            args.append(member)
        if room:
            conds.append("room = ?")
            args.append(room)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY ts DESC LIMIT 1"
        conn = self.connect()
        with self._lock:
            row = conn.execute(sql, args).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["evidence"] = json.loads(d.pop("evidence_json") or "[]")
        except Exception:
            d["evidence"] = []
            d.pop("evidence_json", None)
        return d

    def upsert_candidate_rule(self, name: str, steps: list, time_window: str = "",
                              infer: str = "", confidence: float = 0.0,
                              source: str = "inference",
                              evidence: list | None = None) -> tuple[str, str]:
        """按 name 去重写入候选规则；已存在则刷新。返回 (rule_id, action)。"""
        # 质量闸门：自环过滤（相邻两步同一 entity_id）
        step_entities = [s.get("entity_id", "") for s in (steps or []) if isinstance(s, dict)]
        for i in range(len(step_entities) - 1):
            if step_entities[i] and step_entities[i] == step_entities[i + 1]:
                return "", "rejected_self_loop"
        # 质量闸门：置信度下限 0.3（transit 等单证据规则 0.4 可进候选）
        if float(confidence) < 0.3:
            return "", "rejected_low_confidence"
        steps_json = json.dumps(steps or [], ensure_ascii=False)
        now = now_local(self.tz_offset_hours).isoformat(sep="T")
        conn = self.connect()
        with self._lock:
            row = conn.execute(
                "SELECT rule_id FROM candidate_rules WHERE name = ?", (name,)
            ).fetchone()
            if row:
                rid = row["rule_id"]
                conn.execute(
                    """UPDATE candidate_rules SET steps_json=?, time_window=?, infer=?,
                       confidence=?, evidence_json=?, updated_at=? WHERE rule_id=?""",
                    (steps_json, time_window, infer, float(confidence),
                     json.dumps(evidence or [], ensure_ascii=False), now, rid),
                )
                conn.commit()
                return rid, "updated"
            rid = uuid.uuid4().hex[:16]
            conn.execute(
                """INSERT INTO candidate_rules(
                     rule_id, name, steps_json, time_window, infer, confidence,
                     source, status, evidence_json, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,'staging',?,?,?)""",
                (rid, name, steps_json, time_window, infer, float(confidence),
                 source, json.dumps(evidence or [], ensure_ascii=False), now, now),
            )
            conn.commit()
        return rid, "added"

    def update_candidate_rule_status(self, rule_id: str, status: str) -> dict | None:
        """同 set_candidate_rule_status，额外返回更新后的整行（MCP 工具需要）。"""
        if not self.set_candidate_rule_status(rule_id, status):
            return None
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM candidate_rules WHERE rule_id=?", (rule_id,)
            ).fetchone()
            if not row:
                return None
            cols = [d[0] for d in conn.execute("SELECT * FROM candidate_rules LIMIT 0").description]
        return dict(zip(cols, row, strict=True))

    def add_bug_report(self, tool_name: str, description: str,
                       expected: str = "", actual: str = "",
                       severity: str = "minor", reporter: str = "") -> dict:
        """记录一条 bug 上报。"""
        import time as _time, uuid as _uuid
        conn = self.connect()
        with self._lock:
            bug_id = f"bug_{_uuid.uuid4().hex[:10]}"
            now = _time.strftime("%Y-%m-%dT%H:%M:%S")
            conn.execute(
                """INSERT INTO bug_reports
                   (bug_id, tool_name, description, expected, actual, severity, status, reporter, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
                (bug_id, tool_name, description, expected, actual, severity, reporter, now)
            )
            conn.commit()
            return {"bug_id": bug_id, "status": "open", "created_at": now}

    def list_bug_reports(self, status: str = "open", limit: int = 50) -> list[dict]:
        """列出 bug 上报。status: open|resolved|all"""
        sql = "SELECT * FROM bug_reports"
        params = []
        if status and status != "all":
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(clamp_limit(limit, 50, 500, label="list_bug_reports"))
        with self._db() as conn:
            rows = conn.execute(sql, params).fetchall()
            cols = [d[0] for d in conn.execute("SELECT * FROM bug_reports LIMIT 0").description]
        return [dict(zip(cols, row, strict=True)) for row in rows]

    def list_candidate_rules(self, status: str | None = None, limit: int = 200) -> list[dict]:
        """列出候选序列规则（默认全部 status），解析 steps/evidence。"""
        sql = "SELECT * FROM candidate_rules"
        args: list = []
        if status and status != "all":
            sql += " WHERE status = ?"
            args.append(status)
        # TTL：staging 超过 14 天自动标 rejected
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(int(limit))
        conn = self.connect()
        with self._lock:
            rows = conn.execute(sql, args).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            try:
                d["steps"] = json.loads(d.pop("steps_json") or "[]")
            except Exception:
                d["steps"] = []
                d.pop("steps_json", None)
            try:
                d["evidence"] = json.loads(d.pop("evidence_json") or "[]")
            except Exception:
                d["evidence"] = []
                d.pop("evidence_json", None)
            out.append(d)
        return out

    def get_candidate_rule(self, rule_id: str) -> dict | None:
        """取一条候选规则（解析 steps/evidence），供生效通道做门槛判定。"""
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM candidate_rules WHERE rule_id=?", (rule_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["steps"] = json.loads(d.get("steps_json") or "[]")
        except Exception:
            d["steps"] = []
        try:
            d["evidence"] = json.loads(d.get("evidence_json") or "[]")
        except Exception:
            d["evidence"] = []
        return d

    def get_candidate_rule_by_name(self, name: str) -> dict | None:
        """按名字取候选（解析形状与 :meth:`get_candidate_rule` 同一把尺）。

        手动录入这条路径需要先看「这个名字是不是已经被机器建议占用了」——
        ``upsert_candidate_rule`` 判重就是按 name，读侧也必须按同一个键，
        否则查重与写入各按一套口径，撞名那条会静默覆盖。
        """
        with self._db() as conn:
            row = conn.execute(
                "SELECT rule_id FROM candidate_rules WHERE name=?", (name,)
            ).fetchone()
        return self.get_candidate_rule(str(row["rule_id"])) if row else None

    def update_candidate_rule(self, rule_id: str, *, steps: list | None = None,
                              time_window: str | None = None, infer: str | None = None,
                              confidence: float | None = None,
                              evidence: list | None = None) -> bool:
        """按 ``rule_id`` 刷新候选内容（只改传进来的字段）。

        与 ``upsert_candidate_rule`` 的分工：那条按 name 去重、给挖掘链路刷新建议；
        这条给人改**自己那条**草稿，所以按主键定位，不靠名字找。name 不可改——
        它是判重键，改名等于删一条再建一条，语义该由撤回+重录承担。
        """
        updates: list[str] = []
        params: list = []
        if steps is not None:
            updates.append("steps_json=?")
            params.append(json.dumps(steps, ensure_ascii=False))
        if time_window is not None:
            updates.append("time_window=?")
            params.append(str(time_window))
        if infer is not None:
            updates.append("infer=?")
            params.append(str(infer))
        if confidence is not None:
            updates.append("confidence=?")
            params.append(float(confidence))
        if evidence is not None:
            updates.append("evidence_json=?")
            params.append(json.dumps(evidence, ensure_ascii=False))
        if not updates:
            return False
        updates.append("updated_at=?")
        params.append(now_local(self.tz_offset_hours).isoformat(sep="T"))
        params.append(rule_id)
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                f"UPDATE candidate_rules SET {', '.join(updates)} WHERE rule_id=?",
                params,
            )
            conn.commit()
            return bool(cur.rowcount)

    def delete_candidate_rule(self, rule_id: str,
                              allow_statuses: tuple[str, ...]) -> bool:
        """按状态放行删除候选；不在白名单里的状态一律不删（返回 False）。

        刻意把 ``allow_statuses`` 交给调用方并写进 SQL 的 WHERE：已晋升
        （``promoted``）的候选对应着 ``active_rules`` 里的一行，删候选行会把这条
        链路的上游抹掉，而那条规则还在跑——下架它的路径是
        ``rule_lifecycle.revoke()``（红线 3：连带回滚推断、留审计）。
        """
        if not allow_statuses:
            return False
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                f"DELETE FROM candidate_rules WHERE rule_id=? "
                f"AND status IN ({', '.join('?' * len(allow_statuses))})",
                (rule_id, *allow_statuses),
            )
            conn.commit()
            return bool(cur.rowcount)

    def set_candidate_rule_status(self, rule_id: str, status: str) -> bool:
        """更新候选规则状态（staging | accepted | rejected | promoted），并同步 user_confirmed 红线位。

        词表只有一套：HTTP 与 MCP 都写 ``accepted`` 表示"人已确认"。历史上 MCP
        写过 ``confirmed``，导致该状态既不进 WebUI 的 accepted 列表、又不被任何读方识别。
        """
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                "UPDATE candidate_rules SET status=?, user_confirmed=?, updated_at=? WHERE rule_id=?",
                (status, 1 if status in (CANDIDATE_ACCEPTED, CANDIDATE_PROMOTED) else 0,
                 now_local(self.tz_offset_hours).isoformat(sep="T"), rule_id),
            )
            conn.commit()
            return bool(cur.rowcount)

    def set_candidate_rule_cooldown(self, rule_id: str,
                                    cooldown_seconds: int | None) -> bool:
        """写下这条候选晋升后采用的冷却上限（DCD 20261004 MA-裁1 Q1）。

        传 ``None`` = 撤回设定：列回到未设定状态，晋升端会重新拒绝。数值合法性
        由晋升端判（那里才有「拒」的动作与审计留痕），这里只做整数化落库。
        """
        value = None if cooldown_seconds is None else int(cooldown_seconds)
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                "UPDATE candidate_rules SET cooldown_seconds=?, updated_at=? WHERE rule_id=?",
                (value, now_local(self.tz_offset_hours).isoformat(sep="T"), rule_id),
            )
            conn.commit()
            return bool(cur.rowcount)

    # ── 行为异常（P1.1 过程挖掘：偏离过程模型的 case）──────────────────────

    def upsert_behavior_anomaly(
        self, case_key: str, day: str = "", room: str = "",
        reasons: list | None = None, activities: list | None = None,
        rare_edges: list | None = None, rare_activities: list | None = None,
        severity: float = 0.0, engine: str = "pure",
    ) -> tuple[str, str]:
        """按 ``case_key`` 去重写入行为异常；已存在则刷新判据。

        **保留人工 status**（new/confirmed/ignored）——重跑挖掘不应把人工复核
        结果洗掉。返回 ``(anomaly_id, "added"|"updated")``。
        """
        aid = hashlib.md5(str(case_key).encode("utf-8")).hexdigest()[:16]
        now = now_local(self.tz_offset_hours).isoformat(sep="T")
        conn = self.connect()
        with self._lock:
            row = conn.execute(
                "SELECT anomaly_id FROM behavior_anomalies WHERE case_key = ?", (case_key,)
            ).fetchone()
            if row:
                conn.execute(
                    """UPDATE behavior_anomalies SET day=?, room=?, reasons_json=?,
                       activities_json=?, rare_edges_json=?, rare_acts_json=?,
                       severity=?, engine=?, updated_at=? WHERE anomaly_id=?""",
                    (day, room, json.dumps(reasons or [], ensure_ascii=False),
                     json.dumps(activities or [], ensure_ascii=False),
                     json.dumps(rare_edges or [], ensure_ascii=False),
                     json.dumps(rare_activities or [], ensure_ascii=False),
                     float(severity), engine, now, aid),
                )
                conn.commit()
                return aid, "updated"
            conn.execute(
                """INSERT INTO behavior_anomalies(
                     anomaly_id, case_key, day, room, reasons_json, activities_json,
                     rare_edges_json, rare_acts_json, severity, engine, status,
                     detected_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,'new',?,?)""",
                (aid, case_key, day, room,
                 json.dumps(reasons or [], ensure_ascii=False),
                 json.dumps(activities or [], ensure_ascii=False),
                 json.dumps(rare_edges or [], ensure_ascii=False),
                 json.dumps(rare_activities or [], ensure_ascii=False),
                 float(severity), engine, now, now),
            )
            conn.commit()
        return aid, "added"

    def list_behavior_anomalies(
        self, status: str | None = None, day_from: str | None = None,
        day_to: str | None = None, room: str | None = None, limit: int = 200,
    ) -> list[dict]:
        """列出行为异常（严重度降序、天倒序），解析 JSON 字段。"""
        sql = "SELECT * FROM behavior_anomalies"
        conds: list[str] = []
        args: list = []
        if status:
            conds.append("status = ?")
            args.append(status)
        if room:
            conds.append("room = ?")
            args.append(room)
        if day_from:
            conds.append("day >= ?")
            args.append(day_from)
        if day_to:
            conds.append("day <= ?")
            args.append(day_to)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY severity DESC, day DESC LIMIT ?"
        args.append(max(1, min(int(limit), 2000)))
        conn = self.connect()
        with self._lock:
            rows = conn.execute(sql, args).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            for key, col in (("reasons", "reasons_json"), ("activities", "activities_json"),
                             ("rare_edges", "rare_edges_json"),
                             ("rare_activities", "rare_acts_json")):
                try:
                    d[key] = json.loads(d.pop(col) or "[]")
                except Exception:
                    d[key] = []
                    d.pop(col, None)
            out.append(d)
        return out

    def set_behavior_anomaly_status(self, anomaly_id: str, status: str) -> bool:
        """更新行为异常状态（new | confirmed | ignored）。"""
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                "UPDATE behavior_anomalies SET status=?, updated_at=? WHERE anomaly_id=?",
                (status, now_local(self.tz_offset_hours).isoformat(sep="T"), anomaly_id),
            )
            conn.commit()
            return bool(cur.rowcount)

    def purge_behavior_anomalies(self, before_day: str) -> int:
        """清理 ``day < before_day`` 的异常（保留 confirmed，便于复盘）。"""
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                "DELETE FROM behavior_anomalies WHERE day < ? AND status != 'confirmed'",
                (before_day,),
            )
            conn.commit()
            return int(cur.rowcount or 0)

    # ── 在线异常 / 概念漂移（P1.2，river）────────────────────────────────

    def upsert_behavior_drift(
        self, bucket_ts: str, kind: str = "drift", day: str = "",
        density: float = 0.0, score: float = 0.0,
        tags: list | None = None, detail: dict | None = None,
    ) -> tuple[str, str]:
        """按 ``(bucket_ts, kind)`` 幂等写入漂移/异常点，返回 ``(drift_id, added|updated)``。

        幂等键用时间桶+类型：同一天重跑挖掘不会重复堆记录（与 P1.1 的 case 幂等同理）。
        """
        did = hashlib.md5(f"{bucket_ts}|{kind}".encode("utf-8")).hexdigest()[:16]
        now = now_local(self.tz_offset_hours).isoformat(sep="T")
        day = day or str(bucket_ts)[:10]
        conn = self.connect()
        with self._lock:
            row = conn.execute(
                "SELECT drift_id FROM behavior_drifts WHERE bucket_ts = ? AND kind = ?",
                (bucket_ts, kind),
            ).fetchone()
            if row:
                conn.execute(
                    """UPDATE behavior_drifts SET day=?, density=?, score=?,
                       tags_json=?, detail_json=?, detected_at=? WHERE drift_id=?""",
                    (day, float(density), float(score),
                     json.dumps(tags or [], ensure_ascii=False),
                     json.dumps(detail or {}, ensure_ascii=False), now, did),
                )
                conn.commit()
                return did, "updated"
            conn.execute(
                """INSERT INTO behavior_drifts(
                     drift_id, bucket_ts, day, kind, density, score,
                     tags_json, detail_json, detected_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (did, bucket_ts, day, kind, float(density), float(score),
                 json.dumps(tags or [], ensure_ascii=False),
                 json.dumps(detail or {}, ensure_ascii=False), now),
            )
            conn.commit()
        return did, "added"

    def list_behavior_drifts(
        self, kind: str | None = None, day_from: str | None = None,
        day_to: str | None = None, limit: int = 200,
    ) -> list[dict]:
        """列出漂移/异常点（按时间倒序），解析 JSON 字段。"""
        sql = "SELECT * FROM behavior_drifts"
        conds: list[str] = []
        args: list = []
        if kind:
            conds.append("kind = ?")
            args.append(kind)
        if day_from:
            conds.append("day >= ?")
            args.append(day_from)
        if day_to:
            conds.append("day <= ?")
            args.append(day_to)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY bucket_ts DESC LIMIT ?"
        args.append(max(1, min(int(limit), 2000)))
        conn = self.connect()
        with self._lock:
            rows = conn.execute(sql, args).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            for key, col in (("tags", "tags_json"), ("detail", "detail_json")):
                try:
                    d[key] = json.loads(d.pop(col) or ("[]" if key == "tags" else "{}"))
                except Exception:
                    d[key] = [] if key == "tags" else {}
                    d.pop(col, None)
            out.append(d)
        return out

    def purge_behavior_drifts(self, before_day: str) -> int:
        """清理 ``day < before_day`` 的漂移点。"""
        conn = self.connect()
        with self._lock:
            cur = conn.execute("DELETE FROM behavior_drifts WHERE day < ?", (before_day,))
            conn.commit()
            return int(cur.rowcount or 0)

    # ── 幂等键（v0.9 MCP 契约：写工具防重复执行）──────────────────────────

    def get_idempotency(self, idem_key: str) -> dict | None:
        """取幂等键缓存结果；不存在、已过期、或仍是占位（未执行完）返回 None。"""
        conn = self.connect()
        with self._lock:
            row = conn.execute(
                "SELECT idem_key, tool, result_text, is_error, created_at, expires_at,"
                " COALESCE(state, 'done') AS state"
                " FROM idempotency_keys WHERE idem_key = ?", (idem_key,)
            ).fetchone()
        if not row:
            return None
        if (row["state"] or "done") != "done":
            return None
        if (row["expires_at"] or "") < self._idem_now():
            return None
        return dict(row)

    def _idem_now(self) -> str:
        """幂等 TTL 的比较基准：完整时间戳（M-1 前只有日期，粒度差 24 倍）。"""
        return now_local(self.tz_offset_hours).strftime(IDEMPOTENCY_TS_FMT)

    def _idem_expires(self, ttl_hours: int) -> str:
        return (now_local(self.tz_offset_hours)
                + timedelta(hours=max(1, int(ttl_hours)))).strftime(IDEMPOTENCY_TS_FMT)

    def save_idempotency(self, idem_key: str, tool: str, result_text: str,
                         is_error: bool = False, ttl_hours: int = 24) -> None:
        """记录幂等键 → 结果映射（重复调用可直接返回缓存）。

        这是「先执行、后写缓存」的旧入口，并发下同一 key 会各自执行一次；
        分发层请改走 ``reserve_idempotency`` + ``finalize_idempotency``
        （第六轮审计 CRITICAL-1）。
        """
        now = now_local(self.tz_offset_hours)
        conn = self.connect()
        with self._lock:
            conn.execute(
                """INSERT OR REPLACE INTO idempotency_keys
                   (idem_key, tool, result_text, is_error, created_at, expires_at, state)
                   VALUES (?,?,?,?,?,?,'done')""",
                (idem_key, tool, result_text, 1 if is_error else 0,
                 now.isoformat(timespec="seconds"), self._idem_expires(ttl_hours)),
            )
            conn.commit()

    def reserve_idempotency(self, idem_key: str, tool: str,
                            ttl_hours: int = 24) -> dict:
        """原子占位：把「查缓存」和「开始执行」压成同一步（第六轮审计 CRITICAL-1）。

        返回 ``{"state": ...}``：

        * ``reserved``——本次调用拿到执行权，随后**必须** finalize 或 release，
          否则这一路调用永远不会缓存结果；
        * ``done``——已有结果（带 ``result_text`` / ``is_error``），直接回放；
        * ``in_flight``——同一 key 正在别处执行，调用方**不得**再执行工具。

        占位靠的是 ``idem_key`` 主键 + ``INSERT OR IGNORE``，判定全在同一把锁与同
        一个写连接里完成，因此跨线程成立；崩溃残留的 pending 行到期后可被重新占位。
        """
        now_ts = self._idem_now()
        expires = self._idem_expires(ttl_hours)
        created = now_local(self.tz_offset_hours).isoformat(timespec="seconds")
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                """INSERT OR IGNORE INTO idempotency_keys
                   (idem_key, tool, result_text, is_error, created_at, expires_at, state)
                   VALUES (?, ?, '', 0, ?, ?, 'pending')""",
                (idem_key, tool, created, expires),
            )
            if cur.rowcount == 1:
                conn.commit()
                return {"state": "reserved", "idem_key": idem_key}
            row = conn.execute(
                "SELECT result_text, is_error, expires_at, COALESCE(state, 'done') AS state"
                " FROM idempotency_keys WHERE idem_key = ?", (idem_key,)
            ).fetchone()
            if (row["state"] or "done") == "done" and (row["expires_at"] or "") >= now_ts:
                conn.commit()
                return {"state": "done", "result_text": row["result_text"],
                        "is_error": bool(row["is_error"])}
            # 过期行（done 或 pending 崩溃残留）：条件改写，只有抢到的人 rowcount==1，
            # 第二个并发者的 WHERE expires_at < now 已不成立 → in_flight。
            cur = conn.execute(
                """UPDATE idempotency_keys
                     SET tool = ?, result_text = '', is_error = 0,
                         created_at = ?, expires_at = ?, state = 'pending'
                   WHERE idem_key = ? AND expires_at < ?""",
                (tool, created, expires, idem_key, now_ts),
            )
            conn.commit()
            if cur.rowcount == 1:
                return {"state": "reserved", "idem_key": idem_key}
            return {"state": "in_flight", "idem_key": idem_key}

    def finalize_idempotency(self, idem_key: str, result_text: str,
                             is_error: bool = False, ttl_hours: int = 24) -> None:
        """占位成功后写入真实结果，行转为 done（幂等回放的数据来源）。"""
        conn = self.connect()
        with self._lock:
            conn.execute(
                """UPDATE idempotency_keys
                     SET result_text = ?, is_error = ?, state = 'done', expires_at = ?
                   WHERE idem_key = ?""",
                (result_text, 1 if is_error else 0, self._idem_expires(ttl_hours), idem_key),
            )
            conn.commit()

    def release_idempotency(self, idem_key: str) -> int:
        """执行失败/异常时撤掉占位，允许调用方重试（只删 pending，绝不动已完成结果）。"""
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                "DELETE FROM idempotency_keys"
                " WHERE idem_key = ? AND COALESCE(state, 'done') = 'pending'",
                (idem_key,),
            )
            conn.commit()
            return int(cur.rowcount or 0)

    def pending_idempotency_count(self) -> int:
        """占位行计数（长跑可观测：占位只增不减说明有执行路径漏了 finalize/release）。"""
        conn = self.connect()
        with self._lock:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM idempotency_keys WHERE state = 'pending'"
            ).fetchone()
        return int(row["c"] or 0)

    def purge_idempotency(self) -> int:
        """清理过期幂等键（含崩溃后残留的占位行）。"""
        cutoff = self._idem_now()
        conn = self.connect()
        with self._lock:
            cur = conn.execute("DELETE FROM idempotency_keys WHERE expires_at < ?", (cutoff,))
            conn.commit()
            return cur.rowcount or 0

    # ── 在场查询（豆包管家对接：docs/交接单_MA对接_成员档案与在场查询.md 需求2）──

    def recent_presence(
        self, since_iso: str, room: str | None = None, limit: int = 500
    ) -> list[dict]:
        """最近 ``since_iso`` 之后各成员最后一次被识别到的时间与来源。

        直接读 ``behavior_events.persons_json``，不建新表。只统计**已识别身份**
        （``未识别`` / ``未识别成员`` / ``陌生人`` 一律跳过）——管家的问候逻辑
        只对「认出是谁」感兴趣，未识别的人不该触发称呼式问候。
        返回按 last_seen 倒序的列表，每项：
        ``{name, member_id, via, confidence, last_seen, room, trigger}``。
        """
        sql = "SELECT server_ts, room, trigger, persons_json FROM behavior_events WHERE server_ts >= ?"
        args: list = [since_iso]
        if room:
            sql += " AND room = ?"
            args.append(room)
        # 倒序取：同名成员第一次出现即为「最近一次」
        sql += " ORDER BY server_ts DESC LIMIT ?"
        args.append(max(50, min(int(limit), 2000)))
        conn = self.connect()
        with self._lock:
            rows = conn.execute(sql, args).fetchall()

        latest: dict[str, dict] = {}
        for r in rows:
            try:
                persons = Store._deserialize_persons(r["persons_json"])
            except Exception:
                continue
            if not isinstance(persons, list):
                continue
            for p in persons:
                if not isinstance(p, dict):
                    continue
                name = str(p.get("name") or "").strip()
                if not name or name in _UNKNOWN_IDENTITIES:
                    continue
                item = latest.get(name)
                if item is not None:
                    continue  # rows 已倒序，先前记录的即为更晚的时间
                latest[name] = {
                    "name": name,
                    "member_id": p.get("member_id"),
                    # TV 端上报用 via="face"，与补认的 "arcface" 等价，统一成 arcface 便于消费
                    "via": _VIA_ALIAS.get(str(p.get("via") or ""), str(p.get("via") or "")),
                    "via_raw": p.get("via"),
                    "confidence": _as_float(
                        p.get("match_confidence", p.get("confidence"))
                    ),
                    "last_seen": r["server_ts"],
                    "room": r["room"],
                    "trigger": r["trigger"],
                }
        return sorted(latest.values(), key=lambda x: x["last_seen"], reverse=True)

    def recent_occupancy(
        self, since_iso: str, room: str | None = None, limit: int = 2000
    ) -> list[dict]:
        """在场融合用：最近 ``since_iso`` 之后**每个房间最新一条**事件的占用快照。

        返回 ``[{room, persons:[...], count:int}]``——``count`` 为房间人数（含未识别），
        ``persons`` 为已识别成员名（``未识别``/``陌生人`` 之类占位已被剔除）。
        融合层据此算「未识别人数 = count - len(persons)」，做名册消除法身份推断
        （见 ``presence_fusion.fuse_presence``）。

        取每房间**最新一条**作为瞬时占用快照（与 ``recent_presence`` 的就近语义一致）；
        若某房间最新一条 ``count=0`` 且 ``persons=[]``，即视为该房间当前无人。
        """
        sql = (
            "SELECT room, persons_json, count FROM behavior_events WHERE server_ts >= ?"
        )
        args: list = [since_iso]
        if room:
            sql += " AND room = ?"
            args.append(room)
        # 倒序：每个房间先遇到的即为「最近一条」
        sql += " ORDER BY server_ts DESC LIMIT ?"
        args.append(max(50, min(int(limit), 5000)))
        conn = self.connect()
        with self._lock:
            rows = conn.execute(sql, args).fetchall()

        latest: dict[str, dict] = {}
        for r in rows:
            rm = (r["room"] or "").strip()
            if not rm or rm in latest:
                continue
            try:
                persons = Store._deserialize_persons(r["persons_json"])
            except Exception:
                persons = []
            rec = [
                str(p.get("name") or "").strip()
                for p in persons
                if isinstance(p, dict)
                and str(p.get("name") or "").strip()
                and str(p.get("name")).strip() not in _UNKNOWN_IDENTITIES
            ]
            cnt = int(r["count"] or 0)
            if cnt <= 0:
                cnt = len(rec)
            latest[rm] = {"room": rm, "persons": rec, "count": cnt}
        return list(latest.values())

    def member_schedule(self, name: str, days: int = 14, start: str = None, end: str = None) -> dict:
        """某成员在场时间分布（需求 3，供管家校准作息档），支持时间窗与时间维度切片。

        - 默认取最近 ``days`` 天；传 ``start``/``end``（YYYY-MM-DD）可拉取历史窗口，
          实现「作息演变可回溯」（任务 1 时间维度）。
        - 返回 ``segments``：按该成员习惯记忆（agent_memories 中 topic_key 以 ``habit:``
          开头、带 valid_from/valid_to）的有效窗口对每日样本分段，给出每段中位首/末次
          出现时间，使「上学期 22 点睡 → 这学期 23 点半」这类演变可被计算与展示；
          未被任何习惯覆盖的样本归入「(未归类)」段。
        appearances 为当天该成员被识别到的事件条数，受巡检频率与冷却限制，仅作趋势参考。
        """
        today = now_local(self.tz_offset_hours)
        today_s = today.strftime("%Y-%m-%d")
        if end:
            day_to = end
        else:
            day_to = today_s
        if start:
            day_from = start
        elif end:
            try:
                d = datetime.strptime(end, "%Y-%m-%d")
                day_from = (d - timedelta(days=clamp_days(days) - 1)).strftime("%Y-%m-%d")
            except Exception:
                day_from = (today - timedelta(days=clamp_days(days) - 1)).strftime("%Y-%m-%d")
        else:
            day_from = (today - timedelta(days=clamp_days(days) - 1)).strftime("%Y-%m-%d")
        conn = self.connect()
        with self._lock:
            rows = conn.execute(
                "SELECT day, server_ts, room, persons_json FROM behavior_events "
                "WHERE day >= ? AND day <= ? ORDER BY server_ts ASC",
                (day_from, day_to),
            ).fetchall()

        per_day: dict[str, dict] = {}
        for r in rows:
            try:
                persons = Store._deserialize_persons(r["persons_json"])
            except Exception:
                continue
            if not isinstance(persons, list):
                continue
            if not any(
                isinstance(p, dict) and str(p.get("name") or "").strip() == name
                for p in persons
            ):
                continue
            bucket = per_day.get(r["day"])
            if bucket is None:
                bucket = per_day[r["day"]] = {
                    "day": r["day"],
                    "first_seen": r["server_ts"],
                    "last_seen": r["server_ts"],
                    "appearances": 0,
                    "rooms": [],
                }
            bucket["appearances"] += 1
            ts = r["server_ts"] or ""
            if ts and (not bucket["first_seen"] or ts < bucket["first_seen"]):
                bucket["first_seen"] = ts
            if ts and ts > bucket["last_seen"]:
                bucket["last_seen"] = ts
            if r["room"] and r["room"] not in bucket["rooms"]:
                bucket["rooms"].append(r["room"])

        samples = [per_day[d] for d in sorted(per_day)]
        summary: dict = {
            "days_with_data": len(samples),
            "days_requested": int(days),
            "window": {"start": day_from, "end": day_to},
        }
        if samples:
            first_times = [s["first_seen"][11:16] for s in samples if len(s["first_seen"]) >= 16]
            last_times = [s["last_seen"][11:16] for s in samples if len(s["last_seen"]) >= 16]
            summary["median_first_seen"] = _median_hhmm(first_times)
            summary["median_last_seen"] = _median_hhmm(last_times)
        segments = self._member_habit_segments(name, day_from, day_to, per_day)
        return {
            "name": name,
            "days": int(days),
            "window": {"start": day_from, "end": day_to},
            "samples": samples,
            "summary": summary,
            "segments": segments,
        }

    def _member_habit_segments(self, name: str, day_from: str, day_to: str, per_day: dict) -> list:
        """按成员习惯记忆的有效窗口对样本分段，给出每段作息中位值（演变可回溯）。"""
        try:
            with self._db() as conn:
                rows = conn.execute(
                "SELECT topic_key, valid_from, valid_to, text FROM agent_memories "
                "WHERE tags_json LIKE ? ESCAPE '\\' AND topic_key LIKE 'habit:%' "
                "AND state <> 'revoked' AND valid_from <> '' "
                "ORDER BY valid_from ASC",
                (f"%member:{self._escape_like(name)}%",),
            ).fetchall()
        except Exception:
            return []
        segs: list = []
        uncovered = list(per_day.keys())
        for r in rows:
            vf = (r["valid_from"] or "")[:10]
            vt = (r["valid_to"] or "")[:10]
            if not vf:
                continue
            w_lo = vf if vf >= day_from else day_from
            w_hi = vt if vt and vt <= day_to else day_to
            if w_lo > w_hi:
                continue
            in_win = [per_day[d] for d in per_day if w_lo <= d <= w_hi]
            uncovered = [d for d in uncovered if not (w_lo <= d <= w_hi)]
            firsts = [s["first_seen"][11:16] for s in in_win if len(s["first_seen"]) >= 16]
            lasts = [s["last_seen"][11:16] for s in in_win if len(s["last_seen"]) >= 16]
            activity = (r["topic_key"] or "").split(":")[-1]
            segs.append({
                "activity": activity,
                "text": (r["text"] or "")[:120],
                "window": {"valid_from": r["valid_from"], "valid_to": r["valid_to"] or ""},
                "days_with_data": len(in_win),
                "median_first_seen": _median_hhmm(firsts),
                "median_last_seen": _median_hhmm(lasts),
            })
        if uncovered:
            firsts = [
                per_day[d]["first_seen"][11:16]
                for d in uncovered if len(per_day[d]["first_seen"]) >= 16
            ]
            lasts = [
                per_day[d]["last_seen"][11:16]
                for d in uncovered if len(per_day[d]["last_seen"]) >= 16
            ]
            segs.append({
                "activity": "(未归类)",
                "text": "",
                "window": {"valid_from": day_from, "valid_to": day_to},
                "days_with_data": len(uncovered),
                "median_first_seen": _median_hhmm(firsts),
                "median_last_seen": _median_hhmm(lasts),
            })
        return segs

    def clear_behavior_snapshots(self, before_day: str) -> list[str]:
        """清空 before_day 之前行的 snapshot_path（文件删除由服务层做），返回被清理的路径。"""
        conn = self.connect()
        with self._lock:
            rows = conn.execute(
                "SELECT snapshot_path FROM behavior_events "
                "WHERE snapshot_path IS NOT NULL AND snapshot_path != '' AND day < ?",
                (before_day,),
            ).fetchall()
            if not rows:
                return []
            paths = [r["snapshot_path"] for r in rows]
            conn.execute(
                "UPDATE behavior_events SET snapshot_path = NULL "
                "WHERE snapshot_path IS NOT NULL AND snapshot_path != '' AND day < ?",
                (before_day,),
            )
            conn.commit()
            return paths

    def get_behavior_event(self, event_id: int) -> dict | None:
        """按自增 id 取单条行为事件（标注闭环用）。"""
        conn = self.connect()
        with self._lock:
            row = conn.execute(
                "SELECT * FROM behavior_events WHERE id = ?", (event_id,)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["persons"] = Store._deserialize_persons(d.pop("persons_json"))
        except Exception:
            d["persons"] = []
        ap = d.get("appearance_json")
        try:
            d["appearance"] = json.loads(ap) if ap else None
        except Exception:
            d["appearance"] = None
        d.pop("appearance_json", None)
        return d

    def update_behavior_event_persons(
        self, event_id: int, persons: list[dict], via: str | None = None
    ) -> None:
        """回写行为事件的人员列表（人工标注时改写 name/vi）。"""
        if via:
            for p in persons:
                if isinstance(p, dict):
                    p["via"] = via
        conn = self.connect()
        with self._lock:
            conn.execute(
                "UPDATE behavior_events SET persons_json = ? WHERE id = ?",
                (Store._serialize_persons(persons), event_id),
            )
            conn.commit()

    def merge_member_appearance(self, member_id: str, appearance: Any) -> dict | None:
        """把一条未识别事件的外观并入成员档案（学习/补全）。

        以「非空字段覆盖」方式合并：新外观提供的字段覆盖旧值，未提供的字段保留。
        返回合并后的成员外观 dict。"""
        m = self.get_member(member_id)
        if not m:
            return None
        existing = m.get("appearance_json")
        try:
            existing = json.loads(existing) if isinstance(existing, str) else existing
        except Exception:
            existing = None
        if not isinstance(existing, dict):
            existing = {}
        new = appearance if isinstance(appearance, dict) else {}
        merged = dict(existing)
        for k, v in new.items():
            if v in (None, "", [], {}):
                continue
            # 保护前端结构化 clothing：若已有 dict 而新值是纯字符串（VLM 描述），保留结构
            if k == "clothing" and isinstance(merged.get(k), dict) and not isinstance(v, dict):
                continue
            merged[k] = v
        self.update_member(member_id, appearance_json=merged)
        return merged

    # -- 家庭成员 / 生活习惯档案 --------------------------------------------

    def create_member(
        self, name: str, avatar_emoji: str = "", avatar_bg: str = "#0EA5E9",
        avatar_url: str = "", note: str = "", appearance_json: Any = None
    ) -> dict:
        """创建家庭成员。返回新成员行 dict（含 rooms/devices/tags 空集合）。

        appearance_json 接受 dict 或已序列化的 JSON 字符串，便于从多模态识别结果
        直接写成员外观档案。"""
        conn = self.connect()
        mid = uuid.uuid4().hex
        stamp = now_local(self.tz_offset_hours).isoformat()
        ap = self._normalize_appearance(appearance_json)
        with self._lock:
            conn.execute(
                """INSERT INTO members(id, name, avatar_emoji, avatar_bg, avatar_url,
                                      note, appearance_json, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (mid, name, avatar_emoji, avatar_bg, avatar_url, note, ap, stamp, stamp),
            )
            conn.commit()
        m = self.get_member(mid) or {}
        m["rooms"] = []
        m["devices"] = []
        m["tags"] = []
        return m

    @staticmethod
    def _normalize_appearance(value: Any) -> str:
        """把外观档案规整为可入库的 JSON 字符串；非法/空值返回空串。"""
        if value is None or value == "":
            return ""
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except Exception:
                return ""  # 非合法 JSON 字符串不入库存脏数据
            value = parsed
        if not isinstance(value, dict):
            return ""
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _normalize_profile(value: Any) -> str:
        """把成员档案（profile_json）规整为可入库的 JSON 字符串。

        与 appearance 的区别：**不校验内部字段**（schema 归豆包管家所有，
        本服务只做透明存取），仅保证入库的是合法 JSON，避免脏数据。
        空值（None / ""）返回空串；非法 JSON 字符串返回 ``None`` 交由调用方报错。
        """
        if value is None or value == "":
            return ""
        if isinstance(value, str):
            try:
                json.loads(value)
            except Exception:
                return ""  # 与 appearance 一致：非合法 JSON 不入库
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _parse_json_field(raw: Any) -> Any:
        """把库里的 JSON 文本列解析成对象，失败返回 None。"""
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def get_member(self, member_id: str) -> dict | None:
        conn = self.connect()
        with self._lock:
            row = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
        if row is None:
            return None
        m = dict(row)
        # profile 为 profile_json 的解析视图，方便调用方（豆包管家）直接取对象
        m["profile"] = self._parse_json_field(m.get("profile_json"))
        m["rooms"] = self._member_rooms(conn, member_id)
        m["devices"] = self._member_devices(conn, member_id)
        m["tags"] = self.list_member_tags(member_id)
        return m

    @staticmethod
    def member_public_view(member: dict) -> dict:
        """返回成员的公开视图（剔除生物特征等敏感字段）。

        安全加固（审计 P1-8）：face_feature 是人脸特征向量，属于生物识别数据，
        不应随 API/MCP 工具结果流出（agent 上下文、日志、转述给用户都可能泄露）。
        所有对外出参必须过本函数，内部逻辑需要 face_feature 时直接用 get_member。
        """
        if not isinstance(member, dict):
            return member
        out = dict(member)
        out.pop("face_feature", None)
        return out

    def list_members(self) -> list[dict]:
        """列出全部成员，并附带其关联房间/设备与标签。

        审计 S1：原先逐成员各查 3 次（rooms/devices/tags）→ 1+3N 次 SQL。
        改为一次性批量拉取三张关联表并在 Python 侧按 member_id 分组（共 4 次 SQL）。
        """
        conn = self.connect()
        with self._lock:
            rows = conn.execute("SELECT * FROM members ORDER BY created_at").fetchall()
            rooms_map: dict[str, list[str]] = {}
            for r in conn.execute(
                "SELECT member_id, room_name FROM member_rooms ORDER BY member_id, room_name"
            ).fetchall():
                rooms_map.setdefault(r["member_id"], []).append(r["room_name"])
            devices_map: dict[str, list[str]] = {}
            for r in conn.execute(
                "SELECT member_id, entity_id FROM member_devices ORDER BY member_id, entity_id"
            ).fetchall():
                devices_map.setdefault(r["member_id"], []).append(r["entity_id"])
            tags_map: dict[str, list[dict]] = {}
            for r in conn.execute(
                "SELECT * FROM member_tags ORDER BY member_id, category, confidence DESC"
            ).fetchall():
                tags_map.setdefault(r["member_id"], []).append(self._tag_from_row(r))
            members = []
            for r in rows:
                m = dict(r)
                m["profile"] = self._parse_json_field(m.get("profile_json"))
                m["rooms"] = rooms_map.get(m["id"], [])
                m["devices"] = devices_map.get(m["id"], [])
                m["tags"] = tags_map.get(m["id"], [])
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
        # 安全加固（审计 P0-3）：face_feature 不在通用更新白名单内，
        # 人脸特征必须通过专门的注册/录入流程写入，防止 API 任意篡改生物特征。
        allowed = {"name", "avatar_emoji", "avatar_bg", "avatar_url", "note",
                   "appearance_json", "profile_json"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        assert set(sets).issubset(allowed), "update_member 列名越出白名单"
        if not sets:
            return self.get_member(member_id)
        # appearance_json 需规整为 JSON 字符串后入库
        if "appearance_json" in sets:
            sets["appearance_json"] = self._normalize_appearance(sets["appearance_json"])
        # profile_json：透明存取，但必须是合法 JSON（非法值拒绝写入而非静默清空）
        if "profile_json" in sets:
            raw_profile = sets["profile_json"]
            normalized = self._normalize_profile(raw_profile)
            if normalized == "" and raw_profile not in (None, ""):
                raise ValueError("profile_json 必须是合法 JSON（对象/数组/JSON 字符串）")
            sets["profile_json"] = normalized
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

    def merge_members(self, source_id: str, target_id: str) -> dict:
        """把 source 成员合并进 target，然后删除 source。

        合并策略（非破坏式，target 优先）：
        - 关联房间：并集
        - 专属设备：并集
        - 生活习惯标签：同名标签保留置信度更高的一条；置信度相同则合并证据
        - 外观档案 / 人脸特征 / 豆包 profile：target 为空才继承 source
        - 备注：target 为空才继承 source
        """
        if source_id == target_id:
            raise ValueError("源成员与目标成员不能相同")
        source = self.get_member(source_id)
        target = self.get_member(target_id)
        if not source:
            raise ValueError("源成员不存在")
        if not target:
            raise ValueError("目标成员不存在")

        # 1) 房间 / 设备并集
        merged_rooms = sorted(set((target.get("rooms") or []) + (source.get("rooms") or [])))
        merged_devices = sorted(set((target.get("devices") or []) + (source.get("devices") or [])))
        self.set_member_rooms(target_id, merged_rooms)
        self.set_member_devices(target_id, merged_devices)

        # 2) 标签：按 tag 名去重，高置信度优先
        target_tags = {t["tag"]: t for t in (target.get("tags") or [])}
        for st in source.get("tags") or []:
            name = st.get("tag")
            if not name:
                continue
            tt = target_tags.get(name)
            if tt and (tt.get("confidence") or 0) >= (st.get("confidence") or 0):
                # 目标已有且置信度更高/相等，跳过（保留目标）
                continue
            evidence = []
            if tt and tt.get("evidence"):
                evidence.extend(tt["evidence"])
            if st.get("evidence"):
                evidence.extend(st["evidence"])
            # 去重并保持顺序
            seen = set()
            evidence = [e for e in evidence if not (e in seen or seen.add(e))]
            self.add_member_tag(
                member_id=target_id,
                tag=name,
                category=st.get("category") or (tt.get("category") if tt else "other") or "other",
                emoji=st.get("emoji") or (tt.get("emoji") if tt else "") or "",
                confidence=float(st.get("confidence") or 0),
                evidence=evidence or None,
                source=st.get("source") or (tt.get("source") if tt else "agent") or "agent",
            )

        # 3) 外观 / 人脸 / profile / 备注：target 为空才继承
        update_fields = {}
        if not target.get("appearance_json") and source.get("appearance_json"):
            update_fields["appearance_json"] = source["appearance_json"]
        if not target.get("face_feature") and source.get("face_feature"):
            update_fields["face_feature"] = source["face_feature"]
        if not target.get("profile_json") and source.get("profile_json"):
            update_fields["profile_json"] = source["profile_json"]
        if not (target.get("note") or "").strip() and (source.get("note") or "").strip():
            update_fields["note"] = source["note"]
        if update_fields:
            self.update_member(target_id, **update_fields)

        # 4) 删除源成员
        self.delete_member(source_id)
        return self.get_member(target_id)

    # ── 人脸库中央集权（ArcSoft 特征，下行同步给节点 / 上行回写）────────────

    def set_member_face_feature(self, member_id: str, face_feature: str) -> bool:
        """写回某成员的 ArcSoft 特征（JSON 字符串，由节点上行）。空串表示清除。"""
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                "UPDATE members SET face_feature=?, updated_at=? WHERE id=?",
                (face_feature or "", now_local(self.tz_offset_hours).isoformat(), member_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_face_lib(self) -> list[dict]:
        """返回已注册特征的成员清单，供节点下行同步。

        每个元素：{member_id, name, face_feature}。face_feature 为节点上传的
        JSON 字符串（ArcSoft 特征 blob / base64）。"""
        conn = self.connect()
        with self._lock:
            rows = conn.execute(
                "SELECT id, name, face_feature FROM members "
                "WHERE face_feature IS NOT NULL AND face_feature != ''"
            ).fetchall()
        return [
            {"member_id": r["id"], "name": r["name"], "face_feature": r["face_feature"]}
            for r in rows
        ]

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
        exclude_entities: list[str] | None = None,
    ) -> tuple[str, list[Any]]:
        """构造统一的 WHERE 片段。所有查询/聚合共用，保证过滤语义一致。"""
        # 审计 S2：IN 子句实参过多会撑爆 SQLite 变量上限(32766)导致崩溃。
        # 设远高于正常家庭规模、又远低于 32766 的护栏，避免误伤合法大查询。
        MAX_IN = 2000
        for _name, _lst in (
            ("entities", entities), ("rooms", rooms), ("domains", domains),
            ("exclude_entities", exclude_entities), ("exclude_domains", exclude_domains),
            ("states", states),
        ):
            if _lst and len(_lst) > MAX_IN:
                raise ValueError(f"{_name} 过滤项过多（{len(_lst)}），请缩小范围后重试")
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
        if exclude_entities:
            sql.append(f"AND entity_id NOT IN ({','.join('?' * len(exclude_entities))})")
            args.extend(exclude_entities)
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
        exclude_entities: list[str] | None = None,
    ) -> int:
        where, args = self._filter_sql(
            start, end, rooms, entities, domains, person, states, exclude_domains, exclude_entities
        )
        conn = self.connect()
        with self._lock:
            return int(
                conn.execute(
                    f"SELECT COUNT(*) FROM events WHERE 1=1 {where}", args
                ).fetchone()[0]
            )


    def query_unified_events(
        self,
        person: str | None = None,
        room: str | None = None,
        start: str | None = None,
        end: str | None = None,
        source: str | None = None,
        limit: int = 100,
        order: str = "desc",
    ) -> dict:
        """vMA-1.3 统一事件查询（视图口径，只读）。

        聚合 events + behavior_events + perception_events。所有参数均可选，
        不传返回最近事件。返回列集与 ``unified_events`` 视图逐列一致。

        审计 P0-6/P2-4：不直接 ``SELECT ... FROM unified_events``。视图是
        UNION ALL，SQLite 不会把 WHERE / ORDER BY+LIMIT 下推进复合分支，
        直接查它等于把三张表连 payload 全量物化再排序（30 万行实测 3.1s，
        全程持全局 RLock）。这里把过滤和每支的 top-N 下推到分支，
        外层只合并 3×limit 行——口径不变，代价从「全表」降到「索引取 N 条」。
        """
        limit = max(1, min(int(limit), 500))
        order_dir = "DESC" if str(order).lower() == "desc" else "ASC"
        flt = {"person": person, "room": room, "start": start, "end": end}
        branches = [b for b in _UNIFIED_BRANCHES
                    if not source or b["source"] == source]

        conn = self.connect()
        with self._lock:
            rows: list[dict] = []
            if branches:
                # 每支先各自取 top-limit 再合并：SQLite 允许 CTE 内带 ORDER BY+LIMIT
                # （复合 SELECT 的「每支各自 ORDER BY」写法在这个版本上直接报语法错）。
                # 全局前 limit 名必然落在各支自己的前 limit 名里，所以结果与查视图等价。
                ctes, params, names = [], [], []
                for i, b in enumerate(branches):
                    conds, args = self._unified_conds(b, flt)
                    where = (" WHERE " + " AND ".join(conds)) if conds else ""
                    name = f"_ue{i}"
                    ctes.append(f"{name} AS ({b['select']}{where} "
                                f"ORDER BY {b['ts']} {order_dir} LIMIT ?)")
                    names.append(name)
                    params.extend(args)
                    params.append(limit)
                sql = (
                    "WITH " + ", ".join(ctes) + " SELECT "
                    + ", ".join(_UNIFIED_COLS) + " FROM ("
                    + " UNION ALL ".join(f"SELECT * FROM {n}" for n in names)
                    + f") ORDER BY server_ts {order_dir} LIMIT ?"
                )
                cur = conn.execute(sql, [*params, limit])
                rows = [dict(r) for r in cur.fetchall()]
            # 本页没填满 = LIMIT 没截断任何行，len(rows) 就是精确总数；
            # 只有本页被填满时才需要真去数（数也是走分支，不物化视图）。
            total = len(rows)
            if total >= limit:
                total = self._count_unified_events(conn, flt, source)

        return {
            "ok": True,
            "total": total,
            "count": len(rows),
            "rows": rows,
        }

    @staticmethod
    def _unified_conds(branch: dict, flt: dict) -> tuple[list[str], list[Any]]:
        """把一个视图级过滤翻成该分支自己的谓词（列表达式取自视图定义）。"""
        conds: list[str] = []
        args: list[Any] = []
        if branch.get("extra"):
            conds.append(branch["extra"])
        for key, op in (("person", "="), ("room", "="), ("start", ">="), ("end", "<=")):
            val = flt.get(key)
            if not val:
                continue
            col = branch["ts"] if key in ("start", "end") else branch[key]
            conds.append(f"{col} {op} ?")
            args.append(val)
        return conds, args

    def _count_unified_events(self, conn, flt: dict, source: str | None) -> int:
        """``SELECT COUNT(*) FROM unified_events WHERE ...`` 的等价下推版。"""
        total = 0
        for b in _UNIFIED_BRANCHES:
            if source and b["source"] != source:
                continue
            conds, args = self._unified_conds(b, flt)
            where = (" WHERE " + " AND ".join(conds)) if conds else " WHERE 1=1"
            head = b["select"].index(" FROM ")
            count_sql = "SELECT COUNT(*)" + b["select"][head:] + where
            total += int(conn.execute(count_sql, args).fetchone()[0])
        return total

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

    def list_excluded_entities(self, limit: int = 500) -> list[dict]:
        """NEW-P2-2：审计视图——因 domain 属于 TELEMETRY_DOMAINS 而被行为聚合排除的实体。
        运维可核对是否有设备被 HA 误配 domain（如插座报为 sensor）导致静默漏数。
        """
        placeholders = ",".join("?" * len(TELEMETRY_DOMAINS))
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                f"SELECT entity_id, domain, COUNT(*) AS event_count, MAX(ts) AS last_seen "
                f"FROM events WHERE domain IN ({placeholders}) "
                f"GROUP BY entity_id, domain ORDER BY event_count DESC LIMIT ?",
                (*TELEMETRY_DOMAINS, max(1, min(int(limit), 5000))),
            )
            return [
                {
                    "entity_id": r["entity_id"],
                    "domain": r["domain"],
                    "event_count": r["event_count"],
                    "last_seen": r["last_seen"] or "",
                    "excluded_reason": f"domain={r['domain']} 属于遥测域，行为聚合默认排除",
                }
                for r in cur.fetchall()
            ]

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
            last_ts = conn.execute("SELECT MAX(ts) FROM events").fetchone()[0]
        return {
            "total_events": total,
            "first_day": rng[0] or "",
            "last_day": rng[1] or "",
            "rooms": rooms,
            "entities": entities,
            "days_covered": days,
            "last_event_ts": last_ts or "",
        }

    # -- 聚合分析（供 LLM 上下文压缩使用）-----------------------------------

    def hour_histogram(
        self,
        start: str,
        end: str,
        rooms: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        entities: list[str] | None = None,
        exclude_entities: list[str] | None = None,
    ) -> dict[str, list[int]]:
        """房间 × 24 小时活跃直方图。

        ``exclude_domains`` 用于剔除功率/温湿度等周期性遥测，
        否则直方图会被「每分钟一条」的传感器拍平成均匀噪声。
        """
        where, wargs = self._filter_sql(
            start, end, rooms, entities, None, None, None, exclude_domains, exclude_entities
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
        exclude_entities: list[str] | None = None,
    ) -> list[dict]:
        where, wargs = self._filter_sql(
            start, end, rooms, None, None, None, None, exclude_domains, exclude_entities
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
        _JOB_COLS = {"status", "progress_json", "result_json", "error", "finished_at"}
        assert all(s.split(" = ?")[0] in _JOB_COLS for s in sets), \
            "update_job 列名越出白名单"
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

    def purge_old(self, retention_days: int, *, batch_rows: int | None = None) -> int:
        """按保留期清理事件（分批删除 + 分批提交）。

        审计 P0-7：原来是一条 ``DELETE FROM events WHERE day < cutoff`` 包在
        ``with self._lock`` 里。全局 RLock 是全进程共用的——实测 30 万行一次删完
        持锁 **143 秒**，期间采集线程、WebUI、MCP 全部排队（一个并发读者实测等了
        143151 ms）。改成每批只锁到本批提交，锁释放点摊平，其它线程在批间隙能进。

        代价：不再是一个原子事务。保留清理是幂等的周期任务（`runtime.py` 的
        `AppRuntime._run_retention_cleanup` 走 `to_thread`，启动一轮、之后每
        `data_retention_interval_seconds` 重跑一轮），中途失败下次接着删即可，
        不值得为它牺牲全局可用性的两分钟。
        """
        if retention_days <= 0:
            return 0
        cutoff = (
            now_local(self.tz_offset_hours) - timedelta(days=clamp_days(retention_days, hi=LONG_WINDOW_MAX))
        ).strftime("%Y-%m-%d")
        conn = self.connect()
        step = int(batch_rows or PURGE_BATCH_ROWS)
        removed = 0
        while True:
            # ``DELETE ... LIMIT`` 不是 SQLite 标准语法（需 SQLITE_ENABLE_UPDATE_DELETE_LIMIT），
            # 用 rowid 子查询取每批的行，任何构建都能跑。
            with self._lock:
                cur = conn.execute(
                    "DELETE FROM events WHERE rowid IN ("
                    "SELECT rowid FROM events WHERE day < ? LIMIT ?)",
                    (cutoff, step),
                )
                n = int(cur.rowcount or 0)
                conn.commit()
            removed += n
            if n < step:
                break
        with self._lock:
            conn.execute("DELETE FROM collect_days WHERE day < ?", (cutoff,))
            conn.commit()
        if removed:
            print(f"[Store] 按保留策略清理 {removed} 条事件（早于 {cutoff}）")
        # 审计 P0-8：DELETE 只把页放进空闲链，磁盘不归还文件系统（实测删 50 万行
        # 体积 198MB 纹丝不动）。NAS/树莓派盘小，删得多的那一轮顺手回收一次。
        if removed >= PURGE_VACUUM_MIN_ROWS:
            with self._lock:
                conn.execute("VACUUM")
            print(f"[Store] 清理后执行 VACUUM 回收磁盘（本轮删除 {removed} 条）")
        return removed

    # ── Agent 记忆（参与式写回向量库）────────────────────────────────────────

    def get_event(self, event_id: str):
        """按 id 取单条事件（供 source_refs 真溯源校验）。"""
        with self._db() as conn:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None

    def get_events_by_entity(self, entity_id: str, day: str = "") -> list:
        """按实体 id（可选限定日期）取事件列表，供 explain_insight 由 entity_id 重建底层证据。"""
        with self._db() as conn:
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
        source: str = "ma",
        prev_id: str = "",
        valid_from: str = "",
        observed_at: str = "",
        member_id: str = "",  # WO-MA-005: 成员归属
    ) -> str:
        now = now_local(self.tz_offset_hours)
        created_at = created_at or now.isoformat(timespec="seconds")
        expires_at = (now + timedelta(days=clamp_days(ttl_days, lo=0, hi=LONG_WINDOW_MAX))).strftime("%Y-%m-%d")
        # vMA-1.2.1: 记忆文本入库前统一脱敏（成员姓名→成员N，长数字串→***）
        text = self.sanitize_feedback_text(text)
        memory_id = memory_id or hashlib.sha1(
            f"{session_id}|{text}|{created_at}|{uuid.uuid4().hex}".encode("utf-8")
        ).hexdigest()[:16]
        # v0.9 时间有效性：valid_from 缺省=观测时刻；valid_to 空=仍有效
        observed_at = observed_at or created_at
        valid_from = valid_from or observed_at
        conn = self.connect()
        with self._lock:
            conn.execute(
                """INSERT OR REPLACE INTO agent_memories
                   (memory_id, session_id, text, topic_key, source, tags_json,
                    source_refs_json, state, trust, ttl_days,
                    auto_promote_blocked, mirror_dirty, feedback_up,
                    feedback_down, created_at, updated_at, expires_at, prev_id,
                    valid_from, valid_to, observed_at, member_id)
                   VALUES (?,?,?,?,?,?,?,?,0,?,?,0,0,0,?,?,?,?,?,'',?,?)""",
                (
                    memory_id, session_id, text, topic_key, source, tags_json,
                    source_refs_json, state, ttl_days, auto_promote_blocked,
                    created_at, created_at, expires_at, prev_id,
                    valid_from, observed_at, member_id,
                ),
            )
            conn.commit()
        return memory_id

    def close_agent_memory_validity(self, memory_id: str, valid_to: str) -> None:
        """v0.9 时间有效性：关闭一条记忆的事实有效区间（``valid_to``）。

        时间切片用：被 INVALIDATE 的旧记忆 ``valid_to`` = 新事实的 ``valid_from``，
        使作息演变（上学期 22 点睡 → 这学期 23 点半）可回溯。
        """
        conn = self.connect()
        with self._lock:
            conn.execute(
                "UPDATE agent_memories SET valid_to=?, updated_at=?, mirror_dirty=1 WHERE memory_id=?",
                (valid_to, now_local(self.tz_offset_hours).isoformat(sep="T"), memory_id),
            )
            conn.commit()

    def merge_update_agent_memory(
        self,
        memory_id: str,
        text: str,
        tags_json: str,
        source_refs_json: str,
        prev_id: str = "",
        ttl_days: int | None = None,
    ) -> None:
        """v0.8-2 mem0 式合并：刷新一条记忆的文本/标签/溯源，并记录演变链 prev_id。

        保留 ``created_at`` / ``trust`` / ``feedback_*``（不 REPLACE 整行），仅更新内容相关字段。
        """
        now = now_local(self.tz_offset_hours)
        updated_at = now.isoformat(timespec="seconds")
        # vMA-1.2.1: 合并更新同样过脱敏，避免合并路径绕过入库脱敏
        text = self.sanitize_feedback_text(text)
        conn = self.connect()
        with self._lock:
            if ttl_days is not None:
                expires_at = (now + timedelta(days=clamp_days(ttl_days, lo=0, hi=LONG_WINDOW_MAX))).strftime("%Y-%m-%d")
                conn.execute(
                    """UPDATE agent_memories SET text=?, tags_json=?, source_refs_json=?,
                       prev_id=?, expires_at=?, updated_at=?, mirror_dirty=1
                       WHERE memory_id=?""",
                    (text, tags_json, source_refs_json, prev_id, expires_at,
                     updated_at, memory_id),
                )
            else:
                conn.execute(
                    """UPDATE agent_memories SET text=?, tags_json=?, source_refs_json=?,
                       prev_id=?, updated_at=?, mirror_dirty=1
                       WHERE memory_id=?""",
                    (text, tags_json, source_refs_json, prev_id,
                     updated_at, memory_id),
                )
            conn.commit()

    def get_agent_memory(self, memory_id: str):
        with self._db() as conn:
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

    def list_agent_memories(self, state: str = "all", source: str = "", limit: int = 500,
                            member_id: str = "", *, exact_member: bool = False) -> list:
        # WO-MA-005: 成员归属过滤。member_id 非空时只返回该成员的记忆。
        # vMA-1.2.2: exact_member=True 时空字符串也精确匹配 member_id=''（公共记忆），
        # 用于对外 API 的 fail-closed；内部 sweep 不传此参数以保持"看全部"语义。
        where_parts = []
        params: list = []
        if state != "all":
            where_parts.append("state=?")
            params.append(state)
        if source:
            where_parts.append("source=?")
            params.append(source)
        if exact_member:
            where_parts.append("member_id=?")
            params.append(member_id)
        elif member_id:
            where_parts.append("member_id=?")
            params.append(member_id)
        with self._db() as conn:
            if where_parts:
                where_sql = "WHERE " + " AND ".join(where_parts)
                rows = conn.execute(
                    f"SELECT * FROM agent_memories {where_sql} ORDER BY updated_at DESC LIMIT ?",
                    (*params, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agent_memories ORDER BY updated_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(r) for r in rows]

    def search_agent_memories_fts(self, query: str, limit: int = 20,
                                  state: str = "live") -> list:
        """v0.8-4 FTS5 关键词检索（混合检索的第二路）。

        对查询做 phrase 包裹以规避 FTS5 语法字符；bm25 rank 越小越相关。
        FTS5 不可用时返回空列表（调用方回退纯向量）。
        """
        q = (query or "").strip()
        if not q:
            return []
        # 以 phrase 包裹，转义内部双引号，避免 MATCH 语法注入
        phrase = '"' + q.replace('"', '""') + '"'
        with self._db() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT a.memory_id, a.text, a.topic_key, a.state, a.trust,
                           a.source, a.tags_json, a.member_id, f.rank
                    FROM (
                        SELECT rowid AS rid, bm25(agent_memories_fts) AS rank
                        FROM agent_memories_fts
                        WHERE agent_memories_fts MATCH ?
                    ) f
                    JOIN agent_memories a ON a.rowid = f.rid
                    WHERE a.state = ?
                    ORDER BY f.rank
                    LIMIT ?
                    """,
                    (phrase, state, limit),
                ).fetchall()
            except Exception as exc:  # pragma: no cover
                print(f"[Store] FTS 检索失败: {exc}")
                return []
        return [dict(r) for r in rows]

    def list_dirty_agent_mirrors(self) -> list:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_memories WHERE mirror_dirty=1 AND memory_id IS NOT NULL AND memory_id != ''"
            ).fetchall()
        return [dict(r) for r in rows]

    def expire_overdue_agent_memories(self) -> int:
        today = now_local(self.tz_offset_hours).strftime("%Y-%m-%d")
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                """UPDATE agent_memories SET state='revoked', updated_at=?, mirror_dirty=1
                   WHERE state IN ('staging','live','pending_review')
                   AND expires_at < ?""",
                (now_local(self.tz_offset_hours).isoformat(timespec="seconds"), today),
            )
            conn.commit()
            return cur.rowcount or 0

    @staticmethod
    def _sanitize_pii(text: str, member_names: list | None = None) -> str:
        """vMA-1.2.1 反馈/记忆入库前的 PII 脱敏。

        规则（顺序即优先级）：
        1. 成员姓名 → ``成员N``（N 为该成员在名册中的序号，来自 members 表）；
        2. 手机号 / 邮箱本地名 / 身份证**整段掩掉**（替换串不带回溯引用，
           原串的每一位都不保留——见 `tests/test_vma121_intent_and_pii.py` 那把"零残留"锁）；
        3. 兜底：任意 **连续 ≥7 位数字** 整体替换为 ``***``（订单号/卡号/固话等）。

        实际行为比"前几位可读"的常见写法更严：宁可语义受损也不留可拼合的片段。
        ⚠️ 想放宽成"保留可读位"必须先过 DCD 出境面脱敏口径那道裁定
        （`20261003-...出境面姓名脱敏口径统一`）——放宽是单向不可逆的：库里已经少掉的位数补不回来。
        该裁定已落字（DCD 20261004 MA-裁3 Q1=A）：**分层**——S1 本体一字不改，出境包
        ``feedback_pack`` 里另附一份走过 S1 的 ``trace_anon.txt``，可读姓名那份留给有名册的收件方。
        姓名走替换不走删除，避免脱敏后语义断裂。
        """
        import re
        if not text:
            return text
        for idx, name in enumerate(member_names or [], start=1):
            name = str(name or "").strip()
            if len(name) >= 2:
                text = text.replace(name, f"成员{idx}")
        # 手机号：11 位整段掩掉（前 3 后 4 一律不留）；`138-1234-5678` 这类带分隔符的写法同样掩掉。
        # 两端的数字边界是必需的，不是修饰：没有 `(?<!\d)…(?!\d)`，这条会在 18 位身份证号
        # **内部**命中 11 位，把身份证拦腰咬断，剩下"前 6 位地区码 + 末位"两段短数字——短于兜底
        # 的 7 位阈值，于是永远漏在外面。第七轮之后的这次复核实测到的就是这个形状（旧实现
        # `110101199001011234` → `110101****4`），锁见 `tests/test_vma121_intent_and_pii.py`。
        text = re.sub(r"(?<!\d)1[3-9]\d[\s-]?\d{4}[\s-]?\d{4}(?!\d)", "****", text)
        # 邮箱：本地名整段掩掉，域名保留（域名不是个人标识，且要留住可归类信息）。
        # 本地名字符集**必须限定 ASCII**：`[\w.]*` 里的 `\w` 认中文，旧写法会把紧邻邮箱的
        # 中文一起当本地名吃掉（实测「邮箱someone@mail.com」→「***@mail.com」，"邮箱"两个字没了），
        # 违反本函数自己写的"不语义断裂"；反过来 1 字符合规本地名（`a@x.com`）当时因
        # `{2}` 下限而整条放过，是漏掩。改成 ASCII 字符集两头的毛病一起收。
        text = re.sub(r"[A-Za-z0-9._%+-]+@", "***@", text)
        # 身份证：18 位（末位含 X）按真值形状整段掩掉。原先写的是裸 `(\d{6})\d{8}(\d{4})`，
        # 任何连续 18 位数字都会命中——19 位银行卡被它吃掉前 18 位、尾巴留 1 位明文。
        text = re.sub(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
                      r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)", "********", text)
        # 兜底：连续 7 位及以上数字一律打码
        text = re.sub(r"\d{7,}", "***", text)
        return text

    def _pii_member_names(self) -> list:
        """脱敏用的成员名册（顺序稳定=序号稳定）。失败返回空表，不阻断写入。"""
        try:
            return [m.get("name") or "" for m in self.list_members()]
        except Exception:
            return []

    def sanitize_feedback_text(self, text: str) -> str:
        """对外暴露的入库前脱敏入口（反馈/记忆文本统一走这里）。"""
        if not isinstance(text, str) or not text:
            return text or ""
        return self._sanitize_pii(text, self._pii_member_names())

    def record_agent_feedback(self, memory_id: str, useful: bool, trust_step: float = 0.2,
                               comment: str = "", question: str = "") -> dict | None:
        # vMA-1.2.1 / DCD R1：反馈评论与"当初的问题文本"入库前脱敏（成员姓名 → 成员N）
        safe_comment = self.sanitize_feedback_text((comment or "")[:_FEEDBACK_TEXT_MAX])
        safe_question = self.sanitize_feedback_text((question or "")[:_FEEDBACK_TEXT_MAX])
        # 第六轮审计 CRITICAL-2：读-改-写原先跨在锁区两侧，并发反馈会互相覆盖（lost update）。
        # 整段放进事务区，counters 与 trust 的增量才对得上同一次读到的行。
        with self.transaction() as conn:
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
                expires_at = (now + timedelta(days=clamp_days(ttl, lo=0, hi=LONG_WINDOW_MAX))).strftime("%Y-%m-%d")
            else:
                expires_at = (now + timedelta(days=clamp_days(ttl // 2, hi=LONG_WINDOW_MAX))).strftime("%Y-%m-%d")
            conn.execute(
                """UPDATE agent_memories SET feedback_up=?, feedback_down=?,
                   trust=?, expires_at=?, updated_at=?, mirror_dirty=1,
                   feedback_question=?, feedback_comment=? WHERE memory_id=?""",
                (up, down, trust, expires_at, now.isoformat(timespec="seconds"),
                 safe_question, safe_comment, memory_id),
            )
        return {"feedback_up": up, "feedback_down": down, "trust": trust,
                "expires_at": expires_at, "comment": safe_comment,
                "question": safe_question}

    def list_negative_feedback(self, limit: int = 50, with_question_only: bool = True) -> list:
        """vMA-2.0 知识图谱门的取料口：被点 👎 的记忆 + 当初的问题文本。

        DCD 2026-10-01 R1 裁定 A 的前提是"门可测"：默认只返回带问题文本的行，
        因为没写下当初问了什么的 👎 事后无法还原成 badcase，放进分母只会稀释判据。
        """
        where = "WHERE feedback_down > 0"
        if with_question_only:
            where += " AND feedback_question <> ''"
        with self._db() as conn:
            rows = conn.execute(
                f"""SELECT memory_id, text, topic_key, state, trust,
                            feedback_up, feedback_down, feedback_question,
                            feedback_comment, updated_at
                     FROM agent_memories {where}
                     ORDER BY updated_at DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()
        return [dict(zip(
            ["memory_id", "text", "topic_key", "state", "trust", "feedback_up",
             "feedback_down", "feedback_question", "feedback_comment", "updated_at"], r,
            strict=True
        )) for r in rows]

    def member_insight_feedback(self, member_id: str, member_name: str = "",
                                limit: int = 50) -> dict:
        """v0.8-3 成员维度洞察反馈聚合。

        匹配 tags_json 含 ``member:<id>`` 或 ``member:<name>`` 的 Agent 记忆
        （researcher 产出的记忆 tags 形如 ``member:lidicn``），汇总 👍/👎 与明细，
        供成员详情展示并可继续反馈（把洞察反馈反哺到成员档案视图）。
        """
        keys = [f"member:{self._escape_like(member_id)}"]
        if member_name and member_name != member_id:
            keys.append(f"member:{self._escape_like(member_name)}")
        clause = " OR ".join(["tags_json LIKE ? ESCAPE '\\'"] * len(keys))
        with self._db() as conn:
            rows = conn.execute(
                f"SELECT * FROM agent_memories WHERE ({clause}) ORDER BY updated_at DESC LIMIT ?",
                (*[f"%{k}%" for k in keys], limit),
            ).fetchall()
        out = []
        up = down = 0
        for r in rows:
            # 逐列收敛，不再让一条脏行把整个成员的洞察页打成 HTTP 500（第二期审计 MA-02）。
            # tags 用 [] 兜底（这里 tags 是展示元数据，不是约束条件，方向与 patterns 的
            # 坏 condition 不同），数字列用 row_number：真实表把 feedback_up 声明成 INTEGER
            # 仍收得下 'abc'，SQLite 弱类型不会因为声明而报错。
            fid = str(r["memory_id"])
            f_up = row_number(r["feedback_up"], 0, label="feedback_up", row=fid)
            f_down = row_number(r["feedback_down"], 0, label="feedback_down", row=fid)
            up += f_up
            down += f_down
            out.append({
                "memory_id": r["memory_id"],
                "text": r["text"],
                "state": r["state"],
                "trust": row_number(r["trust"], 0.0, label="trust", row=fid),
                "topic_key": r["topic_key"],
                "tags": safe_json_loads(r["tags_json"] or "[]", []),
                "feedback_up": f_up,
                "feedback_down": f_down,
                "updated_at": r["updated_at"],
            })
        return {"member_id": member_id, "count": len(out),
                "up": up, "down": down, "memories": out}

    def researcher_direction_feedback(self) -> list:
        """v0.8-3 按方向聚合研究员洞察的 👍/👎（方向/模板权重反哺参考）。"""
        with self._db() as conn:
            rows = conn.execute(
                "SELECT tags_json, feedback_up, feedback_down, trust FROM agent_memories "
                "WHERE tags_json LIKE '%auto-researcher%'"
            ).fetchall()
        agg: dict = {}
        for r in rows:
            tags = safe_json_loads(r["tags_json"] or "[]", [])
            direction = next(
                (t.split(":", 1)[1] for t in tags if t.startswith("direction:")), "unknown"
            )
            a = agg.setdefault(direction, {"direction": direction, "up": 0, "down": 0,
                                           "count": 0, "_trust": 0.0})
            # tags 有 try 不代表整批安全：同一段里的三个数字列原先是裸 int()/float()，
            # 一条脏 feedback_up 照样把整个方向聚合打断（第二期审计 MA-02 的同一形状）。
            a["up"] += row_number(r["feedback_up"], 0, label="feedback_up")
            a["down"] += row_number(r["feedback_down"], 0, label="feedback_down")
            a["count"] += 1
            a["_trust"] += row_number(r["trust"], 0.0, label="trust")
        out = []
        for a in agg.values():
            a["avg_trust"] = round(a.pop("_trust") / max(1, a["count"]), 3)
            out.append(a)
        out.sort(key=lambda x: -((x["up"] - x["down"]) or x["count"]))
        return out

    def get_session_agent_trust(self, session_id: str, strict_threshold: float = -0.3) -> dict:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT trust, state FROM agent_memories WHERE session_id=?", (session_id,)
            ).fetchall()
        if not rows:
            return {"session_id": session_id, "count": 0, "avg_trust": 0.0,
                    "live": 0, "revoked": 0, "strict": False}
        # 一条脏 trust 原先让整句 float() 抛 ValueError，调用点（agent_memory.add_semantic_memory
        # 的声誉回路）裸调 ⇒ 该 session 在"入口校验之后、写库之前"炸掉，新记忆一条也写不进去。
        # 坏值按 0.0（中性）计入，读数继续可用且 WARNING 留痕（第二期审计 MA-03）。
        trusts = [row_number(r["trust"], 0.0, label="trust", row=session_id) for r in rows]
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
                    entities_json, events_json, source_rule_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        r.get("activity_id"), r.get("day"), r.get("activity"),
                        r.get("confidence"), json.dumps(r.get("evidence"), ensure_ascii=False),
                        r.get("room"), r.get("session_id", ""), r.get("created_at", ""),
                        r.get("entities_json", "[]"), r.get("events_json", "[]"),
                        r.get("source_rule_id", ""),
                    )
                    for r in rows
                ],
            )
            conn.commit()

    # ── 规则生命周期（DCD R3 四红线：证据门槛 / 观察期 / 可回滚 / 审计）──────

    def log_rule_lifecycle(self, rule_id: str, action: str, *,
                           source_rule_id: str = "", actor: str = "",
                           from_state: str = "", to_state: str = "",
                           reason: str = "", detail: dict | None = None) -> dict:
        """写入一条"机器建议 → 人工确认 → 生效 → 转正/撤销"审计记录。"""
        conn = self.connect()
        now = now_local(self.tz_offset_hours).isoformat(sep="T")
        with self._lock:
            cur = conn.execute(
                """INSERT INTO rule_lifecycle_audit
                   (rule_id, source_rule_id, action, actor, from_state, to_state,
                    reason, detail_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (rule_id, source_rule_id, action, actor, from_state, to_state,
                 self.sanitize_feedback_text(reason),
                 json.dumps(detail or {}, ensure_ascii=False), now),
            )
            conn.commit()
            return {"audit_id": cur.lastrowid, "created_at": now}

    def set_rule_mode(self, rule_id: str, mode: str, *,
                      enabled: bool | None = None, revoked: bool = False) -> bool:
        """切换规则生效模式（dry_run | live | revoked）。

        只由生效通道调用：HTTP/MCP 的 update_rule 白名单里没有 ``mode``，
        否则一次 PUT 就能跳过观察期（红线"观察期"会被绕过）。
        """
        conn = self.connect()
        sets = ["mode = ?", "updated_at = ?"]
        args: list = [mode, now_local(self.tz_offset_hours).isoformat(sep="T")]
        if enabled is not None:
            sets.append("enabled = ?")
            args.append(1 if enabled else 0)
        if revoked:
            sets.append("revoked_at = ?")
            args.append(now_local(self.tz_offset_hours).isoformat(sep="T"))
        args.append(rule_id)
        with self._lock:
            cur = conn.execute(
                f"UPDATE active_rules SET {', '.join(sets)} WHERE rule_id = ?", args)
            conn.commit()
            return bool(cur.rowcount)

    def list_rule_lifecycle(self, rule_id: str = "", limit: int = 100) -> list[dict]:
        """列出规则生命周期审计（默认全局最近 N 条）。"""
        sql = "SELECT * FROM rule_lifecycle_audit"
        args: list = []
        if rule_id:
            sql += " WHERE rule_id = ?"
            args.append(rule_id)
        sql += " ORDER BY audit_id DESC LIMIT ?"
        args.append(clamp_limit(limit, 100, 500, label="list_rule_lifecycle"))
        with self._db() as conn:
            rows = conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["detail"] = json.loads(d.pop("detail_json") or "{}")
            except Exception:
                d["detail"] = {}
                d.pop("detail_json", None)
            out.append(d)
        return out

    def rollback_detected_activities(self, source_rule_id: str) -> int:
        """删除某条规则产生的全部推断活动（红线"可回滚"），返回删除行数。"""
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                "DELETE FROM detected_activities WHERE source_rule_id = ?",
                (source_rule_id,),
            )
            conn.commit()
            return cur.rowcount

    def list_rule_triggers(self, rule_id: str, *, since: str = "",
                           mode: str | None = None) -> list[dict]:
        """列出规则触发历史，可按时间下界与试运行标记过滤。"""
        sql = "SELECT * FROM rule_trigger_history WHERE rule_id = ?"
        args: list = [rule_id]
        if since:
            sql += " AND triggered_at >= ?"
            args.append(since)
        if mode == "dry_run":
            sql += " AND dry_run = 1"
        elif mode == "live":
            sql += " AND dry_run = 0"
        sql += " ORDER BY trigger_id DESC LIMIT 500"
        with self._db() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def mark_rule_trigger_false_positive(self, trigger_id: int) -> bool:
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                "UPDATE rule_trigger_history SET false_positive = 1 WHERE trigger_id = ?",
                (int(trigger_id),),
            )
            conn.commit()
            return bool(cur.rowcount)

    def count_rule_false_positives(self, rule_id: str, *, since: str = "") -> int:
        sql = "SELECT COUNT(*) FROM rule_trigger_history WHERE rule_id = ? AND false_positive = 1"
        args: list = [rule_id]
        if since:
            sql += " AND triggered_at >= ?"
            args.append(since)
        with self._db() as conn:
            return int(conn.execute(sql, args).fetchone()[0])

    def purge_rule_triggers(self, cutoff: str, max_rows: int = 100_000) -> dict:
        """裁剪触发历史：先删 ``triggered_at < cutoff`` 的，再删超额的最旧行。

        DCD 2026-10-01 §Q3.1 裁定「保留期 7 天 + 行数上限 10 万」——本方法的
        例外是**被人工标为误报（false_positive=1）的行不受保留期与行数上限约束**：
        整个反馈面 currently 是 0 条标注（裁定 §Q3 的原话），有标注的行是唯一的
        负样本，裁掉就等于把攒 badcase 门的机会又清零一次。裁观测记录不是裁用户
        数据，但裁掉"人的判断"比裁掉机器日志贵。
        """
        conn = self.connect()
        expired = 0
        over_cap = 0
        with self._lock:
            cur = conn.execute(
                "DELETE FROM rule_trigger_history "
                "WHERE triggered_at < ? AND false_positive = 0",
                (cutoff,),
            )
            expired = int(cur.rowcount or 0)
            total = int(conn.execute(
                "SELECT COUNT(*) FROM rule_trigger_history WHERE false_positive = 0"
            ).fetchone()[0] or 0)
            if total > max_rows:
                cur = conn.execute(
                    """
                    DELETE FROM rule_trigger_history WHERE trigger_id IN (
                        SELECT trigger_id FROM rule_trigger_history
                        WHERE false_positive = 0
                        ORDER BY triggered_at ASC, trigger_id ASC
                        LIMIT ?
                    )
                    """,
                    (total - max_rows,),
                )
                over_cap = int(cur.rowcount or 0)
            conn.commit()
            remaining = int(conn.execute(
                "SELECT COUNT(*) FROM rule_trigger_history").fetchone()[0] or 0)
            labeled = int(conn.execute(
                "SELECT COUNT(*) FROM rule_trigger_history WHERE false_positive = 1"
            ).fetchone()[0] or 0)
        return {
            "cutoff": cutoff,
            "max_rows": int(max_rows),
            "deleted_expired": expired,
            "deleted_over_cap": over_cap,
            "remaining": remaining,
            "kept_labeled": labeled,
        }

    def get_detected_activity(self, activity_id: str):
        with self._db() as conn:
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
        sql = (
            "SELECT rule_id, name, room, tags_json, start_hour, end_hour, "
            "min_events, confidence, note, enabled FROM activity_rules"
        )
        if enabled_only:
            sql += " WHERE enabled=1"
        with self._db() as conn:
            rows = conn.execute(sql).fetchall()
        out = []
        for r in rows:
            tags = safe_json_loads(r["tags_json"] or "[]", None)
            if tags is None:
                # 坏 tags_json 不能退化成 []：[] 的语义是"不限标签"，一条读不出来的规则
                # 就此匹配该房间全部事件（同 patterns._json_object 的判断）。跳过这一条，
                # 其余规则照常生效——改前 json.loads 在循环里裸调，一条脏行让整批一起抛。
                continue
            out.append(
                {
                    "rule_id": r["rule_id"],
                    "name": r["name"],
                    "room": r["room"],
                    "tags": tags,
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
        with self.transaction() as conn:
            cur = conn.execute("DELETE FROM activity_rules WHERE name=?", (name,))
            return cur.rowcount > 0

    # ── 信号硬排除层（学习策略：teach_signal kind='hard' 落表）──
    def upsert_signal_exclusion(
        self,
        entity_id: str,
        scope: str = "all",
        reason: str = "",
        created_by: str = "user",
        exclusion_type: str = "exclude",
    ) -> str:
        """写入/更新一条信号硬排除规则（幂等：同 entity_id+scope 复用同一条，便于撤销与审计）。"""
        entity_id = (entity_id or "").strip()
        if not entity_id:
            raise ValueError("signal_exclusions.entity_id 不能为空")
        scope = (scope or "all").strip().lower()
        exclusion_id = hashlib.sha1(
            f"{entity_id}|{scope}".encode("utf-8")
        ).hexdigest()[:16]
        now = _iso(datetime.now(timezone.utc))
        conn = self.connect()
        with self._lock:
            conn.execute(
                """INSERT OR REPLACE INTO signal_exclusions
                   (exclusion_id, entity_id, scope, exclusion_type, reason, created_by,
                    created_at, revoked, revoked_at)
                   VALUES (?,?,?,?,?,?,?, COALESCE((SELECT revoked FROM signal_exclusions WHERE exclusion_id=?),0),
                           COALESCE((SELECT revoked_at FROM signal_exclusions WHERE exclusion_id=?),?))""",
                (
                    exclusion_id, entity_id, scope, (exclusion_type or "exclude"),
                    (reason or ""), (created_by or "user"), now,
                    exclusion_id, exclusion_id, None,
                ),
            )
            conn.commit()
        return exclusion_id

    def list_signal_exclusions(self, include_revoked: bool = False) -> list:
        """返回（默认仅生效的）信号硬排除规则。"""
        sql = (
            "SELECT exclusion_id, entity_id, scope, exclusion_type, reason, "
            "created_by, created_at, revoked, revoked_at FROM signal_exclusions"
        )
        if not include_revoked:
            sql += " WHERE revoked=0"
        with self._db() as conn:
            rows = conn.execute(sql).fetchall()
        return [
            {
                "exclusion_id": r["exclusion_id"],
                "entity_id": r["entity_id"],
                "scope": r["scope"],
                "exclusion_type": r["exclusion_type"],
                "reason": r["reason"],
                "created_by": r["created_by"],
                "created_at": r["created_at"],
                "revoked": bool(r["revoked"]),
                "revoked_at": r["revoked_at"],
            }
            for r in rows
        ]

    def revoke_signal_exclusion(self, exclusion_id: str) -> bool:
        """硬排除墓碑（置 revoked=1），保留审计轨迹。"""
        conn = self.connect()
        now = _iso(datetime.now(timezone.utc))
        with self._lock:
            cur = conn.execute(
                "UPDATE signal_exclusions SET revoked=1, revoked_at=? WHERE exclusion_id=?",
                (now, exclusion_id),
            )
            conn.commit()
        return cur.rowcount > 0

    def get_signal_exclusion(self, exclusion_id: str) -> dict | None:
        with self._db() as conn:
            r = conn.execute(
                "SELECT exclusion_id, entity_id, scope, exclusion_type, reason, "
                "created_by, created_at, revoked, revoked_at FROM signal_exclusions WHERE exclusion_id=?",
                (exclusion_id,),
            ).fetchone()
        if not r:
            return None
        return {
            "exclusion_id": r["exclusion_id"],
            "entity_id": r["entity_id"],
            "scope": r["scope"],
            "exclusion_type": r["exclusion_type"],
            "reason": r["reason"],
            "created_by": r["created_by"],
            "created_at": r["created_at"],
            "revoked": bool(r["revoked"]),
            "revoked_at": r["revoked_at"],
        }


# ── 模块级工具 ──────────────────────────────────────────────────────────

def safe_json_loads(raw: Any, default: Any = None) -> Any:
    """NEW-P1-1：安全解析 JSON，失败记 WARNING 并返回默认值，让坏数据可见但不断链。"""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as _e:
        logging.getLogger(__name__).warning("safe_json_loads 解析失败: %s (前80字符: %r)", _e, str(raw)[:80])
        return default


def clamp_limit(value, default: int, hi: int, *, label: str = "") -> int:
    """`LIMIT` 参数收敛进 `[1, hi]`（第二期审计 MA-07~MA-10）。

    放在 **store 层**而不是路由层，是为了治第三轮点出的根因：同一个查询方法有 API 与
    MCP 两个入口，"一个入口修了另一个没修"（MA-09）就是这么来的。只要最后一道出口钳住，
    哪个入口都拉不走全表；路由层的 `_num` 继续负责给调用方 400，这里只兜底不打断。

    算术交给 `day_bounds._clamped`，不自己写 `int(value)`：`int(float('inf'))` 抛的是
    `OverflowError`，而 `except (TypeError, ValueError)` 接不住它（第八轮 lesson 86 量过的格子）。
    ⇒ `±inf → 同号边界`、`NaN/无法解析 → default`、超大整数走 int 快路径直接落 `hi`。

    坏值不静默：无法解析与下界收敛各留一条 WARNING。`hi` 取各方法对应的既有入口口径
    （`list_rule_lifecycle` 对齐 API 侧 `_num` 的 `hi=500`），不是随手挑的整数。
    """
    if value is None:
        return default
    n = _clamped(value, default, 1, hi, integer=True)
    log = logging.getLogger(__name__)
    if isinstance(value, int):                     # 含 bool：大整数不经 float()
        requested = float(value) if abs(value) < 10 ** 308 else float("inf")
    else:
        try:
            requested = float(value)
        except (TypeError, ValueError, OverflowError):
            log.warning("clamp_limit 收到非数字 limit=%r，按默认档 %s 处理（%s）",
                        value, default, label)
            return n
    if requested < 1:
        log.warning(
            "clamp_limit 下界收敛 limit=%s → %s（负数与 0 在 SQLite 里都是「不限行数」，%s）",
            value, n, label)
    return n


def row_number(raw: Any, default: float, *, label: str = "", row: str = ""):
    """列值 → 数字：一条脏列只丢那一列，不再让 `int()`/`float()` 打断整批读取。

    表声明了 INTEGER/REAL 不构成保护——SQLite 弱类型照样收得下 'abc'（第二期审计
    MA-02/MA-03 的实测就是拿真实建表语句做的）。失败记 WARNING，坏数据可见。
    返回 `int` 还是 `float` 由 `default` 的类型决定，调用方的投影形状不会因此漂移。
    """
    try:
        val = float(raw)
    except (TypeError, ValueError) as exc:
        logging.getLogger(__name__).warning(
            "row_number 解析失败 %s%s: %s (值前 40 字符: %r)",
            label, f" (行 {row})" if row else "", exc, str(raw)[:40])
        return default
    return int(val) if isinstance(default, int) else val
