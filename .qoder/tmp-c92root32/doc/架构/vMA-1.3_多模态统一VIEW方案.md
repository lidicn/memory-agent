# vMA-1.3 多模态统一数据模型（VIEW 方案）

> DCD 裁定书：20260929-vMA-1.3-view方案PoC-放行
> 方案：VIEW（不建新表，不双写，零存储，永远实时一致）

---

## 1. 概述

`unified_events` 是一个只读 VIEW，把三张异构表 UNION ALL 成统一格式：

| 来源表 | source 值 | 行数（PoC 时） | 说明 |
|--------|-----------|----------------|------|
| `events` | `device` | ~1,069,732 | HA 设备事件（传感器/开关/灯等） |
| `behavior_events` | `vision` | ~3,334（status='ok'） | TV 端 ArcFace + VLM 视觉行为 |
| `perception_events` | `perception` | ~3,513 | 统一感知总线（边缘 AI / VLM / sensor） |

---

## 2. 字段映射（关键）

| VIEW 字段 | device 来源 | vision 来源 | perception 来源 |
|-----------|-------------|-------------|-----------------|
| `server_ts` | `events.ts` | `behavior_events.server_ts` | `perception_events.server_ts` |
| `source` | 固定 `'device'` | 固定 `'vision'` | 固定 `'perception'` |
| `person` | `events.person` | `json_extract(persons_json, '$[0].name')` | `json_extract(payload_json, '$.person')` |
| `room` | `events.room` | `behavior_events.room` | `perception_events.room` |
| `event_type` | `entity_id || ':' || action` | `behavior_events.action` | `perception_events.kind` |
| `payload` | `events.attrs_json` | `json_object(scene, count, camera_src)` | `perception_events.payload_json` |
| `confidence` | `NULL` | `behavior_events.confidence` | `perception_events.confidence` |
| `day` | `events.day` | `behavior_events.day` | `perception_events.day` |

**注意**：
- `person` 空字符串 `''` 表示未识别（不是 NULL）
- `event_type` 三种来源语义不同：device 是 `entity:action`，vision 是 `action`，perception 是 `kind`
- vision 只包含 `status='ok'` 的行（失败行已过滤）

---

## 3. MCP 工具

### `query_unified_events(person, room, start, end, source, limit, order)`

只读工具，走 read scope。

**参数**：
- `person`：成员名（可选）
- `room`：房间名（可选）
- `start`：起始时间 ISO8601（可选）
- `end`：结束时间 ISO8601（可选）
- `source`：来源过滤 `device/vision/perception`（可选）
- `limit`：返回条数，默认 100，最大 500
- `order`：排序 `desc`（默认）/ `asc`

**返回**：
```json
{
  "ok": true,
  "total": 1076579,
  "count": 100,
  "rows": [...]
}
```

---

## 4. 性能（PoC 实测，J3455 1.5GHz）

| 查询 | 耗时 |
|------|------|
| 全表 COUNT(*) | 1.09s |
| 按 room + 7天聚合 | 0.70s |

**结论**：性能够用，无需物化表。数据量涨到 500 万行再评估。

---

## 5. 边界（DCD 放行时明确）

- **只读**：绝不改写入路径
- **只做 MCP 工具**：insights / 时间线 / 跨模态检索暂不接入
- **写 token 不能调**：走 read scope

---

## 6. 行数契约

```
unified_events 行数 = events 行数 + (behavior_events WHERE status='ok') + perception_events 行数
```

如果未来某张源表加了新 kind/字段但 VIEW 没同步，契约测试会暴露。
