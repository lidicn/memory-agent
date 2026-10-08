import os, sys, sqlite3, json
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.setdefault("JWT_SECRET","t"*40)
print("="*78)
print("实测：一条脏 JSON 记录是否会打断整批规则加载")
print("="*78)
# 复刻 rule_engine.py:312-329 的模式
rows=[
    {"id":"r1","condition_json":'{"room":"客厅"}',"action_json":'{"do":"a"}'},
    {"id":"r2","condition_json":'{BROKEN',"action_json":'{"do":"b"}'},   # ← 脏数据
    {"id":"r3","condition_json":'{"room":"书房"}',"action_json":'{"do":"c"}'},
]
print(f"\n   待加载 {len(rows)} 条规则，其中 r2 的 condition_json 损坏\n")
print("   A) MA 现状：直接 json.loads，无 try")
loaded=[]
try:
    for d in rows:
        d["condition"]=json.loads(d.pop("condition_json","{}"))
        d["action"]=json.loads(d.pop("action_json","{}"))
        loaded.append(d)
except json.JSONDecodeError as e:
    print(f"      🔴 在第 {len(loaded)+1} 条抛 JSONDecodeError: {e}")
print(f"      → 成功加载 {len(loaded)}/{len(rows)} 条，其余 {len(rows)-len(loaded)} 条被丢弃")

print("\n   B) 正确写法：单条 try，损坏则跳过")
loaded2=[]
skipped=[]
for d in rows:
    try:
        d2=dict(d)
        d2["condition"]=json.loads(d2.pop("condition_json","{}"))
        d2["action"]=json.loads(d2.pop("action_json","{}"))
        loaded2.append(d2)
    except Exception:
        skipped.append(d.get("id"))
print(f"      ✓ 成功加载 {len(loaded2)}/{len(rows)} 条，跳过 {skipped}")
print()
print("="*78)
print("★ 14 处无保护 json.loads；rule_engine 4 处在规则加载路径上")
print("★ 后果：DB 里一条手工改坏/写入中断的记录 → 整个规则引擎加载失败")
print("★ 且这是『静默降级』：规则没了，系统照常跑，只是不再触发")
print("="*78)
