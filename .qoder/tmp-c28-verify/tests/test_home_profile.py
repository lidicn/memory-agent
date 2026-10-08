"""Phase 2.3 家庭画像（原子写+权重截断）单元测试。"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.home_profile import (
    write_profile_atomic,
    truncate_by_weight,
    build_profile,
)


def test_atomic_write():
    """测试原子写：临时文件 + rename。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "profile.md")
        write_profile_atomic(path, "# 测试\n内容")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            assert f.read() == "# 测试\n内容"
        # 不应该有 .tmp 残留
        tmps = [f for f in os.listdir(tmpdir) if f.endswith(".tmp")]
        assert len(tmps) == 0
    print("✅ test_atomic_write 通过")


def test_atomic_write_overwrite():
    """测试原子写覆盖已有文件。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "profile.md")
        write_profile_atomic(path, "旧内容")
        write_profile_atomic(path, "新内容")
        with open(path, encoding="utf-8") as f:
            assert f.read() == "新内容"
    print("✅ test_atomic_write_overwrite 通过")


def test_truncate_by_weight():
    """测试按权重截断：高权重优先保留。"""
    memories = [
        {"text": "A" * 100, "trust": 0.1},   # 低权重
        {"text": "B" * 100, "trust": 0.9},   # 高权重
        {"text": "C" * 100, "trust": 0.5},   # 中权重
    ]
    # max_chars=150，只能保留 1 条（100 chars），应该是高权重的 B
    kept = truncate_by_weight(memories, max_chars=150)
    assert len(kept) == 1
    assert kept[0]["text"] == "B" * 100
    print("✅ test_truncate_by_weight 通过")


def test_truncate_by_weight_multiple():
    """测试多条保留：按权重降序依次加入。"""
    memories = [
        {"text": "A" * 50, "trust": 0.1},
        {"text": "B" * 50, "trust": 0.9},
        {"text": "C" * 50, "trust": 0.5},
    ]
    # max_chars=120，可以保留 B(50) + C(50) = 100，A(50) 加进去超 120
    kept = truncate_by_weight(memories, max_chars=120)
    assert len(kept) == 2
    assert kept[0]["trust"] == 0.9  # B 第一
    assert kept[1]["trust"] == 0.5  # C 第二
    print("✅ test_truncate_by_weight_multiple 通过")


def test_truncate_empty_text():
    """测试空文本被跳过。"""
    memories = [
        {"text": "", "trust": 0.9},
        {"text": "  ", "trust": 0.8},
        {"text": "有效", "trust": 0.1},
    ]
    kept = truncate_by_weight(memories, max_chars=1000)
    assert len(kept) == 1
    assert kept[0]["text"] == "有效"
    print("✅ test_truncate_empty_text 通过")


def test_build_profile_with_mock_store():
    """测试 build_profile 生成 profile.md 文本。"""
    class MockStore:
        def list_agent_memories(self, state="all", source="", limit=500, member_id=None):
            return [
                {"topic_key": "habit:Kevin:reading", "text": "Kevin 通常在 20:00 阅读", "trust": 0.8},
                {"topic_key": "habit:Emily:play", "text": "Emily 通常在 19:00 玩耍", "trust": 0.6},
                {"topic_key": "other:something", "text": "这条不是 habit，应该被过滤", "trust": 0.9},
            ]

    store = MockStore()
    profile = build_profile(store, max_chars=4000)
    assert "# 家庭画像" in profile
    assert "Kevin 通常在 20:00 阅读" in profile
    assert "Emily 通常在 19:00 玩耍" in profile
    assert "这条不是 habit" not in profile  # 非 habit 被过滤
    assert "权重：0.80" in profile
    print("✅ test_build_profile_with_mock_store 通过")


if __name__ == "__main__":
    test_atomic_write()
    test_atomic_write_overwrite()
    test_truncate_by_weight()
    test_truncate_by_weight_multiple()
    test_truncate_empty_text()
    test_build_profile_with_mock_store()
    print("\n🎉 全部 6 个测试通过")
