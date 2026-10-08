"""容器内读数：MCP 面三方对账 + 重复注册是否还在。"""
import asyncio, io, sys, contextlib

buf = io.StringIO()
with contextlib.redirect_stderr(buf):
    from memory_agent import mcp_server as ms
    from memory_agent import tool_schema
    from memory_agent.mcp_scopes import REGISTERED_TOOLS, WRITE_TOOLS, scope_of

err = buf.getvalue()
print("IMPORT_STDERR_LINES =", len([l for l in err.splitlines() if l.strip()]))
print("TOOL_ALREADY_EXISTS =", sum(1 for l in err.splitlines() if "already exists" in l.lower()))
for l in err.splitlines():
    if l.strip():
        print("  stderr:", l.strip())

server = ms.mcp_server
tools = server._tool_manager._tools
print("LIVE_TOOL_COUNT =", len(tools))
print("SPEC_TOTAL =", len(tool_schema.TOOL_SPECS))
print("SPEC_MCP =", len(tool_schema.TOOL_NAMES))
print("REGISTERED_TOOLS =", len(REGISTERED_TOOLS), "WRITE_TOOLS =", len(WRITE_TOOLS))
for n in ("read_self_diary", "write_self_diary", "generate_self_diary", "query_unified_events"):
    print(f"scope_of({n!r}) =", scope_of(n))
missing = sorted(t for t in tools if scope_of(t) == "unknown")
print("LIVE_UNKNOWN_SCOPE =", missing)
ghost = sorted(n for n in set(REGISTERED_TOOLS) | set(WRITE_TOOLS) if n not in tools)
print("ADMITTED_NOT_ON_WIRE =", ghost)
