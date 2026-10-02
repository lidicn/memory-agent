import os, sys, time
from datetime import datetime, timezone, timedelta
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.setdefault("JWT_SECRET","t"*40)
from memory_agent.store import now_local
print("="*78)
print("关节：时区边界 —— now_local(tz_offset) vs datetime.now() 混用")
print("="*78)
# 项目配置 tz_offset_hours=8，但多处直接用 datetime.now()（容器通常 UTC）
print("\n模拟容器时区=UTC、项目 tz_offset_hours=8：")
import os as _os
_os.environ['TZ']='UTC'; time.tzset()
print(f"   datetime.now()            = {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(f"   now_local(8)  (项目正确)   = {now_local(8).strftime('%Y-%m-%d %H:%M')}")
print(f"   datetime.now(timezone.utc)= {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}")
print()
print("="*78)
print("后果：两条路径算出的 'day' 可能差一天")
print("="*78)
for hh in (0, 7, 8, 16, 23):
    # 构造 UTC 时间的某小时
    utc = datetime(2026,10,1,hh,30)
    local = utc + timedelta(hours=8)
    print(f"   UTC {utc.strftime('%m-%d %H:%M')} → 本地 {local.strftime('%m-%d %H:%M')}"
          f"   day: {utc.strftime('%m-%d')} vs {local.strftime('%m-%d')}"
          f"   {'⚠ 跨天' if utc.strftime('%m-%d')!=local.strftime('%m-%d') else ''}")
print()
print("   ★ 在 16:00-24:00 UTC 区间（即本地 00:00-08:00），两条路径的 day 相差一天")
print("   ★ 混用点：store.now_local(8) 写 day；")
print("     candidate_promotion.py:1089/1434、causal_scanner.py:44、backup.py:33 等用 datetime.now()")
print()
print("="*78)
print("实测：同一时刻两种算法写入的 day 值")
print("="*78)
_os.environ['TZ']='UTC'; time.tzset()
t = datetime(2026,10,1,19,0)   # UTC 19:00 = 本地次日 03:00
print(f"   真实时刻（UTC）: {t}")
print(f"   datetime.now() 口径 day = {t.strftime('%Y-%m-%d')}        ← naive 路径")
print(f"   now_local(8) 口径 day   = {(t+timedelta(hours=8)).strftime('%Y-%m-%d')}  ← 项目正确路径")
print(f"   → 同一条事件可能被归到不同的天，按天聚合/清理会错位")
