"""wire 面 / spec 面 / scope 面 三张集合的对账 + 逐工具真实签名。

判据来自三份真源：
- wire：mcp_server.py 里 @mcp.tool() 装饰的函数（AST，不需要 mcp 包）
- spec：tool_schema.SPEC_BY_NAME 中 expose∋mcp
- scope：mcp_scopes.REGISTERED_TOOLS ∪ WRITE_TOOLS（调用准入，fail-closed）
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

src_text = (ROOT / "src" / "memory_agent" / "mcp_server.py").read_text(encoding="utf-8")
lines = src_text.splitlines()
tree = ast.parse(src_text)

wire = {}
for node in ast.walk(tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if (getattr(target, "attr", None) or getattr(target, "id", "")) != "tool":
            continue
        pos = node.args.posonlyargs + node.args.args
        nd = len(node.args.defaults)
        pos = [a for a in pos if a.arg not in ("self", "cls")]
        defaults = node.args.defaults[-nd:] if nd else []
        pad = len(pos) - len(defaults)
        args = []
        for i, a in enumerate(pos):
            d = defaults[i - pad] if i >= pad else None
            args.append((a.arg,
                         ast.unparse(a.annotation) if a.annotation else "?",
                         ast.unparse(d) if d is not None else None))
        kw = []
        for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults):
            kw.append((a.arg, ast.unparse(a.annotation) if a.annotation else "?",
                       ast.unparse(d) if d is not None else None))
        body_calls = []
        for n in ast.walk(node):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
                    and n.value.id in ("rt", "runtime") and isinstance(n.ctx, ast.Load):
                body_calls.append(f"{n.value.id}.{n.attr}")
        first_line = (ast.get_docstring(node) or "").split("\n")[0].strip()
        # 取返回值里的 ok/error 形状线索
        wire[node.name] = {
            "line": node.lineno, "args": args, "kwonly": kw,
            "doc": first_line, "rt_attrs": sorted(set(body_calls)),
            "src": "\n".join(lines[node.lineno - 1:node.end_lineno]),
        }

from memory_agent import tool_schema as ts  # noqa: E402
from memory_agent.mcp_scopes import REGISTERED_TOOLS, WRITE_TOOLS  # noqa: E402

spec_mcp = {s.name for s in ts.SPEC_BY_NAME.values() if "mcp" in (s.expose or ())}
scoped = set(REGISTERED_TOOLS) | set(WRITE_TOOLS)
gap = sorted(set(wire) - spec_mcp)

print(f"wire={len(wire)} spec_mcp={len(spec_mcp)} gap={len(gap)}")
print(f"scope 登记={len(scoped)}（REGISTERED={len(REGISTERED_TOOLS)} WRITE={len(WRITE_TOOLS)}）")
print(f"\n=== A. 有 wire、无 spec、也无 scope 登记（当前调用即被 fail-closed 拒绝）===")
for n in gap:
    if n not in scoped:
        print(f"  {n}  [line {wire[n]['line']}]")
print(f"\n=== B. spec 有、scope 无（不应存在）===")
for n in sorted(spec_mcp - scoped):
    print(f"  {n}")
print(f"\n=== C. scope 登记、wire 与 spec 都无（幽灵登记）===")
for n in sorted(scoped - set(wire) - spec_mcp):
    print(f"  {n}")

print("\n\n################ 35 条 gap 工具源码 ################")
for n in gap:
    info = wire[n]
    print(f"\n##### {n}  (mcp_server.py:{info['line']})  scope={'WRITE' if n in WRITE_TOOLS else ('READ' if n in REGISTERED_TOOLS else '未登记')}")
    print(info["src"])
