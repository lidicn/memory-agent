"""生产实测：补登记后「看得见也调得动」——走真服务器 call 路径。

只挑只读工具，绝不在生产触发写工具。
"""
import asyncio, json, sys
sys.path.insert(0, "/app/src")

from memory_agent import mcp_server  # noqa: E402

server = mcp_server.mcp_server
tools = asyncio.run(server.list_tools())
names = sorted(t.name for t in tools)
print("PROD_LIST_TOOLS =", len(names))

READONLY = ["read_self_diary", "list_members", "list_behavior_anomalies",
            "query_unified_events", "list_device_health"]


def payload(res):
    out = []
    for c in (getattr(res, "content", None) or []):
        out.append(getattr(c, "text", None) or str(c))
    txt = "\n".join(out)
    try:
        d = json.loads(txt)
        return {k: d[k] for k in list(d)[:5] if not isinstance(d[k], (list, dict))}, len(txt)
    except Exception:
        return txt[:160], len(txt)


for n in READONLY:
    try:
        res = asyncio.run(server.call_tool(n, {}))
        is_err = bool(getattr(res, "isError", False))
        summary, size = payload(res)
        print(f"CALL {n}: isError={is_err} bytes={size} summary={summary}")
    except Exception as e:
        print(f"CALL {n}: EXC {type(e).__name__}: {str(e)[:160]}")
