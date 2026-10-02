import os, sys
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.setdefault("JWT_SECRET","t"*40)
from datetime import datetime, timedelta
print("="*76)
print("幂等键 TTL 精度实测：expires_at 用 %Y-%m-%d 存，TTL 却按小时算")
print("="*76)
# 复刻 store.save_idempotency 的算法
def expires_at(now, ttl_hours):
    return (now + timedelta(hours=max(1,int(ttl_hours)))).strftime("%Y-%m-%d")
print(f"\n{'创建时刻':<22}{'ttl_hours':<10}{'实际过期时刻':<22}{'实际有效时长'}")
print("-"*76)
for hh,mm in ((0,30),(8,0),(14,0),(20,0),(23,30)):
    now=datetime(2026,10,1,hh,mm)
    exp=expires_at(now,24)
    # 复刻 get_idempotency 的判断：if expires_at < today → 过期
    # 即：在 expires_at 当天全天仍有效，第二天起失效
    day_after = datetime.strptime(exp,"%Y-%m-%d")+timedelta(days=1)
    actual = (day_after-now).total_seconds()/3600
    print(f"{now.strftime('%Y-%m-%d %H:%M'):<22}{24:<10}{day_after.strftime('%Y-%m-%d %H:%M'):<22}{actual:.1f} 小时")
print()
print("★ ttl_hours=24 实际有效 24.5 ~ 48 小时（取决于创建时刻）")
print("★ 文档承诺「24h 内再次调用直接返回首次结果」（mcp_server.py:127）")
print("★ 偏差最大 2 倍；写工具（report_bug / save_agent_memory）可能整天被吞")
print()
print("="*76)
print("附：ttl 精度丢失 — 传 1 小时也变成按天")
print("="*76)
now=datetime(2026,10,1,0,30)
for ttl in (1,2,12,24):
    print(f"   ttl_hours={ttl:<3} → expires_at={expires_at(now,ttl)}  （日期串，无时分）")
