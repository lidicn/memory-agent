import asyncio
print("="*78)
print("关节：同步 close() 被 await —— runtime.py:664 `await self.llm.close()`")
print("="*78)
class LLMRouter:            # 复刻真实签名：def close(self) -> None
    def close(self) -> None:
        print("      [LLMRouter.close] 实际执行了（同步）")
async def shutdown_like():
    rt=LLMRouter()
    try:
        await rt.close()          # ★ MA 真实写法
    except Exception as exc:
        print(f"      🔴 捕获: {type(exc).__name__}: {exc}")
        print("      → shutdown 打印『关闭 LLM 客户端异常』并继续")
asyncio.run(shutdown_like())
print()
print("="*78)
print("后果拆解")
print("="*78)
print("   1) close() 本身【已被执行】（await 前先求值）—— 所以连接关闭逻辑跑到了")
print("   2) 但 await None 必抛 TypeError，走进 except 分支")
print("   3) 运维看到日志『关闭 LLM 客户端异常』→ 误判为关闭失败，实际是假警报")
print("   4) 真正的风险：异常分支掩盖了 LLMProvider.close() 内部的")
print("      create_task 发后不管（llm_client.py:124），那个才是可能真的没关干净")
print()
print("★ 同类检查：其他组件的 close 是否也有同步/异步错配")
print("="*78)
import os, sys
sys.path.insert(0,"/data/workspace/ma/src")
import inspect, glob
for p in sorted(glob.glob('/data/workspace/ma/src/memory_agent/**/*.py', recursive=True)):
    try: src=open(p,encoding='utf-8').read(); tree=__import__('ast').parse(src)
    except Exception: continue
    import ast
    for n in ast.walk(tree):
        if isinstance(n, ast.AsyncFunctionDef):
            for sub in ast.walk(n):
                if isinstance(sub, ast.Await) and isinstance(sub.value, ast.Call):
                    f=sub.value.func
                    if isinstance(f, ast.Attribute) and f.attr=='close':
                        print(f"   await ...close()  {p.replace('/data/workspace/ma/src/memory_agent/','')}:{sub.lineno}")
