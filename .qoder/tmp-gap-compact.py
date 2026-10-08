"""三张集合对账 + gap 工具紧凑签名表（含旧手写 TOOL_CATALOG 是否已文档化）。"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

src_text = (ROOT / "src" / "memory_agent" / "mcp_server.py").read_text(encoding="utf-8")
tree = ast.parse(src_text)

wire = {}
for node in ast.walk(tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if (getattr(target, "attr", None) or getattr(target, "id", "")) != "tool":
            continue
        pos = [a for a in node.args.args if a.arg not in ("self", "cls")]
        nd = len(node.args.defaults)
        pad = len(pos) - nd
        args = []
        for i, a in enumerate(pos):
            d = node.args.defaults[i - pad] if i >= pad else None
            args.append((a.arg, ast.unparse(a.annotation) if a.annotation else "?",
                         ast.unparse(d) if d is not None else None))
        kw = [(a.arg, ast.unparse(a.annotation) if a.annotation else "?",
               ast.unparse(d) if d is not None else None)
              for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults)]
        wire[node.name] = {"line": node.lineno, "args": args, "kw": kw,
                           "doc": (ast.get_docstring(node) or "").split("\n")[0].strip()}

# 旧手写 TOOL_CATALOG（mcp_server.py:166 起）里已文档化的名字
legacy = set()
for node in ast.walk(tree):
    tgt = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
    if isinstance(node, (ast.Assign, ast.AnnAssign)) and \
            getattr(tgt, "id", "") == "TOOL_CATALOG" and isinstance(node.value, ast.List):
        for el in node.value.elts:
            for k, v in zip(el.keys, el.values):
                if isinstance(k, ast.Constant) and k.value == "name" and isinstance(v, ast.Constant):
                    legacy.add(v.value)

from memory_agent import tool_schema as ts  # noqa: E402
from memory_agent.mcp_scopes import REGISTERED_TOOLS, WRITE_TOOLS  # noqa: E402

spec_mcp = {s.name for s in ts.SPEC_BY_NAME.values() if "mcp" in (s.expose or ())}
scoped = set(REGISTERED_TOOLS) | set(WRITE_TOOLS)
gap = sorted(set(wire) - spec_mcp)

print(f"wire={len(wire)} spec_mcp={len(spec_mcp)} gap={len(gap)} legacy_catalog={len(legacy)}")
print(f"scope登记={len(scoped)}  gap∩legacy={len(set(gap) & legacy)}  gap∩scoped={len(set(gap) & scoped)}")

print("\n=== A. gap 且无 scope 登记（当前调用被 fail-closed 拒绝）===")
print("  " + (", ".join(n for n in gap if n not in scoped) or "（空）"))
print("=== B. spec 有、scope 无 ===")
print("  " + (", ".join(sorted(spec_mcp - scoped)) or "（空）"))
print("=== C. scope 登记、wire 与 spec 都无（幽灵登记）===")
print("  " + (", ".join(sorted(scoped - set(wire) - spec_mcp)) or "（空）"))

print("\n=== gap 明细 ===")
for n in gap:
    i = wire[n]
    sig = ", ".join(f"{a}:{t}" + (f"={d}" if d is not None else "") for a, t, d in i["args"])
    ksig = ", ".join(f"{a}:{t}" + (f"={d}" if d is not None else "") for a, t, d in i["kw"])
    mark = "L" if n in legacy else "-"
    print(f"[{mark}] {n}  :{i['line']}  scope={'W' if n in WRITE_TOOLS else ('R' if n in scoped else '?')}")
    print(f"     args: {sig}" + (f" | kwonly: {ksig}" if ksig else " | kwonly: -"))
    print(f"     doc: {i['doc']}")
