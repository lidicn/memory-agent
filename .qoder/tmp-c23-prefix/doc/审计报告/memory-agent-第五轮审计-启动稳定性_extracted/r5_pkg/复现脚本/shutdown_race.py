import os, sys, asyncio, time
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.update({"JWT_SECRET":"t"*40,"DB_PATH":"/tmp/ma30.db","TZ_OFFSET_HOURS":"8"})
from memory_agent.store import Store
s=Store("/tmp/ma30.db", tz_offset_hours=8.0); s.connect()

print("="*78)
print("关闭竞态实测：_retention_task 持锁执行 purge_old 时，shutdown 调 store.close()")
print("="*78)
print("   （_retention_task 未被 cancel，shutdown 却会 await store.close）\n")

async def main():
    # 模拟 retention task：在线程里持锁跑长事务
    def purge_like():
        with s._lock:
            time.sleep(3.0)     # 模拟 purge_old 持锁 3 秒（真实实测 5.2 秒）
            return "purge done"
    retention_task = asyncio.create_task(asyncio.to_thread(purge_like))
    await asyncio.sleep(0.3)    # 让它先拿到锁

    # 模拟 shutdown：cancel 那 7 个（不含 retention），然后 await store.close()
    t0=time.time()
    try:
        await asyncio.wait_for(asyncio.to_thread(s.close), timeout=8)
        print(f"   store.close() 完成，耗时 {(time.time()-t0)*1000:.0f} ms")
        print(f"   → shutdown 被 purge_old 阻塞了 {(time.time()-t0):.1f} 秒")
    except asyncio.TimeoutError:
        print(f"   🔴 store.close() 8 秒超时未完成（仍在等锁）")
    r=await retention_task
    print(f"   retention task 结果: {r}")
    print()
    print("   ★ 若 retention_task 被正确 cancel，purge 会中断，close 立即返回")
    print("   ★ 现状：关闭必须等 purge_old 跑完（真实 5.2 秒，大库更久）")
asyncio.run(main())
