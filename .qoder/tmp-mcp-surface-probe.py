"""生产/容器侧实测：MCP 实际工具面 vs scope 登记 vs ToolSpec 登记。

在容器里跑（有 mcp 包）：三张集合的差集才是对外可见的真话。
"""
import asyncio
import inspect
import sys

sys.path.insert(0, "/app/src")

from memory_agent import mcp_server, tool_schema  # noqa: E402
from memory_agent.mcp_scopes import REGISTERED_TOOLS, WRITE_TOOLS, scope_of  # noqa: E402

print(f"tool_schema: SPEC_BY_NAME={len(tool_schema.SPEC_BY_NAME)} "
      f"TOOL_NAMES(expose∋mcp)={len(tool_schema.TOOL_NAMES)} "
      f"generated∧mcp={sum(1 for s in tool_schema.SPEC_BY_NAME.values() if s.generated and 'mcp' in s.expose)}")

server = getattr(mcp_server, "mcp_server", None)
print(f"mcp_server.mcp_server = {type(server).__name__ if server else None}")
if server is None:
    print("RC=2 服务器实例未建成，无法读实际面")
    sys.exit(2)

tools = asyncio.run(server.list_tools())
wire = sorted(t.name for t in tools)
print(f"\n实际 list_tools() = {len(wire)} 个")

spec_mcp = {s.name for s in tool_schema.SPEC_BY_NAME.values() if "mcp" in (s.expose or ())}
scoped = set(REGISTERED_TOOLS) | set(WRITE_TOOLS)

no_spec = [n for n in wire if n not in spec_mcp]
no_scope = [n for n in wire if n not in scoped]
print(f"wire − spec = {len(no_spec)}")
print(f"wire − scope登记 = {len(no_scope)} → {no_scope}")
print(f"caps 少报比例 = {len(spec_mcp & set(wire))}/{len(wire)}")

print("\n=== 未登记工具的真实 scope_of 读数 ===")
for n in no_scope:
    print(f"  {n}: scope_of={scope_of(n)}")

print("\n=== 35 条 gap 工具的实际签名（用于写 ToolSpec）===")
by_name = {t.name: t for t in tools}
for n in no_spec:
    t = by_name[n]
    schema = getattr(t, 'input_schema', None) or getattr(t, 'inputSchema', None) or {}
    props = list((schema.get("properties") or {}).keys())
    req = list(schema.get("required") or [])
    desc = (t.description or "").strip().split("\n")[0][:120]
    print(f"[{n}] required={req}")
    print(f"    props={props}")
    print(f"    desc={desc}")
print("\nRC=0")
