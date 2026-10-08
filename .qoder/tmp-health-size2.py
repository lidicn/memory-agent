import asyncio, sys
sys.path.insert(0, "/app/src")
from memory_agent import mcp_server
res = asyncio.run(mcp_server.mcp_server.call_tool("list_device_health", {}))
blocks = list(res.content or [])
print("content_blocks =", len(blocks))
for i, b in enumerate(blocks):
    t = getattr(b, "text", None)
    print(f"  block{i}: type={type(b).__name__} len={len(t) if t is not None else None}")
txt = "".join((getattr(b, "text", None) or "") for b in blocks)
print("total_chars =", len(txt))
print("head =", repr(txt[:120]))
print("tail =", repr(txt[-240:]))
for cap in ("TRUNCAT", "truncat", "…", "...("):
    print(f"contains {cap!r}:", cap in txt)
