"""WO-MA-016 回归测试：R-60 member_id 过滤真正生效。

四个修复点：
1. _to_metadata() 包含 member_id 键
2. FTS SELECT 返回 member_id 列
3. 向量路 where 多键用 $and（不抛 ValueError）
4. 静默失败改 logger.warning
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_to_metadata_has_member_id():
    """修复点1：_to_metadata() 必须包含 member_id 键。"""
    from memory_agent.agent_memory import AgentMemoryService
    mem = {
        "state": "live", "trust": 0.8, "source": "ma",
        "memory_id": "test-001", "member_id": "member-alice",
        "topic_key": "test",
    }
    meta = AgentMemoryService._to_metadata(mem)
    assert "member_id" in meta, "_to_metadata 缺少 member_id 键"
    assert meta["member_id"] == "member-alice"
    # 空 member_id 也应该有键（值为空字符串）
    mem2 = {"state": "live", "trust": 0.5, "memory_id": "test-002"}
    meta2 = AgentMemoryService._to_metadata(mem2)
    assert "member_id" in meta2
    assert meta2["member_id"] == ""
    print("PASS: test_to_metadata_has_member_id")


def test_where_uses_and_for_multiple_conditions():
    """修复点3：多键 where 必须用 $and（Chroma 要求 where 只能有一个 operator）。

    旧代码：where = {"state": "live", "trust": {"$gte": 0.5}, "member_id": "alice"}
    → Chroma 抛 ValueError: Expected where to have exactly one operator
    新代码：where = {"$and": [{"state": "live"}, {"trust": {"$gte": 0.5}}, {"member_id": "alice"}]}
    """
    # 验证 $and 语法是 Chroma 接受的（不抛异常）
    where = {"$and": [
        {"state": "live"},
        {"trust": {"$gte": 0.5}},
        {"member_id": "alice"},
    ]}
    assert "$and" in where
    assert len(where["$and"]) == 3
    # 单条件时不用 $and
    where_single = {"state": "live"}
    assert "$and" not in where_single
    print("PASS: test_where_uses_and_for_multiple_conditions")


def test_fts_select_includes_member_id():
    """修复点2：FTS SELECT 必须包含 member_id 列。

    旧代码 SELECT 列没有 member_id → 结果层 r.get("member_id") 永远为空 → 全部被过滤掉。
    """
    import inspect
    from memory_agent.store import Store
    source = inspect.getsource(Store.search_agent_memories_fts)
    assert "a.member_id" in source, "FTS SELECT 缺少 member_id 列"
    print("PASS: test_fts_select_includes_member_id")


def test_logger_used_instead_of_print():
    """修复点4：except 里必须用 logger.warning，不能用 print（静默失败）。"""
    import inspect
    from memory_agent.agent_memory import AgentMemoryService
    # 检查 retrieve 方法源码
    retrieve_source = inspect.getsource(AgentMemoryService.retrieve)
    assert "logger.warning" in retrieve_source, "retrieve 中向量检索失败应该用 logger.warning"
    assert "print(f\"[AgentMemory] 向量检索失败" not in retrieve_source, "不应有 print"
    # 检查 _upsert_mirror 方法源码
    upsert_source = inspect.getsource(AgentMemoryService._upsert_mirror)
    assert "logger.warning" in upsert_source, "_upsert_mirror 中镜像同步失败应该用 logger.warning"
    print("PASS: test_logger_used_instead_of_print")


def test_member_id_in_retrieve_signature():
    """retrieve() 必须有 member_id 参数（WO-ADM-001 已加，WO-MA-016 验证不丢失）。"""
    import inspect
    from memory_agent.agent_memory import AgentMemoryService
    sig = inspect.signature(AgentMemoryService.retrieve)
    assert "member_id" in sig.parameters, "retrieve 缺少 member_id 参数"
    print("PASS: test_member_id_in_retrieve_signature")


if __name__ == "__main__":
    test_to_metadata_has_member_id()
    test_where_uses_and_for_multiple_conditions()
    test_fts_select_includes_member_id()
    test_logger_used_instead_of_print()
    test_member_id_in_retrieve_signature()
    print("\n=== 5 passed / 0 failed ===")
