# vMA-1.3 PoC：多模态统一数据模型草稿

> 目的：验证"把视觉/传感器/语义记忆三路数据统一到一个模型"是否可行。
> 演练方式：在副本库上跑一次迁移，不碰生产库。

## 当前三路数据现状

| 路 | 表 | 粒度 | 已有字段 |
|---|---|---|---|
| 视觉 | behavior_events | 每次 VLM 分析 | room, persons, action, scene, confidence, scene_graph_json |
| 感知总线 | perception_events | 每事件 | source, kind, room, entity_id, confidence, payload_json |
| 语义记忆 | agent_memories | 每条记忆 | text, member_id, trust, source, state, topic_key |

## 统一模型设计草案

核心思路：不建新表，用 `unified_events` 视图把三路数据按时间轴对齐。

```sql
-- PoC：统一时间轴视图（不建新表，纯视图）
CREATE VIEW IF NOT EXISTS unified_timeline AS
SELECT
  server_ts,
  'vision' as source,
  room,
  action as summary,
  json_extract(persons_json, '$') as detail,
  confidence
FROM behavior_events
UNION ALL
SELECT
  server_ts,
  'perception' as source,
  room,
  kind as summary,
  payload_json as detail,
  confidence
FROM perception_events
UNION ALL
SELECT
  created_at as server_ts,
  'memory' as source,
  '' as room,
  text as summary,
  json_object('member_id', member_id, 'trust', trust, 'topic_key', topic_key) as detail,
  trust as confidence
FROM agent_memories
WHERE state = 'live';
```

## PoC 验收标准

1. 视图能建成功（SQL 语法正确）
2. 能按时间范围查询统一时间轴
3. 能按 room 过滤
4. 能按 source 过滤
5. 副本库迁移后数据不丢失

## 演练步骤

1. 在 NAS 上复制 /data/memory_agent.db → /tmp/ma_poc_test.db
2. 在副本库上执行上述 SQL
3. 查询验证
4. 报告结果

## 如果 PoC 挂了

- 视图建不出来 → 说明三路数据结构差异太大，需要更多设计
- 查询性能太差 → 需要加索引或改模型
- 数据丢失 → 说明模型不兼容现有数据

## 出口

PoC 通过 → 1.3 正式启动
PoC 不通过 → 记录原因，不硬上
