import asyncio, inspect, sys
sys.path.insert(0, "/app/src")
from memory_agent import mcp_server as ms
srv = ms.mcp_server
print("server type:", type(srv).__name__)
print("attrs:", [a for a in dir(srv) if not a.startswith("__")][:40])
for cand in ("_tool_manager", "sm", "_params"):
    print(cand, "->", hasattr(srv, cand))
tm = getattr(srv, "_tool_manager", None)
if tm is not None:
    print("tool_manager type:", type(tm).__name__, "attrs:", [a for a in dir(tm) if not a.startswith('_')][:20])
    tools = getattr(tm, "_tools", None) or getattr(tm, "tools", None)
    print("tools container:", type(tools).__name__, len(tools) if tools else 0)
    if isinstance(tools, dict):
        t = tools.get("assign_member_device")
        print("assign_member_device ->", type(t).__name__ if t else None)
        if t is not None:
            print("  tool attrs:", [a for a in dir(t) if not a.startswith('_')][:20])
            fn = getattr(t, "fn", None)
            print("  fn:", fn, inspect.signature(fn) if callable(fn) else "")
