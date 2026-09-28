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