"""WO-MA-002 P0-8 回归测试：后端密钥按身份匹配，不按位置。

审计问题：调整配置项顺序即把 A 厂商的 key 发给 B 厂商 endpoint。
修复：_restore_backend_keys / _find_saved_backend 按 (name, model, api_url) 身份匹配。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.api.config_routes import (
    _restore_backend_keys,
    _find_saved_backend,
    _is_masked,
    mask_secret,
)


def test_find_saved_backend_by_identity():
    """按身份(name+model+api_url)匹配，不按位置。"""
    saved = [
        {"name": "A", "model": "gpt-4", "api_url": "https://a.example.com", "api_key": "key-A"},
        {"name": "B", "model": "claude-3", "api_url": "https://b.example.com", "api_key": "key-B"},
    ]
    # 传入 B 的身份，但放在列表第一个位置（模拟前端打乱顺序）
    incoming = {"name": "B", "model": "claude-3", "api_url": "https://b.example.com", "api_key": "***"}
    found = _find_saved_backend(saved, incoming)
    assert found is not None
    assert found["api_key"] == "key-B"  # 应找到 B 的 key，不是 A 的
    print("PASS: test_find_saved_backend_by_identity")


def test_find_saved_backend_fallback_model_url():
    """name 被改时，退而求其次按 model+api_url 匹配。"""
    saved = [
        {"name": "A-old", "model": "gpt-4", "api_url": "https://a.example.com", "api_key": "key-A"},
    ]
    incoming = {"name": "A-new", "model": "gpt-4", "api_url": "https://a.example.com", "api_key": "***"}
    found = _find_saved_backend(saved, incoming)
    assert found is not None
    assert found["api_key"] == "key-A"
    print("PASS: test_find_saved_backend_fallback_model_url")


def test_find_saved_backend_not_found():
    """身份不匹配时返回 None。"""
    saved = [
        {"name": "A", "model": "gpt-4", "api_url": "https://a.example.com", "api_key": "key-A"},
    ]
    incoming = {"name": "C", "model": "gpt-5", "api_url": "https://c.example.com", "api_key": "***"}
    found = _find_saved_backend(saved, incoming)
    assert found is None
    print("PASS: test_find_saved_backend_not_found")


def test_restore_backend_keys_masked_recovered():
    """掩码 key 按身份恢复真实值，未掩码的保持不变。"""
    old = [
        {"name": "A", "model": "gpt-4", "api_url": "https://a.example.com", "api_key": "key-A"},
        {"name": "B", "model": "claude-3", "api_url": "https://b.example.com", "api_key": "key-B"},
    ]
    # 前端回传：顺序打乱，A 的 key 是掩码，B 的 key 是新值（未掩码）
    new = [
        {"name": "B", "model": "claude-3", "api_url": "https://b.example.com", "api_key": "new-key-B"},
        {"name": "A", "model": "gpt-4", "api_url": "https://a.example.com", "api_key": mask_secret("key-A")},
    ]
    restored = _restore_backend_keys(new, old)
    assert len(restored) == 2
    # B 是新值，保持不变
    assert restored[0]["api_key"] == "new-key-B"
    # A 是掩码，应恢复为 old 中的 key-A
    assert restored[1]["api_key"] == "key-A"
    print("PASS: test_restore_backend_keys_masked_recovered")


def test_restore_backend_keys_order_shuffled():
    """关键场景：前端列表顺序与服务端落盘不一致，按身份匹配不错位。

    这是 P0-8 的核心 bug 场景：如果按位置回填，A 的掩码 key 会被还原成 B 的真实 key。
    """
    old = [
        {"name": "A", "model": "gpt-4", "api_url": "https://a.example.com", "api_key": "key-A"},
        {"name": "B", "model": "claude-3", "api_url": "https://b.example.com", "api_key": "key-B"},
        {"name": "C", "model": "doubao", "api_url": "https://c.example.com", "api_key": "key-C"},
    ]
    # 前端回传：完全逆序，全部是掩码
    new = [
        {"name": "C", "model": "doubao", "api_url": "https://c.example.com", "api_key": mask_secret("key-C")},
        {"name": "B", "model": "claude-3", "api_url": "https://b.example.com", "api_key": mask_secret("key-B")},
        {"name": "A", "model": "gpt-4", "api_url": "https://a.example.com", "api_key": mask_secret("key-A")},
    ]
    restored = _restore_backend_keys(new, old)
    # 按身份匹配，每个后端应恢复自己的 key
    assert restored[0]["name"] == "C" and restored[0]["api_key"] == "key-C"
    assert restored[1]["name"] == "B" and restored[1]["api_key"] == "key-B"
    assert restored[2]["name"] == "A" and restored[2]["api_key"] == "key-A"
    print("PASS: test_restore_backend_keys_order_shuffled")


def test_is_masked():
    """掩码检测函数正常。"""
    assert _is_masked(mask_secret("abc")) == True
    assert _is_masked("real-key") == False
    assert _is_masked("") == False
    print("PASS: test_is_masked")


if __name__ == "__main__":
    test_find_saved_backend_by_identity()
    test_find_saved_backend_fallback_model_url()
    test_find_saved_backend_not_found()
    test_restore_backend_keys_masked_recovered()
    test_restore_backend_keys_order_shuffled()
    test_is_masked()
    print("\n=== 6 passed / 0 failed ===")
