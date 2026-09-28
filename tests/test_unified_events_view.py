"""vMA-1.3 unified_events VIEW 契约测试"""
import pytest
import sqlite3
import tempfile
import os


def _make_db():
    """建一个内存 DB，建三张源表 + VIEW"""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE events (
            id TEXT PRIMARY KEY, ts TEXT, day TEXT, room TEXT DEFAULT '',
            entity_id TEXT, domain TEXT DEFAULT '', action TEXT DEFAULT '',
            person TEXT DEFAULT '', old_state TEXT, new_state TEXT, attrs_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE behavior_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, server_ts TEXT, device_ts INTEGER,
            day TEXT, room TEXT, camera_src TEXT, persons_json TEXT DEFAULT '[]',
            count INTEGER DEFAULT 0, action TEXT, scene TEXT, confidence REAL,
            appearance_json TEXT, trigger TEXT, vlm_latency_ms INTEGER,
            snapshot_path TEXT, raw_response TEXT, status TEXT DEFAULT 'ok'
        )
    """)
    conn.execute("""
        CREATE TABLE perception_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE, server_ts TEXT,
            day TEXT, source TEXT, kind TEXT, room TEXT, entity_id TEXT,
            confidence REAL, payload_json TEXT DEFAULT '{}', raw_event_json TEXT
        )
    """)
    conn.execute("""
        CREATE VIEW unified_events AS
            SELECT e.ts AS server_ts, 'device' AS source, e.person, e.room,
                   e.entity_id || ':' || e.action AS event_type,
                   COALESCE(e.attrs_json, '{}') AS payload, NULL AS confidence, e.day
            FROM events e
            UNION ALL
            SELECT be.server_ts, 'vision',
                   COALESCE(json_extract(be.persons_json, '$[0].name'), ''),
                   be.room, be.action,
                   json_object('scene', COALESCE(be.scene, ''),
                               'count', COALESCE(be.count, 0),
                               'camera_src', COALESCE(be.camera_src, '')),
                   be.confidence, be.day
            FROM behavior_events be WHERE be.status = 'ok'
            UNION ALL
            SELECT pe.server_ts, 'perception',
                   COALESCE(json_extract(pe.payload_json, '$.person'), ''),
                   COALESCE(pe.room, ''), pe.kind, pe.payload_json, pe.confidence, pe.day
            FROM perception_events pe
    """)
    return conn


class TestUnifiedEventsContract:
    """行数契约：VIEW 行数 = 三源之和（behavior_events 过滤 status='ok'）"""

    def test_count_contract(self):
        conn = _make_db()
        # 插入 3 条 device
        conn.execute("INSERT INTO events VALUES ('e1','2026-09-28T10:00:00','2026-09-28','客厅','light.1','light','on','lidicn',NULL,NULL,'{}')")
        conn.execute("INSERT INTO events VALUES ('e2','2026-09-28T11:00:00','2026-09-28','书房','light.2','light','off','',NULL,NULL,'{}')")
        conn.execute("INSERT INTO events VALUES ('e3','2026-09-28T12:00:00','2026-09-28','客厅','sensor.temp','sensor','state','',NULL,NULL,'{}')")
        # 插入 2 条 vision（1 ok + 1 failed）
        conn.execute("INSERT INTO behavior_events (server_ts,day,room,persons_json,action,confidence,status) VALUES ('2026-09-28T10:30:00','2026-09-28','客厅','[{\"name\":\"lidicn\"}]','坐在沙发',0.9,'ok')")
        conn.execute("INSERT INTO behavior_events (server_ts,day,room,persons_json,action,confidence,status) VALUES ('2026-09-28T10:31:00','2026-09-28','客厅','[]','unknown',0.1,'vlm_failed')")
        # 插入 1 条 perception
        conn.execute("INSERT INTO perception_events (event_id,server_ts,day,source,kind,room,confidence,payload_json) VALUES ('p1','2026-09-28T10:35:00','2026-09-28','edge_ai','human','客厅',0.8,'{\"person\":\"lidicn\"}')")

        device_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        vision_ok_count = conn.execute("SELECT COUNT(*) FROM behavior_events WHERE status='ok'").fetchone()[0]
        perception_count = conn.execute("SELECT COUNT(*) FROM perception_events").fetchone()[0]
        view_count = conn.execute("SELECT COUNT(*) FROM unified_events").fetchone()[0]

        expected = device_count + vision_ok_count + perception_count
        assert view_count == expected, f"VIEW行数 {view_count} != 预期 {expected}"
        assert view_count == 5, f"应该是 5 行，实际 {view_count}"

    def test_source_values(self):
        conn = _make_db()
        conn.execute("INSERT INTO events VALUES ('e1','2026-09-28T10:00:00','2026-09-28','客厅','light.1','light','on','lidicn',NULL,NULL,'{}')")
        conn.execute("INSERT INTO behavior_events (server_ts,day,room,persons_json,action,confidence,status) VALUES ('2026-09-28T10:30:00','2026-09-28','客厅','[{\"name\":\"Emily\"}]','看电视',0.9,'ok')")
        conn.execute("INSERT INTO perception_events (event_id,server_ts,day,source,kind,room,confidence,payload_json) VALUES ('p1','2026-09-28T10:35:00','2026-09-28','vlm','face_known','客厅',0.8,'{\"person\":\"Kevin\"}')")

        sources = [r[0] for r in conn.execute("SELECT DISTINCT source FROM unified_events ORDER BY source")]
        assert sources == ["device", "perception", "vision"]

    def test_person_filter(self):
        conn = _make_db()
        conn.execute("INSERT INTO events VALUES ('e1','2026-09-28T10:00:00','2026-09-28','客厅','light.1','light','on','lidicn',NULL,NULL,'{}')")
        conn.execute("INSERT INTO events VALUES ('e2','2026-09-28T11:00:00','2026-09-28','书房','light.2','light','off','Emily',NULL,NULL,'{}')")

        lidicn_rows = conn.execute("SELECT COUNT(*) FROM unified_events WHERE person='lidicn'").fetchone()[0]
        emily_rows = conn.execute("SELECT COUNT(*) FROM unified_events WHERE person='Emily'").fetchone()[0]
        assert lidicn_rows == 1
        assert emily_rows == 1

    def test_room_filter(self):
        conn = _make_db()
        conn.execute("INSERT INTO events VALUES ('e1','2026-09-28T10:00:00','2026-09-28','客厅','light.1','light','on','lidicn',NULL,NULL,'{}')")
        conn.execute("INSERT INTO events VALUES ('e2','2026-09-28T11:00:00','2026-09-28','书房','light.2','light','off','',NULL,NULL,'{}')")

        living = conn.execute("SELECT COUNT(*) FROM unified_events WHERE room='客厅'").fetchone()[0]
        study = conn.execute("SELECT COUNT(*) FROM unified_events WHERE room='书房'").fetchone()[0]
        assert living == 1
        assert study == 1
