import os, sys, asyncio
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.update({"JWT_SECRET":"t"*40,"DB_PATH":"/tmp/idem.db",
 "HASS_SERVER":"http://127.0.0.1:18123","HASS_TOKEN":"d","REDIS_HOST":"127.0.0.1",
 "CHROMA_HOST":"127.0.0.1","LLM_API_URL":"http://127.0.0.1:19999/v1","LLM_API_KEY":"d","LLM_MODEL":"t"})
import importlib
mcp = importlib.import_module("memory_agent.mcp_server")
from memory_agent.store import Store

class FakeResult:
    def __init__(self, text): self.text=text; self.is_error=False; self.isError=False; self.content=[]
class FakeServerCls:
    def __init__(self): self.calls=0; self.delay=0.0
    async def call_tool(self, *a, **k):
        self.calls += 1
        if self.delay: await asyncio.sleep(self.delay)
        return FakeResult(f"executed #{self.calls}")

srv = FakeServerCls()
mcp.MCPServer = srv
mcp.requires = lambda name, scopes: True   # 绕过权限层，专测幂等逻辑

print("="*78)
print("幂等键 TOCTOU 实测（mcp_server.py:904-930 真实分发路径）")
print("="*78)
print("\n代码路径：")
print("   cached = _idem_get(cache_key)             # CHECK")
print("   if cached is not None: return _idem_result(cached)")
print("   result = await MCPServer.call_tool(...)   # ACT  ← await，窗口打开")
print("   _idem_save(cache_key, ...)                # SAVE ← 太晚\n")

async def trial(delay, n=5):
    s=Store("/tmp/idem.db", tz_offset_hours=8.0); s.init_schema()
    s.connect().execute("DELETE FROM idempotency_keys"); s.connect().commit()
    mcp.get_runtime().store = s
    srv.calls=0; srv.delay=delay
    await asyncio.gather(*[mcp._tracked_call_tool(srv, "write_tool", {"idempotency_key":"k1"}) for _ in range(n)])
    return srv.calls

async def main():
    print(f"{'工具耗时':<10}{'并发':<6}{'实际执行':<10}{'结论'}")
    print("-"*78)
    for delay,label in ((0.0,"0ms"),(0.05,"50ms"),(0.3,"300ms"),(1.0,"1s")):
        c=await trial(delay)
        v = "✓ 幂等生效" if c==1 else f"✗ 重复执行 {c} 次"
        print(f"{label:<10}{5:<6}{c:<10}{v}")
    print()
    print("="*78)
    print("★ 即使并发 5 次全部携带同一 idempotency_key，工具仍被执行 5 次")
    print("★ 幂等保证仅在【串行】调用下成立（第二次能查到第一次写的结果）")
    print("★ 真实场景：网络重试 / Agent 并发调用 / 客户端超时重发")
    print("="*78)
asyncio.run(main())
