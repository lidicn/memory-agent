-- =============================================================================
-- vMA-1.3 多模态统一数据模型 —— unified_events VIEW（人读镜像）
-- 权威定义在 store.py 的 _SCHEMA_SQL 中（init_schema 每次启动幂等执行）；
-- 本文件为 DCD 20260929 放行函「VIEW 字段语义要写进 doc」提供可独立阅读的副本。
-- 逐列一致性由 tests/test_vma13_unified_events_contract.py 的镜像断言兜底：
-- 改了 _SCHEMA_SQL 的视图列集而没同步这里，测试会判红。
-- =============================================================================

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
