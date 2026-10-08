import ast, glob
ROOT="/data/workspace/ma/src/memory_agent"
results=[]
for path in glob.glob(ROOT+"/**/*.py", recursive=True):
    try: src=open(path,encoding='utf-8').read(); tree=ast.parse(src)
    except Exception: continue
    rel=path.replace(ROOT+"/","")
    lines=src.split('\n')
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef): continue
        # 收集所有被 await 或 to_thread 包裹的 Call 节点
        guarded=set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Await):
                for c in ast.walk(sub.value):
                    if isinstance(c, ast.Call): guarded.add(id(c))
            if isinstance(sub, ast.Call):
                fn=sub.func
                nm = (f"{fn.value.id}.{fn.attr}" if isinstance(fn,ast.Attribute) and isinstance(fn.value,ast.Name)
                      else getattr(fn,'attr',None) if isinstance(fn,ast.Attribute) else getattr(fn,'id',None))
                if nm and nm.endswith("to_thread"):
                    for a in sub.args:
                        if isinstance(a, ast.Call): guarded.add(id(a))
        # 找出未受保护的同步 store/db 调用
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call) or id(sub) in guarded: continue
            f=sub.func
            if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute)): continue
            base=f.value
            if not isinstance(base.value, ast.Name): continue
            if base.attr not in ("store","ha_db","identity","collector"): continue
            results.append((rel, sub.lineno, node.name, f"{base.value.id}.{base.attr}.{f.attr}()",
                            (lines[sub.lineno-1].strip()[:70] if sub.lineno<=len(lines) else "")))
from collections import defaultdict
byf=defaultdict(list)
for r in results: byf[r[0]].append(r)
print("="*80)
print("精确扫描：async 函数内【未被 await / to_thread 保护】的同步 DB 调用")
print("="*80)
print(f"命中 {len(results)} 处\n")
for rel in sorted(byf, key=lambda r:-len(byf[r])):
    items=byf[rel]
    print(f"{rel}  ({len(items)} 处)")
    for _,ln,fn,code,src in items[:6]:
        print(f"   :{ln:<6} {fn:<30} {code:<34} | {src}")
    if len(items)>6: print(f"   ... 另 {len(items)-6} 处")
    print()
