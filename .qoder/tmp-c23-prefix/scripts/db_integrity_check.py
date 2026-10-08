"""数据库健康检查 + 损坏诊断"""
import json, sqlite3, time, os

DB = "/data/memory_agent.db"
BAK = f"/data/memory_agent.db.bak.{int(time.time())}"

# 1. 先备份
print(f"备份 {DB} -> {BAK}")
import shutil
shutil.copy2(DB, BAK)
print(f"备份完成: {os.path.getsize(BAK)/1024/1024:.1f} MB")

# 2. integrity_check
print("\n=== PRAGMA integrity_check ===")
conn = sqlite3.connect(DB)
try:
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    for r in rows[:20]:
        print(f"  {r[0]}")
    if len(rows) > 20:
        print(f"  ... 共 {len(rows)} 行")
except Exception as e:
    print(f"FAIL: {e}")

# 3. quick_check
print("\n=== PRAGMA quick_check ===")
try:
    rows = conn.execute("PRAGMA quick_check").fetchall()
    for r in rows[:10]:
        print(f"  {r[0]}")
except Exception as e:
    print(f"FAIL: {e}")

# 4. 看哪些表能读
print("\n=== 表可读性 ===")
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
bad_tables = []
for t in tables:
    tname = t[0]
    try:
        cnt = conn.execute(f"SELECT COUNT(*) FROM [{tname}]").fetchone()[0]
        print(f"  {tname}: {cnt} rows OK")
    except Exception as e:
        bad_tables.append((tname, str(e)[:60]))
        print(f"  {tname}: CORRUPT - {str(e)[:60]}")

conn.close()

result = {
    "backup": BAK,
    "bad_tables": bad_tables,
    "total_bad": len(bad_tables),
}
with open("/tmp/db_diagnosis.json", "w") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
