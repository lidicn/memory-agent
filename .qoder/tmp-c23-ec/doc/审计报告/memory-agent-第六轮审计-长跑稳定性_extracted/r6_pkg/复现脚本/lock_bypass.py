import os, sys, time, sqlite3, threading
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.setdefault("JWT_SECRET","t"*40)
DB="/tmp/lb.db"
for f in (DB,DB+"-wal",DB+"-shm"):
    if os.path.exists(f): os.remove(f)
raw=sqlite3.connect(DB, check_same_thread=False, timeout=30.0)
raw.execute("PRAGMA journal_mode=WAL")
raw.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, tag TEXT)")
raw.commit(); raw.close()

from memory_agent.store import Store
s=Store(DB, tz_offset_hours=0.0); s.connect()
conn=s.connect()

print("="*78)
print("实测：绕过 _lock 直接 conn.execute() 对事务隔离的破坏")
print("="*78)
print("  场景：线程A 持锁跑多语句事务；线程B 用「无锁 connect()」插一条并提交\n")

result={}
def threadA():
    # 模拟 store 内部正确写法：持锁 + 多语句事务
    with s._lock:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO t (tag) VALUES ('A_step1')")
        time.sleep(0.8)                      # 事务进行中
        mid = conn.execute("SELECT COUNT(*) FROM t WHERE tag LIKE 'A%'").fetchone()[0]
        conn.execute("INSERT INTO t (tag) VALUES ('A_step2')")
        conn.execute("COMMIT")
    result['A']=mid

def threadB():
    time.sleep(0.3)
    # ★ MA 中 18 处的写法：直接 connect() 拿共享连接，不持锁
    c2 = s.connect()          # 返回同一个共享连接
    c2.execute("INSERT INTO t (tag) VALUES ('B_rogue')")
    c2.commit()               # ← 会把 A 的未提交事务一起提交！
    result['B']='committed'

ta=threading.Thread(target=threadA); tb=threading.Thread(target=threadB)
ta.start(); tb.start(); ta.join(); tb.join()

n_total=conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
rows=[r[0] for r in conn.execute("SELECT tag FROM t ORDER BY id")]
print(f"   A 事务中途看到自己插的行数: {result.get('A')}")
print(f"   最终表内容: {rows}")
print()
if 'B_rogue' in rows and 'A_step2' in rows:
    print("   🔴 确认：B 的 commit 把 A 尚未完成的事务一并提交")
    print("      A 的『要么全成功要么全失败』保证被破坏（原子性失效）")
print()
print("="*78)
print("★ 根因：Store.connect() 是单例共享连接，但 18 处调用点直接用")
print("  conn.execute() 而【不持有 _lock】，绕开了 store 的锁保护")
print("="*78)
