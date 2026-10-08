import os, sys, re, glob
sys.path.insert(0,"/data/workspace/ma/src")
from memory_agent.insights.api import InsightService
from memory_agent.insights_legacy import InsightService as Legacy
prod=set(dir(InsightService)); leg=set(dir(Legacy))
missing=[n for n in leg if n not in prod and n.startswith('_')]
files=glob.glob('/data/workspace/ma/src/memory_agent/**/*.py', recursive=True)
print("生产代码中对「生产类缺失方法」的调用点：")
hits={}
for f in files:
    if 'insights_legacy' in f: continue
    try: src=open(f,encoding='utf-8').read()
    except: continue
    for n in missing:
        for m in re.finditer(r'\.%s\s*\(' % re.escape(n), src):
            line=src[:m.start()].count('\n')+1
            hits.setdefault(n,[]).append((f.replace('/data/workspace/ma/src/memory_agent/',''),line))
for n in sorted(hits):
    locs=hits[n]
    print(f"  ✗ .{n}()  被 {len(locs)} 处调用 → {locs[:4]}")
