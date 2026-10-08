import ast, os, sys, glob

ROOT="/data/workspace/ma/src/memory_agent"
# 已知的同步阻塞 API
BLOCKING_OBJ = {"store","ha_db","ha","mqtt","llm","history","collector","vision",
                "agent_memory","identity","insights","activity","tokens","templates"}
BLOCKING_FN  = {"open","time.sleep","requests.get","requests.post"}

findings=[]
for path in glob.glob(ROOT+"/**/*.py", recursive=True):
    try: tree=ast.parse(open(path,encoding='utf-8').read())
    except Exception: continue
    rel=path.replace(ROOT+"/","")
    for node in ast.walk(tree):
        if not isinstance(node,(ast.AsyncFunctionDef,)): continue
        for sub in ast.walk(node):
            # 1) self.store.xxx(...) / rt.store.xxx(...) 同步调用
            if isinstance(sub, ast.Call):
                f=sub.func
                # obj.attr 形式
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute):
                    base=f.value
                    if isinstance(base.value, ast.Name) and base.attr in BLOCKING_OBJ:
                        # 检查是否被 asyncio.to_thread 包裹 —— 简化：记录全部，人工复核
                        findings.append((rel, sub.lineno, node.name,
                                         f"{base.value.id}.{base.attr}.{f.attr}()", "sync-call-in-async"))
                # 2) time.sleep / requests / open
                nm=None
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                    nm=f"{f.value.id}.{f.attr}"
                elif isinstance(f, ast.Name):
                    nm=f.id
                if nm in ("time.sleep","requests.get","requests.post","requests.put"):
                    findings.append((rel, sub.lineno, node.name, nm+"()", "blocking-primitive"))
                if nm=="open":
                    findings.append((rel, sub.lineno, node.name, "open()", "blocking-file-io"))

from collections import Counter
c=Counter(x[4] for x in findings)
print("="*78)
print("AST 扫描：async 函数内的同步阻塞调用")
print("="*78)
print(f"总命中 {len(findings)} 处")
for k,v in c.most_common(): print(f"   {k:<24} {v}")

print("\n【sync-call-in-async】按文件聚合：")
byf={}
for rel,ln,fn,code,kind in findings:
    if kind=="sync-call-in-async": byf.setdefault(rel,[]).append((ln,fn,code))
for rel in sorted(byf, key=lambda r:-len(byf[r])):
    print(f"\n   {rel}  ({len(byf[rel])} 处)")
    for ln,fn,code in byf[rel][:8]:
        print(f"      :{ln:<6} {fn:<34} {code}")
    if len(byf[rel])>8: print(f"      ... 另 {len(byf[rel])-8} 处")
