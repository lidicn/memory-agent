# vMA-1.3 多模态统一：unified_events VIEW 字段语义

> 版本：v1.0 · 2026-09-29
> 状态：PoC 放行（DCD 裁定 `20260929-vMA-1.3-view方案PoC-放行.md`）

## 概述

`unified_events` 是一个**只读 SQL VIEW**，将三张异构事件表统一为单一查询入口：

| 源表 | source 值 | 说明 |
|------|----------|------|
| `events` | `device` | HA 设备状态变化事件 |
| `behavior_events` | `vision` | 视觉行为识别（VLM/TV端），仅 `status='ok'` 的行 |
| `perception_events` | `perception` | 统一感知总线（边缘AI/VLM/sensor） |

**VIEW 只读，绝不动写入路径。** 所有 INSERT/UPDATE 仍走各自原表。

## 字段定义

| 字段 | 类型 | 说明 |
|------|------|------|
| `event_id` | TEXT | 事件唯一 ID（events.id / behavior_events.id / perception_events.event_id） |
| `server_ts` | TEXT | 服务端接收时间 ISO8601 |
| `day` | TEXT | 日期 'YYYY-MM-DD'，冗余列方便按天聚合 |
| `room` | TEXT | 房间名 |
| `source` | TEXT | 来源：`device` / `vision` / `perception` |
| `event_type` | TEXT | 事件类型（语义见下） |
| `person` | TEXT | 关联人员名（可空） |
| `entity_id` | TEXT | 关联实体 ID（device=HA entity_id, vision=camera_src, perception=entity_id） |
| `confidence` | REAL | 置信度（device 为 NULL，vision/perception 为 VLM/AI 置信度） |
| `payload` | TEXT | 原始 JSON 载荷（device=`attrs_json`, vision=`json_object(scene,count,camera_src)`, perception=`payload_json`） |

> 2026-10-02 更正：本行原写作 `raw_json`，视图从未有过这一列（DDL 里是 `AS payload`）。
> 按旧文写 SQL 会直接 `no such column: raw_json`。列名以
> `doc/路线图与规划/unified_events视图字段语义_20261001.md` 与
> `tests/test_vma13_unified_events_contract.py` 的十字列契约为准。

## event_type 语义映射（关键）

| source | event_type 构成 | 示例 |
|--------|----------------|------|
| `device` | `entity_id || ':' || action` | `light.livingroom_main:on` |
| `vision` | `action`（VLM 输出的动作描述） | `坐在电竞沙发看书` |
| `perception` | `kind`（感知事件类型） | `face_known` / `motion` / `human` |

**注意**：device 的 event_type 是 `entity:action` 拼接，不是单纯的 action。查询时需注意区分。

## 过滤规则

- `behavior_events` 仅包含 `status = 'ok'` 的行。`vlm_failed` / `low_confidence` / `skipped` 行被排除。
- `events` 和 `perception_events` 全量包含。

## 行数契约

```
VIEW 行数 = COUNT(events) + COUNT(behavior_events WHERE status='ok') + COUNT(perception_events)
```

实测（2026-09-29）：
- device: 1,053,548
- vision: 3,374
- perception: 3,553
- **合计: 1,060,475** ✅

## MCP 工具

### `query_unified_events(person, room, start, end, days, source, limit, offset)`

- **只读工具**，read scope
- 参数：
  - `person`: 人名模糊过滤（可选）
  - `room`: 房间名模糊过滤（可选）
  - `start` / `end`: ISO 时间范围（可选，未指定时用 days）
  - `days`: 窗口天数，默认 7
  - `source`: 来源过滤 `device|vision|perception`（可选）
  - `limit`: 返回条数，默认 200，上限 2000
  - `offset`: 分页偏移
- 返回：`{total, count, offset, limit, has_more, next_offset, events[]}`

## 设计决策

1. **为什么用 VIEW 而不是新表**：VIEW 是动态的，自动反映源表变化，无需双写/同步，无数据一致性风险。
2. **为什么不接 insights**：insights 已有成熟的查询优化（针对性索引、几万行代码），改走 VIEW 是拿成熟逻辑换未经验证的新路径——风险大、收益小。
3. **为什么 vision 只取 status='ok'**：失败行无有效行为数据，纳入会污染统计。
4. **person 字段从 JSON 提取**：behavior_events 的 persons_json 是数组，取 `$[0].name`（主要人员）；perception_events 从 payload_json 提取。

## 后续演进（需 DCD 评审）

- 接入第二个真实消费方后，升级为 vMA-1.3 正式立项
- 跨模态检索（向量）需等向量库成熟，且需要的是 embedding 索引不是 SQL VIEW
- 时间线/洞察模块接入 VIEW 需单独评审
