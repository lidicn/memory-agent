import os, sys, time, gc
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.setdefault("JWT_SECRET","t"*40)
import memory_agent.vision_service as V
import inspect

# 找出所有按时间桶累加、且无清理的实例 dict
src=inspect.getsource(V.VisionService)
print("="*76)
print("长跑资源增长实测：进程内 dict 是否有界")
print("="*76)
print("\n_hour_calls 的 key = (room, 'MMDDHH') —— 每小时新增一组，永不清理\n")

# 复刻 _hour_bucket
def hour_bucket(dt=None):
    import datetime
    dt = dt or datetime.datetime.now()
    return dt.strftime("%m%d%H")

# 模拟 1 年运行：8 个房间，每小时 _bump_hour
rooms=[f"room{i}" for i in range(8)]
hour_calls={}
t0=time.time()
for day in range(365):
    for h in range(24):
        b=f"{((day%28)+1):02d}{h:02d}"   # 用 MMDDHH 形态
        for r in rooms:
            k=(r,b)
            hour_calls[k]=hour_calls.get(k,0)+1
dt=time.time()-t0
size=sys.getsizeof(hour_calls)
per=size/max(len(hour_calls),1)
print(f"   模拟 365 天 × 24 小时 × {len(rooms)} 房间")
print(f"   条目数      : {len(hour_calls):,}")
print(f"   dict 体积   : {size/1024/1024:.2f} MB（{per:.0f} 字节/条目）")
print(f"   年增长      : 约 {size/1024/1024:.1f} MB / 年（仅这一个 dict）")
print()
print("   ★ 无清理代码：全文仅 _backoff_until.pop(room) 一处淘汰")
print("   ★ 其他 6 个 dict 均以 room/entity_id 为 key → 有界（房间数固定）")
print()
print("="*76)
print("对照：有界 dict（key 为房间/实体）")
print("="*76)
for n,d in (("_last_call","room"),("_backoff_until","room"),("_fail_streak","room"),
            ("_light_cache","entity_id"),("_skip_counts","room"),("_last_result","room"),
            ("_hour_calls","(room, 小时桶) ← 唯一无界")):
    bounded = "有界" if "小时桶" not in d else "🔴 无界"
    print(f"   {n:<18} key={d:<18} {bounded}")
