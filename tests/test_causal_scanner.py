"""P5e 因果归因主动告警测试。"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.causal_scanner import CausalScanner, ALERT_ACTION


def _make_store(tmp_path):
    """创建一个带 behavior_events 表和 members 表的内存 store mock。"""
    import sqlite3
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE behavior_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_ts TEXT, device_ts TEXT, day TEXT, room TEXT, camera_src TEXT,
        persons_json TEXT, count INTEGER, action TEXT, scene TEXT, confidence REAL,
        appearance_json TEXT, trigger TEXT, vlm_latency_ms INTEGER,
        snapshot_path TEXT, raw_response TEXT, status TEXT, client TEXT
    )""")
    db.execute("""CREATE TABLE members (
        member_id TEXT PRIMARY KEY, name TEXT, role TEXT
    )""")
    db.commit()

    store = MagicMock()
    store._lock = MagicMock()
    store._lock.__enter__ = MagicMock()
    store._lock.__exit__ = MagicMock(return_value=False)
    store.connect.return_value = db

    def list_members():
        rows = db.execute("SELECT member_id, name FROM members").fetchall()
        return [dict(r) for r in rows]
    store.list_members.side_effect = list_members

    def insert_behavior_event(payload):
        ts = payload.get("server_ts") or datetime.now().isoformat()
        persons = payload.get("persons") or []
        cur = db.execute(
            "INSERT INTO behavior_events(server_ts, day, room, camera_src, persons_json, count, action, scene, confidence, trigger, client, status, raw_response) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ts, payload.get("day") or ts[:10], payload.get("room") or "",
                payload.get("camera_src") or "",
                json.dumps(persons, ensure_ascii=False),
                int(payload.get("count") or 0),
                payload.get("action"), payload.get("scene"),
                payload.get("confidence"), payload.get("trigger") or "manual",
                payload.get("client") or "", payload.get("status") or "ok",
                payload.get("raw_response"),
            ),
        )
        db.commit()
        return int(cur.lastrowid or 0)
    store.insert_behavior_event.side_effect = insert_behavior_event

    return store, db


def _seed_events(db, person, days=14, action="enter_room", room="livingroom"):
    """播种 N 天的行为事件，前半段早到、后半段晚到（制造变化）。"""
    now = datetime.now()
    for i in range(days):
        dt = now - timedelta(days=days - i)
        # 前 7 天 18:00 到家，后 7 天 21:00 到家（显著变化）
        if i < days // 2:
            dt = dt.replace(hour=18, minute=0)
        else:
            dt = dt.replace(hour=21, minute=0)
        db.execute(
            "INSERT INTO behavior_events(server_ts, day, room, persons_json, action, scene, trigger, status) VALUES(?,?,?,?,?,?,?,?)",
            (
                dt.isoformat(), dt.strftime("%Y-%m-%d"), room,
                json.dumps([{"name": person}], ensure_ascii=False),
                action, room, "test", "ok",
            ),
        )
    db.commit()


class TestCausalScanner:

    def test_no_members_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            store, db = _make_store(td)
            scanner = CausalScanner(store, min_days=2)
            result = scanner.scan()
            assert result["scanned_members"] == 0
            assert result["alerts_written"] == 0

    def test_detects_change_and_writes_alert(self):
        with tempfile.TemporaryDirectory() as td:
            store, db = _make_store(td)
            db.execute("INSERT INTO members(member_id, name) VALUES(?, ?)", ("testuser", "Test User"))
            db.commit()
            _seed_events(db, "testuser", days=14)

            scanner = CausalScanner(store, metrics=("arrival_time",), min_days=2)
            result = scanner.scan()

            assert result["scanned_members"] == 1
            assert result["alerts_written"] >= 1
            assert result["details"][0]["person"] == "testuser"
            assert result["details"][0]["metric"] == "arrival_time"
            assert result["details"][0]["direction"] == "increase"

            # 验证写入的告警事件
            rows = db.execute(
                "SELECT * FROM behavior_events WHERE action = ?",
                (ALERT_ACTION,),
            ).fetchall()
            assert len(rows) >= 1
            alert = dict(rows[0])
            assert alert["scene"].startswith("arrival_time:")
            assert "testuser" in alert["persons_json"]
            detail = json.loads(alert["raw_response"])
            assert detail["metric"] == "arrival_time"
            assert detail["direction"] == "increase"
            assert detail["effect_size"] > 0

    def test_dedup_same_day_same_metric(self):
        with tempfile.TemporaryDirectory() as td:
            store, db = _make_store(td)
            db.execute("INSERT INTO members(member_id, name) VALUES(?, ?)", ("testuser", "Test User"))
            db.commit()
            _seed_events(db, "testuser", days=14)

            scanner = CausalScanner(store, metrics=("arrival_time",), min_days=2)
            r1 = scanner.scan()
            r2 = scanner.scan()

            assert r1["alerts_written"] >= 1
            assert r2["alerts_written"] == 0  # 同一天去重
            assert r2["skipped_dedup"] >= 1

    def test_no_change_no_alert(self):
        with tempfile.TemporaryDirectory() as td:
            store, db = _make_store(td)
            db.execute("INSERT INTO members(member_id, name) VALUES(?, ?)", ("testuser", "Test User"))
            db.commit()
            # 全部 18:00 到家，无变化
            now = datetime.now()
            for i in range(14):
                dt = (now - timedelta(days=14 - i)).replace(hour=18, minute=0)
                db.execute(
                    "INSERT INTO behavior_events(server_ts, day, room, persons_json, action, scene, trigger, status) VALUES(?,?,?,?,?,?,?,?)",
                    (dt.isoformat(), dt.strftime("%Y-%m-%%d"), "livingroom",
                     json.dumps([{"name": "testuser"}]), "enter_room", "livingroom", "test", "ok"),
                )
            db.commit()

            scanner = CausalScanner(store, metrics=("arrival_time",), min_days=2)
            result = scanner.scan()
            assert result["alerts_written"] == 0

    def test_insufficient_data_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            store, db = _make_store(td)
            db.execute("INSERT INTO members(member_id, name) VALUES(?, ?)", ("testuser", "Test User"))
            db.commit()
            # 只有 3 天数据，min_days=5 应该跳过
            _seed_events(db, "testuser", days=3)

            scanner = CausalScanner(store, metrics=("arrival_time",), min_days=5)
            result = scanner.scan()
            assert result["alerts_written"] == 0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
