import asyncio, json, sys
sys.path.insert(0, "/app/src")
from memory_agent import mcp_server
res = asyncio.run(mcp_server.mcp_server.call_tool("list_device_health", {}))
txt = "".join(getattr(c, "text", "") or "" for c in (res.content or []))
d = json.loads(txt)
rows = d.get("health") or []
print("bytes =", len(txt.encode("utf-8")), "health_rows =", len(rows), "logical_devices =", d.get("logical_devices"))
if rows:
    print("keys =", sorted(rows[0].keys()))
    big = max(rows, key=lambda r: len(json.dumps(r, ensure_ascii=False)))
    print("largest_row_bytes =", len(json.dumps(big, ensure_ascii=False).encode("utf-8")))
    print("largest_row =", json.dumps(big, ensure_ascii=False)[:400])
    print("per_field_bytes =", {k: len(json.dumps([r.get(k) for r in rows], ensure_ascii=False).encode()) for k in sorted(rows[0])})
