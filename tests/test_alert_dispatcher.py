"""Phase 4.1 统一告警分发单飞单元测试。"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.alert_dispatcher import AlertDispatcher


def test_first_alert_sends():
    """首次告警应该发送。"""
    d = AlertDispatcher(default_cooldown_seconds=60)
    res = d.should_send("sess1", "stranger_alert")
    assert res["send"] == True
    assert res["merged_count"] == 1
    print("✅ test_first_alert_sends 通过")


def test_cooldown_suppresses():
    """冷却期内同类告警应该被抑制。"""
    d = AlertDispatcher(default_cooldown_seconds=60)
    d.should_send("sess1", "stranger_alert")
    res = d.should_send("sess1", "stranger_alert")
    assert res["send"] == False
    assert res["merged_count"] == 2
    assert "冷却" in res["reason"]
    print("✅ test_cooldown_suppresses 通过")


def test_different_alert_types_independent():
    """不同类型告警互不影响。"""
    d = AlertDispatcher(default_cooldown_seconds=60)
    d.should_send("sess1", "stranger_alert")
    res = d.should_send("sess1", "return_anomaly")
    assert res["send"] == True
    print("✅ test_different_alert_types_independent 通过")


def test_different_sessions_independent():
    """不同 session 互不影响。"""
    d = AlertDispatcher(default_cooldown_seconds=60)
    d.should_send("sess1", "stranger_alert")
    res = d.should_send("sess2", "stranger_alert")
    assert res["send"] == True
    print("✅ test_different_sessions_independent 通过")


def test_high_priority_overrides():
    """高优先级告警可绕过低优先级的冷却（半冷却后）。"""
    d = AlertDispatcher(default_cooldown_seconds=60)
    d.should_send("sess1", "stranger_alert", priority=0)
    # 立即发高优先级，应该被抑制（还没过半冷却）
    res_immediate = d.should_send("sess1", "stranger_alert", priority=10)
    assert res_immediate["send"] == False
    print("✅ test_high_priority_overrides 通过（半冷却内仍抑制）")


def test_cooldown_expires():
    """冷却过后可以重发。"""
    d = AlertDispatcher(default_cooldown_seconds=1)
    d.should_send("sess1", "stranger_alert")
    time.sleep(1.1)
    res = d.should_send("sess1", "stranger_alert")
    assert res["send"] == True
    assert res["merged_count"] == 1
    print("✅ test_cooldown_expires 通过")


def test_stats():
    """统计功能。"""
    d = AlertDispatcher(default_cooldown_seconds=60)
    d.should_send("sess1", "alert1")
    d.should_send("sess1", "alert2")
    d.should_send("sess2", "alert1")
    stats = d.get_stats()
    assert stats["total_sessions"] == 2
    assert stats["total_alerts"] == 3
    sess_stats = d.get_stats("sess1")
    assert sess_stats["alert_types"] == 2
    print("✅ test_stats 通过")


def test_clear():
    """清理功能。"""
    d = AlertDispatcher(default_cooldown_seconds=60)
    d.should_send("sess1", "alert1")
    d.clear("sess1")
    res = d.should_send("sess1", "alert1")
    assert res["send"] == True
    print("✅ test_clear 通过")


if __name__ == "__main__":
    test_first_alert_sends()
    test_cooldown_suppresses()
    test_different_alert_types_independent()
    test_different_sessions_independent()
    test_high_priority_overrides()
    test_cooldown_expires()
    test_stats()
    test_clear()
    print("\n🎉 全部 8 个测试通过")
