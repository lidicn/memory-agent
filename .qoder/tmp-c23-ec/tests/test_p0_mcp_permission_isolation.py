"""P0 MCP 权限隔离：read token 调用写工具必须被拒绝。

QA-MA-003 发现：read token 可执行 create_member/trigger_collection 等写操作。
本测试验证权限检查逻辑本身是否正确。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_read_token_denied_write_tools():
    """read scope 令牌调用写工具必须被拒绝。"""
    from memory_agent.mcp_scopes import requires, WRITE_TOOLS, READ

    # 所有写工具，read token 都应被拒绝
    for tool in WRITE_TOOLS:
        allowed = requires(tool, [READ])
        assert allowed is False, f"read token 不应被允许调用写工具 '{tool}'"

    print(f"PASS: read token 被拒绝调用全部 {len(WRITE_TOOLS)} 个写工具")


def test_write_token_allowed_write_tools():
    """write scope 令牌调用写工具应被允许。"""
    from memory_agent.mcp_scopes import requires, WRITE_TOOLS, WRITE

    for tool in WRITE_TOOLS:
        allowed = requires(tool, [WRITE])
        assert allowed is True, f"write token 应被允许调用写工具 '{tool}'"

    print(f"PASS: write token 被允许调用全部 {len(WRITE_TOOLS)} 个写工具")


def test_read_token_allowed_read_tools():
    """read scope 令牌调用读工具应被允许。"""
    from memory_agent.mcp_scopes import requires, REGISTERED_TOOLS, READ

    for tool in REGISTERED_TOOLS:
        allowed = requires(tool, [READ])
        assert allowed is True, f"read token 应被允许调用读工具 '{tool}'"

    print(f"PASS: read token 被允许调用全部 {len(REGISTERED_TOOLS)} 个读工具")


def test_legacy_scopes_is_readonly():
    """LEGACY_SCOPES 必须是只读（fail-safe），不能包含 write/admin。"""
    from memory_agent.mcp_tokens import LEGACY_SCOPES

    assert "write" not in LEGACY_SCOPES, "LEGACY_SCOPES 不应包含 write（fail-open 漏洞）"
    assert "admin" not in LEGACY_SCOPES, "LEGACY_SCOPES 不应包含 admin"
    assert "read" in LEGACY_SCOPES, "LEGACY_SCOPES 应包含 read"

    print(f"PASS: LEGACY_SCOPES = {LEGACY_SCOPES}（只读，fail-safe）")


def test_legacy_token_scopes_returns_readonly():
    """无 scopes 字段的令牌，scopes() 应返回只读。"""
    from memory_agent.mcp_tokens import MCPTokenStore

    # 模拟一个无 scopes 字段的 token 记录
    class FakeConfig:
        agent_tokens = {
            "legacy-token": {
                "hash": "abc123",
                "prefix": "mcp_test",
                "created_at": "2026-01-01T00:00:00",
                "last_used_at": "",
                # 注意：没有 scopes 字段
            }
        }
        tz_offset_hours = 8

        def save(self):
            pass

    store = MCPTokenStore(FakeConfig())
    scopes = store.scopes("legacy-token")

    assert "write" not in scopes, "无 scopes 字段的令牌不应有 write 权限"
    assert "admin" not in scopes, "无 scopes 字段的令牌不应有 admin 权限"
    assert "read" in scopes, "无 scopes 字段的令牌应有 read 权限"

    print(f"PASS: 无 scopes 字段令牌 scopes() = {scopes}（只读）")


def test_explicit_write_scopes_respected():
    """有显式 scopes 字段的令牌，scopes() 应返回配置的权限。"""
    from memory_agent.mcp_tokens import MCPTokenStore

    class FakeConfig:
        agent_tokens = {
            "write-token": {
                "hash": "abc123",
                "prefix": "mcp_test",
                "created_at": "2026-01-01T00:00:00",
                "last_used_at": "",
                "scopes": ["read", "write"],
            }
        }
        tz_offset_hours = 8

        def save(self):
            pass

    store = MCPTokenStore(FakeConfig())
    scopes = store.scopes("write-token")

    assert "write" in scopes, "显式配置 write 的令牌应有 write 权限"
    assert "read" in scopes, "显式配置 read 的令牌应有 read 权限"

    print(f"PASS: 显式 scopes 令牌 scopes() = {scopes}")


if __name__ == "__main__":
    test_read_token_denied_write_tools()
    test_write_token_allowed_write_tools()
    test_read_token_allowed_read_tools()
    test_legacy_scopes_is_readonly()
    test_legacy_token_scopes_returns_readonly()
    test_explicit_write_scopes_respected()
    print("\n全部 6 项测试通过 ✅")
