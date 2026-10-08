<!-- hex=e53268e900eadef77eac81f537370ea2 title=e53268e900eadef77eac81f537370ea2 turns=1 fetched=2026-09-28T23:58:36+0800 src=dialog/list -->

## 轮 1  ·  2026-09-28 23:53:40  ·  msgId=1427e178e09bca73ea9444cd09ce311d
### 投喂（1521 字符）

# MA-多模态统一数据模型 · 设计需求

> 由 Lever-Hub 进料口从 `MA-多模态统一数据模型.md` 生成（2026-09-28 23:53:09）。提单 agent：MA-worker。目标仓（只读）：`E:/NAS/memory-agent`。
> 请基于以下完整前置环境，输出设计 + 代码 + 单测 + 使用示例。
> 要求：严格遵循现有接口契约；你读不到本地文件，**本文内联的就是全部真相**。

---

## 0 · 项目背景（目标仓现状）

多模态统一数据模型（unified_events 表 + 双写 + 查询 API）

**已知缺口（真机 vs 现状，按爆炸半径排序）**

所有时间戳统一 ISO8601 UTC+8 格式
person 字段空字符串表示未识别（不用 NULL）
source 枚举: device | vision | perception | activity | llm
modality 枚举: sensor | image | audio | text
双写失败只记日志不抛异常（不影响主流程）

---

## 1 · 现有代码（完整内联，你读不到本地文件）


---

## 2 · 设计目标与严格要求

### 2.1 核心目标

多模态统一数据模型（unified_events 表 + 双写 + 查询 API）

### 2.2 严格约束

- 不迁移旧数据（只双写新产生的）
- 不删旧表
- 不引入新依赖（只用 sqlite3 + stdlib）
- 下列文件**一个字都不许改**：`store.py 现有建表 SQL`、`现有 MCP 工具签名`、`agent_memory.py 召回逻辑`

---

## 3 · 模块详细需求

### 3.1 `src/memory_agent/unified_store.py`

（本单要新增的文件之一。接口必须与 §1 的契约逐一对上。）

---

## 4 · 测试要求

- 每条 §2.1 目标至少配 1 条单测；边界与异常路径各自要有。
- **不得出现只断言 `result["ok"] is True` 的空测试**；桩实现（函数体内除 logger 外无外部副作用）一律视为未完成，要在 §7 里明说哪条没做。

---

## 6 · 交付清单

- [ ] `src/memory_agent/unified_store.py`
- [ ] 上述每条验收对应的测试文件

---

## 7 · 关键设计决策（本单未明确处，你自行决定但必须逐条说明理由）

- 数据结构与索引选择；
- 失败/降级语义（哪些 fail-open、哪些 fail-closed）；
- 与 §1 现有实现的兼容边界；
- 你认为需求里说不清的地方，**不要猜，列出来**。

---

## 8 · 验收标准（提单 agent 写的，采信台会逐条真跑）

1. py_compile src/memory_agent/unified_store.py 通过
2. unified_events 表建表 SQL 正确（含索引）
3. 双写函数 unified_store.write_event() 能接收 device/vision/perception/activity 四种来源
4. 查询函数 unified_store.query_events(person, room, start, end) 返回统一格式列表
5. 不修改现有 events/perception_events 表结构

### MiMo 输出（50862 字符，已剥离 1 段推理）

先说一个必须澄清的缺口：**§1「现有代码（完整内联）」这一节是空的**，里面没有任何代码或接口契约，而 §3.1 又要求「接口必须与 §1 的契约逐一对上」。在契约缺席的情况下我不会去猜签名，而是**从验收条文（§8.3/§8.4 的函数名与参数顺序）反推出最小契约**，并把它明确列在下面的「假设 / 待确认」里。除此之外，交付单要求的模块、单测、使用示例我全部实做了（无桩函数）。

---

## 一、关键设计决策（§7 逐条说明）

**1. 表结构**

```sql
CREATE TABLE IF NOT EXISTS unified_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,
    source       TEXT    NOT NULL CHECK (source IN ('device','vision','perception','activity','llm')),
    modality     TEXT    NOT NULL CHECK (modality IN ('sensor','image','audio','text')),
    person       TEXT    NOT NULL DEFAULT '',
    room         TEXT    NOT NULL DEFAULT '',
    event_type   TEXT    NOT NULL DEFAULT '',
    payload      TEXT    NOT NULL DEFAULT '{}',
    created_at   TEXT    NOT NULL
)
```

- `ts` 用 TEXT 而非 INTEGER：需求明确「所有时间戳统一 ISO8601 UTC+8」，TEXT 便于人工排查；同一 UTC+8 规范下字符串**字典序 = 时间序**（见第 3 条）。
- `person`/`room` 一律 `NOT NULL DEFAULT ''`：需求规定空字符串=未识别，用 `NOT NULL` 在库层堵死 NULL，保证 `person = ''` 查询永远可靠。
- `payload` 存紧凑、键排序的 JSON 文本（`sort_keys=True, separators=(",",":")`）：不依赖 sqlite 的 JSON1 扩展，序列化可复现、便于断言。
- `created_at` 是写入时刻（UTC+8）：与业务时间 `ts` 分离，便于审计和回填场景排查写入顺序。
- `source`/`modality` 加 CHECK：Python 层校验之外的第二道防线，脏数据进不了表。

**2. 索引**：`ts`、`(person, ts)`、`(room, ts)`、`(source, ts)`、`(modality, ts)` 共 5 个。查询接口的过滤维度就是 person/room/时间区间（外加可选 source/modality），全部是「等值 + 时间范围」形状，复合索引把等值列放前面、`ts` 放后面，范围扫描可以直接沿索引走。只建索引、不建视图/触发器，避免影响旧表。

**3. 时间戳规范**
- 格式：`YYYY-MM-DDTHH:MM:SS+08:00`；亚秒非零时带 6 位小数 `YYYY-MM-DDTHH:MM:SS.ffffff+08:00`。同一格式族下字典序与时间序一致（无小数部分 `+` < 有小数部分 `.`，恰好等于「整秒 < 带小数」），因此 `WHERE ts >= ? AND ts <= ?` 的字符串比较是安全的。
- 输入接受：`None`（=现在）、`datetime`（naive 按 UTC+8 墙上时间解释，aware 自动换算到 +08:00）、Unix 时间戳（int/float，按 UTC 解释）、ISO8601 字符串（支持 `Z`、`±HH:MM`、`±HHMM`、空格分隔日期时间）。
- `start`/`end` 与写入用**同一个归一化函数**，边界语义：**双闭区间**（`ts >= start AND ts <= end`）；`start > end` 返回 `[]`（不是报错）。
- 明确不接受空字符串 ts（会 raise，避免「悄悄写成 now」）。

**4. person / room 语义**
- 写入：`None` → `''`，字符串做 strip；`'  '` 归一为 `''`（=未识别）。永远不写 NULL。
- 查询：`person=None` = 不过滤；`person=''` = **只查未识别**。这一点最容易踩坑，必须靠文档和测试锁住。

**5. 枚举校验**：大小写不敏感 + strip，落库统一小写（`'DEVICE' → 'device'`）。非法值拒绝写入。

**6. 失败/降级语义（fail-open vs fail-closed）**

| 路径 | 语义 | 行为 |
|---|---|---|
| `write_event` 参数非法（枚举/类型/payload 不可序列化） | 对数据 fail-closed，对主流程 fail-open | 不写库，`logger.warning`，返回 `{"ok": False, "stage": "validation", ...}`，**不抛异常** |
| `write_event` 存储失败（磁盘/锁/SQL） | fail-open | `logger.exception`，返回 `{"ok": False, "stage": "storage", ...}`，**不抛异常** |
| `write_event` 未知字段 | 降级 | 忽略并 `logger.warning` 列出字段名（防止调用方传整行旧表数据时误拒双写） |
| `query_events` 参数非法 | fail-closed | 抛 `TypeError`/`ValueError`（返回值是裸 list，没有 ok 通道；把编程错误吞成 `[]` 会误导调用方） |
| `query_events` 存储失败 | fail-open | `logger.exception`，返回 `[]` |

另外：`write_event` 模块级入口再套一层兜底 `except Exception`，确保「双写失败只记日志不抛异常」这条**在任何输入下都成立**（`KeyboardInterrupt`/`SystemExit` 不捕获）。

**7. 连接与并发**：每次调用独立 `sqlite3.connect(db_path, timeout=5.0)`，天然线程安全；建表用 `CREATE TABLE/INDEX IF NOT EXISTS` + 锁保护的懒初始化。**不启用 WAL**——目标仓在 `E:/NAS/...`（网络文件系统），WAL 在 NAS 上是已知风险点。已知限制：不支持 `:memory:`（连接按次创建）。

**8. 与现有实现的兼容边界**
- 本模块**不 import** `store.py`、`agent_memory.py`，零耦合，也就不可能动到「现有建表 SQL / MCP 工具签名 / 召回逻辑」。
- 对库只有一条写语句：`INSERT INTO unified_events (...)`。无 `DROP/ALTER/DELETE`，旧表数据零迁移、零删除（compat 测试里有静态扫描断言）。
- DB 路径由调用方显式传入，缺省取环境变量 `MEMORY_AGENT_DB_PATH`，再缺省 `./memory_agent.db`。

**9. API 形态**：类 `UnifiedEventStore` + 模块级函数双形态（MCP 工具体里直接 `unified_store.write_event(...)`，测试/嵌入用类）。`query_events(person, room, start, end, ...)` 前四参位置顺序严格对齐 §8.4，其余参数全部位置/关键字皆可，方便契约不明时的兼容调用。

**10. 需求里说不清、我没有猜的地方（需要提单方确认）**

1. **§1 契约为空**——签名、返回结构、双写方向（旧表→unified 还是 unified→旧表）都无法「逐一对上」。我按 §8.3/§8.4 反推：`write_event(event_mapping)`、`query_events(person, room, start, end)`、返回值带 `ok` 键（§4 的措辞暗示了这点）。
2. **双写的落点**：约束禁止我改 `store.py` 建表 SQL / MCP 工具签名，而 §1 又没有工具体代码，所以**双写调用点我无法替你插入**。下面是可直接粘贴的片段，由持有那两个文件的人插入（这是唯一未落地的一项，不是桩函数，是「没有可改的文件」）。
3. DB 路径的真相来源（`store.py` 的 DB_PATH？）——现按环境变量 + 显式传参。
4. naive 时间戳按 UTC+8 解释（而不是 UTC）——需求只说「统一 UTC+8」。
5. `llm` 在枚举里但 §8.3 只列了四种来源——我实现 5 种、测试覆盖 5 种。
6. `room` 只出现在查询签名里，语义未定义——我按 `''`=未知房间处理。
7. person 是精确匹配还是模糊/前缀匹配——现为精确（strip 后比较）。
8. 查询要不要默认 limit 上限——现为 `limit=None` 不封顶，MCP 层可自行加。
9. 是否需要幂等/去重写入（同 ts+source+person 重复写）——需求未提，现为「写入即新行」。
10. 真实 `events`/`perception_events` 建表语句未知，兼容性测试用的是替身 DDL（见下）。
11. Python 版本下限——代码按 3.7+ 写（手动兼容 `fromisoformat` 在 3.11 之前不认 `Z`/`±HHMM`）。

---

## 二、代码：`src/memory_agent/unified_store.py`

```python
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
```

---

## 三、单测

### 3.1 `tests/test_unified_store.py`（验收 1–4 + 边界/异常路径）

```python
# -*- coding: utf-8 -*-
"""tests/test_unified_store.py

验收映射：
  验收 1 -> TestSchema.test_py_compile_passes
  验收 2 -> TestSchema 其余用例（建表 SQL / 列约束 / 索引 / CHECK 生效）
  验收 3 -> TestWriteEvent（device/vision/perception/activity + llm）
  验收 4 -> TestQueryEvents（统一格式列表 + person/room/start/end 过滤）
边界与异常：时间戳归一化、person 空串语义、枚举校验、双写 fail-open、查询 fail-open
           与参数 fail-closed。每条都断言落库内容或返回结构，无"只看 ok"的空测试。
"""
import importlib.util
import pathlib
import py_compile
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "memory_agent" / "unified_store.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("unified_store_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


us = _load_module()

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?\+08:00$")
ROW_KEYS = {
    "id", "ts", "source", "modality", "person", "room", "event_type", "payload", "created_at"
}


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = str(pathlib.Path(self._tmp.name) / "test.db")
        self.store = us.UnifiedEventStore(self.db_path)

    def _fetch_all(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, ts, source, modality, person, room, event_type, payload, created_at "
                "FROM unified_events ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _count(self):
        return len(self._fetch_all())

    def _seed(self):
        seeds = [
            dict(ts="2026-09-28T23:53:00+08:00", source="device", modality="sensor",
                 person="", room="kitchen", event_type="motion", payload={"n": 1}),
            dict(ts="2026-09-28T23:53:01+08:00", source="vision", modality="image",
                 person="张三", room="kitchen", event_type="face", payload={"n": 2}),
            dict(ts="2026-09-28T23:53:02+08:00", source="perception", modality="audio",
                 person="李四", room="living", event_type="voice", payload={"n": 3}),
            dict(ts="2026-09-28T23:53:03+08:00", source="activity", modality="text",
                 person="", room="living", event_type="summary", payload={"n": 4}),
        ]
        for item in seeds:
            result = self.store.write_event(item)
            self.assertEqual(result["ok"], True, result)
        return seeds


class TestSchema(StoreTestCase):
    def test_py_compile_passes(self):
        py_compile.compile(str(MODULE_PATH), doraise=True)  # 抛异常即失败

    def test_create_table_and_indexes(self):
        self.store.init_schema()
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master"
            ).fetchall()
        finally:
            conn.close()
        by_name = {r[1]: r for r in rows}

        self.assertIn("unified_events", by_name)
        self.assertEqual(by_name["unified_events"][0], "table")
        table_sql = by_name["unified_events"][3]
        for col in ("id", "ts", "source", "modality", "person",
                    "room", "event_type", "payload", "created_at"):
            self.assertIn(col, table_sql, "建表 SQL 缺少列 %s" % col)
        self.assertIn("source IN ('device','vision','perception','activity','llm')", table_sql)
        self.assertIn("modality IN ('sensor','image','audio','text')", table_sql)

        for idx in ("idx_unified_events_ts", "idx_unified_events_person_ts",
                    "idx_unified_events_room_ts", "idx_unified_events_source_ts",
                    "idx_unified_events_modality_ts"):
            self.assertIn(idx, by_name, "缺少索引 %s" % idx)
            self.assertEqual(by_name[idx][0], "index")
            self.assertEqual(by_name[idx][2], "unified_events")

    def test_column_types_and_not_null(self):
        self.store.init_schema()
        conn = sqlite3.connect(self.db_path)
        try:
            info = conn.execute("PRAGMA table_info(unified_events)").fetchall()
        finally:
            conn.close()
        cols = {row[1]: row for row in info}
        self.assertEqual(cols["id"][5], 1, "id 必须是主键")
        self.assertEqual(cols["ts"][2].upper(), "TEXT")
        for name in ("ts", "source", "modality", "person", "room",
                     "event_type", "payload", "created_at"):
            self.assertEqual(cols[name][3], 1, "%s 必须 NOT NULL" % name)
        self.assertIn("''", str(cols["person"][4]), "person 必须 DEFAULT ''")

    def test_check_constraint_blocks_bad_enum(self):
        self.store.init_schema()
        conn = sqlite3.connect(self.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO unified_events "
                    "(ts, source, modality, person, room, event_type, payload, created_at) "
                    "VALUES (?, ?, ?, '', '', '', '{}', ?)",
                    ("2026-09-28T23:53:09+08:00", "unknown", "sensor", "2026-09-28T23:53:09+08:00"),
                )
        finally:
            conn.close()

    def test_person_not_null_at_db_level(self):
        self.store.init_schema()
        conn = sqlite3.connect(self.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO unified_events "
                    "(ts, source, modality, person, room, event_type, payload, created_at) "
                    "VALUES (?, ?, ?, NULL, '', '', '{}', ?)",
                    ("2026-09-28T23:53:09+08:00", "device", "sensor", "2026-09-28T23:53:09+08:00"),
                )
        finally:
            conn.close()

    def test_init_schema_is_idempotent(self):
        self.store.init_schema()
        self.store.write_event({"source": "device", "modality": "sensor"})
        self.store.init_schema()
        self.assertEqual(self._count(), 1, "重复 init_schema 不应影响已有数据")


class TestWriteEvent(StoreTestCase):
    def test_accepts_all_four_sources_plus_llm(self):
        for source in ("device", "vision", "perception", "activity", "llm"):
            result = self.store.write_event({
                "source": source, "modality": "sensor", "event_type": "probe",
            })
            self.assertEqual(result["ok"], True, result)
            self.assertIsInstance(result["id"], int)
        rows = self._fetch_all()
        self.assertEqual([r["source"] for r in rows],
                         ["device", "vision", "perception", "activity", "llm"])
        self.assertEqual(len(rows), 5)

    def test_returns_unified_echo(self):
        result = self.store.write_event({
            "source": "vision", "modality": "image", "person": "张三", "room": "kitchen",
            "event_type": "face", "ts": "2026-09-28T23:53:09+08:00",
            "payload": {"bbox": [1, 2, 3, 4], "score": 0.93},
        })
        event = result["event"]
        self.assertEqual(set(event.keys()), ROW_KEYS)
        self.assertEqual(event["source"], "vision")
        self.assertEqual(event["modality"], "image")
        self.assertEqual(event["person"], "张三")
        self.assertEqual(event["room"], "kitchen")
        self.assertEqual(event["ts"], "2026-09-28T23:53:09+08:00")
        self.assertEqual(event["payload"], {"bbox": [1, 2, 3, 4], "score": 0.93})
        row = self._fetch_all()[0]
        self.assertEqual(row["id"], result["id"])
        self.assertIn('"score":0.93', row["payload"], "payload 应以 JSON 文本落库")

    def test_person_empty_string_means_unidentified(self):
        self.store.write_event({"source": "vision", "modality": "image"})
        self.store.write_event({"source": "vision", "modality": "image", "person": None})
        self.store.write_event({"source": "vision", "modality": "image", "person": "   "})
        self.store.write_event({"source": "vision", "modality": "image", "person": " 张三 "})
        rows = self._fetch_all()
        self.assertEqual([r["person"] for r in rows], ["", "", "", "张三"])
        self.assertNotIn(None, [r["person"] for r in rows], "person 永不为 NULL")

    def test_timestamps_normalized_to_utc8(self):
        utc = timezone.utc
        cases = [
            ("2026-09-28T15:53:09Z", "2026-09-28T23:53:09+08:00"),
            ("2026-09-28T15:53:09+00:00", "2026-09-28T23:53:09+08:00"),
            ("2026-09-28T15:53:09+0000", "2026-09-28T23:53:09+08:00"),
            ("2026-09-28T23:53:09+08:00", "2026-09-28T23:53:09+08:00"),
            ("2026-09-28T23:53:09.123456+08:00", "2026-09-28T23:53:09.123456+08:00"),
            ("2026-09-28 23:53:09", "2026-09-28T23:53:09+08:00"),
            (datetime(2026, 9, 28, 23, 53, 9), "2026-09-28T23:53:09+08:00"),
            (datetime(2026, 9, 28, 15, 53, 9, tzinfo=utc), "2026-09-28T23:53:09+08:00"),
            (datetime(2026, 9, 28, 10, 53, 9, tzinfo=timezone(timedelta(hours=-5))),
             "2026-09-28T23:53:09+08:00"),
            (datetime(2026, 9, 28, 15, 53, 9, tzinfo=utc).timestamp(),
             "2026-09-28T23:53:09+08:00"),
        ]
        for given, expected in cases:
            result = self.store.write_event({
                "source": "device", "modality": "sensor", "ts": given,
            })
            self.assertEqual(result["ok"], True, (given, result))
            self.assertEqual(result["event"]["ts"], expected, "输入 %r" % (given,))

    def test_ts_none_means_now(self):
        before = datetime.now(us.TZ_UTC8)
        self.store.write_event({"source": "device", "modality": "sensor"})
        after = datetime.now(us.TZ_UTC8)
        stored = self._fetch_all()[0]["ts"]
        self.assertRegex(stored, TS_RE)
        parsed = us.coerce_datetime(stored)
        self.assertTrue(before - timedelta(seconds=1) <= parsed <= after + timedelta(seconds=1))

    def test_invalid_source_rejected_without_write(self):
        result = self.store.write_event({"source": "unknown", "modality": "sensor"})
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["stage"], "validation")
        self.assertIn("source", result["error"])
        self.assertIn("device", result["error"], "错误信息应给出允许值")
        self.assertEqual(self._count(), 0)

    def test_missing_modality_rejected(self):
        result = self.store.write_event({"source": "device"})
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["stage"], "validation")
        self.assertIn("modality", result["error"])
        self.assertEqual(self._count(), 0)

    def test_enum_case_is_normalized(self):
        result = self.store.write_event({"source": "DEVICE", "modality": " Image "})
        self.assertEqual(result["ok"], True, result)
        row = self._fetch_all()[0]
        self.assertEqual((row["source"], row["modality"]), ("device", "image"))

    def test_bad_payload_rejected(self):
        result = self.store.write_event({
            "source": "device", "modality": "sensor", "payload": {"x": object()},
        })
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["stage"], "validation")
        self.assertEqual(self._count(), 0)

    def test_non_mapping_event_rejected(self):
        result = self.store.write_event("not a mapping")
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["stage"], "validation")

    def test_empty_ts_string_rejected(self):
        result = self.store.write_event({"source": "device", "modality": "sensor", "ts": ""})
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["stage"], "validation")

    def test_unknown_fields_ignored_but_core_written(self):
        result = self.store.write_event({
            "source": "device", "modality": "sensor", "legacy_id": 42,
        })
        self.assertEqual(result["ok"], True, result)
        row = self._fetch_all()[0]
        self.assertNotIn("legacy_id", row)

    def test_write_failure_is_logged_and_swallowed(self):
        bad_path = str(pathlib.Path(self._tmp.name) / "no_such_dir" / "x.db")
        bad = us.UnifiedEventStore(bad_path)
        with self.assertLogs("memory_agent.unified_store", level="ERROR") as captured:
            result = bad.write_event({"source": "device", "modality": "sensor"})
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["stage"], "storage")
        self.assertTrue(any("双写" in line for line in captured.output))
        # 主流程继续可用：换回好库立即成功
        ok_result = self.store.write_event({"source": "device", "modality": "sensor"})
        self.assertEqual(ok_result["ok"], True, ok_result)
        self.assertEqual(self._count(), 1)

    def test_module_level_write_accepts_kwargs_and_never_raises(self):
        result = us.write_event(source="activity", modality="text", person="", db_path=self.db_path)
        self.assertEqual(result["ok"], True, result)
        self.assertEqual(result["event"]["source"], "activity")

        broken = us.write_event(12345, db_path=self.db_path)  # 非法输入也不能抛
        self.assertEqual(broken["ok"], False)
        self.assertEqual(broken["stage"], "validation")


class TestQueryEvents(StoreTestCase):
    def test_returns_unified_format_list(self):
        self._seed()
        result = self.store.query_events(None, None, None, None)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 4)
        for item in result:
            self.assertIsInstance(item, dict)
            self.assertEqual(set(item.keys()), ROW_KEYS)
            self.assertRegex(item["ts"], TS_RE)
            self.assertIsInstance(item["payload"], dict)
        self.assertEqual([r["event_type"] for r in result],
                         ["motion", "face", "voice", "summary"], "默认按 ts 升序")
        self.assertEqual([r["id"] for r in result], sorted(r["id"] for r in result))

    def test_filter_by_person_distinguishes_none_and_empty(self):
        self._seed()
        all_rows = self.store.query_events(None, None, None, None)
        self.assertEqual(len(all_rows), 4)
        only_zhang = self.store.query_events("张三", None, None, None)
        self.assertEqual([r["person"] for r in only_zhang], ["张三"])
        only_unknown = self.store.query_events("", None, None, None)
        self.assertEqual([r["event_type"] for r in only_unknown], ["motion", "summary"])
        for row in only_unknown:
            self.assertEqual(row["person"], "")

    def test_filter_by_room(self):
        self._seed()
        rows = self.store.query_events(None, "kitchen", None, None)
        self.assertEqual([r["event_type"] for r in rows], ["motion", "face"])

    def test_range_bounds_are_inclusive(self):
        self._seed()
        rows = self.store.query_events(
            None, None, "2026-09-28T23:53:01+08:00", "2026-09-28T23:53:02+08:00")
        self.assertEqual([r["event_type"] for r in rows], ["face", "voice"])

        narrow = self.store.query_events(
            None, None, "2026-09-28T23:53:01.000001+08:00", "2026-09-28T23:53:01.999999+08:00")
        self.assertEqual(narrow, [], "边界外的微秒必须被排除")

        reversed_range = self.store.query_events(
            None, None, "2026-09-29T00:00:00+08:00", "2026-09-28T00:00:00+08:00")
        self.assertEqual(reversed_range, [])

    def test_combined_filters_and_datetime_bounds(self):
        self._seed()
        rows = self.store.query_events(
            "", "kitchen", datetime(2026, 9, 28, 23, 53, 0), "2026-09-28T23:53:01+08:00",
            source="device", modality="sensor")
        self.assertEqual([r["event_type"] for r in rows], ["motion"])

    def test_limit_and_order(self):
        self._seed()
        rows = self.store.query_events(None, None, None, None, order="desc", limit=2)
        self.assertEqual([r["event_type"] for r in rows], ["summary", "voice"])
        self.assertEqual(self.store.query_events(None, None, None, None, limit=0), [])

    def test_query_by_source_and_modality(self):
        self._seed()
        rows = self.store.query_events(None, None, None, None, source="perception")
        self.assertEqual([r["modality"] for r in rows], ["audio"])

    def test_unparseable_payload_degrades_to_raw(self):
        self._seed()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO unified_events "
                "(ts, source, modality, person, room, event_type, payload, created_at) "
                "VALUES (?, ?, ?, '', '', 'broken', ?, ?)",
                ("2026-09-28T23:54:00+08:00", "device", "sensor", "not-json",
                 "2026-09-28T23:54:00+08:00"),
            )
            conn.commit()
        finally:
            conn.close()
        rows = self.store.query_events(None, None, None, None, event_type="broken")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["payload"], {"_raw": "not-json"})

    def test_empty_database_returns_empty_list(self):
        self.assertEqual(self.store.query_events(None, None, None, None), [])

    def test_query_storage_failure_returns_empty_list(self):
        bad = us.UnifiedEventStore(str(pathlib.Path(self._tmp.name) / "no_such_dir" / "x.db"))
        with self.assertLogs("memory_agent.unified_store", level="ERROR"):
            self.assertEqual(bad.query_events(None, None, None, None), [])

    def test_query_invalid_arguments_fail_closed(self):
        with self.assertRaises(TypeError):
            self.store.query_events(123, None, None, None)
        with self.assertRaises(ValueError):
            self.store.query_events(None, None, "not-a-date", None)
        with self.assertRaises(ValueError):
            self.store.query_events(None, None, None, None, order="sideways")
        with self.assertRaises(ValueError):
            self.store.query_events(None, None, None, None, limit=-1)
        with self.assertRaises(TypeError):
            self.store.query_events(None, None, None, None, limit=1.5)

    def test_module_level_positional_signature(self):
        self._seed()
        rows = us.query_events("张三", "kitchen", None, None, db_path=self.db_path)
        self.assertIsInstance(rows, list)
        self.assertEqual([r["person"] for r in rows], ["张三"])


if __name__ == "__main__":
    unittest.main()
```

### 3.2 `tests/test_unified_store_compat.py`（验收 5 + 依赖约束）

```python
# -*- coding: utf-8 -*-
"""tests/test_unified_store_compat.py

验收映射：
  验收 5 -> TestLegacyTablesUntouched（不修改 events / perception_events 表结构）
  §2.2  -> TestDependencyBoundary（只用 sqlite3 + 标准库，不 import 现有模块）

注意：交付单 §1「现有代码」为空，读不到真实 events / perception_events 建表 SQL。
      下面的 LEGACY_DDL 是**替身结构**，本文件只断言
      "运行 unified_store 前后旧表原封不动"，因此对真实 DDL 同样成立。
"""
import ast
import importlib.util
import pathlib
import sqlite3
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "memory_agent" / "unified_store.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("unified_store_compat_target", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


us = _load_module()

LEGACY_DDL = [
    "CREATE TABLE IF NOT EXISTS events ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " ts TEXT NOT NULL,"
    " kind TEXT,"
    " payload TEXT)",
    "CREATE TABLE IF NOT EXISTS perception_events ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " ts TEXT NOT NULL,"
    " person TEXT,"
    " note TEXT)",
]


def _snapshot(conn, names=("events", "perception_events")):
    placeholders = ",".join("?" for _ in names)
    master = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name IN (%s) OR tbl_name IN (%s) ORDER BY type, name" % (placeholders, placeholders),
        tuple(names) + tuple(names),
    ).fetchall()
    structure = {}
    for name in names:
        structure[name] = conn.execute("PRAGMA table_info(%s)" % name).fetchall()
    return master, structure


class TestLegacyTablesUntouched(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = str(pathlib.Path(self._tmp.name) / "compat.db")
        self.store = us.UnifiedEventStore(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            for ddl in LEGACY_DDL:
                conn.execute(ddl)
            conn.execute("INSERT INTO events (ts, kind, payload) VALUES (?,?,?)",
                         ("2026-09-28T23:53:09+08:00", "motion", '{"a":1}'))
            conn.execute("INSERT INTO perception_events (ts, person, note) VALUES (?,?,?)",
                         ("2026-09-28T23:53:09+08:00", "张三", "old"))
            conn.commit()
        finally:
            conn.close()

    def test_init_schema_leaves_legacy_tables_untouched(self):
        conn = sqlite3.connect(self.db_path)
        try:
            before_master, before_structure = _snapshot(conn)
        finally:
            conn.close()

        self.store.init_schema()
        self.store.write_event({"source": "device", "modality": "sensor", "person": ""})
        self.store.write_event({"source": "vision", "modality": "image", "person": "张三"})

        conn = sqlite3.connect(self.db_path)
        try:
            after_master, after_structure = _snapshot(conn)
            new_objects = {
                row[1] for row in conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master")
            }
        finally:
            conn.close()

        self.assertEqual(before_master, after_master, "旧表的 sqlite_master 定义必须原封不动")
        self.assertEqual(before_structure, after_structure, "旧表列结构必须原封不动")
        self.assertIn("unified_events", new_objects)
        for idx in ("idx_unified_events_ts", "idx_unified_events_person_ts",
                    "idx_unified_events_room_ts", "idx_unified_events_source_ts",
                    "idx_unified_events_modality_ts"):
            self.assertIn(idx, new_objects)

    def test_write_event_only_inserts_into_unified_events(self):
        conn = sqlite3.connect(self.db_path)
        try:
            before = {
                "events": conn.execute("SELECT * FROM events ORDER BY id").fetchall(),
                "perception_events": conn.execute(
                    "SELECT * FROM perception_events ORDER BY id").fetchall(),
            }
        finally:
            conn.close()

        for source in ("device", "vision", "perception", "activity"):
            self.store.write_event({"source": source, "modality": "sensor", "person": ""})

        conn = sqlite3.connect(self.db_path)
        try:
            after = {
                "events": conn.execute("SELECT * FROM events ORDER BY id").fetchall(),
                "perception_events": conn.execute(
                    "SELECT * FROM perception_events ORDER BY id").fetchall(),
            }
            unified_count = conn.execute("SELECT COUNT(*) FROM unified_events").fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(before, after, "旧表数据不迁移、不改动")
        self.assertEqual(unified_count, 4)

    def test_module_never_drops_or_alters_tables(self):
        source = MODULE_PATH.read_text(encoding="utf-8").upper()
        for forbidden in ("DROP TABLE", "ALTER TABLE", "DELETE FROM", "DROP INDEX"):
            self.assertNotIn(forbidden, source, "模块里出现 %s" % forbidden)
        self.assertIn("CREATE TABLE IF NOT EXISTS UNIFIED_EVENTS", source)


class TestDependencyBoundary(unittest.TestCase):
    def test_only_stdlib_imports(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    self.fail("不允许相对导入: %s" % ast.dump(node))
                if node.module:
                    roots.add(node.module.split(".")[0])
        allowed = {
            "__future__", "collections", "datetime", "json", "logging",
            "os", "sqlite3", "threading", "typing",
        }
        self.assertTrue(roots <= allowed, "出现计划外依赖: %s" % sorted(roots - allowed))

    def test_no_import_of_existing_modules(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn("memory_agent", node.module.split("."))
                self.assertNotIn("store", node.module.split("."))
                self.assertNotIn("agent_memory", node.module.split("."))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("memory_agent", alias.name.split("."))


if __name__ == "__main__":
    unittest.main()
```

运行方式（无需任何第三方库）：

```bash
python -m unittest discover -s tests -v
# 或单文件
python tests/test_unified_store.py
python tests/test_unified_store_compat.py
```

---

## 四、使用示例

```python
# --- 1) 初始化（幂等，可重复调用；只建 unified_events，不动旧表）---
from memory_agent import unified_store   # 或按你的 sys.path 方式 import unified_store

DB = r"E:/NAS/memory-agent/data/memory_agent.db"
unified_store.init_schema(db_path=DB)

# --- 2) 双写：在现有写旧表成功之后追加一行；失败只记日志，不影响主流程 ---
result = unified_store.write_event(
    {
        "source": "vision",        # device | vision | perception | activity | llm
        "modality": "image",       # sensor | image | audio | text
        "person": "",              # "" = 未识别（不写 NULL）
        "room": "kitchen",
        "event_type": "face_detected",
        "ts": "2026-09-28T23:53:09+08:00",
        "payload": {"bbox": [12, 30, 80, 120], "score": 0.93},
    },
    db_path=DB,
)
# result == {"ok": True, "id": 7, "event": {...统一格式...}}
# 失败时 == {"ok": False, "stage": "validation"|"storage", "error": "..."}

# 关键字写法等价：
unified_store.write_event(source="activity", modality="text",
                          event_type="summary", person="张三", db_path=DB)

# --- 3) 统一查询：query_events(person, room, start, end) ---
rows = unified_store.query_events(
    "张三", "kitchen",
    "2026-09-28T00:00:00+08:00", "2026-09-29T00:00:00+08:00",
    db_path=DB,
)
for row in rows:
    # {'id': 7, 'ts': '2026-09-28T23:53:09+08:00', 'source': 'vision',
    #  'modality': 'image', 'person': '张三', 'room': 'kitchen',
    #  'event_type': 'face_detected', 'payload': {...}, 'created_at': '...+08:00'}
    print(row["ts"], row["source"], row["person"] or "<未识别>", row["payload"])

# person=None 不过滤；person="" 只查未识别
unified_store.query_events("", None, None, None, db_path=DB)

# --- 4) 与现有 MCP 工具的接线（片段，需由持有 store.py / 工具文件的人插入）---
# 工具签名保持一字不改，只在写完旧表之后追加一行：
#     unified_store.write_event({"source": "device", "modality": "sensor",
#                                "person": "", "room": room,
#                                "event_type": kind, "ts": ts, "payload": payload},
#                               db_path=DB_PATH)
```

---

## 五、验收对照与未完成项

| 验收条目 | 落点 | 状态 |
|---|---|---|
| 1. `py_compile` 通过 | `TestSchema.test_py_compile_passes` | 已做 |
| 2. 建表 SQL 正确（含索引） | `TestSchema` 4 条（DDL 文本、列 NOT NULL/DEFAULT、CHECK 生效、5 索引） | 已做 |
| 3. `write_event` 接收 4 种来源 | `TestWriteEvent.test_accepts_all_four_sources_plus_llm`（含 llm，共 5 种） | 已做 |
| 4. `query_events(person, room, start, end)` 统一格式 | `TestQueryEvents` 12 条（格式、过滤、区间、排序、limit、异常） | 已做 |
| 5. 不改 events/perception_events | `TestLegacyTablesUntouched` 3 条 + 静态 SQL 扫描 | 已做（替身 DDL，需对真实表复跑） |

**未完成 / 需要人工落地的项（不是桩函数，是「没有可改的文件」）**

1. **双写调用点未插入 `store.py` / MCP 工具体**——约束要求「一个字都不许改」现有签名，且 §1 无代码可对照，我无法定位插入位置。上面 §4.4 的片段就是待插入内容，需要由持有这两个文件的同学执行。
2. **与真实 `events`/`perception_events` 建表 SQL 的一致性未验证**——§1 为空，compat 测试用替身 DDL；拿到真实 DDL 后请把 `LEGACY_DDL` 替换掉再跑一次。
3. **接口契约对齐未完成**——§1 契约为空，本单的签名是从 §8.3/§8.4 反推的。若真实契约里 `write_event` 是关键字签名、或要求同时写旧表、或返回结构不同，需要按 §7 第 10 条逐项确认后调整（改动面很小：只在 `write_event`/`query_events` 两个入口）。

**本模块内没有任何桩实现**：`coerce_datetime`、`format_ts`、`normalize_event`、`init_schema`、`write_event`、`query_events`、缓存与路径解析全部有真实行为，测试断言的都是落库内容、返回结构与日志，不存在只判 `result["ok"] is True` 的空测试。

<!-- tpsMetrics={"metricsVersion":4,"outputTps":621,"outputSseCount":163,"thinkingTps":413,"thinkingSseCount":406,"thinkingDurationSec":79.34,"thinkingTokens":32943,"thinkingCompleted":true,"thinkingTokenSource":"native_usage_reasoning_tokens","firstResponseTimeSec":1.046,"totalDurationSec":105.285,"outputDurationSec":104.239,"completionTokens":48038,"promptTokens":3004,"totalTokens":48038,"samples":[0,358,429,470,403,422,430,418,408,404,396,414,407,399,399,399,392,398,400,398,395,389,391,389,381,379,377,372,368,366,367,374,377,374,375,374,367,372,370,372,372,373,372,370,369,368,370,369,371,370,367,367,366,368,375,381,384,383,385,387,389,388,387,388,388,386,387,386,387,387,387,387,387,389,389,388,386,386,384,384,383,382,384,384,385,386,387,388,389,387,385,385,384,383,382,385,385,387,387,387,387,387,387,387,390,393,395,394,397,400,402,404,407,406,408,410,411,416,414,417,417,419,419,419,423,421,420,420,420,421,421,420,420,421,421,421,421,420,420,419,419,420,419,418,418,419,418,417,418,417,416,416,416,416,349,1364,804,679,599,564,519,490,473,459,487,535,533,560,572,585,599,618,623,632,639,643,644,652,659,658,665,670,676,675,677,674,680,677,675,678,679,675,680,676,678,676,673,663,655,644],"thinkingEndIdx":153,"peakTps":1364,"savedAt":1790610924729} -->

### 推理段（1 段 / 122,488 字符，非交付物，勿当代码采信）

<think>Let me look at this request carefully. The user wants a design + code + unit tests + usage examples for a "MA-多模态统一数据模型" (multimodal unified data model) — a `unified_store.py` module with a `unified_events` table, dual-write, and query API.

The request claims to inline "全部真相" (all truth) but §1 现有代码 is empty — there's no actual code inlined. The section says "现有代码（完整内联，你读不到本地文件）" but nothing follows. That's a gap I must flag honestly rather than fabricate contract details.

Also §3.1 says "接口必须与 §1 的契约逐一对上" — but §1 has no contract. So I cannot know the exact interface. I should design a reasonable interface, be explicit about assumptions, and list the ambiguities in §7 as required ("你认为需求里说不清的地方，不要猜，列出来").

Constraints:
- All timestamps ISO8601 UTC+8
- person empty string means unidentified (not NULL)
- source enum: device | vision | perception | activity | llm
- modality enum: sensor | image | audio | text
- dual-write failure only logs, doesn't raise (fail-open)
- No migration of old data, no dropping old tables, no new deps (sqlite3 + stdlib only)
- Don't change existing store.py table SQL, MCP tool signatures, agent_memory.py recall logic

Acceptance:
1. py_compile passes
2. unified_events table SQL correct with indexes
3. write_event() accepts device/vision/perception/activity sources (and llm per enum)
4. query_events(person, room, start, end) returns unified format list
5. Not modify existing events/perception_events table structure

I need to produce:
- Design doc (§7 key decisions)
- `src/memory_agent/unified_store.py`
- tests
- usage example

This is a code deliverable. There's no brand safety issue here. No sensitive political content. Fine to proceed.

Let me think about the design.

Schema for unified_events:

```sql
CREATE TABLE IF NOT EXISTS unified_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,              -- ISO8601 UTC+8, e.g. 2026-09-28T23:53:09+08:00
    source TEXT NOT NULL CHECK (source IN ('device','vision','perception','activity','llm')),
    modality TEXT NOT NULL CHECK (modality IN ('sensor','image','audio','text')),
    person TEXT NOT NULL DEFAULT '',  -- '' = unidentified
    room TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',  -- JSON
    created_at TEXT NOT NULL DEFAULT ...
)
```

Wait — should created_at be included? Maybe keep it simple but useful. Let me include `id`, `ts`, `source`, `modality`, `person`, `room`, `event_type`, `payload`, `schema_version`. Hmm, keep it lean but justified.

Indexes:
- idx_unified_events_ts on (ts)
- idx_unified_events_person_ts on (person, ts)
- idx_unified_events_room_ts on (room, ts)
- idx_unified_events_source_ts on (source, ts)

Query: query_events(person, room, start, end) returns list of dicts with unified format.

Unified format — define explicitly:

```python
{
    "id": int,
    "ts": "2026-09-28T23:53:09+08:00",
    "source": "device",
    "modality": "sensor",
    "person": "",           # '' = 未识别
    "room": "living_room",
    "event_type": "motion",
    "payload": {...},       # dict
}
```

Timestamp handling: all timestamps ISO8601 UTC+8. Normalization function: accept datetime, ISO string with any offset or naive (assume UTC+8? or local?). The spec says "所有时间戳统一 ISO8601 UTC+8 格式" — so normalize everything to +08:00 offset. Naive datetimes: ambiguous — I'll assume naive means already UTC+8 local wall time (since the product runs in UTC+8). Flag in §7 as a decision. Use `datetime.timezone(timedelta(hours=8))`.

Format: `YYYY-MM-DDTHH:MM:SS+08:00` — should I include microseconds? For sorting consistency and lexicographic ordering to match chronological ordering, a fixed-width format is good. I'll keep microseconds if nonzero? That breaks fixed width but lexicographic ordering still works if the base part is fixed width... Actually "2026-09-28T23:53:09+08:00" vs "2026-09-28T23:53:09.123456+08:00" — lexicographically "+" (0x2B) < "." (0x2E), so "…09+08:00" < "…09.123456+08:00" — that's correct ordering actually since no fraction < with fraction... wait, in terms of time, "…09.123456" is after "…09.000000" i.e. after "…09". And lexicographically "…" + "+08:00" vs "…" + ".123456+08:00": compare "+" vs "." → "+" < ".", so base < with-fraction. Correct ordering. But "…09.123+08:00" vs "…09.9+08:00" would misorder if fraction widths differ ("1" < "9" ok, but "…09.12+08:00" vs "…09.9+08:00": "1" < "9" correct; "…09.2" vs "…09.12": "2" > "1" correct). Problem: "…09.123" vs "…09.2" → "1" < "2" correct. Hmm, fractional comparison with variable width: "0.12" vs "0.2" → "1"<"2" correct; "0.2" vs "0.12"? "2">"1" correct. Actually variable-length fractional strings misorder in cases like "0.9" vs "0.10"? "9" > "1" → 0.9 > 0.10 lexicographically, and numerically 0.9 > 0.10. Correct. What about "0.10" vs "0.1"? Equal numerically but different strings; ordering between "0.1" and "0.10" — "0.1" is prefix of "0.10" so shorter first. Fine.

Real misorder: "0.25" vs "0.3"? "2"<"3" correct. Hmm, standard result: fractional decimal strings of different lengths do misorder, e.g. "0.12" vs "0.2" compares "1"<"2" → correct since 0.12<0.2. Try to find misorder: compare "0.9" and "0.11": 0.9 > 0.11, lexicographic "9" > "1" → correct. Consider "0.19" vs "0.2": 0.19 < 0.2, lexico "1"<"2" → correct. It seems comparing digit by digit works as long as digits align... The misordering case is when one is a prefix: "0.1" vs "0.12" → numerically 0.1 < 0.12 → correct. Hmm actually lexicographic comparison of decimal fractions: digits compared position by position; the first differing digit decides, which is exactly numeric comparison unless one is a prefix of the other, in which case the longer one is larger numerically (since trailing digits... wait "0.1" vs "0.10" numerically equal, lexicographic says shorter first). And "0.1" vs "0.1000001" → longer larger. Correct. So variable-length fractions sort fine except for trailing-zero equivalence. Good — but simpler: always store with microseconds zero-padded to 6 digits (fixed width), or drop sub-second precision entirely.

Decision: normalize to `YYYY-MM-DDTHH:MM:SS+08:00` with optional 6-digit microseconds when nonzero? Or always include microseconds? Let me just always normalize with `timespec="seconds"`... but then sub-second ordering lost. Hmm. For an events table, second precision might be enough but losing ordering is bad.

I'll go with: always output microseconds as 6 digits if non-zero... Actually simplest and defensible: fixed format `%Y-%m-%dT%H:%M:%S.%f+08:00`? That's ugly for humans. Many tools produce "2026-09-28T23:53:09+08:00".

Alternative: store normalized canonical string with `timespec='microseconds'` only when microsecond != 0... I showed lexicographic ordering still works. But equality/dedup issues are minor.

Let me decide: canonical format is `%Y-%m-%dT%H:%M:%S+08:00` when microsecond == 0, otherwise `%Y-%m-%dT%H:%M:%S.%f+08:00` (6-digit fraction). This keeps human-readable default and preserves precision. And note in design that ordering works lexicographically for this family of strings (same fixed-width prefix; fraction comparison as shown). Also range queries compare on ts string — for that to be correct with mixed formats, comparison must handle it. Hmm, range query: `WHERE ts >= ? AND ts <= ?` with string comparison. If user passes start="2026-09-28T23:53:09+08:00" and there are rows "2026-09-28T23:53:09.5+08:00" — wait that would be 6 digits ".500000". Row ".500000" > start "+08:00" lexicographically ("." > "+") — correct since 09.5 > 09.0. But if start = "2026-09-28T23:53:09.500000+08:00" and rows include "2026-09-28T23:53:09+08:00" (equal second, no fraction = 09.000000): row < start lexicographically ("+" < ".") → excluded. Correct: 09.0 < 09.5. And end bound: end="2026-09-28T23:53:09+08:00" should include row "2026-09-28T23:53:09+08:00" and exclude ".500000" row. Row ".500000" vs end "+08:00": ".500000" > "+08:00" → excluded. Correct. 

But careful: what about end="23:53:09.250000+08:00" and row "23:53:09.300000"? ".2..." < ".3..." fine. What about row "23:53:09.25" — but we always use 6 digits, so fine. OK — as long as normalization is applied to both stored values and query bounds (which I'll do — normalize start/end through the same function), string comparison is consistent... Is it? Start normalized "09.500000" vs row "09.000000"? row excluded correctly. Row "09.500000" vs start "09.500000" equal → included with >=. Good.

Hmm wait, one subtle issue: row "09" (no fraction) vs bound "09.000000"? If bound is normalized from a datetime with microsecond=0, it produces "09" (no fraction). Consistent. If the input had microseconds 0 explicitly, still microsecond == 0 → "09". So both forms arise only from microsecond==0, so no mismatch. Good. Actually to be safe and simple, maybe always store 6-digit microseconds? Then stored strings all fixed width, and normalized bounds also fixed width → totally consistent lexicographic ordering. Downside: human readability slightly worse. Hmm.

But mixed rows would only happen if... no, storage always uses same normalize function. So either all "09" style or all "09.000000" style consistently. Mixed forms can't occur in storage. And bounds normalized the same way. So the "conditional fraction" scheme is internally consistent. But is it? Consider end bound = "23:53:09" (no fraction) and row = "23:53:09.500000": row > end → excluded. But semantically, is the range [start, end] inclusive of the whole second 09? Ambiguous. That's a documented boundary semantic: inclusive on exact instant. Fine — document it.

I'll go with conditional microseconds. Actually, let me reconsider — simpler for reviewers and tests: normalize to seconds precision always? Loses sub-second dedup/ordering. Events from multiple sources within the same second would collapse ordering (id tiebreak handles ordering anyway — ORDER BY ts, id). Hmm, that's a point: ORDER BY ts, id gives stable ordering regardless.

I think keeping microseconds is better fidelity. Go with conditional.

Now the API. §1 contract is missing, so I define a reasonable one and flag it:

```python
class UnifiedEventStore:
    def __init__(self, db_path: str, *, logger=None): ...
    def init_schema(self) -> None  # CREATE TABLE IF NOT EXISTS unified_events ...
    def write_event(self, *, source, modality, ts=None, person="", room="", event_type="", payload=None) -> dict  # returns {"ok": True, "id": ...} or {"ok": False, "error": ...}
    def query_events(self, person=None, room=None, start=None, end=None, *, source=None, limit=..., order="asc") -> list[dict]
    def dual_write(self, ...)  # wrapper that never raises
```

Requirement says "双写函数 unified_store.write_event()" — acceptance #3 calls write_event the dual-write function. So write_event itself must never raise (fail-open, log only). Hmm, but then how do tests assert errors? It returns a dict with ok False. Acceptance mentions `result["ok"]` style in §4 ("不得出现只断言 result["ok"] is True 的空测试") — implying the return contract includes `ok`. So write_event returns `{"ok": bool, ...}`. Good, that hints the interface: functions return dicts with "ok".

So contract guess:
- `write_event(event: dict) -> dict` with keys? Or kwargs? The acceptance says "能接收 device/vision/perception/activity 四种来源" — a source field. I'll support both: `write_event(event: Mapping) -> dict` where event dict has keys source, modality, ts, person, room, event_type, payload. Hmm, which? Since §1 is empty, I'll define `write_event(event: Mapping[str, Any]) -> Dict[str, Any]` and also accept keyword args? Keep it simple: mapping-based plus optional kwargs override? That's over-engineering. Let me define:

```python
def write_event(self, event: Mapping[str, Any]) -> Dict[str, Any]:
```

And module-level convenience functions for MCP tool integration:

```python
def get_store(db_path=None) -> UnifiedEventStore  # singleton-ish
def write_event(event) -> dict
def query_events(person="", room="", start=None, end=None, **kw) -> list[dict]
```

The acceptance says `unified_store.write_event()` and `unified_store.query_events(person, room, start, end)` — module-level callables! "查询函数 unified_store.query_events(person, room, start, end) 返回统一格式列表". So module-level functions with positional args person, room, start, end. So I'll provide module-level `write_event(...)` and `query_events(person=None, room=None, start=None, end=None, ...)` delegating to a default store bound via `configure(db_path)` / `init_schema`.

Let me design module-level API:

```python
DEFAULT_DB_PATH  # from env var? or lazily from memory_agent.store? 
def configure(db_path: str) -> None
def get_store() -> UnifiedEventStore
def init_schema(db_path=None) -> None
def write_event(event: Mapping, *, db_path=None) -> dict
def query_events(person=None, room=None, start=None, end=None, *, source=None, modality=None, limit=None, order="asc", db_path=None) -> list[dict]
def dual_write(...)?
```

For db path default: since we can't import existing store.py safely? Actually we can import it — the constraint says don't modify it, not don't import. But since I can't see it, importing `memory_agent.store` for its DB path is risky. Better: use env var `MEMORY_AGENT_DB` else a local default `memory_agent.db`... Hmm. I'll do: default db path resolution order: explicit arg > `MEMORY_AGENT_DB_PATH` env > `Path(__file__).resolve().parents[2] / "data" / "memory_agent.db"`? Too speculative. Simpler: env `MEMORY_AGENT_DB_PATH`, else `./memory_agent.db` relative to CWD? I'll flag it in §7 as an open question and make it injectable everywhere. Actually a nice approach: try to read the path from the existing `store` module if available (e.g., `store.DB_PATH`), and fall back. That adds fragile coupling. Keep it explicit + env var + documented default; flag as open question.

Also "双写" (dual-write) means: writes go both to the legacy tables (events / perception_events) and to unified_events? Or unified_events written in addition to legacy writes in the caller? Since we must not modify existing store.py and the dual-write failure must not affect the main flow, the intended architecture is likely: legacy write path stays as-is (in store.py / MCP tools), and callers additionally invoke `unified_store.write_event()`; if it fails, log and continue. So `unified_store` is the "second write". I'll implement `write_event` to write only into unified_events and never raise (fail-open), returning `{"ok": False, "error": ...}` on failure. And also provide a `dual_write(...)` helper? The acceptance calls write_event the dual-write function. So write_event = the dual-write entry. Good — document that "双写" = 主流程写旧表 + 调用 write_event 写 unified_events.

Validation:
- source must be in enum; modality in enum. What if invalid? Fail-open means return {"ok": False, "error": ...} without raising — for validation errors, fail-closed within the write (don't write garbage), but fail-open for the caller (no exception). Document: invalid input → reject write, log warning, return ok False (fail-closed for data quality, fail-open for the caller's main flow). That's a nuance worth explaining in §7.
- person: coerce None → "" ; reject non-str? Coerce to str. Empty string = unidentified. Should we normalize whitespace? Strip? Stripping "  " → "" seems reasonable but might alter data. I'll coerce None→"", and require str (else str() it? or reject). Keep: if person is None → ""; if not isinstance(str) → TypeError → ok False. Simpler: `str(person)` if not None? Hmm. Decide: person must be str or None; None → ""; other types → validation error (ok False). Document.
- room: same as person but "" means unspecified room. Not specified in requirements — flag.
- payload: must be JSON-serializable mapping; store as JSON string with `ensure_ascii=False`, `sort_keys=True`? sort_keys helps determinism in tests. Yes, deterministic serialization: `json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`. Hmm, separators compact — fine. Default payload {} if None.

Timestamps: `ts` param accepts:
- None → now (UTC+8)
- datetime (naive → assume UTC+8; aware → convert to UTC+8)
- str ISO8601 with offset, or naive ISO string → assume UTC+8
- int/float epoch seconds? Maybe accept — flag as decision. I'll accept (int, float) as Unix epoch UTC seconds — convenient and unambiguous. Document.

Also, `Z` suffix handling → +00:00, then convert.

Return format of query_events: list of dicts (unified format), each with keys id, ts, source, modality, person, room, event_type, payload(dict). Ordering: ts ASC then id ASC. Limit default? Maybe 1000 with `limit=None` meaning no limit? For safety default 1000? Requirement doesn't say. I'll default `limit=None` → no limit? Hmm, safer to have a default cap but that might surprise. I'll set `limit: Optional[int] = None` (no cap) but document that callers should pass a limit; or default 1000. I'll do `limit=None` (unbounded) to make query semantics predictable and let MCP layer cap. Flag in §7.

Filtering semantics:
- person filter: exact match. What about "unidentified" query? `person=""` matches rows with person=''. But what if person is None → no filter. So `person=""` filters to unidentified rows, `person=None` no filter. That's a nice distinction and must be documented. Same for room.
- start/end: normalize through the same ts normalization; `WHERE ts >= start AND ts <= end` inclusive both ends. Document inclusive bounds.

Query return: what if db error? fail-open → return [] and log? Or raise? Query is read path; returning [] on error silently could hide problems. Requirement only says dual-write failure must not raise. For query, I'd say: raise? But then MCP tool breaks. Hmm — "失败/降级语义（哪些 fail-open、哪些 fail-closed）" is exactly the §7 question. My decision:
- write_event: never raises (fail-open), returns {"ok": False, "error": ...}.
- query_events: never raises either? Consistent "never break the main flow" philosophy... but silent empty results on DB corruption is bad. Compromise: query_events raises on invalid arguments (fail-closed on bad input: ValueError/TypeError — since the caller explicitly asked and it's a read API where silent [] would be misleading)... Hmm but then it differs from write. Actually let me think about who calls: MCP tools. If query raises, MCP tool handler might crash. But query arg validation errors are programming errors.

Decision:
- Argument validation errors in query_events → raise ValueError/TypeError (fail-closed; you asked a malformed query, you get told).
- DB/IO errors in query_events → return [] and log the exception? or raise? I'll say: DB errors during query are logged and re-raised... Hmm. Let me pick: fail-open with `[]` would violate "不得出现只断言 result['ok'] is True 的空测试" spirit? Not relevant.

I think a cleaner contract: `query_events` returns a list; on storage failure it logs and returns `[]` — consistent with "不影响主流程" philosophy and the fact that it returns a bare list (no ok channel). Document explicitly: callers that must distinguish "no data" from "query failed" should use `UnifiedEventStore.query_events(..., strict=True)`? That's extra API surface. Alternative: add optional `raise_on_error: bool = False`. Hmm, keep it minimal: bare list + log. And note it in §7 as a deliberate fail-open choice with the caveat. Actually I could make query_events return list and provide the store method with a `strict` flag defaulting False... I'll keep simple: log + return []. But add an internal way tests can verify the error path (e.g., monkeypatch connection to raise and assert [] returned and logger called).

Hmm, but there's a risk the acceptance harness (采信台) runs tests expecting exceptions. §1 contract missing — I'll just be explicit.

Now, connection management: use sqlite3 with `check_same_thread=False`? Each call opens a new connection (`sqlite3.connect(db_path)`) — simple, thread-safe-ish, stdlib only. For write, use a transaction. Or keep a persistent connection in the store object? Persistent connection with a lock is more efficient but riskier in multi-threaded MCP servers. Use per-call connection with context manager; set `conn.row_factory = sqlite3.Row`; `PRAGMA journal_mode=WAL`? Setting WAL changes the db file mode — acceptable, doesn't change table structure. Maybe set `PRAGMA busy_timeout=5000` to avoid "database is locked". These are pragmas, not table changes — allowed. But careful: WAL on a network filesystem (E:/NAS) can be problematic! The target repo is E:/NAS/memory-agent — NAS storage. WAL on NAS is known to be risky. So: do NOT enable WAL; use default journal (delete) mode, with busy_timeout. Good catch — mention in §7. Actually, should I even run pragmas? `busy_timeout` via `sqlite3.connect(..., timeout=...)` param — the timeout param sets busy timeout. Use `timeout=5.0`. Good, no pragmas needed.

Indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_unified_events_ts ON unified_events (ts);
CREATE INDEX IF NOT EXISTS idx_unified_events_person_ts ON unified_events (person, ts);
CREATE INDEX IF NOT EXISTS idx_unified_events_room_ts ON unified_events (room, ts);
CREATE INDEX IF NOT EXISTS idx_unified_events_source_ts ON unified_events (source, ts);
CREATE INDEX IF NOT EXISTS idx_unified_events_modality_ts ON unified_events (modality, ts);
```

Maybe 5 is fine; justify query shapes.

CHECK constraints on source/modality — nice for data integrity, and tests can verify. But careful: CHECK constraints that reject legacy-ish data? No migration, so fine. Also add `CHECK (person IS NOT NULL)` implicitly by NOT NULL.

Also `ts` CHECK? Could check format via GLOB but keep it simple; enforce in Python.

Table columns (final):

```sql
CREATE TABLE IF NOT EXISTS unified_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    source      TEXT    NOT NULL CHECK (source IN ('device','vision','perception','activity','llm')),
    modality    TEXT    NOT NULL CHECK (modality IN ('sensor','image','audio','text')),
    person      TEXT    NOT NULL DEFAULT '',
    room        TEXT    NOT NULL DEFAULT '',
    event_type  TEXT    NOT NULL DEFAULT '',
    payload     TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    NOT NULL
);
```

created_at = write wall time (UTC+8) — useful for audit/ordering when ts is backfilled. Justify.

Should I include `schema_version`? Not necessary; skip, mention.

Now the code. Let me write it carefully.

```python
"""MA-多模态统一数据模型 unified_store.py"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

logger = logging.getLogger("memory_agent.unified_store")

TZ_UTC8 = timezone(timedelta(hours=8), name="+08:00")

SOURCES = ("device", "vision", "perception", "activity", "llm")
MODALITIES = ("sensor", "image", "audio", "text")

SCHEMA_SQL = """..."""
INDEX_SQL = (...)

DB_PATH_ENV = "MEMORY_AGENT_DB_PATH"
DEFAULT_DB_PATH = "memory_agent.db"

IsoTimestamp = str
```

Functions:

```python
def _now_utc8() -> datetime: return datetime.now(TZ_UTC8)

def format_ts(dt) -> str:
    dt = _coerce_datetime(ts)
    dt = dt.astimezone(TZ_UTC8)
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    if dt.microsecond:
        return f"{base}.{dt.microsecond:06d}+08:00"
    return base + "+08:00"
```

Note: `strftime` on Windows for years < 1900 — not an issue.

Wait: for years before 1970 etc. fine.

`_coerce_datetime(value)`:
- None → now
- datetime: naive → replace(tzinfo=TZ_UTC8)
- date? skip.
- int/float: `datetime.fromtimestamp(value, TZ_UTC8)` — must guard range errors (OSError/ValueError/OverflowError) → raise ValueError with clear message.
- str: strip; if endswith 'Z' or 'z' → replace with '+00:00'; use `datetime.fromisoformat` (Python 3.7+ handles 'Z'? No — fromisoformat accepts 'Z' only in 3.11+. So manual replace). Also handle trailing '+0800' (fromisoformat handles). For naive string → assume UTC+8.
  - Also maybe support 'YYYY-MM-DD HH:MM:SS' (space separator) — fromisoformat in 3.11+ accepts; older needs replace(' ', 'T', 1). Do that for compatibility.
- else → TypeError.

Edge: empty string ts → ValueError (or now?). I'd raise ValueError for empty string (explicit bad input) — for write_event it becomes ok False. Hmm, or treat '' as now? Spec says person '' = unidentified; nothing about ts ''. Treat '' as invalid → ValueError. Document.

Then `normalize_ts(value) -> str` = format_ts(_coerce_datetime(value)).

Validation of source/modality: lowercase? Sources given lowercase. Should I lowercase input ("Device" → "device")? Normalizing case is friendly and unambiguous. Decision: case-insensitive match, store lowercase. Document. Hmm — or reject non-canonical? Normalizing is friendlier; enum values stored canonical. Go with case-insensitive + strip. Also strip whitespace.

person/room: `None → ""`; must be str else TypeError; strip? For person: don't strip (names may have meaning?) — but "  " as person is silly. Decision: strip whitespace, so "  " → "" = unidentified. Document. Hmm, is stripping risky? Person identifiers like "张三 " → "张三". Fine. Actually careful: could stripping collapse two distinct identities? Only if they differ by surrounding whitespace — acceptable normalization. Document it.

payload: None → {}; must be Mapping (dict) else TypeError? Or any JSON-serializable? Requirement 4 says "返回统一格式列表" — payload dict. Accept Mapping; also accept str containing JSON? Keep Mapping-only, document. json.dumps failure (non-serializable, e.g. datetime inside payload) → ValueError → ok False. Or attempt `default=str`? That silently coerces — I'd rather fail. Hmm, fail-open philosophy... For payload containing non-serializable values, using `default=str` keeps main flow going and preserves info as string. But then round-trip type is lost. Decision: `json.dumps(..., default=str)`? I'd rather strict serialize and reject — data quality. But "双写失败只记日志不抛异常（不影响主流程）" implies rejection just logs — fine, main flow unaffected since caller continues. So strict is safe. Decision: strict JSON serialization; failure → ok False + log. Document.

event_type: str, default ""; stripped? Sure, strip. Must be str or None.

Now `write_event` return dict:
```python
{"ok": True, "id": int, "ts": str, "event": {...normalized echo...}}?
```
Keep: `{"ok": True, "id": id, "ts": normalized_ts}`. And failure: `{"ok": False, "error": "message", "error_type": "validation"|"storage"}`. That helps tests assert meaningful things (not just ok True).

Hmm, `error_type` naming: maybe `"stage": "validation"|"storage"`. Fine, I'll include `"error"` and `"reason"` keys: `{"ok": False, "error": "...", "stage": "validation"}`.

Query API:

```python
def query_events(self, person=None, room=None, start=None, end=None, *, source=None, modality=None, event_type=None, limit=None, order="asc") -> List[Dict[str, Any]]
```

Requirement signature: `query_events(person, room, start, end)` — positional order matters. I'll make person, room, start, end the first four positional-or-keyword params, rest keyword-only. Module-level mirrors it.

Order: "asc"|"desc", default "asc". Validate.

Rows → dicts with payload parsed via json.loads (fallback: keep raw string if parse fails? If parse fails, log and set payload {} or raw string? Put `payload` as dict normally; on corrupt JSON, log warning and return `{"_raw": "<string>"}`? Hmm. Simpler: try json.loads; on failure log and use `{}`... that loses data. Use `{"_raw": raw}`? I'd do: if parse fails, keep `payload` as the raw string and log — but type inconsistency in unified format. Compromise documented: `payload` is a dict; on unparseable stored JSON, return `{"_raw": <raw string>}` and log a warning. Good, type stays dict. Tests can cover.

Now module-level default store:

```python
_default_store: Optional[UnifiedEventStore] = None
_default_lock = threading.Lock()

def get_default_store() -> UnifiedEventStore:
    global _default_store
    if _default_store is None:
        with _default_lock:
            if _default_store is None:
                _default_store = UnifiedEventStore(resolve_db_path())
    return _default_store

def configure(db_path) -> UnifiedEventStore: sets default store, returns it
def reset_default_store() -> None  # for tests
```

`resolve_db_path()`: explicit > env MEMORY_AGENT_DB_PATH > DEFAULT_DB_PATH ("memory_agent.db" in CWD). Flag.

write_event(event=None, **fields)? Let me define module-level:

```python
def write_event(event: Optional[Mapping[str, Any]] = None, **fields: Any) -> Dict[str, Any]:
```

Hmm, complexity. Acceptance #3: "双写函数 unified_store.write_event() 能接收 device/vision/perception/activity 四种来源" — they'll call `unified_store.write_event({...})` presumably with a dict, or with keywords. Supporting both is safer: if `event` is None, use `fields`; if both, merge fields over event. Actually a hybrid signature `def write_event(event=None, **fields)` handles both `write_event({"source": ...})` and `write_event(source=..., modality=...)`. Good defensive design given missing contract. Same for `query_events(person=None, room=None, start=None, end=None, **kw)` — that's already natural.

But careful: `write_event(source='device')` via fields — and what if someone calls `write_event({'source':'device'}, person='x')`? merge. Fine.

Also class method `write_event(self, event, **fields)`? Keep class method taking a mapping only plus optional overrides? Let me just have class `write_event(self, event: Mapping[str, Any]) -> Dict` and module-level accepts both forms by merging to a dict then delegating.

Hmm, but what if the harness calls `unified_store.write_event(source="device", modality="sensor", ...)`? Handled.

What if harness calls `UnifiedEventStore.write_event(...)`? Also exists.

Also `init_schema(db_path=None) -> None` module-level, and `UnifiedEventStore.init_schema()`.

And maybe `dual_write(...)` alias = write_event? Acceptance calls write_event the dual-write function; no separate dual_write needed. But a `dual_write` alias could help if harness calls it... speculative API creep. I'll add a thin alias `dual_write = write_event`? Might confuse. Skip; mention in §7? Nah — I could add `write_event` only. Keep focused. Hmm, but cheap insurance... No, keep the surface clean and documented.

Never-raise guarantee for write_event: wrap the whole body in try/except Exception → log `logger.exception(...)` and return ok False. But careful: KeyboardInterrupt/SystemExit are BaseException, not caught — good.

Should validation errors also log? Yes, `logger.warning` (not exception) for validation; `logger.exception` for storage.

Now tests. Need test files covering:
- schema creation SQL + indexes (query sqlite_master)
- write_event accepts 4 sources (+ llm) — assert row persisted with correct fields (not just ok True)
- source/modality validation rejected
- person '' = unidentified, None → ''
- timestamps normalized to +08:00 for various inputs (Z, +00:00 offset, naive, epoch, datetime, None)
- query_events filters person/room/start/end correctly, returns unified format list with parsed payload
- inclusive bounds semantics
- fail-open on storage error (e.g., db_path points to a directory or read-only location or invalid → write_event returns ok False, no exception; logger called)
- fail-open query returns [] on storage error
- doesn't modify existing tables: create fake legacy tables events/perception_events in a temp db with specific SQL, run init_schema, assert their sql in sqlite_master unchanged and columns unchanged.
- no new deps: hard to test; can test imports of unified_store are stdlib-only via checking module imports? Could add a test that scans `unified_store` module's `__globals__` modules against stdlib allowlist. Eh — could do a simple test asserting `unified_store` imports only from stdlib modules (inspect sys.modules names of its imported modules). Let me include a light test: parse the source file with ast and assert all top-level imports are in stdlib allowlist. That's cute and directly validates acceptance constraint "不引入新依赖". I like it.

Test framework: stdlib `unittest` (no new deps! pytest would be a dev dep... The repo may use pytest but "不引入新依赖（只用 sqlite3 + stdlib）" refers to runtime deps. Tests using unittest run with `python -m unittest` and also work under pytest. Safer: unittest, runnable both ways. Yes.

Where to put tests: `tests/test_unified_store.py`? Delivery checklist says "上述每条验收对应的测试文件" — plural files maybe one per acceptance. I could create `tests/test_unified_store_schema.py`, `tests/test_unified_store_write.py`, `tests/test_unified_store_query.py`, `tests/test_unified_store_compat.py` — mapping to acceptance items 1-5. That satisfies "每条验收对应的测试文件". But shared helpers then need a common module. Use `tests/unified_store_helpers.py`? Or put all in one file with multiple TestCase classes — simpler to run. Hmm, the checklist: "上述每条验收对应的测试文件" — literally "the test file(s) corresponding to each acceptance item". One file with clearly labeled test classes per acceptance item is defensible and easier. But to be safe and explicit, I'll split into a small package: `tests/test_unified_store.py` with classes named per acceptance? Let me do 2-4 files with a shared `tests/_unified_store_util.py`... that risks import issues when running `python -m unittest discover` (tests dir needs __init__ or path). If tests are run as `python -m pytest tests/` rootdir insertion handles it. With `python -m unittest discover -s tests`, the tests dir is added to sys.path... Actually `discover` inserts the start directory at sys.path[0] if it's not a package. So `import _unified_store_util` works. But under pytest with rootdir conftest, `tests/` typically added via rootdir? pytest inserts the test file's dir into sys.path when there's no __init__.py (rootdir-based "rootdir/conftest" — with the default `prepend` import mode, the basedir of the test file gets inserted into sys.path). So `import _unified_store_util` works in both. But fragile-ish.

Simplest robust choice: one test file `tests/test_unified_store.py` with a `helpers` mixin defined inside. Then mention mapping to acceptance criteria in the report and in docstrings. Plus maybe a second file `tests/test_unified_store_compat.py` for acceptance #5 (legacy tables untouched) and the no-new-deps check. Two files, each self-contained. That maps nicely: main file covers #1-#4, compat file covers #5 + dependency constraint. Good.

Wait — but I also need to import `src/memory_agent/unified_store`. Tests must add `src` to sys.path. In the test file: 

```python
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path: sys.path.insert(0, str(SRC))
from memory_agent import unified_store  # or import unified_store directly
```

But `memory_agent` package `__init__.py` may import things (agent_memory, store) that fail (e.g., missing deps or missing db). Safer: insert `src` into sys.path and `import unified_store`? But `unified_store` will be at `src/memory_agent/unified_store.py`. Importing `memory_agent.unified_store` runs `memory_agent/__init__.py`. If that imports MCP stuff not installed → ImportError. Risky since I can't see it. Options:
- In test, load the module directly via importlib.util.spec_from_file_location to bypass package __init__:

```python
import importlib.util
spec = importlib.util.spec_from_file_location("unified_store", ROOT / "src/memory_agent/unified_store.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
```

That's robust and avoids package __init__. But it means tests don't exercise "import via package" path. Alternatively add sys.path and try package import with fallback. I'll do: try `from memory_agent import unified_store` and fall back to importlib file loading. Hmm, complexity in tests. Let me do the importlib-by-file approach with a helper in each test file (or just do sys.path insert of `src` and `import unified_store`... but the file lives in `src/memory_agent/`, so `import unified_store` requires sys.path entry `src/memory_agent`).

Decision: tests use a small loader that inserts both `src` and `src/memory_agent` into sys.path and imports `unified_store` as a top-level module (bypassing package __init__), and I'll note that `unified_store` intentionally has no intra-package imports (stdlib only, self-contained), so this is safe and also proves the "no coupling/no new deps" property. Actually that's a nice property: unified_store should NOT import from store.py or agent_memory.py at all (we can't modify them and don't want coupling). But then db path default resolution can't come from store.py. Fine — env var + explicit path. Document.

Hmm, but a real integration would want the same DB as the main store. So the caller passes db_path (e.g., whatever store.py uses). Since contract is missing, I'll note "调用方应传入现有 DB 路径（如 store.py 的 DB_PATH）" as an integration note and flag as open question.

Also provide a documented usage example section (§ usage) with code.

Now, `py_compile` acceptance: my code must compile on the target (Python version unknown). Use `from __future__ import annotations` and avoid 3.9+ only syntax in runtime positions. `Dict`, `List`, `Optional` from typing (safe). `datetime.fromisoformat` behavior differs pre-3.11 → I handle Z and space manually. `zoneinfo` not used. Walrus? avoid. f-strings fine (3.6+). `str.removeprefix` is 3.9+ — avoid. Match statement — avoid. So target 3.7+ compatible. Note: `from __future__ import annotations` requires 3.7+. Fine.

Careful with `datetime.fromtimestamp(x, TZ_UTC8)` on Windows for negative/absurd values → OverflowError/OSError/ValueError → catch and raise ValueError.

Also, on Windows, `strftime("%Y")` for years < 1000 pads? Not a concern.

Let me also handle `ts` given as `datetime.date`? Skip.

Now let me write the actual code.

```python
"""MA 多模态统一数据模型：unified_events 表、双写写入与统一查询。

设计约束（见交付单 §2.2）：
- 只用 sqlite3 + stdlib，不引入任何新依赖；
- 不迁移旧数据、不删旧表、不改 events / perception_events 结构；
- 本模块不 import 现有 store.py / agent_memory.py，避免耦合与误改；
- 写路径失败 fail-open：只记日志、返回 ``{"ok": False, ...}``，绝不抛异常。

时间戳规范：
- 全部 ISO8601、UTC+8，形如 ``2026-09-28T23:53:09+08:00``；
- 亚秒非零时追加 6 位小数：``2026-09-28T23:53:09.123456+08:00``。
"""
```

Then constants, normalization, class, module-level API, `__all__`.

Let me write out the whole file mentally and check details.

```python
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

__all__ = [...]

logger = logging.getLogger("memory_agent.unified_store")

TZ_UTC8 = timezone(timedelta(hours=8))

SOURCES: Tuple[str, ...] = ("device", "vision", "perception", "activity", "llm")
MODALITIES: Tuple[str, ...] = ("sensor", "image", "audio", "text")

SOURCE_SET = frozenset(SOURCES)  # or just check in SOURCES
MODALITY_SET = ...

DB_PATH_ENV = "MEMORY_AGENT_DB_PATH"
DEFAULT_DB_NAME = "memory_agent.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS unified_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,
    source       TEXT    NOT NULL CHECK (source IN ('device','vision','perception','activity','llm')),
    modality     TEXT    NOT NULL CHECK (modality IN ('sensor','image','audio','text')),
    person       TEXT    NOT NULL DEFAULT '',
    room         TEXT    NOT NULL DEFAULT '',
    event_type   TEXT    NOT NULL DEFAULT '',
    payload      TEXT    NOT NULL DEFAULT '{}',
    created_at   TEXT    NOT NULL
)
"""

INDEX_SQL: Tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_unified_events_ts ON unified_events (ts)",
    "CREATE INDEX IF NOT EXISTS idx_unified_events_person_ts ON unified_events (person, ts)",
    "CREATE INDEX IF NOT EXISTS idx_unified_events_room_ts ON unified_events (room, ts)",
    "CREATE INDEX IF NOT EXISTS idx_unified_events_source_ts ON unified_events (source, ts)",
    "CREATE INDEX IF NOT EXISTS idx_unified_events_modality_ts ON unified_events (modality, ts)",
)
```

Timestamp helpers:

```python
def coerce_datetime(value: Any) -> datetime:
    """把 None / datetime / 数字 / ISO8601 字符串 转成带时区的 datetime（UTC+8 语义见文档）。"""
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
            raise ValueError("ts 不能是空字符串（要“现在”请传 None）")
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        text = text.replace(" ", "T", 1)  # 'YYYY-MM-DD HH:MM:SS' -> ISO 'T'
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("ts 不是合法 ISO8601: %r" % (value,)) from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_UTC8)
        return dt
    raise TypeError("ts 类型不支持: %r" % (type(value).__name__,))
```

Careful: `text.replace(" ", "T", 1)` — if string has "+08:00 " trailing space already stripped. But what about "2026-09-28 23:53:09 +08:00"? fromisoformat may fail. Edge, ignore.

Also strings like "2026-09-28" (date only) → fromisoformat gives datetime at midnight naive → treat as UTC+8 midnight. Acceptable, document? Just mention accepted forms.

Note `datetime.fromisoformat` in 3.7-3.10 requires exactly ISO formats it knows: "2026-09-28T23:53:09+08:00" ok; "2026-09-28T23:53:09.123456+08:00" ok; "+0800" ok.

```python
def format_ts(value: Any) -> str:
    dt = coerce_datetime(value).astimezone(TZ_UTC8)
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    if dt.microsecond:
        return "%s.%06d+08:00" % (base, dt.microsecond)
    return base + "+08:00"
```

Hmm `%Y` on year < 1000 on Windows gives "999"? Edge; ignore.

Wait: `astimezone(TZ_UTC8)` on a naive datetime raises? No — we made it aware. Fine.

Validation helpers:

```python
def _norm_enum(value, *, field, allowed, default=None): ...
def _norm_text(value, *, field) -> str  # person/room/event_type
```

Let me write:

```python
def _require_str_or_none(value, field):
    if value is None: return None
    if isinstance(value, str): return value
    raise TypeError("%s 必须是 str 或 None，收到 %s" % (field, type(value).__name__))

def _norm_person(value) -> str:
    v = _require_str_or_none(value, "person")
    return "" if v is None else v.strip()

def _norm_room(value) -> str: same

def _norm_event_type(value) -> str: same

def _norm_enum(value, field, allowed):
    v = _require_str_or_none(value, field)
    if v is None:
        raise ValueError("%s 必填，允许值: %s" % (field, ", ".join(allowed)))
    v = v.strip().lower()
    if v not in allowed:
        raise ValueError("%s 非法: %r，允许值: %s" % (field, value, ", ".join(allowed)))
    return v

def _norm_payload(value) -> str:
    if value is None: return "{}"
    if not isinstance(value, Mapping): raise TypeError("payload 必须是 mapping/dict")
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("payload 不是合法 JSON 可序列化对象: %s" % exc) from exc
```

allow_nan=False avoids NaN/Infinity non-standard JSON. Good — document.

Normalization result: an internal dict/tuple. I'll build an "normalized event" dict and serialize payload.

Class:

```python
class UnifiedEventStore:
    def __init__(self, db_path, *, connection_timeout=5.0):
        self.db_path = str(db_path)
        self.connection_timeout = float(connection_timeout)
        self._init_lock = threading.Lock()
        self._schema_ready = False

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=self.connection_timeout)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self._init_lock:
            if self._schema_ready:
                return
            conn = self._connect()
            try:
                with conn:   # hmm, DDL in transaction
                    conn.execute(SCHEMA_SQL)
                    for sql in INDEX_SQL: conn.execute(sql)
            finally:
                conn.close()
            self._schema_ready = True
```

Careful: `with conn` on DDL executes and commits. Fine. But if `init_schema` is called per write, the `_schema_ready` flag avoids repeated DDL. But two processes... `CREATE TABLE IF NOT EXISTS` is idempotent. OK.

Wait — is it safe for write_event to auto-init schema? Yes: dual-write into a fresh DB should create the table. But constraint "不删旧表" fine. Also creating the unified_events table in the existing db file is exactly the ask.

But careful with the "don't modify existing tables" acceptance: init_schema only runs CREATE IF NOT EXISTS on unified_events and its indexes. Good.

Write:

```python
    def write_event(self, event: Mapping[str, Any]) -> Dict[str, Any]:
        try:
            normalized = normalize_event(event)   # may raise
        except (TypeError, ValueError) as exc:
            logger.warning("unified_store.write_event 参数非法，已跳过双写: %s", exc)
            return {"ok": False, "error": str(exc), "stage": "validation"}
        try:
            self.init_schema()
            conn = self._connect()
            try:
                cur = conn.execute(
                    "INSERT INTO unified_events (ts, source, modality, person, room, event_type, payload, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (...)
                )
                conn.commit()
                row_id = cur.lastrowid
            finally:
                conn.close()
        except Exception as exc:   # noqa: BLE001 - 双写 fail-open
            logger.exception("unified_store.write_event 双写失败，已忽略（不影响主流程）: %s", exc)
            return {"ok": False, "error": str(exc), "stage": "storage"}
        return {"ok": True, "id": row_id, "ts": normalized["ts"], "event": normalized}
```

Hmm returning "event" echo — useful for tests and callers. But maybe overkill; keep it, cheap.

Wait: is `sqlite3.connect` with a nonexistent directory path raising? Yes, "unable to open database file" (sqlite3.OperationalError) → caught → ok False. Good test for fail-open: db_path = str(tmp / "no_such_dir" / "x.db"). Also read-only dir on POSIX; on Windows readonly is tricky. The no-such-dir approach is cross-platform. 

For query fail-open test: same trick → [].

normalize_event:

```python
def normalize_event(event: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(event, Mapping):
        raise TypeError("event 必须是 mapping/dict")
    unknown = set(event) - ALLOWED_KEYS
    if unknown: logger.debug? or raise ValueError?
```

Should unknown keys be rejected or ignored? The dual-write could receive richer dicts from callers (e.g., legacy event rows with extra fields). Fail-open philosophy → ignore unknown keys but log debug. But silently dropping data... maybe put unknown keys into payload? That's nice: if payload is absent and unknown keys exist, fold them into payload? Over-clever. Decision: ignore unknown keys, log a warning listing them (so nothing silently disappears). Document. Hmm — or reject? If a caller passes a legacy row dict with `id`, `timestamp`... rejecting would break dual-write integration. Ignore + warn is friendlier. But then `timestamp` vs `ts` mismatch would silently produce now() timestamp — bad! Accept alias `timestamp` for `ts`? That helps integrate legacy rows. Hmm, speculation. I'll accept `ts` and `timestamp` as aliases (documented), since the legacy `events` table almost certainly has a timestamp column. And unknown keys → ignored with warning. Good balance.

So normalize_event returns:

```python
{
  "ts": str, "source": str, "modality": str, "person": str, "room": str,
  "event_type": str, "payload": dict (original) , "payload_json": str, "created_at": str
}
```

Let me define a `_Normalized` plain dict with keys: ts, source, modality, person, room, event_type, payload_json, created_at. And for return echo, maybe include payload as dict. Keep simple: return dict with keys id, ts, source, modality, person, room, event_type, payload(dict). i.e. the unified row format. That makes write return echo in the same "统一格式" as query — nice symmetry. Then tests can assert `result["event"]["source"] == "device"`.

Hmm, but `ok`, `id`... Let me define the row format function `_row_to_event(row) -> Dict` used by query, and write returns `{"ok": True, "id": ..., "event": {...}}` where event is the unified dict (with id). Fine.

Query:

```python
    def query_events(self, person=None, room=None, start=None, end=None, *, source=None, modality=None, event_type=None, limit=None, order="asc") -> List[Dict[str, Any]]:
```

Validation of filters (raise ValueError/TypeError — fail-closed on malformed query):
- person: None → no filter; "" → filter person=''; else stripped exact match. Should filter matching be on stripped? Yes, since writes strip.
- room: same.
- start/end: normalize to ts strings via format_ts (raises on bad input).
- Also enforce start <= end? If start > end, return [] (log debug) rather than raise? I'd return [] — reasonable. Or raise ValueError? Simpler: return [] — documented.
- source/modality: validate enum (raise on invalid). Accept list/tuple of sources? Keep single value or None. Maybe accept str or sequence? Keep str|None for simplicity. Hmm, could be handy but skip.
- limit: None or positive int; bool excluded; 0 → returns []? Negative → ValueError. Let me: `limit` must be None or int >= 0; 0 → empty list.
- order: "asc"|"desc" (case-insensitive), default "asc".

Build SQL:

```sql
SELECT id, ts, source, modality, person, room, event_type, payload
FROM unified_events
WHERE 1=1 [AND person = ?] [AND room = ?] [AND ts >= ?] [AND ts <= ?] [AND source = ?] [AND modality = ?] [AND event_type = ?]
ORDER BY ts {ASC|DESC}, id {ASC|DESC}
[LIMIT ?]
```

Note ORDER BY ts then id — but for desc, id desc too for stability.

Error handling: wrap DB errors → log exception, return []. But validation errors raise before the try.

Hmm wait: should validation errors raise or return []? I said fail-closed (raise). But consistency with write_event which returns ok False... Different return type (list vs dict) gives no error channel, so raising for programmer error is right. Document clearly.

Row to event:

```python
def _row_to_event(row) -> Dict[str, Any]:
    raw = row["payload"]
    try:
        payload = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        logger.warning(...)
        payload = {"_raw": raw}
    if not isinstance(payload, dict):  # JSON could be a list/number
        payload = {"_value": payload}? 
```

Hmm, since we only write dicts, stored payload is always a JSON object. If someone hand-inserts a non-object, wrap: `{"_value": payload}`. Or just keep as-is and type would be inconsistent. I'll wrap non-dict into {"_value": ...} to keep the unified format (payload is always dict) and log. Fine.

Actually simpler: if json.loads returns non-dict → payload = {"_raw": raw} with warning. Consistent with the parse-failure path. Good, less code.

Return dict keys: id, ts, source, modality, person, room, event_type, payload. Should it include created_at? Could include for completeness; the "统一格式" — I'll include `created_at` too since it exists in table. Hmm, tests would assert set of keys. I'll include it and document. Actually, let me keep the returned unified format explicit and stable: keys `id, ts, source, modality, person, room, event_type, payload` — and omit created_at? created_at is an implementation detail (audit). I'll include it — more useful than harmful. Decide: include. Document the exact key order/set.

Hmm, one more consideration: acceptance #4 "query_events(person, room, start, end) 返回统一格式列表". A harness might call positionally `query_events("alice", "kitchen", "2026-01-01T00:00:00+08:00", "2026-01-02T00:00:00+08:00")`. Works.

Might a harness call with positional-only 4 args and expect no exception if person is None? Works.

Module-level functions:

```python
def resolve_db_path(db_path=None) -> str:
    if db_path: return str(db_path)
    env = os.environ.get(DB_PATH_ENV)
    if env: return env
    return DEFAULT_DB_NAME

def get_store(db_path=None) -> UnifiedEventStore:  # default store or new?
```

Design: `configure(db_path) -> UnifiedEventStore` sets `_default_store`. `get_store() -> UnifiedEventStore` returns default (creating with resolve_db_path()). Module-level `write_event(...)` / `query_events(...)` accept optional `db_path=` kwarg; if provided and different from default... complexity. Simplify: module-level functions delegate to `get_store()` unless `db_path` is given, in which case they use a `UnifiedEventStore(db_path)` (fresh instance, schema idempotent). That's predictable and test-friendly (tests can pass tmp db paths explicitly).

Hmm, fresh instance per call means init_schema DDL each call — cheap-ish but a bit wasteful. Cache stores per path in a dict with lock. Nice: `_stores: Dict[str, UnifiedEventStore]`. Let me implement `get_store(db_path=None)` with a per-path cache. Keeps things efficient and simple.

```python
_store_cache: Dict[str, UnifiedEventStore] = {}
_cache_lock = threading.Lock()

def get_store(db_path=None) -> UnifiedEventStore:
    path = resolve_db_path(db_path)
    with _cache_lock:
        store = _store_cache.get(path)
        if store is None:
            store = UnifiedEventStore(path)
            _store_cache[path] = store
        return store

def reset_stores() -> None:  # 测试用
    with _cache_lock: _store_cache.clear()
```

Module-level write_event:

```python
def write_event(event=None, *, db_path=None, **fields) -> Dict[str, Any]:
    payload_event = dict(event) if event is not None else {}
    if fields: payload_event.update(fields)
    return get_store(db_path).write_event(payload_event)
```

Hmm signature `write_event(event=None, *, db_path=None, **fields)` — but harness might call `write_event(source=..., modality=..., ts=...)` positionally? Unlikely; sources need names. But what about `write_event({...}, "extra")`? no.

Wait, but if someone calls `write_event(event_dict)` where event_dict has "db_path"? Not a field — unknown key ignored with warning. Fine.

Careful: what if harness calls `unified_store.write_event(source="device", modality="sensor")` and my `fields` collects them. Good.

But there's a subtlety: if event is a mapping and fields given, fields override. Fine.

Also, what if harness passes a non-mapping positional (e.g. a list)? `dict(event)` raises ValueError → should return ok False not raise! Since write_event is the never-raise entry. So wrap: 

```python
def write_event(event=None, *, db_path=None, **fields):
    try:
        merged = _merge_event(event, fields)
    except (TypeError, ValueError) as exc:
        logger.warning(...); return {"ok": False, "error": str(exc), "stage": "validation"}
    return get_store(db_path).write_event(merged)
```

Also get_store could raise? resolve path fine; UnifiedEventStore ctor trivial. But be safe: wrap whole module-level write_event body in try/except to honor "绝不抛异常". Yes — belt and braces: catch Exception at module level too.

Module-level query_events:

```python
def query_events(person=None, room=None, start=None, end=None, *, source=None, modality=None, event_type=None, limit=None, order="asc", db_path=None) -> List[Dict[str, Any]]:
    return get_store(db_path).query_events(...)
```

Validation raises propagate (fail-closed). Storage errors → []. Fine.

Also add `init_schema(db_path=None) -> None` module-level.

Also maybe `insert_event` alias? no.

Let me now also think about `created_at` format: `format_ts(None)` = now UTC+8. Same format. Good.

Now tests. Let me write test files.

File 1: `tests/test_unified_store.py` — covering acceptance 1-4 + behavior requirements. Uses unittest, tempfile.TemporaryDirectory.

Helper at top:

```python
import importlib.util, pathlib, sys, unittest, tempfile, sqlite3, logging, json, os
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "memory_agent" / "unified_store.py"

def _load_module():
    spec = importlib.util.spec_from_file_location("memory_agent_unified_store", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

unified_store = _load_module()
```

Wait — should I instead do sys.path insertion of src and `from memory_agent import unified_store`? The risk of package __init__ side effects is real. But the harness (采信台) may run tests differently... my tests just need to run. I'll use importlib with a fallback to normal import? Just importlib — deterministic and self-contained. But note: if `memory_agent/__init__.py` exists and the harness runs pytest with rootdir having conftest that imports stuff... not my problem.

Hmm, but there's a subtlety: acceptance #1 is `py_compile src/memory_agent/unified_store.py` — they'll run that themselves. Fine.

Test cases (file 1):

class TestSchema (acceptance 1 & 2):
- test_py_compile_module: `py_compile.compile(str(MODULE_PATH), doraise=True)` → asserts no exception. (Acceptance 1 verifiable.)
- test_schema_creates_table_and_indexes: init store on tmp db; query sqlite_master for table unified_events; assert SQL contains required columns (parse via PRAGMA table_info for names + types + notnull), CHECK constraints present in sqlite SQL text, indexes exist (5 names), and index SQL references unified_events.
- test_init_schema_idempotent / does not touch other tables (maybe in file 2).
- test_no_existing_tables_dropped: create events/perception_events before, run init_schema, assert still there. (file 2)

class TestWrite (acceptance 3):
- test_write_all_sources: for each source in 4 (+ llm) write an event, then read raw rows from table, assert source values and count; assert returned ids increasing and events echoed with right fields.
- test_write_returns_unified_echo: assert result["event"] keys and payload dict round-trip, person '' etc.
- test_person_empty_means_unidentified: write without person → stored ''; write person=None → ''; write person='  张三 ' → '张三'.
- test_source_modality_validation: invalid source → ok False + error mentions allowed values + no row inserted (count 0); also case-insensitive normalization 'DEVICE' → 'device'; missing source → ok False.
- test_timestamp_normalization: parametrized-ish loop: inputs (None, epoch, datetime naive, datetime aware UTC, '...Z', '...+00:00', '...+08:00', '... with microseconds') assert stored ts matches expected '+08:00' string and lex ordering preserved. Careful with None → "now" range check.
- test_write_never_raises_on_storage_failure: store with db_path in nonexistent dir → result ok False, stage storage, and no exception. Assert logger.exception called via assertLogs? assertLogs on logger name with level ERROR. Yes: `with self.assertLogs("memory_agent.unified_store", level="ERROR") as cm: ...` then assert any("双写" in msg...). Good — this validates "只记日志不抛异常".
- test_write_never_raises_on_bad_payload: payload with unserializable object (e.g., {"x": object()}) → ok False stage validation.
- test_dual_write_failure_does_not_block_main_flow: simulate main flow: write legacy row (create legacy table ourselves), then call write_event with invalid input; assert legacy row still there and function returned dict. Eh — the "main flow" is the caller; the assertion is really "write_event doesn't raise". Cover with the storage failure test + a test that a subsequent write succeeds after failure (recovery).

class TestQuery (acceptance 4):
- setUp: tmp db + seed known events across persons/rooms/times/sources.
- test_query_returns_unified_format: assert exact key set, payload is dict, ts format regex, ordering by ts asc.
- test_query_by_person / room / range / combined; empty-string person filter returns only unidentified; person=None returns all.
- test_range_bounds_inclusive: event exactly at start and exactly at end included; one microsecond outside excluded.
- test_query_limit_and_order: limit=2 desc returns last two.
- test_query_returns_list_not_dict: `isinstance(result, list)` and items dicts.
- test_query_storage_failure_returns_empty_list: store with bad path, assert [] and log.
- test_query_invalid_argument_raises: person=123 → TypeError; start="not-a-date" → ValueError; order="sideways" → ValueError; limit=-1 → ValueError.
- test_query_no_filter_returns_all / returns [] when no rows.

File 2: `tests/test_unified_store_compat.py`:
- test_legacy_tables_untouched: create events & perception_events with representative SQL (my own stand-in since I can't see the real one — note that), capture sqlite_master sql + PRAGMA table_info + indexes, run unified_store.init_schema, re-capture, assert unchanged; assert only new objects are unified_events + its 5 indexes.
- test_no_new_dependencies: ast-parse the module source, collect top-level imports (Import / ImportFrom), assert all module names are stdlib (allowlist) and none are from memory_agent/relative.
- test_module_has_no_circular_or_local_imports: assert no `from .` or `from memory_agent` imports.
- maybe test write_event doesn't touch legacy tables (writes only unified_events): after writes, legacy table contents unchanged.

Also a "usage example" — deliver as a section in the report with code (README-style). Maybe also `examples/unified_store_usage.py`? The delivery checklist mentions only the module and test files. I'll give the usage example inline in the answer (and optionally mention it can be saved). Keep the answer focused: design decisions (§7), code, tests, usage, open questions, and a mapping to acceptance items.

Length: this will be long. That's expected for this kind of ticket. But I should keep it organized and not bloat. Let me be reasonably complete but tight. The code file is the biggest chunk (~350 lines). Tests maybe ~300 lines across 2 files. Plus design notes. Total maybe 900-1100 lines of markdown. That's a lot but justified. Maybe I can trim tests to essentials while covering all required paths. Let me aim for thorough but not padded.

Let me now write the final code carefully, checking for bugs.

```python
# -*- coding: utf-8 -*-
"""..."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

__all__ = [
    "TZ_UTC8", "SOURCES", "MODALITIES", "SCHEMA_SQL", "INDEX_SQL",
    "UnifiedEventStore", "normalize_event", "format_ts", "coerce_datetime",
    "resolve_db_path", "get_store", "reset_stores", "init_schema",
    "write_event", "query_events",
]

logger = logging.getLogger("memory_agent.unified_store")

TZ_UTC8 = timezone(timedelta(hours=8), "+08:00")

SOURCES: Tuple[str, ...] = ("device", "vision", "perception", "activity", "llm")
MODALITIES: Tuple[str, ...] = ("sensor", "image", "audio", "text")
```

Note: `timezone(timedelta(hours=8), "+08:00")` fine.

Normalize functions... Let me write `normalize_event(event) -> Dict[str, Any]` returning:

```python
{
    "ts": str,
    "source": str,
    "modality": str,
    "person": str,
    "room": str,
    "event_type": str,
    "payload": dict,       # 原样
    "payload_json": str,
    "created_at": str,
}
```

Constants: `EVENT_FIELDS = ("ts","source","modality","person","room","event_type","payload")`, `ACCEPTED_KEYS = EVENT_FIELDS + ("timestamp","created_at"?)`. Hmm created_at input? Not needed; ignore. Accepted keys: ts, timestamp, source, modality, person, room, event_type, payload. Unknown → warn.

Now writing write SQL insert columns: (ts, source, modality, person, room, event_type, payload, created_at).

Full code below (drafting):

```python
def coerce_datetime(value: Any) -> datetime: ...
def format_ts(value: Any = None) -> str: ...
def normalize_event(event: Mapping[str, Any]) -> Dict[str, Any]: ...
def _row_to_event(row: sqlite3.Row) -> Dict[str, Any]: ...
```

normalize_event details:

```python
def normalize_event(event):
    if not isinstance(event, Mapping):
        raise TypeError("event 必须是 mapping/dict，收到 %s" % type(event).__name__)
    data = dict(event)
    unknown = sorted(k for k in data if k not in _ACCEPTED_KEYS)
    if unknown:
        logger.warning("unified_store: 忽略未知字段 %s", ", ".join(unknown))
    raw_ts = data["ts"] if data.get("ts") is not None else data.get("timestamp")
    ts = format_ts(raw_ts)
    ...
```

Careful: `data.get("ts") is not None else data.get("timestamp")` — if ts missing → None → falls back to timestamp → if also missing → None → now. But if user explicitly passes ts=None and timestamp set → uses timestamp. Fine.

Hmm, but `format_ts(None)` = now, so no need for the None dance except the alias. Simplify:

```python
    if "ts" in data and data["ts"] is not None:
        raw_ts = data["ts"]
    else:
        raw_ts = data.get("timestamp")
    ts = format_ts(raw_ts)
```

Then created_at = format_ts(None) (now).

payload normalization returns (payload_dict, payload_json).

Now `write_event` on the class: insert and commit. Use `with conn:` for transaction? Explicit `conn.commit()` is clear. Actually use try/except sqlite3.Error and let general Exception also catch. My outer `except Exception` covers everything.

Note: `cur.lastrowid` for AUTOINCREMENT INTEGER PRIMARY KEY works.

Now query builder. Let me write:

```python
    def query_events(self, person=None, room=None, start=None, end=None, *, source=None, modality=None, event_type=None, limit=None, order="asc"):
        person_f = None if person is None else _norm_text(person, "person")  # '' allowed
        room_f = None if room is None else _norm_text(room, "room")
        start_f = None if start is None else format_ts(start)
        end_f = None if end is None else format_ts(end)
        source_f = None if source is None else _norm_enum(source, "source", SOURCES)
        modality_f = None if modality is None else _norm_enum(modality, "modality", MODALITIES)
        event_type_f = None if event_type is None else _norm_text(event_type, "event_type")
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise TypeError("limit 必须是 int 或 None")
            if limit < 0: raise ValueError("limit 不能为负")
        order_f = _norm_order(order)
        if start_f is not None and end_f is not None and start_f > end_f:
            logger.debug(...); return []
        ...
```

Wait, `_norm_text` for person in query: `person=None` means no filter but `person=""` means filter to unidentified. So `_norm_text(person)` returns "" for "" — fine, and None handled separately. But careful: `_norm_text(None)` returns "" in my write path (None→""). In query I check `person is None` first. Good.

Hmm — subtle: what about `person` given as `"  "` in query → strip → "" → filters to unidentified. Consistent with write normalization. Good, document.

SQL and params assembly, then:

```python
        try:
            self.init_schema()
            conn = self._connect()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        except Exception:
            logger.exception("unified_store.query_events 查询失败，返回空列表")
            return []
        return [_row_to_event(r) for r in rows]
```

Hmm, careful: `init_schema` raising → caught here → []. But init_schema inside try is fine.

But wait: should query_events create the table if missing (init_schema)? For a read API, creating is harmless and idempotent; it also ensures PRAGMA/queries work. But it means a query on a fresh db creates the table. Acceptable. Alternatively catch "no such table" → []. I'll call init_schema (documented: 查询前确保存在表，空库返回 []).

_row_to_event:

```python
def _row_to_event(row):
    raw = row["payload"]
    payload = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("unified_events.id=%s 的 payload 不是合法 JSON，已降级为 _raw", row["id"])
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
        else:
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
```

Keys of a sqlite3.Row accessed by name — fine.

Now module-level. Also a `read`/`list` convenience? no.

Edge: `resolve_db_path(db_path)` when db_path is "" → falls to env → document; maybe treat empty string as error? If db_path is falsy → env/default. Fine.

One more design point: thread-safety and multi-process. Per-call connections handle threads; concurrent writers rely on sqlite locking with timeout. Document.

Another point for §7: why no WAL (NAS filesystem on E:/NAS) — mention.

Also §7: why CHECK constraints (data integrity at the storage layer as second line of defense), why person NOT NULL DEFAULT '' (never NULL so `person = ''` queries are reliable), why created_at, why module-level + class API (MCP tools call module-level; tests embed class), why fail-open on write and fail-closed on query args, why `payload` JSON text (no JSON1 dependency/extension), why composite indexes, why ORDER BY ts,id, why limit default None, why `fromtimestamp` for epoch, why naive = UTC+8.

Also compatibility boundary with §1: since §1 is empty, list explicitly what I assumed:
- DB engine is sqlite3 and legacy tables named `events` / `perception_events` (from acceptance #5).
- The dual-write is invoked from existing write paths (store.py / MCP tools) with a mapping; my module never imports them.
- DB path must be passed in or set via env var.
- Return contract uses `{"ok": ...}` (implied by §4's "只断言 result["ok"] is True" wording).
- Function names `write_event` / `query_events(person, room, start, end)` (from acceptance #3/#4).

Open questions (§7 "不要猜，列出来"):
1. §1 现有代码/契约是空的 — I cannot align "逐一对上" with an empty contract. List what needs confirming: exact signatures, whether write_event takes dict or kwargs, whether it should also write legacy tables (double write direction), return shapes.
2. DB path source of truth (store.py DB_PATH?) — env var default used.
3. Whether `llm` source is actually written by some module (enum includes it but acceptance #3 lists only 4) — implemented 5, tests cover 5.
4. Time zone policy for naive timestamps — assumed UTC+8.
5. person matching: exact vs prefix/fuzzy; whether empty-string filter semantics wanted.
6. Room field not mentioned in the gap list but present in query signature — added as NOT NULL DEFAULT ''.
7. Whether payload should preserve unknown fields automatically.
8. Whether a limit cap should be default-applied for MCP safety.
9. Whether `id` should be UUID/text instead of autoincrement integer (multi-writer dedup across processes).
10. Legacy table names/columns unknown → compat test uses stand-in DDL; must be re-run against real schema.
11. Python version floor (I targeted 3.7+; `datetime.fromisoformat` limitations pre-3.11 handled manually).
12. Whether timestamps should be required to be UTC+8-only input (reject offsets) or accept+convert — I convert.

Also mention what I did NOT do (per §4's requirement to name stubs): everything requested in the delivery list is implemented; but items that cannot be done because §1 is empty — e.g. exact-contract alignment, integrating into store.py/MCP tools (can't modify them; and the "双写" call site needs to be added by whoever owns those files — I provide the snippet but cannot edit those files per constraint).

Hmm — actually wait. Constraint says store.py's table SQL, MCP tool signatures, and agent_memory recall logic must not change. Adding a call to `unified_store.write_event(...)` inside MCP tool *bodies* would be a change to those files... "一个字都不许改：store.py 现有建表 SQL、现有 MCP 工具签名、agent_memory.py 召回逻辑" — signatures, not whole files. So adding dual-write calls in tool bodies is allowed but requires seeing those files. Since §1 is empty, I can't. I must flag: the call site snippet is provided as an example, to be inserted by the integrator (this is the honest "未完成" item). Good — that's exactly the kind of honesty §4 demands.

Let me also double check acceptance #3 wording: "双写函数 unified_store.write_event() 能接收 device/vision/perception/activity 四种来源" — tests cover those four plus llm.

Now let me write everything concretely and correctly. I'll be careful with Python syntax since it must compile.

Writing final `unified_store.py`:

```python
# -*- coding: utf-8 -*-
"""MA 多模态统一数据模型：unified_events 表 + 双写写入 + 统一查询。

...docstring...
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

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

SCHEMA_SQL = """..."""

INDEX_SQL: Tuple[str, ...] = (...)
```

Wait: `from typing import ... Mapping` — `isinstance(event, Mapping)` with typing.Mapping works at runtime in 3.5+? `isinstance(x, typing.Mapping)` is allowed (typing generic aliases support isinstance for non-subscripted). Yes, `isinstance({}, typing.Mapping)` works. But cleaner: `from collections.abc import Mapping as _MappingABC` for isinstance and use typing.Mapping in annotations. To avoid ambiguity, I'll import `Mapping` from `collections.abc` and use it in annotations too (works at runtime for annotations as strings due to `from __future__ import annotations`... but if someone evaluates them... fine). Hmm, with `from __future__ import annotations`, annotations are strings, so `collections.abc.Mapping` in annotations is fine even on 3.7.

Let me use:
```python
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Tuple
```
And annotations use `Mapping[str, Any]` — as string annotation, fine.

Careful: `Dict[str, Any]` etc. fine.

Now, writing out all the code for real. I'll produce it in the final answer directly, but let me pre-verify tricky spots.

`format_ts`:
```python
def format_ts(value: Any = None) -> str:
    dt = coerce_datetime(value)
    dt = dt.astimezone(TZ_UTC8)
    head = dt.strftime("%Y-%m-%dT%H:%M:%S")
    if dt.microsecond:
        return "%s.%06d+08:00" % (head, dt.microsecond)
    return head + "+08:00"
```

`coerce_datetime` with bool check before int check (bool is subclass of int). Good.

Also `datetime.fromtimestamp(value, TZ_UTC8)` for float — fine.

String parsing:
```python
        text = value.strip()
        if not text:
            raise ValueError("ts 不能是空字符串（需要“此刻”请传 None）")
        if text[-1] in ("Z", "z"):
            text = text[:-1] + "+00:00"
        if " " in text and "T" not in text:
            text = text.replace(" ", "T", 1)
```
Hmm, "2026-09-28 23:53:09 +08:00"? edge. Keep the simple `text.replace(" ", "T", 1)` only when no 'T'. Fine.

```python
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("ts 不是合法 ISO8601 字符串: %r" % (value,)) from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_UTC8)
        return dt
```

Note pre-3.11 `fromisoformat` doesn't accept "2026-09-28T23:53:09+0800"? It does accept +0800 I believe (fromisoformat accepts the output of isoformat(), which produces "+08:00"; but it also parses "+0800"? Python docs: `datetime.fromisoformat` accepts any valid ISO 8601 date format... in 3.7-3.10 it was more limited: it accepted "YYYY-MM-DD[*HH[:MM[:SS[.fff[fff]]]][+HH:MM[:SS[.ffffff]]]]"... Actually the 3.7 implementation used `timespec` regex-ish parsing supporting "+HH:MM" and "+HH:MM:SS" and "+HHMM"? Let me think: CPython's `_parse_isoformat_date` / `_parse_isoformat_time` handle offsets with colon optional? In CPython 3.7's `datetime.fromisoformat`, the time part regex... The implementation: it splits on 'T', parses date, then time string via `_parse_isoformat_time`, which handles 'Z' ? No. Offsets: the code splits the time part on '+' or '-'... I recall `fromisoformat("2026-09-28T23:53:09+0800")` raises ValueError in older versions. To be safe, add explicit handling: if the string has an offset without colon, normalize by inserting colon. Let me write a small normalizer:

```python
def _normalize_offset_colon(text):
    # 处理 +0800 / -0530 这类无冒号偏移，兼容 Python<3.11 的 fromisoformat
    for sign in ("+", "-"):
        idx = text.rfind(sign)
        if idx > 0:
            tail = text[idx+1:]
            if tail.isdigit() and len(tail) == 4:
                return text[:idx+1] + tail[:2] + ":" + tail[2:]
    return text
```

Careful: rfind of "-" would hit the date separators ("2026-09-28..."), rfind finds last occurrence — the offset minus sign if present, else date's '-'... e.g. "2026-09-28T23:53:09" rfind("-") = index of "09"? Let's see: "2026-09-28T23:53:09" — last "-" is at position 7 (before 28)? positions: "2026-09-28..." the dashes at 4 and 7. rfind gives 7, tail = "28T23:53:09" which isn't all digits → skip. Good. For "+": "2026-09-28T23:53:09+0800" → rfind("+") at offset index, tail "0800" digits len 4 → insert colon. Good. For "2026-09-28T23:53:09.123456-0530": rfind("-") → the offset minus (position after fraction), tail "0530" → fix. But what about date-only with negative? covered above. What about "20260928" (basic format)? tail not digits len 4 → unchanged; fromisoformat will fail → ValueError. Acceptable.

Also microseconds with comma separator "2026-09-28T23:53:09,123+08:00"? ISO allows comma. fromisoformat might not. Skip, or replace "," with "." if it appears after seconds. Cheap: `text = text.replace(",", ".", 1)`? Risky if comma elsewhere — ISO8601 strings won't have other commas. I'll do it only when the fraction position... skip it. Keep code lean; mention accepted input forms.

Also handle `date` instances? `datetime` check first, then `date`? If someone passes `datetime.date`, we'd hit TypeError. Fine — but TypeError message lists supported types.

Now `normalize_event`:

```python
_ACCEPTED_KEYS = ("ts", "timestamp", "source", "modality", "person", "room", "event_type", "payload")

def _norm_text(value, field):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError("%s 必须是 str 或 None，收到 %s" % (field, type(value).__name__))
    return value.strip()

def _norm_enum(value, field, allowed):
    if value is None:
        raise ValueError("%s 必填，允许值: %s" % (field, "/".join(allowed)))
    if not isinstance(value, str):
        raise TypeError("%s 必须是 str，收到 %s" % (field, type(value).__name__))
    text = value.strip().lower()
    if text not in allowed:
        raise ValueError("%s 非法: %r，允许值: %s" % (field, value, "/".join(allowed)))
    return text
```

`allowed` is tuple → `text not in allowed` works with tuple.

`_norm_payload(value)` returns tuple(payload_dict, json_text).

```python
def _norm_payload(value):
    if value is None:
        return {}, "{}"
    if not isinstance(value, Mapping):
        raise TypeError("payload 必须是 dict/mapping，收到 %s" % type(value).__name__)
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("payload 无法序列化为 JSON: %s" % exc) from exc
    return dict(value), text
```

`normalize_event`:

```python
def normalize_event(event):
    if not isinstance(event, Mapping):
        raise TypeError("event 必须是 mapping/dict，收到 %s" % type(event).__name__)
    data = dict(event)
    unknown = sorted(str(k) for k in data if k not in _ACCEPTED_KEYS)
    if unknown:
        logger.warning("unified_store: 忽略未识别字段（不会写入 unified_events）: %s", ", ".join(unknown))
    raw_ts = data["ts"] if data.get("ts") is not None else data.get("timestamp")
    ts = format_ts(raw_ts)
    source = _norm_enum(data.get("source"), "source", SOURCES)
    modality = _norm_enum(data.get("modality"), "modality", MODALITIES)
    person = _norm_text(data.get("person"), "person")
    room = _norm_text(data.get("room"), "room")
    event_type = _norm_text(data.get("event_type"), "event_type")
    payload, payload_json = _norm_payload(data.get("payload"))
    return {
        "ts": ts,
        "source": source,
        "modality": modality,
        "person": person,
        "room": room,
        "event_type": event_type,
        "payload": payload,
        "payload_json": payload_json,
        "created_at": format_ts(None),
    }
```

Careful: `k not in _ACCEPTED_KEYS` where keys might be non-str — fine.

Class `UnifiedEventStore`:

```python
class UnifiedEventStore:
    def __init__(self, db_path, connection_timeout=5.0):
        self.db_path = str(db_path)
        self.connection_timeout = float(connection_timeout)
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=self.connection_timeout)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self):
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
```

Note: `sqlite3.connect` with default `isolation_level=""` does implicit transaction management for DML; DDL in Python 3.6+ sqlite3... `conn.execute("CREATE TABLE ...")` will run within implicit transaction handling and `conn.commit()` commits. Fine.

Hmm: `sqlite3.connect(self.db_path, ...)` when db_path is a `Path`? We str() it. When db_path is ":memory:" — each _connect creates a new empty DB! That breaks in-memory usage. Tests use tmp files so fine, but a user passing ":memory:" gets weird behavior. Handle: if db_path == ":memory:", keep a single persistent connection? Simpler: document that ":memory:" is not supported for this class because connections are per-call; use a temp file. Or implement: `if self.db_path == ":memory:": use shared URI "file:unified_mem_<id>?mode=memory&cache=shared"` with a kept-open anchor connection. Overkill. Document limitation. Actually let me support it minimally: keep a persistent connection for ":memory:"? That complicates locking. Just document: `:memory:` not supported (per-call connections). Note in §7. Or... let me just handle it: maintain `self._keepalive_conn` if db_path == ":memory:" and return that same connection from _connect with a lock? Then close semantics break (finally conn.close() would close the shared one). Nah. Document it.

write_event:

```python
    def write_event(self, event):
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
                    "INSERT INTO unified_events (ts, source, modality, person, room, event_type, payload, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (normalized["ts"], normalized["source"], normalized["modality"],
                     normalized["person"], normalized["room"], normalized["event_type"],
                     normalized["payload_json"], normalized["created_at"]),
                )
                conn.commit()
                row_id = cur.lastrowid
            finally:
                conn.close()
        except Exception as exc:  # 双写 fail-open：吞掉一切异常
            logger.exception("unified_store.write_event 双写失败（已忽略，不影响主流程）: %s", exc)
            return {"ok": False, "error": str(exc), "stage": "storage"}
        event_out = {
            "id": row_id,
            "ts": normalized["ts"],
            "source": normalized["source"],
            "modality": normalized["modality"],
            "person": normalized["person"],
            "room": normalized["room"],
            "event_type": normalized["event_type"],
            "payload": normalized["payload"],
            "created_at": normalized["created_at"],
        }
        return {"ok": True, "id": row_id, "event": event_out}
```

Note: if `conn.execute` raises, `conn.close()` in finally; then the exception propagates to outer except. Good. But careful: `row_id = cur.lastrowid` before close — yes I set it inside try before finally. Good.

Wait — `logger.exception(..., exc)` with `%s` formatting and exc arg: `logger.exception("...: %s", exc)` fine.

query_events — write it:

```python
    def query_events(self, person=None, room=None, start=None, end=None,
                     source=None, modality=None, event_type=None,
                     limit=None, order="asc"):
        person_f = None if person is None else _norm_text(person, "person")
        room_f = None if room is None else _norm_text(room, "room")
        start_f = None if start is None else format_ts(start)
        end_f = None if end is None else format_ts(end)
        source_f = None if source is None else _norm_enum(source, "source", SOURCES)
        modality_f = None if modality is None else _norm_enum(modality, "modality", MODALITIES)
        type_f = None if event_type is None else _norm_text(event_type, "event_type")
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise TypeError("limit 必须是 int 或 None")
            if limit < 0:
                raise ValueError("limit 不能为负数")
        order_f = _norm_order(order)

        if start_f is not None and end_f is not None and start_f > end_f:
            logger.debug("unified_store.query_events: start > end，返回空列表")
            return []

        where = ["1=1"]
        params: List[Any] = []
        if person_f is not None:
            where.append("person = ?"); params.append(person_f)
        if room_f is not None:
            where.append("room = ?"); params.append(room_f)
        if start_f is not None:
            where.append("ts >= ?"); params.append(start_f)
        if end_f is not None:
            where.append("ts <= ?"); params.append(end_f)
        if source_f is not None:
            where.append("source = ?"); params.append(source_f)
        if modality_f is not None:
            where.append("modality = ?"); params.append(modality_f)
        if type_f is not None:
            where.append("event_type = ?"); params.append(type_f)

        sql = ("SELECT id, ts, source, modality, person, room, event_type, payload, created_at"
               " FROM unified_events WHERE " + " AND ".join(where) +
               " ORDER BY ts " + order_f + ", id " + order_f)
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        ...
```

`order_f` is "ASC"/"DESC" — from a whitelist, so no injection.

Note: if limit == 0 → `LIMIT 0` returns []. Good.

`_norm_order`:
```python
def _norm_order(order):
    if not isinstance(order, str):
        raise TypeError("order 必须是 str")
    text = order.strip().lower()
    if text in ("asc", "ascending"): return "ASC"
    if text in ("desc", "descending"): return "DESC"
    raise ValueError("order 只能是 asc/desc，收到 %r" % (order,))
```

Now module-level:

```python
_store_cache: Dict[str, "UnifiedEventStore"] = {}
_store_lock = threading.Lock()

def resolve_db_path(db_path=None):
    if db_path:
        return str(db_path)
    env = os.environ.get(DB_PATH_ENV)
    if env:
        return env
    return DEFAULT_DB_NAME

def get_store(db_path=None):
    path = resolve_db_path(db_path)
    with _store_lock:
        store = _store_cache.get(path)
        if store is None:
            store = UnifiedEventStore(path)
            _store_cache[path] = store
        return store

def reset_stores():
    with _store_lock:
        _store_cache.clear()

def init_schema(db_path=None):
    get_store(db_path).init_schema()

def write_event(event=None, db_path=None, **fields):
    try:
        merged = _merge_event(event, fields)
        return get_store(db_path).write_event(merged)
    except Exception as exc:
        logger.exception("unified_store.write_event 双写失败（已忽略，不影响主流程）: %s", exc)
        return {"ok": False, "error": str(exc), "stage": "storage"}
```

Hmm — careful: if `get_store` fails (bad path?), stage storage. Fine.

But careful #2: `write_event(event=None, db_path=None, **fields)` — positional second arg is db_path. If someone calls `write_event({...}, something)` positionally, second positional becomes db_path. Unlikely.

Hmm, should `db_path` be keyword-only? `def write_event(event=None, *, db_path=None, **fields)` — then `**fields` can't capture a field named db_path... Actually if a field named "db_path" is passed it goes to db_path param. Unlikely. Making it keyword-only is cleaner. But `fields` with keyword-only params: `def write_event(event=None, *, db_path=None, **fields)` — valid Python. Yes. Let me use that.

Wait, but then `write_event(event_dict, db_path="x")` works. Good.

`_merge_event`:
```python
def _merge_event(event, fields):
    if event is None:
        merged = {}
    elif isinstance(event, Mapping):
        merged = dict(event)
    else:
        raise TypeError("event 必须是 mapping/dict，收到 %s" % type(event).__name__)
    if fields:
        merged.update(fields)
    return merged
```

query_events module-level:

```python
def query_events(person=None, room=None, start=None, end=None, *,
                 source=None, modality=None, event_type=None,
                 limit=None, order="asc", db_path=None):
    return get_store(db_path).query_events(
        person=person, room=room, start=start, end=end,
        source=source, modality=modality, event_type=event_type,
        limit=limit, order=order,
    )
```

Good.

Now `typing` imports used: Any, Dict, List, Optional, Tuple. And `Mapping` from collections.abc. OK.

Now tests. Let me write test file 1 content.

```python
# -*- coding: utf-8 -*-
"""tests/test_unified_store.py

覆盖交付单验收 1-4：
  1) py_compile 通过
  2) unified_events 建表 SQL 正确（含索引）
  3) write_event 接收 device/vision/perception/activity（及 llm）来源
  4) query_events(person, room, start, end) 返回统一格式列表
外加边界/异常路径：时间戳规范化、person 空串语义、枚举校验、
双写 fail-open（只记日志不抛异常）、查询 fail-open / 参数 fail-closed。
"""
```

Loader:

```python
import importlib.util
import json
import logging
import pathlib
import py_compile
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "memory_agent" / "unified_store.py"


def load_unified_store():
    spec = importlib.util.spec_from_file_location("unified_store_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


us = load_unified_store()
```

Hmm: `spec.loader.exec_module` — my module imports stdlib only. Fine.

Then a base TestCase with tmpdir:

```python
class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = str(pathlib.Path(self._tmp.name) / "test.db")
        self.store = us.UnifiedEventStore(self.db_path)
```

Note: `us` module-level cache with `get_store` — tests for module-level API should pass explicit db_path to avoid cross-test pollution. Fine.

TestSchema:

```python
class TestSchema(StoreTestCase):
    def test_py_compile_passes(self):
        py_compile.compile(str(MODULE_PATH), doraise=True)   # 不抛异常即通过

    def test_create_table_and_indexes(self):
        self.store.init_schema()
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall()
        finally:
            conn.close()
        by_name = {r[1]: r for r in rows}
        self.assertIn("unified_events", by_name)
        table_sql = by_name["unified_events"][3]
        for col in ("ts", "source", "modality", "person", "room", "event_type", "payload", "created_at"):
            self.assertIn(col, table_sql)
        self.assertIn("CHECK (source IN ('device','vision','perception','activity','llm'))", table_sql)
        self.assertIn("CHECK (modality IN ('sensor','image','audio','text'))", table_sql)
        self.assertIn("person", table_sql)  # ...
        # 索引
        for idx in ("idx_unified_events_ts", "idx_unified_events_person_ts", "idx_unified_events_room_ts", "idx_unified_events_source_ts", "idx_unified_events_modality_ts"):
            self.assertIn(idx, by_name, "缺少索引 %s" % idx)
            self.assertEqual(by_name[idx][0], "index")
            self.assertEqual(by_name[idx][2], "unified_events")
```

Also test columns via PRAGMA table_info and NOT NULL:

```python
    def test_column_types_and_not_null(self):
        self.store.init_schema()
        conn = sqlite3.connect(self.db_path)
        try:
            info = conn.execute("PRAGMA table_info(unified_events)").fetchall()
        finally: conn.close()
        cols = {row[1]: row for row in info}
        self.assertEqual(cols["id"][5], 1)  # pk
        for name in ("ts","source","modality","person","room","event_type","payload","created_at"):
            self.assertEqual(cols[name][3], 1, name + " 必须 NOT NULL")
        self.assertEqual(cols["ts"][2].upper(), "TEXT")
```

Indices of PRAGMA table_info: cid(0), name(1), type(2), notnull(3), dflt_value(4), pk(5). Good.

Also test CHECK constraint actually enforced at DB level:
```python
    def test_check_constraint_blocks_bad_source(self):
        self.store.init_schema()
        conn = sqlite3.connect(self.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO unified_events (ts, source, modality, person, room, event_type, payload, created_at) VALUES (?,?,?,?,?,?,?,?)",
                             ("2026-09-28T23:53:09+08:00", "unknown", "sensor", "", "", "", "{}", "2026-09-28T23:53:09+08:00"))
        finally:
            conn.close()
```

TestWrite (acceptance 3 + write semantics):

```python
class TestWriteEvent(StoreTestCase):
    def test_write_each_source_persists_row(self):
        for source in ("device", "vision", "perception", "activity"):
            result = self.store.write_event({"source": source, "modality": "sensor", "event_type": "probe"})
            self.assertTrue(result["ok"], result)   # hmm, "不得出现只断言 ok is True 的空测试" — I assert more below, fine.
            self.assertIsInstance(result["id"], int)
        rows = self._fetch_all()
        self.assertEqual([r["source"] for r in rows], ["device","vision","perception","activity"])
        self.assertEqual(len(rows), 4)
```

Wait — need ordering deterministic: order by id.

Also llm source test.

Then assertions on echo, payload round-trip, person '', timestamp.

Test timestamp normalization:

```python
    def test_timestamps_normalized_to_utc8(self):
        cases = [
            ("2026-09-28T15:53:09Z", "2026-09-28T23:53:09+08:00"),
            ("2026-09-28T15:53:09+00:00", "2026-09-28T23:53:09+08:00"),
            ("2026-09-28T23:53:09+08:00", "2026-09-28T23:53:09+08:00"),
            ("2026-09-28T23:53:09.123456+08:00", "2026-09-28T23:53:09.123456+08:00"),
            ("2026-09-28 23:53:09", "2026-09-28T23:53:09+08:00"),
            (datetime(2026, 9, 28, 23, 53, 9), "2026-09-28T23:53:09+08:00"),
            (datetime(2026, 9, 28, 15, 53, 9, tzinfo=timezone.utc), "2026-09-28T23:53:09+08:00"),
            (1789000000, ...),  # compute
        ]
```

Compute epoch for 2026-09-28T15:53:09Z? Better compute expected dynamically: `datetime(2026,9,28,15,53,9,tzinfo=timezone.utc).timestamp()` and expect "2026-09-28T23:53:09+08:00". I'll compute inside the test.

Also test datetime with UTC-5 offset: `datetime(2026,9,28,10,53,9,tzinfo=timezone(timedelta(hours=-5)))` → "2026-09-28T23:53:09+08:00".

And test now() default: `ts=None` → within a window of now in UTC+8, and matches regex.

Regex for format: `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?\+08:00$`.

Test person semantics:

```python
    def test_person_empty_string_means_unidentified(self):
        self.store.write_event({"source":"vision","modality":"image"})            # 缺省
        self.store.write_event({"source":"vision","modality":"image","person":None})
        self.store.write_event({"source":"vision","modality":"image","person":"   "})
        self.store.write_event({"source":"vision","modality":"image","person":" 张三 "})
        rows = self._fetch_all()
        self.assertEqual([r["person"] for r in rows], ["", "", "", "张三"])
        self.assertNotIn(None, [r["person"] for r in rows])
        # DB 层 NOT NULL
        ...
```

Validation tests:

```python
    def test_invalid_source_rejected_without_write(self):
        result = self.store.write_event({"source":"unknown","modality":"sensor"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "validation")
        self.assertIn("source", result["error"])
        self.assertEqual(self._count(), 0)

    def test_missing_modality_rejected / case_normalized / payload_not_serializable / event_not_mapping / ts_empty_string
```

Fail-open storage test:

```python
    def test_write_failure_is_logged_and_swallowed(self):
        bad = us.UnifiedEventStore(str(pathlib.Path(self._tmp.name) / "no_such_dir" / "x.db"))
        with self.assertLogs("memory_agent.unified_store", level="ERROR") as cm:
            result = bad.write_event({"source":"device","modality":"sensor"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "storage")
        self.assertTrue(any("双写" in line for line in cm.output))
```

Note: assertLogs requires at least one log record; logger.exception logs at ERROR. Good.

Careful: `bad.write_event` calls `self.init_schema()` which raises sqlite3.OperationalError → caught by outer except → logs. Good.

Also test module-level write_event never raises:

```python
    def test_module_level_write_never_raises(self):
        result = us.write_event({"source": "device", "modality": "sensor"}, db_path=self.db_path)
        self.assertTrue(result["ok"]) and id...
        result2 = us.write_event("not a mapping", db_path=self.db_path)  # 位置参数非法
        self.assertFalse(result2["ok"])  # 也不抛异常
```

Careful: `us.write_event("not a mapping", db_path=...)` — event is str → `_merge_event` raises TypeError → caught by outer → returns ok False with stage "storage" (since my outer except labels stage storage). Hmm, that's a validation error labeled storage. Let me improve: in module-level write_event, catch (TypeError, ValueError) from merge separately → stage "validation". Let me restructure:

```python
def write_event(event=None, *, db_path=None, **fields):
    try:
        merged = _merge_event(event, fields)
    except (TypeError, ValueError) as exc:
        logger.warning(...)
        return {"ok": False, "error": str(exc), "stage": "validation"}
    try:
        return get_store(db_path).write_event(merged)
    except Exception as exc:
        logger.exception(...)
        return {"ok": False, "error": str(exc), "stage": "storage"}
```

And note `get_store(db_path).write_event` itself never raises, so the second try is belt-and-braces. But `_merge_event` could raise other exceptions? Only TypeError. Fine. But careful: `isinstance(event, Mapping)` on weird objects could raise? edge.

Hmm, but also `**fields` — if someone passes `write_event(**d)` where d has non-str keys? impossible with **.

Test recovery after failure: write to bad db → ok False; then write to good db → ok True (fail-open doesn't poison state).

TestQuery:

Seed helper:

```python
    def _seed(self):
        base = "2026-09-28T23:53:00+08:00"
        rows = [
            {"ts": "2026-09-28T23:53:00+08:00", "source":"device","modality":"sensor","person":"","room":"kitchen","event_type":"motion","payload":{"n":1}},
            {"ts": "2026-09-28T23:53:01+08:00", "source":"vision","modality":"image","person":"张三","room":"kitchen","event_type":"face","payload":{"n":2}},
            {"ts": "2026-09-28T23:53:02+08:00", "source":"perception","modality":"audio","person":"李四","room":"living","event_type":"voice","payload":{"n":3}},
            {"ts": "2026-09-28T23:53:03+08:00", "source":"activity","modality":"text","person":"","room":"living","event_type":"summary","payload":{"n":4}},
        ]
```

Then tests:
- test_query_returns_unified_list: `result = self.store.query_events(None, None, None, None)` → list of 4 dicts, key sets equal, payload dicts, ids ascending, ts sorted.
- test_query_by_person_exact: person="张三" → 1 row; person="" → 2 rows (unidentified); person=None → 4.
- test_query_by_room: room="kitchen" → 2.
- test_query_by_range_inclusive: start="2026-09-28T23:53:01+08:00", end="2026-09-28T23:53:02+08:00" → 2 rows (inclusive both ends). And range with start after end → []. And range excluding boundaries: start="2026-09-28T23:53:00.500000+08:00" → excludes first row.

Careful with mixed format comparison: stored "2026-09-28T23:53:01+08:00" and bound "2026-09-28T23:53:01.000001+08:00" → string compare: "…01+" vs "…01." → "+" < "." → stored < bound → excluded. Correct since 01.000000 < 01.000001. And bound "2026-09-28T23:53:00.500000+08:00" vs stored "…53:01+08:00": compare at position of "0" vs "1" in seconds: "…53:00." vs "…53:01+" → '0'<'1' → stored > bound → included. Correct.

Good, ordering consistent as analyzed.

- test_query_filters_combined: person + room + range + source.
- test_query_limit_order: order="desc", limit=2 → last two by ts.
- test_query_unparseable_payload_degraded: insert raw row with bad payload via sqlite3 directly, then query → payload == {"_raw": ...}.
- test_query_storage_failure_returns_empty_list: bad path store → [] with ERROR log.
- test_query_bad_args_raise: person=123 → TypeError; start="abc" → ValueError; order="sideways" → ValueError; limit=-1 → ValueError; limit=1.5 → TypeError.
- test_module_level_query_signature_positional: `us.query_events("张三", "kitchen", None, None, db_path=self.db_path)` → works, returns list.

Also verify no row writes to legacy tables in file 2.

File 2 `tests/test_unified_store_compat.py`:

```python
# 覆盖验收 5（不改 events / perception_events 表结构）+ 依赖约束（只用 sqlite3 + stdlib）
```

Tests:
- setUp: create legacy tables with representative DDL:

```sql
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS perception_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    person TEXT,
    note TEXT
);
```
Mark clearly in a comment: 这是替身 DDL（§1 现有代码为空，读不到真实建表语句）；测试只断言 "运行 unified_store 之后这些表原封不动"。

- snapshot function: `sqlite_master` entries (type,name,sql) for these tables + their indexes + PRAGMA table_info.
- test_init_schema_leaves_legacy_tables_untouched: snapshot before/after init_schema + writes → equal.
- test_only_new_objects_are_unified_events_and_indexes: after init, the set of new names == expected.
- test_write_event_only_touches_unified_events: seed legacy rows, write events, assert legacy row contents unchanged (counts and rows).
- test_module_imports_only_stdlib: ast parse source; collect imports; assert each root module in a stdlib allowlist (use `sys.stdlib_module_names` if available (3.10+), else a hardcoded allowlist). Better: check against hardcoded set {"json","logging","os","sqlite3","threading","datetime","typing","collections","abc", plus "from __future__"}. Robust across versions. Also assert no `memory_agent`/relative imports.
- test_py_compile (already in file1; skip duplicate).

Let me write the ast test:

```python
    def test_no_new_third_party_dependencies(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    self.fail("不允许相对导入: %s" % ast.dump(node))
                if node.module:
                    roots.add(node.module.split(".")[0])
        allowed = {"__future__", "json", "logging", "os", "sqlite3", "threading", "datetime", "typing", "collections"}
        self.assertTrue(roots <= allowed, "出现计划外依赖: %s" % sorted(roots - allowed))
```

`MODULE_PATH.read_text(encoding=...)` — pathlib read_text supports encoding. Fine (3.5+).

Also assert "memory_agent" not in roots (implied by allowlist).

Good.

Now usage example — I'll include as a code block in the report:

```python
# 1) 初始化（幂等，可重复调用）
from memory_agent import unified_store  # 或 sys.path 处理后 import unified_store

unified_store.init_schema(db_path=r"E:/NAS/memory-agent/data/memory_agent.db")

# 2) 双写（在现有写入成功后追加一行；失败只记日志，不影响主流程）
result = unified_store.write_event({
    "source": "vision",          # device | vision | perception | activity | llm
    "modality": "image",         # sensor | image | audio | text
    "person": "",                # "" = 未识别
    "room": "kitchen",
    "event_type": "face_detected",
    "ts": "2026-09-28T23:53:09+08:00",
    "payload": {"bbox": [12, 30, 80, 120], "score": 0.93},
}, db_path=DB)

# 3) 查询
rows = unified_store.query_events("张三", "kitchen", "2026-09-28T00:00:00+08:00", "2026-09-29T00:00:00+08:00", db_path=DB)
for row in rows:
    print(row["ts"], row["source"], row["person"] or "<未识别>", row["payload"])
```

And MCP tool wiring snippet (to be inserted by integrator, since I can't see §1 code):

```python
# 现有 MCP 工具体内（写完旧表后追加，勿改签名）：
from . import unified_store   # 或绝对导入
unified_store.write_event({...})   # 失败自动只记日志
```

Now, response structure:

1. 前置说明：§1 是空的（关键！）→ 我基于验收条文推导契约，列出假设。
2. 设计决策（§7）逐条。
3. 代码 `unified_store.py`。
4. 测试文件 ×2。
5. 使用示例。
6. 验收对照表 + 未完成/待确认清单。

I must be honest: the request says "严格遵循现有接口契约" but no contract is provided. I should lead with that flag briefly and constructively, then deliver maximum value.

Also should I keep the response in Chinese? Yes — the user writes Chinese.

Formatting: use headings, code blocks. Keep the design-decision list tight. Don't use emojis. No asterisk actions.

Let me now finalize the code text completely, double-checking syntax.

One more check on `write_event` echo construction: I build `event_out` from normalized — includes created_at. Fine.

Potential issue: in `test_write_each_source_persists_row` I use `self.assertTrue(result["ok"])` — the ban is on tests that *only* assert `result["ok"] is True`. My tests assert more. Fine. But maybe I should avoid `assertTrue(result["ok"])` entirely and assert specific fields to be safe. I'll assert `result["id"]` and row contents primarily, and use assertTrue(result["ok"], result) with a message where convenient. Actually to fully comply with the spirit, I'll assert `self.assertIsInstance(result, dict)` and `self.assertEqual(result["ok"], True)` plus deep content assertions. Fine.

Let me write the tests with `_fetch_all` helper:

```python
    def _fetch_all(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT id, ts, source, modality, person, room, event_type, payload, created_at FROM unified_events ORDER BY id").fetchall()]
        finally:
            conn.close()
```

Now think about `test_write_each_source_persists_row` ordering: writes in order device, vision, perception, activity → ids ascending. assert list equality.

Timestamp test details — write events then read `row["ts"]`.

Let me write the "now" test:

```python
    def test_ts_none_means_now_in_utc8(self):
        before = datetime.now(us.TZ_UTC8)
        result = self.store.write_event({"source":"device","modality":"sensor"})
        after = datetime.now(us.TZ_UTC8)
        stored = self._fetch_all()[0]["ts"]
        self.assertRegex(stored, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?\+08:00$")
        parsed = datetime.strptime(...)  # hmm, need to parse back
```

Parsing back: use `us.coerce_datetime(stored)` → aware datetime → compare `before <= parsed <= after` (allow small skew? coerce_datetime of stored keeps offset +08:00, comparisons with aware datetimes fine). Add a tiny margin: `before - timedelta(seconds=1) <= parsed <= after + timedelta(seconds=1)`. Good.

Note `datetime.strptime` not needed since coerce_datetime exists — good, also exercises it.

Now the test for inclusive bounds with microsecond edge:

```python
    def test_range_bounds_are_inclusive(self):
        self._seed()
        got = self.store.query_events(None, None, "2026-09-28T23:53:01+08:00", "2026-09-28T23:53:02+08:00")
        self.assertEqual([r["event_type"] for r in got], ["face", "voice"])
        got2 = self.store.query_events(None, None, "2026-09-28T23:53:01.000001+08:00", "2026-09-28T23:53:01.999999+08:00")
        self.assertEqual(got2, [])   # 中间没有任何行
```

Careful: is "…23:53:01+08:00" excluded by start "…23:53:01.000001+08:00"? String: stored "…01+" vs start "…01." → stored < start → excluded. And row "…02+08:00" vs end "…01.999999+08:00": compare seconds digit "2" vs "1" → row > end → excluded. So [] — correct.

And boundary equality: start = exact stored ts → included (`ts >= ?`). Covered by first case.

Test `test_query_person_empty_matches_unidentified_only`.

Now — one important check: `query_events(None, None, None, None)` with no filters — `where` = ["1=1"] → "SELECT ... WHERE 1=1 ORDER BY ts ASC, id ASC". Fine.

Now the compat test's snapshot of legacy tables. Implementation:

```python
LEGACY_DDL = [
    """CREATE TABLE IF NOT EXISTS events (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         ts TEXT NOT NULL,
         kind TEXT,
         payload TEXT)""",
    """CREATE TABLE IF NOT EXISTS perception_events (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         ts TEXT NOT NULL,
         person TEXT,
         note TEXT)""",
]

def snapshot(conn):
    return conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name IN ('events','perception_events') OR tbl_name IN ('events','perception_events') ORDER BY type, name"
    ).fetchall()
```

Compare before/after lists equality. Also PRAGMA table_info for each.

Then `test_write_event_only_inserts_into_unified_events`: seed legacy rows, run writes, assert `SELECT * FROM events` unchanged (compare list of tuples before/after), counts equal.

Also assert that unified_store never issues DROP/ALTER on legacy: could scan the module's SQL strings for "DROP TABLE" / "ALTER TABLE". Nice cheap test:

```python
    def test_module_never_drops_or_alters_tables(self):
        source = MODULE_PATH.read_text(encoding="utf-8").upper()
        self.assertNotIn("DROP TABLE", source)
        self.assertNotIn("ALTER TABLE", source)
        self.assertNotIn("DELETE FROM", source)
        self.assertNotIn("DROP INDEX", source)
```

Careful: my docstring might contain those words? I'll avoid writing "DROP TABLE" in docstrings... but I might mention "不删旧表" in Chinese — that's fine (English uppercase check). Make sure the code/docstrings don't contain the literal English strings. I'll be careful. Actually the check `source.upper()` — my Chinese docstrings won't contain English SQL keywords. But "CREATE TABLE IF NOT EXISTS" is present — that's not in the denylist. OK. But careful about the word "ALTER" appearing in Chinese text? No.

Hmm — the string "DELETE FROM"? not present. Good.

Now also the acceptance #5 test name should make mapping obvious.

Finally, "桩实现…要在 §7 里明说哪条没做" — I'll have a §7/§8 subsection "未做/待补" listing:
- 双写调用点尚未落到 store.py / MCP 工具体内（约束禁止我改、且 §1 无代码可对照），只提供插入片段；
- 与真实 events/perception_events 建表语句的一致性未验证（§1 为空），compat 测试用替身 DDL；
- 未做数据迁移/旧表删除（按要求）；
- 未做 person 模糊匹配、去重/幂等写入、批量写入接口（需求未提）。

Good. Also note no stub functions: everything in the module has real behavior (no placeholder functions). I'll state that explicitly.

Now write the answer. I'll try to keep the code correct on first pass.

Let me write out the final module code in full detail now (mentally compile):

```python
# -*- coding: utf-8 -*-
"""MA 多模态统一数据模型：``unified_events`` 表 + 双写 + 统一查询。

职责
----
1. 在现有 sqlite 库里幂等地建 ``unified_events`` 表（含索引）。
2. 提供双写写入口 ``write_event()``：**fail-open**，任何异常只记日志并返回
   ``{"ok": False, ...}``，绝不影响调用方主流程。
3. 提供统一查询 ``query_events(person, room, start, end) -> List[dict]``。

硬约束（交付单 §2.2）
--------------------
* 只用 ``sqlite3`` + 标准库，不引入任何第三方依赖；
* 不迁移旧数据、不删旧表、不改 ``events`` / ``perception_events`` 结构；
* 本模块不 import ``store.py`` / ``agent_memory.py``，避免与现有实现耦合。

数据规范
--------
* 时间戳：ISO8601、UTC+8，``2026-09-28T23:53:09+08:00``；
  亚秒非零时带 6 位小数：``2026-09-28T23:53:09.123456+08:00``。
  输入允许 None / datetime / Unix 时间戳 / ISO8601 字符串（带 Z、±HH:MM、
  ±HHMM、空格分隔均可）；naive 值一律按 UTC+8 墙上时间解释。
* person：空字符串表示“未识别”，永远不写 NULL（room 同理，``''`` 表示未知）。
* source ∈ device | vision | perception | activity | llm（大小写不敏感，落库为小写）。
* modality ∈ sensor | image | audio | text（同上）。
* payload：JSON 对象（dict），落库为紧凑、键排序的 JSON 文本，保证可复现。
"""
```

Careful: docstring contains "不删旧表" in Chinese — fine for the "DROP TABLE" check.

Then code. Let me write the whole thing in the answer.

One more consideration about `format_ts` accepting `None` default → now. And `coerce_datetime` public.

Also in `normalize_event` I use `data.get("ts")` — but what if event has key "ts" with value None and "timestamp" with a value? Handled.

What if both "ts" and "timestamp" present and ts non-None → uses ts. Good.

Let me write `_ACCEPTED_KEYS` as a frozenset.

Final check on `query_events` signature order for positional compatibility: `query_events(self, person=None, room=None, start=None, end=None, *, source=None, ...)`. If harness calls `query_events(person="x", room="y", start=s, end=e)` — fine. If harness calls with 4 positionals — fine.

But what if harness calls `query_events(person, room, start, end, limit)` positionally? limit is keyword-only → TypeError. Risk. Should limit be positional-allowed? Making params after `end` positional-or-keyword is more permissive: `def query_events(self, person=None, room=None, start=None, end=None, source=None, modality=None, event_type=None, limit=None, order="asc")`. Then all are positional-or-keyword; a harness calling 9 positionals works, and keyword calls work. Slightly less protective against arg-order mistakes but more compatible. Given the missing contract, be permissive. Do it — no `*` separator. Same for module-level. And `write_event(event=None, db_path=None, **fields)` — hmm, `db_path` as second positional could collide if a harness passes a second positional field dict? Unlikely. Keep `db_path` keyword-only? If keyword-only and harness does `write_event({...}, "path.db")` it breaks. Which is more likely? They'd call `write_event({...})`. I'll make db_path keyword-only to avoid stealing positional slots for future extension... but `**fields` after keyword-only is fine.

Hmm, actually: `def write_event(event=None, *, db_path=None, **fields)`. Good.

For `UnifiedEventStore.__init__(self, db_path, connection_timeout=5.0)` — positional-or-keyword. Fine.

Now writing the answer. Also I should mention file paths: `src/memory_agent/unified_store.py`, `tests/test_unified_store.py`, `tests/test_unified_store_compat.py`.

Length control: I'll write it out fully but avoid redundant prose. Let me produce the final answer.

One more consideration on the CHECK constraint string in the test: my SCHEMA_SQL formatting must match exactly the substring `CHECK (source IN ('device','vision','perception','activity','llm'))`. In my CREATE TABLE I'll write it exactly like that with single spaces. Let me make the test assertion less brittle: assert `"CHECK (source IN ("` and each enum name present, or use regex. Better: assert `"source IN ('device','vision','perception','activity','llm')" in table_sql`. I'll write the DDL with that exact single-spaced form and assert the substring without "CHECK (". Let me assert: `self.assertIn("source IN ('device','vision','perception','activity','llm')", table_sql)`. Fine.

Also assert `NOT NULL` for person and `DEFAULT ''`: `self.assertIn("person", ...)` — better via PRAGMA table_info: notnull==1 and dflt_value == "''". PRAGMA dflt_value returns "''" as string including quotes. Yes, for `DEFAULT ''` it returns `''` (two quote chars). I'll assert `cols["person"][4] == "''"`. Slightly brittle across sqlite versions but stable. Maybe just assert notnull and that default is not None. I'll assert `str(cols["person"][4]).strip() == "''"`. Hmm, keep it simple: assert notnull == 1 and default contains "''". Fine.

Also the test that NULL cannot be inserted into person (DB-level enforcement):

```python
    def test_person_not_null_at_db_level(self):
        self.store.init_schema()
        conn = sqlite3.connect(self.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO unified_events (ts, source, modality, person, room, event_type, payload, created_at) VALUES (?,?,?,NULL,'','','{}','2026-01-01T00:00:00+08:00')", ("2026-01-01T00:00:00+08:00","device","sensor"))
        finally:
            conn.close()
```

Wait the SQL has 8 columns and I give params for ts/source/modality plus literals... Let me write it clean with 5 placeholders. I'll write:

```python
sql = ("INSERT INTO unified_events (ts, source, modality, person, room, event_type, payload, created_at) "
       "VALUES (?, ?, ?, NULL, '', '', '{}', ?)")
conn.execute(sql, ("2026-01-01T00:00:00+08:00", "device", "sensor", "2026-01-01T00:00:00+08:00"))
```
Raises IntegrityError (NOT NULL constraint failed: unified_events.person). Good.

Note: sqlite3 in Python 3.6+ doesn't wrap IntegrityError. Fine.

Careful: I should not include the literal `"DELETE FROM"` etc. in the module source — my denylist test. Module has "INSERT INTO" only. Good. But wait: the test itself contains "DELETE FROM"? The denylist checks MODULE_PATH source only. Fine.

Let me also double check `test_module_never_drops_or_alters_tables` won't hit `"ALTER"` in my English words like "alternative"? Uppercase check `"ALTER TABLE"` specifically. Fine.

Now, writing the answer. Also, include a short 验收对照 (acceptance mapping) table.

I'll write the response now. Given the size, I'll be efficient but complete.

Also note: instructions say MiMo should not use emojis, fine. And note about the environment — no issue.

Should I caveat "my info may be outdated"? Not relevant here.

Final answer structure:

## 先说一个必须澄清的缺口
§1 是空的 → 契约未内联 → 我按验收条文推导契约并列出假设/待确认。

## 一、设计决策（对应 §7）
numbered list.

## 二、代码：src/memory_agent/unified_store.py

## 三、单测
### tests/test_unified_store.py
### tests/test_unified_store_compat.py

## 四、使用示例

## 五、验收对照 + 未完成项

Let me write it.

For design decisions, cover:
1. 表结构与字段语义（列、类型、约束、payload JSON 文本、created_at）
2. 索引选择（5 个复合/单列索引 + 理由）
3. 时间戳规范与比较（字符串定长比较、含/不含微秒、naive=UTC+8、Z/±HHMM 兼容、epoch）
4. person/room 空串语义 + strip 归一 + 查询里 `None`=不过滤 vs `""`=查未识别
5. 枚举校验（大小写不敏感、落库小写、DB 层 CHECK 双保险）
6. 失败/降级语义：写 fail-open（validation → ok False + warning；storage → ok False + exception log）；查询参数 fail-closed（raise）；查询 IO fail-open（[] + exception log）；未知字段忽略 + warning；payload 不可序列化拒绝。
7. 连接与并发：每次调用开连接 + timeout 5s；不开 WAL（NAS 文件系统）；线程安全的 schema 懒初始化；:memory: 不支持。
8. 兼容边界：不 import 现有模块；不改旧表；写入仅 INSERT INTO unified_events；db_path 显式/环境变量。
9. API 形态：类 + 模块级函数；返回值含 ok/id/event；write_event 永不抛异常；query_events 返回 list。
10. 未做/存疑清单。

Good. Write it.

For the "not sure, don't guess" list — emphasize.

Now produce output. I'll keep code blocks accurate.

Let me write the module code fully one final time in my head as I type it into the answer — being careful with indentation and syntax.

Also: `logger.exception("...: %s", exc)` — fine.

In `write_event` of the class, after the try/except, `row_id` defined. If `conn.execute` succeeded but `conn.commit()` failed → exception → return ok False. Fine.

One subtlety: `cur.lastrowid` — `conn.execute` returns Cursor. Yes.

Also I should double-check `_norm_text` naming vs `event_type` param shadowing the class attribute? Fine.

In `query_events`, parameter named `event_type` and I create `type_f`. OK.

Potential pitfall: `params: List[Any] = []` — variable annotations with `from __future__ import annotations` are fine on 3.6+? Variable annotations syntax `params: List[Any] = []` requires 3.6+. OK.

Writing now. I'll also add `def __repr__`? Not needed.

Regarding tests importing `py_compile` — stdlib. Fine.

I think I'm ready. Length will be big; that's fine.</think>
