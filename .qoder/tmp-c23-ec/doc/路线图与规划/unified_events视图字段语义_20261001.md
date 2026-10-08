# unified_events 视图字段语义（vMA-1.3 PoC）

- 日期：2026-10-01
- 依据：DCD《20260929-vMA-1.3-view方案PoC-放行》
- 实现位置：`src/memory_agent/store.py`（`_SCHEMA_SQL` 建库块 + `init_schema()` 迁移块，两处定义逐列一致）
- 查询入口：`Store.query_unified_events()` / MCP 只读工具 `query_unified_events`（read scope）

## 性质

`CREATE VIEW IF NOT EXISTS unified_events` —— **纯只读视图，零双写、零存储**，
三个源表 UNION ALL 实时计算。视图不含任何 INSERT/UPDATE/DELETE 触发器，
写入路径（`events`/`behavior_events`/`perception_events` 的落库函数）与视图无耦合。

## 统一行格式

`(event_id, server_ts, day, room, source, event_type, person, entity_id, confidence, payload)`

## 三源字段映射

| 统一字段 | events（HA 设备事件） | behavior_events（视觉行为） | perception_events（感知总线） |
|---|---|---|---|
| event_id | `id` | `CAST(id AS TEXT)` | `COALESCE(event_id, CAST(id AS TEXT))` |
| server_ts | `ts` | `server_ts` | `server_ts` |
| day | `day` | `day` | `day` |
| room | `room` | `room` | `COALESCE(room, '')` |
| source | `'device'` | `'vision'` | `'perception'` |
| **event_type** | `entity_id \|\| ':' \|\| action`（即 `entity:action`，如 `light.bedroom:on`） | `COALESCE(action, '')`（动作描述，如 `坐在沙发看书`） | `kind`（感知类别，如 `face_known`/`human`/`motion`） |
| person | `person` | `json_extract(persons_json, '$[0].name')`，空则 `''` | `json_extract(payload_json, '$.person')`，空则 `''` |
| entity_id | `entity_id` | `camera_src`（go2rtc 流名充当实体） | `entity_id` |
| confidence | `NULL`（设备事件无置信度） | `confidence` | `confidence` |
| payload | `COALESCE(attrs_json, '{}')` | `json_object('scene',…,'count',…,'camera_src',…)` | `payload_json` |

## event_type 语义裁定（DCD 约束 2）

`event_type` 是三源异构动作字段的**归一化承载列**，取值语义按 `source` 区分：

- `source='device'` → `entity:action` 复合格式（分号分隔实体与动作）；
- `source='vision'` → 视觉行为描述文本（VLM 产出的 action）；
- `source='perception'` → 感知事件 kind 枚举（face_known / face_unknown / human / pet / cry / gesture / day_night / fav_area / no_human / motion / object）。

消费方（Agent/前端）跨源聚合时应先按 `source` 分桶，再解释 `event_type`，
不得把三种语义混作同一枚举。

## 过滤口径（行数契约）

- `behavior_events` 仅取 `status = 'ok'` 行（`vlm_failed` / `low_confidence` / `skipped` 不入视图）；
- `events`、`perception_events` 全量入视图。

因此契约恒等式为：

```
VIEW 行数 = COUNT(events) + COUNT(behavior_events WHERE status='ok') + COUNT(perception_events)
```

## 视图初始化与自愈（幂等）

- 新库：`_SCHEMA_SQL` 的 `CREATE VIEW IF NOT EXISTS` 建视图；
- 旧库：`init_schema()` 迁移块检测 `PRAGMA table_info(unified_events)`，
  若视图存在但缺 `event_id`/`entity_id`/`payload` 任一列（历史上曾有两版漂移定义），
  则 `DROP VIEW` 后按 canonical 定义重建 —— 只重建视图定义，不触碰任何源表数据；
- 重复执行 `init_schema()` 不报错（幂等），派生视图 `unified_events_daily` 所依赖的
  `day/source/room/person/server_ts` 列名在 canonical 中保持不变。

## MCP 工具

`query_unified_events(person, room, start, end, days, source, limit, offset)`：
- 只读 SELECT 上述十列，登记于 `mcp_scopes.REGISTERED_TOOLS`（read scope，非 WRITE_TOOLS）；
- 未授权/未登记工具由 `requires()` fail-close 拒绝（WO-MA-004 ② 现有机制原样沿用）。

## 相关文档

- `doc/多模态统一/unified_events_VIEW字段语义-v1.0.md`（PoC 期版本，列集以本文为准）
- `doc/架构/vMA-1.3_多模态统一VIEW方案.md`
- `doc/路线图与规划/vMA-1.3-PoC-多模态统一数据模型.md`
- 契约测试：`tests/test_vma13_unified_events_contract.py`、`tests/test_unified_events_view.py`
