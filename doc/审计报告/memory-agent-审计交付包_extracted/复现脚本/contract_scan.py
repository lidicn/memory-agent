import os, sys, re, json
os.environ.update({"JWT_SECRET":"t"*40,"DB_PATH":"/data/workspace/madata/c.db"})
sys.path.insert(0,"/data/workspace/ma/src")

# 从测试源码中提取 "obj.attr(" 形式调用，检查生产类是否有该属性
import ast, glob
from memory_agent.insights.api import InsightService
from memory_agent.insights_legacy import InsightService as Legacy

prod = set(n for n in dir(InsightService) if not n.startswith('__'))
leg  = set(n for n in dir(Legacy) if not n.startswith('__'))

print("="*70)
print("InsightService API 契约比对：生产 vs legacy 实现")
print("="*70)
missing = sorted(n for n in leg if n.startswith('_') and n not in prod and callable(getattr(Legacy, n, None)))
print(f"legacy 有、生产缺失的私有方法（共 {len(missing)} 个）：")
for n in missing[:40]:
    print(f"   ✗ {n}")
