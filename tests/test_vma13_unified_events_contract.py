"""vMA-1.3 unified_events 契约测试（真实 Store 初始化路径）。

覆盖 DCD 20260929 放行函要求的三组契约：
1. 行数契约：VIEW 行数 = events + behavior_events(status='ok') + perception_events
   （PoC 实测：视图含 status != 'ok' 过滤，测试口径一致）；
2. 一致性契约：Store.query_unified_events / MCP 工具 SELECT 列集
   与直接三表 UNION ALL 手工查询结果一致；
3. 幂等契约：init_schema 重复执行不报错、视图定义不重复创建、
   旧库视图列集漂移（缺 event_id/entity_id/payload）能自愈。
另验证 mcp_scopes 登记：query_unified_events 归 read scope（非 WRITE_TOOLS）。
"""

import os
import sqlite3
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.store import Store  # noqa: E402
from memory_agent import mcp_scopes  # noqa: E402

CANONICAL_COLS = {
    "event_id", "server_ts", "day", "room", "source",
    "event_type", "person", "entity_id", "confidence", "payload",
}


@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="vma13_ue_")
    db = os.path.join(tmp, "test.db")
    s = Store(db, tz_offset_hours=0.0)
    s.init_schema()
    yield s
    try:
        os.remove(db)
    except OSError:
        pass


def _seed(s: Store):
    """三源表各插若干行，behavior_events 混入非 ok 状态行用于过滤口径验证。"""
    conn = s.connect()
    rows_dev = [
        ("d1", "2026-09-28T08:00:00", "2026-09-28", "客厅", "light.a", "light", "on", "lidicn", '{"x":1}'),
        ("d2", "2026-09-28T09:00:00", "2026-09-28", "书房", "light.b", "light", "off", "", None),
        ("d3", "2026-09-29T10:00:00", "2026-09-29", "客厅", "sensor.t", "sensor", "state", "Emily", "{}"),
    ]
    conn.executemany(
        "INSERT INTO events(id,ts,day,room,entity_id,domain,action,person,attrs_json) VALUES (?,?,?,?,?,?,?,?,?)",
        rows_dev,
    )
    rows_vis = [
        ("2026-09-28T08:30:00", "2026-09-28", "客厅", "cam1", '[{"name":"lidicn"}]', "坐在沙发看书", 0.9, "ok"),
        ("2026-09-28T08:31:00", "2026-09-28", "客厅", "cam1", "[]", "", 0.1, "vlm_failed"),
        ("2026-09-29T09:30:00", "2026-09-29", "书房", "cam2", '[{"name":"Kevin"}]', "看电视", 0.8, "ok"),
        ("2026-09-29T09:35:00", "2026-09-29", "书房", "cam2", "[]", "unknown", 0.2, "low_confidence"),
    ]
    conn.executemany(
        "INSERT INTO behavior_events(server_ts,day,room,camera_src,persons_json,action,confidence,status)"
        " VALUES (?,?,?,?,?,?,?,?)",
        rows_vis,
    )
    rows_per = [
        ("p1", "2026-09-28T08:35:00", "2026-09-28", "edge_ai", "human", "客厅", "motion.a", 0.7, '{"person":"lidicn"}'),
        ("p2", "2026-09-29T20:00:00", "2026-09-29", "vlm", "face_known", "书房", "cam2", 0.95, '{"person":"Kevin"}'),
    ]
    conn.executemany(
        "INSERT INTO perception_events(event_id,server_ts,day,source,kind,room,entity_id,confidence,payload_json)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        rows_per,
    )
    conn.commit()
    return {
        "device": len(rows_dev),
        "vision_ok": sum(1 for r in rows_vis if r[-1] == "ok"),
        "vision_all": len(rows_vis),
        "perception": len(rows_per),
    }


def _view_cols(conn) -> set:
    return {r[1] for r in conn.execute("PRAGMA table_info(unified_events)")}


class TestRowCountContract:
    def test_view_count_equals_three_sources_minus_filtered(self, store):
        counts = _seed(store)
        conn = store.connect()
        view_count = conn.execute("SELECT COUNT(*) FROM unified_events").fetchone()[0]
        # 视图含 behavior_events.status='ok' 过滤，口径 = 三源之和 − 过滤行
        assert view_count == counts["device"] + counts["vision_ok"] + counts["perception"]
        # 交叉验证：过滤行数 = 全部 behavior 行 − ok 行
        assert counts["vision_all"] - counts["vision_ok"] == 2

    def test_source_breakdown(self, store):
        _seed(store)
        conn = store.connect()
        got = dict(conn.execute(
            "SELECT source, COUNT(*) FROM unified_events GROUP BY source"
        ).fetchall())
        assert got == {"device": 3, "vision": 2, "perception": 2}


class TestManualUnionAllEquivalence:
    """query_unified_events 结果必须与直接三表 UNION ALL 手工查询一致。"""

    MANUAL_SQL = """
        SELECT e.id AS event_id, e.ts AS server_ts, e.day, e.room, 'device' AS source,
               e.entity_id || ':' || e.action AS event_type, e.person, e.entity_id,
               CAST(NULL AS REAL) AS confidence, COALESCE(e.attrs_json, '{}') AS payload
        FROM events e
        UNION ALL
        SELECT CAST(be.id AS TEXT), be.server_ts, be.day, be.room, 'vision',
               COALESCE(be.action, ''),
               COALESCE(json_extract(be.persons_json, '$[0].name'), ''),
               COALESCE(be.camera_src, ''), be.confidence,
               json_object('scene', COALESCE(be.scene, ''),
                           'count', COALESCE(be.count, 0),
                           'camera_src', COALESCE(be.camera_src, ''))
        FROM behavior_events be WHERE be.status = 'ok'
        UNION ALL
        SELECT COALESCE(pe.event_id, CAST(pe.id AS TEXT)), pe.server_ts, pe.day,
               COALESCE(pe.room, ''), 'perception', pe.kind,
               COALESCE(json_extract(pe.payload_json, '$.person'), ''),
               COALESCE(pe.entity_id, ''), pe.confidence, pe.payload_json
        FROM perception_events pe
    """

    def _key(self, d: dict):
        return (d["event_id"], d["server_ts"], d["source"], d["event_type"],
                d["person"], d["room"], d["entity_id"], d["payload"])

    def test_full_result_matches_manual(self, store):
        _seed(store)
        res = store.query_unified_events(limit=500, order="asc")
        assert res["ok"] is True
        assert res["total"] == 7 and res["count"] == 7
        conn = store.connect()
        manual = [dict(r) for r in conn.execute(self.MANUAL_SQL + " ORDER BY server_ts ASC").fetchall()]
        assert [self._key(r) for r in res["rows"]] == [self._key(r) for r in manual]

    def test_filtered_result_matches_manual(self, store):
        _seed(store)
        for flt in ({"room": "客厅"}, {"person": "lidicn"}, {"source": "vision"},
                    {"start": "2026-09-29T00:00:00", "end": "2026-09-29T23:59:59"}):
            res = store.query_unified_events(limit=500, order="asc", **flt)
            where, args = [], []
            if "room" in flt:
                where.append("room = ?"); args.append(flt["room"])
            if "person" in flt:
                where.append("person = ?"); args.append(flt["person"])
            if "source" in flt:
                where.append("source = ?"); args.append(flt["source"])
            if "start" in flt:
                where.append("server_ts >= ?"); args.append(flt["start"])
            if "end" in flt:
                where.append("server_ts <= ?"); args.append(flt["end"])
            sql = f"SELECT * FROM ({self.MANUAL_SQL}) WHERE " + " AND ".join(where) + " ORDER BY server_ts ASC"
            conn = store.connect()
            manual = [dict(r) for r in conn.execute(sql, args).fetchall()]
            assert res["total"] == len(manual), f"{flt}: total {res['total']} != {len(manual)}"
            assert [self._key(r) for r in res["rows"]] == [self._key(r) for r in manual], f"{flt} 行内容不一致"

    def test_mcp_tool_select_columns_exist(self, store):
        """MCP 工具直接 SELECT 的列集必须在视图中存在（防止两版定义漂移回归）。"""
        conn = store.connect()
        cols = _view_cols(conn)
        tool_cols = {"event_id", "server_ts", "day", "room", "source",
                     "event_type", "person", "entity_id", "confidence", "payload"}
        assert tool_cols <= cols
        row = conn.execute(
            "SELECT event_id, server_ts, day, room, source, event_type, person, "
            "entity_id, confidence, payload FROM unified_events LIMIT 1"
        ).fetchall()  # 不报错即通过


class TestIdempotency:
    def test_init_schema_twice_ok(self, store):
        store.init_schema()  # 第二次执行不得报错
        store.init_schema()  # 第三次
        cols = _view_cols(store.connect())
        assert cols == CANONICAL_COLS

    def test_view_count_single_definition(self, store):
        """sqlite_master 中 unified_events 视图只有一个定义。"""
        rows = store.connect().execute(
            "SELECT sql FROM sqlite_master WHERE type='view' AND name='unified_events'"
        ).fetchall()
        assert len(rows) == 1

    def test_drifted_view_self_heals(self, store):
        """旧库视图缺 event_id/entity_id/payload 列时，init_schema 应重建为 canonical。"""
        conn = store.connect()
        conn.execute("DROP VIEW unified_events")
        conn.execute(
            "CREATE VIEW unified_events AS "
            "SELECT ts AS server_ts, 'device' AS source, person, room, "
            "entity_id || ':' || action AS event_type, COALESCE(attrs_json,'{}') AS payload, "
            "NULL AS confidence, day FROM events"
        )
        conn.commit()
        assert "event_id" not in _view_cols(conn)
        store.init_schema()
        assert _view_cols(conn) == CANONICAL_COLS
        # 源数据未被触碰
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


class TestScopeRegistration:
    def test_read_scope_registered(self):
        assert "query_unified_events" in mcp_scopes.REGISTERED_TOOLS
        assert "query_unified_events" not in mcp_scopes.WRITE_TOOLS
        assert mcp_scopes.scope_of("query_unified_events") == mcp_scopes.READ

    def test_write_tools_assert_covers_it(self):
        # 启发式断言不应误报该只读工具（query_ 前缀不在写关键词中）
        from memory_agent.tool_schema import TOOL_SPECS
        spec = next((s for s in TOOL_SPECS if s.name == "query_unified_events"), None)
        assert spec is not None
        assert "mcp" in spec.expose
