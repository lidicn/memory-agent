"""P1-5 回归测试：LIKE 通配符转义，防止跨成员检索。

审计问题：name 含 % 或 _ 时 LIKE 匹配范围扩大 → 返回其它成员记忆。
修复：_escape_like 转义 % _ \，SQL 加 ESCAPE '\\'。
"""
import sys
import os
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_escape_like_basic():
    """_escape_like 转义 % _ \\"""
    from memory_agent.store import Store
    assert Store._escape_like("hello") == "hello"
    assert Store._escape_like("100%") == "100\\%"
    assert Store._escape_like("a_b") == "a\\_b"
    assert Store._escape_like("a\\b") == "a\\\\b"
    assert Store._escape_like("") == ""
    assert Store._escape_like(None) == ""
    print("PASS: test_escape_like_basic")


def test_escape_like_sql_behavior():
    """SQL 层验证：含 % 的 name 不匹配全部，只匹配字面 %。"""
    from memory_agent.store import Store
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (tags TEXT)")
    # 插入两条：一条含 member:alice，一条含 member:bob
    conn.execute("INSERT INTO t VALUES ('[\"member:alice\"]')")
    conn.execute("INSERT INTO t VALUES ('[\"member:bob\"]')")
    conn.commit()

    # 用 % 作为 name（未转义时会匹配全部）
    name_with_percent = "%"
    escaped = Store._escape_like(name_with_percent)
    param = f"%member:{escaped}%"
    rows = conn.execute("SELECT * FROM t WHERE tags LIKE ? ESCAPE '\\'", (param,)).fetchall()
    # 转义后 % 是字面量，不应匹配任何一条（因为没有 member:%）
    assert len(rows) == 0, f"含 % 的 name 不应匹配任何记录，实际 {len(rows)} 条"

    # 正常 name 应正确匹配
    escaped_alice = Store._escape_like("alice")
    param_alice = f"%member:{escaped_alice}%"
    rows_alice = conn.execute("SELECT * FROM t WHERE tags LIKE ? ESCAPE '\\'", (param_alice,)).fetchall()
    assert len(rows_alice) == 1
    assert "alice" in rows_alice[0][0]

    # _ 作为 name（未转义时匹配单字符，可能匹配 bob 的 o）
    name_with_underscore = "o_"
    escaped_underscore = Store._escape_like(name_with_underscore)
    param_underscore = f"%member:{escaped_underscore}%"
    rows_underscore = conn.execute("SELECT * FROM t WHERE tags LIKE ? ESCAPE '\\'", (param_underscore,)).fetchall()
    # 转义后 _ 是字面量，不应匹配 bob（bob 不含 o_）
    assert len(rows_underscore) == 0, f"含 _ 的 name 不应匹配，实际 {len(rows_underscore)} 条"

    conn.close()
    print("PASS: test_escape_like_sql_behavior")


def test_cross_member_isolation():
    """关键场景：name 含 % 时不返回其他成员的记忆（跨成员隔离）。"""
    from memory_agent.store import Store
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE agent_memories (memory_id TEXT, tags_json TEXT, state TEXT)")
    conn.execute("INSERT INTO agent_memories VALUES ('1', '[\"member:alice\", \"habit:sleep\"]', 'live')")
    conn.execute("INSERT INTO agent_memories VALUES ('2', '[\"member:bob\", \"habit:sleep\"]', 'live')")
    conn.commit()

    # 攻击者用 % 作为 name，试图匹配全部成员
    malicious_name = "%"
    escaped = Store._escape_like(malicious_name)
    param = f"%member:{escaped}%"
    rows = conn.execute(
        "SELECT * FROM agent_memories WHERE tags_json LIKE ? ESCAPE '\\' AND state <> 'revoked'",
        (param,)
    ).fetchall()
    # 转义后 % 是字面量，不应匹配 alice 或 bob
    assert len(rows) == 0, f"跨成员隔离失败：含 % 的 name 返回了 {len(rows)} 条其他成员记忆"

    # 正常 name alice 只返回 alice 的记忆
    escaped_alice = Store._escape_like("alice")
    param_alice = f"%member:{escaped_alice}%"
    rows_alice = conn.execute(
        "SELECT * FROM agent_memories WHERE tags_json LIKE ? ESCAPE '\\' AND state <> 'revoked'",
        (param_alice,)
    ).fetchall()
    assert len(rows_alice) == 1
    assert rows_alice[0][0] == "1"  # alice 的记忆

    conn.close()
    print("PASS: test_cross_member_isolation")


if __name__ == "__main__":
    test_escape_like_basic()
    test_escape_like_sql_behavior()
    test_cross_member_isolation()
    print("\n=== 3 passed / 0 failed ===")
