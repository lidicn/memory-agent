"""P1-7 回归测试：热路径查询索引 + 审计写异步化

验证：
1. behavior_events 表有 (server_ts) 和 (room, server_ts) 索引
2. _record_mcp_call 是 async 函数（审计写不再阻塞事件循环）
3. store.py schema 定义包含新索引
"""
import ast
import os
import sqlite3
import sys
import tempfile
import unittest

# 源码路径：优先用环境变量，其次相对项目根目录（兼容本地与 CI）
SRC_DIR = os.environ.get("MA_SRC_DIR", os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.abspath(SRC_DIR))


class TestP17BehaviorEventsIndexes(unittest.TestCase):
    """验证 behavior_events 热路径查询索引"""

    def test_schema_contains_server_ts_index(self):
        """store.py schema 定义包含 idx_be_server_ts 索引"""
        store_path = os.path.join(SRC_DIR, "memory_agent", "store.py")
        with open(store_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn(
            "idx_be_server_ts ON behavior_events(server_ts)",
            content,
            "store.py 缺少 idx_be_server_ts 索引定义",
        )

    def test_schema_contains_room_server_ts_index(self):
        """store.py schema 定义包含 idx_be_room_server_ts 索引"""
        store_path = os.path.join(SRC_DIR, "memory_agent", "store.py")
        with open(store_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn(
            "idx_be_room_server_ts ON behavior_events(room, server_ts)",
            content,
            "store.py 缺少 idx_be_room_server_ts 索引定义",
        )

    def test_in_memory_db_creates_indexes(self):
        """内存数据库初始化后，behavior_events 有三个索引"""
        from memory_agent.store import Store

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = Store(db_path=db_path, tz_offset_hours=8)
            store.init_schema()

            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("PRAGMA index_list(behavior_events)")
            indexes = {row[1] for row in cur.fetchall()}
            conn.close()

            self.assertIn("idx_be_day_room", indexes)
            self.assertIn("idx_be_server_ts", indexes)
            self.assertIn("idx_be_room_server_ts", indexes)

    def test_query_uses_indexes(self):
        """EXPLAIN QUERY PLAN 验证热路径查询使用索引"""
        from memory_agent.store import Store

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = Store(db_path=db_path, tz_offset_hours=8)
            store.init_schema()

            # 插入测试数据
            conn = sqlite3.connect(db_path)
            for i in range(100):
                conn.execute(
                    "INSERT INTO behavior_events(server_ts, day, room, action, status) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"2026-09-20T{i:02d}:00:00", "2026-09-20", "客厅", "test", "ok"),
                )
            conn.commit()

            # 验证按 server_ts 查询使用索引
            cur = conn.cursor()
            cur.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM behavior_events "
                "WHERE server_ts > '2026-09-20T10:00:00' ORDER BY server_ts DESC LIMIT 10"
            )
            plan = "\n".join(str(row) for row in cur.fetchall())
            self.assertIn("idx_be_server_ts", plan, "按 server_ts 查询未使用索引")

            # 验证按 room + server_ts 查询使用索引
            cur.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM behavior_events "
                "WHERE room='客厅' AND server_ts > '2026-09-20T10:00:00' "
                "ORDER BY server_ts DESC LIMIT 10"
            )
            plan = "\n".join(str(row) for row in cur.fetchall())
            self.assertIn("idx_be_room_server_ts", plan, "按 room+server_ts 查询未使用索引")

            conn.close()


class TestP17AuditAsync(unittest.TestCase):
    """验证审计写异步化（不再阻塞事件循环）"""

    def test_record_mcp_call_is_async(self):
        """_record_mcp_call 是 async 函数"""
        mcp_path = os.path.join(SRC_DIR, "memory_agent", "mcp_server.py")
        with open(mcp_path, encoding="utf-8") as f:
            tree = ast.parse(f.read())

        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_record_mcp_call":
                found = True
                break
        self.assertTrue(found, "_record_mcp_call 不是 async 函数")

    def test_record_mcp_call_uses_to_thread(self):
        """_record_mcp_call 使用 asyncio.to_thread 包裹审计写"""
        mcp_path = os.path.join(SRC_DIR, "memory_agent", "mcp_server.py")
        with open(mcp_path, encoding="utf-8") as f:
            content = f.read()

        # 找到 _record_mcp_call 函数体
        start = content.find("async def _record_mcp_call")
        self.assertGreater(start, -1, "未找到 _record_mcp_call")
        # 取函数体（到下一个 def 或文件末尾）
        end = content.find("\ndef ", start + 10)
        if end == -1:
            end = len(content)
        func_body = content[start:end]

        self.assertIn(
            "asyncio.to_thread",
            func_body,
            "_record_mcp_call 未使用 asyncio.to_thread",
        )
        self.assertIn(
            "log_mcp_audit",
            func_body,
            "_record_mcp_call 未调用 log_mcp_audit",
        )

    def test_no_sync_audit_in_tracked_call(self):
        """_tracked_call_tool 中没有同步调用 _record_mcp_call（都加了 await）"""
        mcp_path = os.path.join(SRC_DIR, "memory_agent", "mcp_server.py")
        with open(mcp_path, encoding="utf-8") as f:
            content = f.read()

        # 找到 _tracked_call_tool 函数体
        start = content.find("async def _tracked_call_tool")
        self.assertGreater(start, -1, "未找到 _tracked_call_tool")
        end = content.find("\ndef ", start + 10)
        if end == -1:
            end = len(content)
        func_body = content[start:end]

        # 所有 _record_mcp_call 调用都应该有 await
        import re
        calls = re.findall(r'(?<!await\s)_record_mcp_call\(', func_body)
        self.assertEqual(
            len(calls), 0,
            f"_tracked_call_tool 中有 {len(calls)} 处同步调用 _record_mcp_call（未加 await）",
        )


if __name__ == "__main__":
    unittest.main()
