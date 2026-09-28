-- =============================================================================
-- vMA-1.3 多模态统一数据模型 —— VIEW 方案
-- 裁定：DCD 20260928-MA多模态统一-打回.md
-- 原则：零双写、零存储、永远实时一致
-- 三张表 UNION ALL 成统一视图，查询时自动从原表实时计算
-- =============================================================================

-- 统一事件视图：把 events / behavior_events / perception_events 拼成一张表
-- 统一字段：
--   server_ts   事件时间（ISO8601）
--   source      来源：device | vision | perception
--   person      人员（空串=未识别）
--   room        房间
--   event_type  事件类型
--   payload     JSON 扩展数据
--   confidence  置信度（设备事件为 NULL）
-- =============================================================================

CREATE VIEW IF NOT EXISTS unified_events AS

  -- 来源 1：设备事件（HA 采集）
  SELECT
    e.ts AS server_ts,
    'device' AS source,
    e.person,
    e.room,
    e.entity_id || ':' || e.action AS event_type,
    COALESCE(e.attrs_json, '{}') AS payload,
    NULL AS confidence,
    e.day AS day
  FROM events e

  UNION ALL

  -- 来源 2：视觉行为事件（TV端 ArcFace + VLM）
  SELECT
    be.server_ts,
    'vision' AS source,
    -- persons_json 是 JSON 数组，取第一个元素的 name 作为 person
    COALESCE(
      json_extract(be.persons_json, '$[0].name'),
      ''
    ) AS person,
    be.room,
    be.action AS event_type,
    -- 把 scene/count/camera_src 拼成 JSON
    json_object(
      'scene', COALESCE(be.scene, ''),
      'count', COALESCE(be.count, 0),
      'camera_src', COALESCE(be.camera_src, '')
    ) AS payload,
    be.confidence,
    be.day AS day
  FROM behavior_events be
  WHERE be.status = 'ok'

  UNION ALL

  -- 来源 3：统一感知总线（边缘 AI / VLM / sensor）
  SELECT
    pe.server_ts,
    'perception' AS source,
    -- perception_events 没有 person 字段，从 payload_json 里取
    COALESCE(
      json_extract(pe.payload_json, '$.person'),
      ''
    ) AS person,
    COALESCE(pe.room, '') AS room,
    pe.kind AS event_type,
    pe.payload_json AS payload,
    pe.confidence,
    pe.day AS day
  FROM perception_events pe;

-- =============================================================================
-- 统一事件统计视图（按天聚合，方便查询"今天干了啥"）
-- =============================================================================

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

-- =============================================================================
-- 验证查询（PoC 用）
-- =============================================================================
-- 1. 查最近 7 天每天各来源的事件数
-- SELECT day, source, event_count FROM unified_events_daily
-- WHERE day >= date('now', '-7 days') ORDER BY day DESC, source;

-- 2. 查 lidicn 最近 24 小时的所有事件
-- SELECT server_ts, source, room, event_type FROM unified_events
-- WHERE person = 'lidicn' AND server_ts >= datetime('now', '-1 day')
-- ORDER BY server_ts DESC LIMIT 50;

-- 3. 查客厅最近 1 小时的事件
-- SELECT server_ts, source, person, event_type FROM unified_events
-- WHERE room = '客厅' AND server_ts >= datetime('now', '-1 hour')
-- ORDER BY server_ts DESC LIMIT 100;
