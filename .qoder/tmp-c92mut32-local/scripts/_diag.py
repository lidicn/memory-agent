import socket, subprocess, sys
s = socket.socket()
s.settimeout(3)
r = s.connect_ex(("127.0.0.1", 8000))
print("port_8000:", "OPEN" if r == 0 else "CLOSED")
s.close()
# 试 import mcp_server 看是否挂起
try:
    import memory_agent.mcp_server as m
    print("mcp_server_import: OK")
    print("registered_tools_count:", len(getattr(m, "TOOL_NAMES", [])))
except Exception as e:
    print("mcp_server_import: FAIL", type(e).__name__, str(e)[:300])
