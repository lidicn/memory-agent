import os, sys, asyncio, time, inspect
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.update({"JWT_SECRET":"t"*40,"DB_PATH":"/tmp/ma30.db","TZ_OFFSET_HOURS":"8"})
import memory_agent.runtime as R

src=inspect.getsource(R.AppRuntime.startup)
canc=inspect.getsource(R.AppRuntime.shutdown)
import re
created=set(re.findall(r'self\.(_[a-z_]*task)\s*=\s*asyncio\.create_task', src))
canceled=set(re.findall(r'getattr\(self,\s*"(_[a-z_]*task)"', canc))
print("="*78); print("startup 创建 vs shutdown 取消 —— 静态对照"); print("="*78)
print(f"\nstartup 创建 {len(created)} 个常驻 task:")
for t_ in sorted(created): print(f"   {t_}")
print(f"\nshutdown 取消 {len(canceled)} 个:")
for t_ in sorted(canceled): print(f"   {t_}")
leaked=sorted(created-canceled)
print(f"\n🔴 从未被 cancel 的 task（{len(leaked)} 个）:")
for t_ in leaked: print(f"   ⚠ {t_}")
print()
print("="*78)
print("运行时实测：这些 task 在 shutdown 后是否仍在运行")
print("="*78)
async def main():
    # 复刻 shutdown 的行为：只 cancel 那 7 个
    running={}
    async def mk(name):
        try:
            while True: await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            running[name]="cancelled"; raise
    tasks={}
    for t_ in sorted(created):
        tasks[t_]=asyncio.create_task(mk(t_)); running[t_]="running"
    await asyncio.sleep(0.2)
    for t_ in sorted(canceled):
        if t_ in tasks: tasks[t_].cancel()
    await asyncio.sleep(0.2)
    alive=[t_ for t_ in sorted(created) if running.get(t_)=="running"]
    print(f"   shutdown 后仍存活: {len(alive)} 个")
    for t_ in alive: print(f"      ⚠ {t_}  仍在运行（未被 cancel）")
    # 清理
    for t_ in tasks.values(): t_.cancel()
    await asyncio.sleep(0.1)
asyncio.run(main())
