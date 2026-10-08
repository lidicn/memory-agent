"""P1-4 回归测试：persons 序列化统一，防止在场人员静默丢弃。

审计问题：写侧 persons 是字符串数组，读侧 isinstance(p, dict) 为 False → 整批静默丢弃。
修复：_serialize_persons 统一写侧格式（字符串→dict），_deserialize_persons 统一读侧（字符串→dict + 记ERROR）。
"""
import sys
import os
import json
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_serialize_persons_string_array():
    """写侧：字符串数组转为 dict 数组。"""
    from memory_agent.store import Store
    result = Store._serialize_persons(["alice", "bob"])
    parsed = json.loads(result)
    assert len(parsed) == 2
    assert parsed[0] == {"name": "alice", "confidence": 0.0}
    assert parsed[1] == {"name": "bob", "confidence": 0.0}
    print("PASS: test_serialize_persons_string_array")


def test_serialize_persons_dict_array():
    """写侧：dict 数组保持不变。"""
    from memory_agent.store import Store
    input_data = [{"name": "alice", "confidence": 0.9, "via": "face"}]
    result = Store._serialize_persons(input_data)
    parsed = json.loads(result)
    assert parsed == input_data
    print("PASS: test_serialize_persons_dict_array")


def test_serialize_persons_mixed():
    """写侧：混合数组（字符串 + dict）统一为 dict。"""
    from memory_agent.store import Store
    input_data = ["alice", {"name": "bob", "confidence": 0.8}]
    result = Store._serialize_persons(input_data)
    parsed = json.loads(result)
    assert len(parsed) == 2
    assert parsed[0]["name"] == "alice"
    assert parsed[1]["name"] == "bob"
    assert parsed[1]["confidence"] == 0.8
    print("PASS: test_serialize_persons_mixed")


def test_serialize_persons_empty():
    """写侧：空输入返回 []。"""
    from memory_agent.store import Store
    assert Store._serialize_persons([]) == "[]"
    assert Store._serialize_persons(None) == "[]"
    assert Store._serialize_persons("") == "[]"
    print("PASS: test_serialize_persons_empty")


def test_deserialize_persons_string_array():
    """读侧：旧格式字符串数组转为 dict（不丢弃）。"""
    from memory_agent.store import Store
    raw = json.dumps(["alice", "bob"])
    result = Store._deserialize_persons(raw)
    assert len(result) == 2
    assert result[0]["name"] == "alice"
    assert result[1]["name"] == "bob"
    print("PASS: test_deserialize_persons_string_array")


def test_deserialize_persons_dict_array():
    """读侧：dict 数组保持不变。"""
    from memory_agent.store import Store
    raw = json.dumps([{"name": "alice", "confidence": 0.9}])
    result = Store._deserialize_persons(raw)
    assert len(result) == 1
    assert result[0]["confidence"] == 0.9
    print("PASS: test_deserialize_persons_dict_array")


def test_deserialize_persons_invalid_json():
    """读侧：无效 JSON 返回空列表（不崩溃）。"""
    from memory_agent.store import Store
    result = Store._deserialize_persons("not valid json{{{")
    assert result == []
    print("PASS: test_deserialize_persons_invalid_json")


def test_deserialize_persons_not_list():
    """读侧：JSON 不是数组返回空列表。"""
    from memory_agent.store import Store
    result = Store._deserialize_persons('{"name": "alice"}')
    assert result == []
    print("PASS: test_deserialize_persons_not_list")


def test_roundtrip_write_read():
    """关键场景：写侧字符串数组 → 读侧正确返回 dict（不丢弃）。

    这是 P1-4 的核心 bug 场景：旧代码写字符串数组，读侧 isinstance(p, dict) 为 False → 静默丢弃。
    """
    from memory_agent.store import Store
    # 模拟写侧：上游传字符串数组
    written = Store._serialize_persons(["alice", "bob", "charlie"])
    # 模拟读侧：从数据库读回
    read_back = Store._deserialize_persons(written)
    # 所有人都应该在，不应该被丢弃
    assert len(read_back) == 3, f"期望 3 人，实际 {len(read_back)} 人（旧代码会静默丢弃）"
    names = [p["name"] for p in read_back]
    assert "alice" in names
    assert "bob" in names
    assert "charlie" in names
    # 都是 dict
    assert all(isinstance(p, dict) for p in read_back)
    print("PASS: test_roundtrip_write_read")


def test_old_data_compatibility():
    """旧数据兼容：数据库中已存的字符串数组能正确读取。"""
    from memory_agent.store import Store
    # 模拟旧数据：直接存的字符串数组（未经 _serialize_persons）
    old_data = json.dumps(["alice", "bob"])
    result = Store._deserialize_persons(old_data)
    assert len(result) == 2
    assert result[0]["name"] == "alice"
    print("PASS: test_old_data_compatibility")


if __name__ == "__main__":
    test_serialize_persons_string_array()
    test_serialize_persons_dict_array()
    test_serialize_persons_mixed()
    test_serialize_persons_empty()
    test_deserialize_persons_string_array()
    test_deserialize_persons_dict_array()
    test_deserialize_persons_invalid_json()
    test_deserialize_persons_not_list()
    test_roundtrip_write_read()
    test_old_data_compatibility()
    print("\n=== 10 passed / 0 failed ===")
