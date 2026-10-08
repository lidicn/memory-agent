# Insights 框架 · 生产数据库 Schema 契约

> 本文件是 MiMo 重写 StoreRepository / BehaviorService 时**必须严格对齐**的生产库事实。
> 之前的版本假设了错误的 schema（`entities` 表、`events.state`、`events.attributes`），导致全部查询失败。
> 以下是实际生产库的真实结构，以 `store.py init_schema` 为准。

---

## 一、Store 类可用接口（数据访问入口）

### 1. `Store.db_query(sql: str, params: tuple = ()) -> list[dict]`
- **用途**：执行 raw SQL（只读），返回行字典列表
- **参数**：`sql` 用 `?` 占位符（sqlite3 qmark 风格），`params` 是元组
- **返回**：`list[dict]`，每行是 `{列名: 值}`
- **示例**：`store.db_query("SELECT COUNT(*) as c FROM events WHERE day=?", ("2026-09-29",))`

### 2. `Store.query_events(start, end, rooms, entities, domains, person, limit, offset, order, states, exclude_domains) -> list[dict]`
- **用途**：高层事件查询（已封装 WHERE 构建 + 分页 + 排序）
- **返回**：`list[dict]`，每行包含 events 表所有列
- **注意**：`limit` 最大 5000，`start/end` 是 ISO 字符串

### 3. `Store.count_events(...) -> int`
- 同 query_events 参数，返回匹配行数

### 4. `Store.day_counts(start_day: str, end_day: str) -> dict[str, int]`
- 返回 `{day_str: event_count}`，按天统计 events 表行数

### 5. `Store.entity_last_seen() -> dict[str, str]`
- 返回 `{entity_id: last_ts_iso}`，每个实体最后出现时间

### 6. `Store.entity_event_counts(start, end) -> dict[str, int]`
- 返回 `{entity_id: count}`，时间窗口内每个实体的事件数

---

## 二、events 表（设备状态变更事件，主表 ~105 万行）

```sql
CREATE TABLE events (
    id          TEXT PRIMARY KEY,        -- UUID
    ts          TEXT NOT NULL,           -- ISO8601 字符串，如 "2026-09-29T12:00:00"（不是 epoch！）
    day         TEXT NOT NULL,           -- "YYYY-MM-DD"，冗余列
    room        TEXT NOT NULL DEFAULT '',-- 房间名，如 "客厅"
    entity_id   TEXT NOT NULL,           -- HA entity_id，如 "light.living_room_main"
    domain      TEXT NOT NULL DEFAULT '',-- 域，如 "light"/"climate"/"sensor"
    action      TEXT NOT NULL DEFAULT '',-- 动作，如 "turned_on"/"turned_off"
    person      TEXT NOT NULL DEFAULT '',-- 触发者（可空）
    old_state   TEXT,                     -- 变更前状态（可空）
    new_state   TEXT,                     -- 变更后状态（可空）← 新框架之前误用的 "state" 实际是这个
    attrs_json  TEXT                      -- 属性 JSON 字符串（可空）← 新框架之前误用的 "attributes" 实际是这个
);
```

**关键索引**：`idx_events_day(day)`、`idx_events_room_ts(room, ts)`、`idx_events_entity_ts(entity_id, ts)`、`idx_events_ts(ts)`

**没有的列**：没有 `state` 列（用 `new_state`）、没有 `attributes` 列（用 `attrs_json`）、没有 `category` 列（从 entity_id 推断）

---

## 三、behavior_events 表（VLM 视觉行为事件 ~3600 行）

```sql
CREATE TABLE behavior_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_ts TEXT NOT NULL,        -- ISO8601 字符串
    device_ts INTEGER,              -- 设备上报 ms 时间戳（可空）
    day TEXT NOT NULL,
    room TEXT NOT NULL,
    camera_src TEXT,
    persons_json TEXT NOT NULL DEFAULT '[]',  -- JSON 数组字符串
    count INTEGER NOT NULL DEFAULT 0,
    action TEXT,                    -- 行为描述，如 "坐在电竞沙发看书"
    scene TEXT,                     -- 场景概括
    confidence REAL,
    appearance_json TEXT,
    trigger TEXT,                   -- count_change/identity_change/heartbeat/patrol/manual/face
    vlm_latency_ms INTEGER,
    snapshot_path TEXT,
    raw_response TEXT,
    status TEXT NOT NULL DEFAULT 'ok'  -- ok | vlm_failed | low_confidence | skipped
);
```

---

## 四、perception_events 表（统一感知总线 ~3500 行）

```sql
CREATE TABLE perception_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE,           -- 幂等键
    server_ts TEXT NOT NULL,        -- ISO8601 字符串
    day TEXT NOT NULL,
    source TEXT NOT NULL,           -- edge_ai | vlm | sensor
    kind TEXT NOT NULL,             -- face_known | face_unknown | human | pet | cry | gesture | day_night | fav_area | no_human | motion | object
    room TEXT,
    entity_id TEXT,
    confidence REAL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    raw_event_json TEXT
);
```

---

## 五、不存在的表（不要假设）

- ❌ **没有 `entities` 表** —— 实体信息来自 config.json 的实体注册表 + events 表聚合
  - 获取实体列表：`store.db_query("SELECT DISTINCT entity_id, room, domain FROM events")`
  - 获取实体友好名：从 config.json 的 entity_registry 读取，或用 `store._config_entities()`（内部方法）
- ❌ **没有 `entity_catalog` 表** —— 目录是运行时构建的（legacy `InsightService.entity_catalog()`）

---

## 六、时间格式约定

- **所有时间列都是 ISO8601 字符串**，如 `"2026-09-29T12:00:00"`，**不是 epoch 浮点数**
- `day` 列是 `"YYYY-MM-DD"` 字符串
- 比较时间用字符串比较即可（ISO 格式字典序 = 时间序）
- 当前时区：UTC+8（深圳）

---

## 七、InsightConfig（新框架自用配置类）

在 `insights/models.py` 中定义，有默认值，不需要从生产 Config 复制。关键字段：
- `cache_ttl: int = 300`
- `default_days: int = 7`
- `default_limit: int = 100`
- `max_limit: int = 5000`
- `max_scan: int = 30000`
- `noise_ratio_cap: float = 0.6`
- `offline_days: int = 3`

---

## 八、api.py 门面类对 BehaviorService 的调用约定

`api.py` 的 `InsightService` 持有 `self.core = BehaviorService(self.repo, self.resolver, self.config)`。
BehaviorService 的方法返回 `dict`，api.py 负责包装成分页信封 `{"items": [...], "total": ..., "offset": ..., "limit": ..., "has_more": ...}`。

BehaviorService 必须实现的方法（api.py 会调用）：
- `coverage(tr: TimeRange) -> dict` — 数据覆盖率
- `usage(tr, room, category, entity_id) -> dict` — 设备使用统计
- `device_health(tr, room, category) -> dict` — 设备健康
- `anomaly_report(tr, room, category) -> dict` — 异常报告
- `behavior_insights(tr, room, category) -> dict` — 行为洞察
- `compare_insights(compare_days, room, category) -> dict` — 对比洞察
- `rhythm(tr, room) -> dict` — 作息节律
- `infer_activities(tr, rooms) -> dict` — 活动推断
- `user_persona(days) -> dict` — 用户画像
- `data_quality(tr) -> dict` — 数据质量

`TimeRange` 在 `insights/models.py` 中定义，有 `start_ts`/`end_ts`（epoch float）、`start_iso`/`end_iso`（ISO 字符串）、`days`、`split_days()` 方法。

**注意**：TimeRange 的 `start_ts`/`end_ts` 是 epoch float，但数据库的 `ts` 列是 ISO 字符串。查询时需要用 `start_iso`/`end_iso` 与数据库比较，或者用 `datetime.fromtimestamp(ts).isoformat()` 转换。
