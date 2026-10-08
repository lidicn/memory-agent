"""Phase 3 主动规则引擎单元测试。"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.perception_rules import RuleEngine, STATIC_RULES


def test_rule_match():
    """测试规则匹配：trigger + room 匹配。"""
    engine = RuleEngine()
    # face_unknown + 客厅 应该匹配 stranger_alert
    hits = engine.evaluate("face_unknown", "客厅")
    assert len(hits) == 1
    assert hits[0]["rule_id"] == "stranger_alert"
    assert hits[0]["action"] == "alert"
    print("✅ test_rule_match 通过")


def test_room_mismatch():
    """测试房间不匹配：stranger_alert 只在客厅触发。"""
    engine = RuleEngine()
    hits = engine.evaluate("face_unknown", "卧室")
    assert len(hits) == 0  # 卧室不匹配客厅规则
    print("✅ test_room_mismatch 通过")


def test_room_wildcard():
    """测试空 room 通配：day_night_log 的 room 为空，任何房间都匹配。"""
    engine = RuleEngine()
    hits = engine.evaluate("day_night", "任意房间")
    assert len(hits) == 1
    assert hits[0]["rule_id"] == "day_night_log"
    print("✅ test_room_wildcard 通过")


def test_cooldown():
    """测试冷却期：同规则 5 分钟内不重复触发。"""
    engine = RuleEngine()
    # 第一次触发
    hits1 = engine.evaluate("face_unknown", "客厅")
    assert len(hits1) == 1
    # 立即第二次，应该在冷却期内
    hits2 = engine.evaluate("face_unknown", "客厅")
    assert len(hits2) == 0
    print("✅ test_cooldown 通过")


def test_cooldown_expires():
    """测试冷却过期后可以重新触发。"""
    # 用自定义规则，冷却 0.1 秒
    rules = [
        {"id": "test", "trigger": "test_event", "room": "", "action": "log",
         "cooldown_seconds": 0.1, "description": "test"},
    ]
    engine = RuleEngine(rules=rules)
    hits1 = engine.evaluate("test_event", "")
    assert len(hits1) == 1
    time.sleep(0.15)
    hits2 = engine.evaluate("test_event", "")
    assert len(hits2) == 1
    print("✅ test_cooldown_expires 通过")


def test_no_cooldown():
    """测试 cooldown=0 的规则不冷却。"""
    rules = [
        {"id": "test", "trigger": "test_event", "room": "", "action": "log",
         "cooldown_seconds": 0, "description": "test"},
    ]
    engine = RuleEngine(rules=rules)
    hits1 = engine.evaluate("test_event", "")
    hits2 = engine.evaluate("test_event", "")
    assert len(hits1) == 1
    assert len(hits2) == 1  # 冷却 0，每次都触发
    print("✅ test_no_cooldown 通过")


def test_custom_rules():
    """测试自定义规则列表。"""
    rules = [
        {"id": "custom1", "trigger": "custom_event", "room": "书房", "action": "alert",
         "cooldown_seconds": 60, "description": "自定义规则"},
    ]
    engine = RuleEngine(rules=rules)
    # 自定义规则匹配
    hits = engine.evaluate("custom_event", "书房")
    assert len(hits) == 1
    assert hits[0]["rule_id"] == "custom1"
    # 默认规则不存在
    hits2 = engine.evaluate("face_unknown", "客厅")
    assert len(hits2) == 0
    print("✅ test_custom_rules 通过")


def test_static_rules_count():
    """测试默认 STATIC_RULES 有 2 条。"""
    assert len(STATIC_RULES) == 2
    ids = [r["id"] for r in STATIC_RULES]
    assert "stranger_alert" in ids
    assert "day_night_log" in ids
    print("✅ test_static_rules_count 通过")


if __name__ == "__main__":
    test_rule_match()
    test_room_mismatch()
    test_room_wildcard()
    test_cooldown()
    test_cooldown_expires()
    test_no_cooldown()
    test_custom_rules()
    test_static_rules_count()
    print("\n🎉 全部 8 个测试通过")
