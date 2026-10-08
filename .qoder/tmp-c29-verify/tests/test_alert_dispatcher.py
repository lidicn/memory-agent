"""Phase 4.1 统一告警分发单飞 回归测试。"""

import os
import sys
import time

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.alert_dispatcher import AlertDispatcher


def test_single_flight_cooldown():
    d = AlertDispatcher(default_cooldown_seconds=10)
    r1 = d.should_send("sess1", "stranger")
    assert r1["send"] is True
    assert r1["merged_count"] == 1
    r2 = d.should_send("sess1", "stranger")
    assert r2["send"] is False
    assert r2["merged_count"] == 2


def test_different_sessions_independent():
    d = AlertDispatcher(default_cooldown_seconds=10)
    d.should_send("sess1", "stranger")
    r = d.should_send("sess2", "stranger")
    assert r["send"] is True


def test_different_types_independent():
    d = AlertDispatcher(default_cooldown_seconds=10)
    d.should_send("sess1", "stranger")
    r = d.should_send("sess1", "known_person")
    assert r["send"] is True


def test_priority_override():
    d = AlertDispatcher(default_cooldown_seconds=0.1)
    d.should_send("sess1", "stranger", priority=1)
    time.sleep(0.06)  # 等待超过 cooldown/2 (0.05s)
    r = d.should_send("sess1", "stranger", priority=10)
    assert r["send"] is True
    assert r["reason"] == "高优先级覆盖"


def test_cooldown_expiry():
    d = AlertDispatcher(default_cooldown_seconds=0.05)
    d.should_send("sess1", "stranger")
    time.sleep(0.1)
    r = d.should_send("sess1", "stranger")
    assert r["send"] is True
    assert r["merged_count"] == 1


def test_get_stats():
    d = AlertDispatcher(default_cooldown_seconds=10)
    d.should_send("sess1", "stranger")
    d.should_send("sess1", "stranger")
    d.should_send("sess2", "stranger")
    global_stats = d.get_stats()
    assert global_stats["total_sessions"] == 2
    sess_stats = d.get_stats(session_id="sess1")
    assert sess_stats["types"]["stranger"]["count"] == 2


def test_clear():
    d = AlertDispatcher(default_cooldown_seconds=10)
    d.should_send("sess1", "stranger")
    d.clear(session_id="sess1")
    assert d.get_stats()["total_sessions"] == 0


def test_away_mode_uses_dispatcher():
    from memory_agent.away_mode import AwayModeManager
    from memory_agent.store import Store
    import tempfile
    tmp = tempfile.mkdtemp(prefix="mw_alert_")
    db = os.path.join(tmp, "test.db")
    store = Store(db, tz_offset_hours=0.0)
    store.init_schema()
    dispatcher = AlertDispatcher(default_cooldown_seconds=300)
    mgr = AwayModeManager(store, room="客厅", alert_dispatcher=dispatcher)
    mgr.handle_event("no_human", "客厅")
    r1 = mgr.handle_event("face_unknown", "客厅")
    assert r1["alert"] is True
    r2 = mgr.handle_event("face_unknown", "客厅")
    assert r2["alert"] is False
    assert r2["merged_count"] == 2


def test_away_mode_fallback_local():
    from memory_agent.away_mode import AwayModeManager
    from memory_agent.store import Store
    import tempfile
    tmp = tempfile.mkdtemp(prefix="mw_alert_fb_")
    db = os.path.join(tmp, "test.db")
    store = Store(db, tz_offset_hours=0.0)
    store.init_schema()
    mgr = AwayModeManager(store, room="客厅")
    mgr.handle_event("no_human", "客厅")
    r1 = mgr.handle_event("face_unknown", "客厅")
    assert r1["alert"] is True
    r2 = mgr.handle_event("face_unknown", "客厅")
    assert r2["alert"] is False


def test_vision_dispatcher_integration():
    d = AlertDispatcher(default_cooldown_seconds=300)
    r1 = d.should_send(session_id="vision:客厅", alert_type="stranger", priority=5, cooldown_seconds=300)
    assert r1["send"] is True
    r2 = d.should_send(session_id="vision:客厅", alert_type="stranger", priority=5, cooldown_seconds=300)
    assert r2["send"] is False
    r3 = d.should_send(session_id="vision:书房", alert_type="stranger", priority=5, cooldown_seconds=300)
    assert r3["send"] is True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed / {failed} failed")
    sys.exit(0 if failed == 0 else 1)