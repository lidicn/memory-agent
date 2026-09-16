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

# 审计 S8：语音问答缓存软上限，超过则按创建时间淘汰最旧 10% 防止无限增长
_VOICE_CACHE_MAX = int(os.getenv("MA_VOICE_CACHE_MAX", "2000"))

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
            # v0.5 记忆统一入库：agent_memories 增加来源字段 source（缺省 'ma' 视为本服务原生）
            try:
                conn.execute(
                    "ALTER TABLE agent_memories ADD COLUMN source TEXT NOT NULL DEFAULT 'ma'"
                )
            except Exception:
                pass  # 列已存在
            # v0.8-2 记忆演变链：prev_id 指向被合并/失效的上一个版本
            try:
                conn.execute(
                    "ALTER TABLE agent_memories ADD COLUMN prev_id TEXT NOT NULL DEFAULT ''"
                )
            except Exception:
                pass  # 列已存在
            # v0.9 时间有效性（Graphiti 时间模型）：事实有效区间 + 观测时间
            for _col in ("valid_from", "valid_to", "observed_at"):
                try:
                    conn.execute(
                        f"ALTER TABLE agent_memories ADD COLUMN {_col} TEXT NOT NULL DEFAULT ''"
                    )
                except Exception:
                    pass  # 列已存在
            # v0.8-4 混合检索：FTS5 关键词索引（external content + trigger 自动同步）
            # 优先 trigram（中文子串/专名友好），不支持则回退 unicode61；均不可用则纯向量
            for _tok in ("trigram", "unicode61"):
                try:
                    for _t in ("agent_memories_fts_ai", "agent_memories_fts_ad",
                               "agent_memories_fts_au"):
                        conn.execute(f"DROP TRIGGER IF EXISTS {_t}")
                    conn.execute("DROP TABLE IF EXISTS agent_memories_fts")
                    conn.executescript(
                        f"""
                        CREATE VIRTUAL TABLE agent_memories_fts USING fts5(
                            text, topic_key, tags_json,
                            content='agent_memories', content_rowid='rowid',
                            tokenize='{_tok}'
                        );
                        CREATE TRIGGER agent_memories_fts_ai
                        AFTER INSERT ON agent_memories BEGIN
                            INSERT INTO agent_memories_fts(rowid, text, topic_key, tags_json)
                            VALUES (new.rowid, new.text, new.topic_key, new.tags_json);
                        END;
                        CREATE TRIGGER agent_memories_fts_ad
                        AFTER DELETE ON agent_memories BEGIN
                            INSERT INTO agent_memories_fts(agent_memories_fts, rowid, text, topic_key, tags_json)
                            VALUES ('delete', old.rowid, old.text, old.topic_key, old.tags_json);
                        END;
                        CREATE TRIGGER agent_memories_fts_au
                        AFTER UPDATE ON agent_memories BEGIN
                            INSERT INTO agent_memories_fts(agent_memories_fts, rowid, text, topic_key, tags_json)
                            VALUES ('delete', old.rowid, old.text, old.topic_key, old.tags_json);
                            INSERT INTO agent_memories_fts(rowid, text, topic_key, tags_json)
                            VALUES (new.rowid, new.text, new.topic_key, new.tags_json);
                        END;
                        """
                    )
                    # 启动即重建索引，保证 tokenizer 变更/历史数据一致（记忆量小，成本可忽略）
                    conn.execute("INSERT INTO agent_memories_fts(agent_memories_fts) VALUES('rebuild')")
                    print(f"[Store] FTS5 关键词索引就绪（tokenize={_tok}）")
                    break
                except Exception as exc:
                    print(f"[Store] FTS5({_tok}) 初始化失败: {exc}")
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
            # members 表 appearance_json 列迁移
            try:
                conn.execute("ALTER TABLE members ADD COLUMN appearance_json TEXT DEFAULT ''")
            except Exception:
                pass  # 列已存在
            # 人脸库中央集权：成员 ArcSoft 特征（可移植性见交接单风险项）
            try:
                conn.execute("ALTER TABLE members ADD COLUMN face_feature TEXT DEFAULT ''")
            except Exception:
                pass  # 列已存在
            # 成员档案（豆包管家对接：作息/兴趣/课程，本服务不解释内容）
            try:
                conn.execute("ALTER TABLE members ADD COLUMN profile_json TEXT DEFAULT ''")
            except Exception:
                pass  # 列已存在
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
            # v0.9 MCP 契约：幂等键表（写工具防重复执行）
            conn.execute(
                """CREATE TABLE IF NOT EXISTS idempotency_keys (
                    idem_key    TEXT PRIMARY KEY,
                    tool        TEXT NOT NULL,
                    result_text TEXT NOT NULL,
                    is_error    INTEGER NOT NULL DEFAULT 0,
                    created_at  TEXT NOT NULL,
                    expires_at  TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_idempotency_expires ON idempotency_keys(expires_at)"
            )
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
        args.append(int(limit))
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
        """清理过期审计（默认保留 30 天，路线图风险的「保留周期上限」要求）。"""
        cutoff = now_local(self.tz_offset_hours) - timedelta(days=max(1, int(keep_days)))
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

    def list_device_health(self, state: str = "") -> list[dict]:
        """健康/墓碑清单；state 为空返回全部，否则按状态过滤。"""
        with self._lock:
            if state:
                rows = self._conn.execute(
                    "SELECT * FROM device_health WHERE state=? ORDER BY updated_at DESC",
                    (state,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM device_health ORDER BY updated_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.commit()
                    self._conn.close()
                except Exception:
                    pass
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
                    "SELECT cache_key FROM voice_answer_cache ORDER BY created_at ASC LIMIT ?)",
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
                     snapshot_path, raw_response, status)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ts,
                    payload.get("device_ts"),
                    payload.get("day") or str(ts)[:10],
                    payload.get("room") or "",
                    payload.get("camera_src") or "",
                    json.dumps(payload.get("persons") or [], ensure_ascii=False),
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
                ),
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def list_behavior_events(
        self, room: str | None = None, member: str | None = None,
        day_from: str | None = None, day_to: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """查询行为事件。member 过滤用 JSON 解析完成（量小，见 spec §7.1）。"""
        sql = "SELECT * FROM behavior_events"
        conds: list[str] = []
        args: list = []
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
        sql += " ORDER BY server_ts DESC LIMIT ?"
        args.append(int(limit))
        conn = self.connect()
        with self._lock:
            rows = conn.execute(sql, args).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            try:
                d["persons"] = json.loads(d.pop("persons_json") or "[]")
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
        if member:
            out = [
                d for d in out
                if any(p.get("name") == member for p in d.get("persons") or [])
            ]
        return out

    # ── 行为状态 / 候选规则（v0.9.5 主动感知·行为推断层）────────────────────

    def add_behavior_state(self, member: str, room: str, activity: str,
                           confidence: float, ts: str | None = None,
                           source: str = "inference", evidence: list | None = None) -> str:
        """写入一条 canonical 行为状态（权威），返回 state_id。"""
        sid = uuid.uuid4().hex[:16]
        ts = ts or now_local(self.tz_offset_hours).isoformat(sep="T")
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

    def list_candidate_rules(self, status: str | None = None, limit: int = 200) -> list[dict]:
        """列出候选序列规则（默认全部 status），解析 steps/evidence。"""
        sql = "SELECT * FROM candidate_rules"
        args: list = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
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

    def set_candidate_rule_status(self, rule_id: str, status: str) -> bool:
        """更新候选规则状态（staging | accepted | rejected）。"""
        conn = self.connect()
        with self._lock:
            cur = conn.execute(
                "UPDATE candidate_rules SET status=?, updated_at=? WHERE rule_id=?",
                (status, now_local(self.tz_offset_hours).isoformat(sep="T"), rule_id),
            )
            conn.commit()
            return bool(cur.rowcount)

    # ── 幂等键（v0.9 MCP 契约：写工具防重复执行）──────────────────────────

    def get_idempotency(self, idem_key: str) -> dict | None:
        """取幂等键缓存结果；不存在或已过期返回 None。"""
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM idempotency_keys WHERE idem_key = ?", (idem_key,)
        ).fetchone()
        if not row:
            return None
        today = now_local(self.tz_offset_hours).strftime("%Y-%m-%d")
        if (row["expires_at"] or "") < today:
            return None
        return dict(row)

    def save_idempotency(self, idem_key: str, tool: str, result_text: str,
                         is_error: bool = False, ttl_hours: int = 24) -> None:
        """记录幂等键 → 结果映射（重复调用可直接返回缓存）。"""
        now = now_local(self.tz_offset_hours)
        expires = (now + timedelta(hours=max(1, int(ttl_hours)))).strftime("%Y-%m-%d")
        conn = self.connect()
        with self._lock:
            conn.execute(
                """INSERT OR REPLACE INTO idempotency_keys
                   (idem_key, tool, result_text, is_error, created_at, expires_at)
                   VALUES (?,?,?,?,?,?)""",
                (idem_key, tool, result_text, 1 if is_error else 0,
                 now.isoformat(timespec="seconds"), expires),
            )
            conn.commit()

    def purge_idempotency(self) -> int:
        """清理过期幂等键。"""
        today = now_local(self.tz_offset_hours).strftime("%Y-%m-%d")
        conn = self.connect()
        with self._lock:
            cur = conn.execute("DELETE FROM idempotency_keys WHERE expires_at < ?", (today,))
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
                persons = json.loads(r["persons_json"] or "[]")
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
                persons = json.loads(r["persons_json"] or "[]")
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
                day_from = (d - timedelta(days=max(1, int(days)) - 1)).strftime("%Y-%m-%d")
            except Exception:
                day_from = (today - timedelta(days=max(1, int(days)) - 1)).strftime("%Y-%m-%d")
        else:
            day_from = (today - timedelta(days=max(1, int(days)) - 1)).strftime("%Y-%m-%d")
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
                persons = json.loads(r["persons_json"] or "[]")
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
            rows = self.connect().execute(
                "SELECT topic_key, valid_from, valid_to, text FROM agent_memories "
                "WHERE tags_json LIKE ? AND topic_key LIKE 'habit:%' "
                "AND state <> 'revoked' AND valid_from <> '' "
                "ORDER BY valid_from ASC",
                (f"%member:{name}%",),
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
            d["persons"] = json.loads(d.pop("persons_json") or "[]")
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
                (json.dumps(persons, ensure_ascii=False), event_id),
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
        allowed = {"name", "avatar_emoji", "avatar_bg", "avatar_url", "note",
                   "appearance_json", "face_feature", "profile_json"}
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
        source: str = "ma",
        prev_id: str = "",
        valid_from: str = "",
        observed_at: str = "",
    ) -> str:
        now = now_local(self.tz_offset_hours)
        created_at = created_at or now.isoformat(timespec="seconds")
        expires_at = (now + timedelta(days=ttl_days)).strftime("%Y-%m-%d")
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
                    valid_from, valid_to, observed_at)
                   VALUES (?,?,?,?,?,?,?,?,0,?,?,0,0,0,?,?,?,?,?,'',?)""",
                (
                    memory_id, session_id, text, topic_key, source, tags_json,
                    source_refs_json, state, ttl_days, auto_promote_blocked,
                    created_at, created_at, expires_at, prev_id,
                    valid_from, observed_at,
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
                "UPDATE agent_memories SET valid_to=?, updated_at=? WHERE memory_id=?",
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
        conn = self.connect()
        with self._lock:
            if ttl_days is not None:
                expires_at = (now + timedelta(days=ttl_days)).strftime("%Y-%m-%d")
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

    def list_agent_memories(self, state: str = "all", source: str = "", limit: int = 500) -> list:
        conn = self.connect()
        if state == "all" and not source:
            rows = conn.execute(
                "SELECT * FROM agent_memories ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        elif state != "all" and not source:
            rows = conn.execute(
                "SELECT * FROM agent_memories WHERE state=? ORDER BY updated_at DESC LIMIT ?",
                (state, limit),
            ).fetchall()
        elif state == "all" and source:
            rows = conn.execute(
                "SELECT * FROM agent_memories WHERE source=? ORDER BY updated_at DESC LIMIT ?",
                (source, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_memories WHERE state=? AND source=? "
                "ORDER BY updated_at DESC LIMIT ?",
                (state, source, limit),
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
        conn = self.connect()
        try:
            rows = conn.execute(
                """
                SELECT a.memory_id, a.text, a.topic_key, a.state, a.trust,
                       a.source, a.tags_json, f.rank
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

    def member_insight_feedback(self, member_id: str, member_name: str = "",
                                limit: int = 50) -> dict:
        """v0.8-3 成员维度洞察反馈聚合。

        匹配 tags_json 含 ``member:<id>`` 或 ``member:<name>`` 的 Agent 记忆
        （researcher 产出的记忆 tags 形如 ``member:lidicn``），汇总 👍/👎 与明细，
        供成员详情展示并可继续反馈（把洞察反馈反哺到成员档案视图）。
        """
        import json as _json
        conn = self.connect()
        keys = [f"member:{member_id}"]
        if member_name and member_name != member_id:
            keys.append(f"member:{member_name}")
        clause = " OR ".join(["tags_json LIKE ?"] * len(keys))
        rows = conn.execute(
            f"SELECT * FROM agent_memories WHERE ({clause}) ORDER BY updated_at DESC LIMIT ?",
            (*[f"%{k}%" for k in keys], limit),
        ).fetchall()
        out = []
        up = down = 0
        for r in rows:
            up += int(r["feedback_up"])
            down += int(r["feedback_down"])
            out.append({
                "memory_id": r["memory_id"],
                "text": r["text"],
                "state": r["state"],
                "trust": float(r["trust"]),
                "topic_key": r["topic_key"],
                "tags": _json.loads(r["tags_json"] or "[]"),
                "feedback_up": int(r["feedback_up"]),
                "feedback_down": int(r["feedback_down"]),
                "updated_at": r["updated_at"],
            })
        return {"member_id": member_id, "count": len(out),
                "up": up, "down": down, "memories": out}

    def researcher_direction_feedback(self) -> list:
        """v0.8-3 按方向聚合研究员洞察的 👍/👎（方向/模板权重反哺参考）。"""
        import json as _json
        conn = self.connect()
        rows = conn.execute(
            "SELECT tags_json, feedback_up, feedback_down, trust FROM agent_memories "
            "WHERE tags_json LIKE '%auto-researcher%'"
        ).fetchall()
        agg: dict = {}
        for r in rows:
            try:
                tags = _json.loads(r["tags_json"] or "[]")
            except Exception:
                tags = []
            direction = next(
                (t.split(":", 1)[1] for t in tags if t.startswith("direction:")), "unknown"
            )
            a = agg.setdefault(direction, {"direction": direction, "up": 0, "down": 0,
                                           "count": 0, "_trust": 0.0})
            a["up"] += int(r["feedback_up"])
            a["down"] += int(r["feedback_down"])
            a["count"] += 1
            a["_trust"] += float(r["trust"])
        out = []
        for a in agg.values():
            a["avg_trust"] = round(a.pop("_trust") / max(1, a["count"]), 3)
            out.append(a)
        out.sort(key=lambda x: -((x["up"] - x["down"]) or x["count"]))
        return out

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
        conn = self.connect()
        sql = (
            "SELECT exclusion_id, entity_id, scope, exclusion_type, reason, "
            "created_by, created_at, revoked, revoked_at FROM signal_exclusions"
        )
        if not include_revoked:
            sql += " WHERE revoked=0"
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
        conn = self.connect()
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
