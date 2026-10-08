"""用 ast 读 tmp-b63-mut.py 的 MUTANTS 字面量，逐条核对工作区树里"锚点在位、变异形状不在"。

为什么要这么量：20261006 我把这份 harness 当模块 import 了一次想看它的条数，而它末尾是
模块级 `sys.exit(main())` —— import 即开跑变异腿，跑到 M8（`if not (1 <= value <= …)` →
`if False:`）时被 kill，**还原没发生**，工作区就这样留了一条死门。手动抽查三条锚点当时
判了"干净"，因为查的是 M20/M21/M22，而泄漏的是 M8。所以这里改成全表核对，且只用 ast 解析、
绝不 import 被测脚本。

用法：python .qoder/tmp-b63-mut-verify.py   （RC=0 表示 22 条锚点各命中 1 次、变异形状 0 次）
"""
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
HARNESS = ROOT / ".qoder" / "tmp-b63-mut.py"


def mutants_from_ast():
    tree = ast.parse(HARNESS.read_text(encoding="utf-8"))
    names = {}
    target = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names[t.id] = node.value.value
        if any(isinstance(t, ast.Name) and t.id == "MUTANTS" for t in node.targets):
            target = node.value
    if target is None:
        raise SystemExit("MUTANTS 字面量没找到")
    out = []
    for item in target.elts:
        row = []
        for elt in item.elts:
            if isinstance(elt, ast.Name):
                row.append(names[elt.id])
            else:
                row.append(ast.literal_eval(elt))
        out.append(tuple(row))
    return out


def main():
    muts = mutants_from_ast()
    print(f"PARSE_ONLY_RC=0 MUTANTS_PARSED={len(muts)}")
    bad = 0
    cache = {}
    syntax_bad = 0
    for label, rel, old, new in muts:
        path = ROOT / rel
        if path not in cache:
            cache[path] = path.read_text(encoding="utf-8")
        text = cache[path]
        hit = text.count(old)
        # 删除式变异（M10：把 `if bad: return error(bad)` 两行摘掉）的 new 是 old 的前缀，
        # 干净树里必然命中一次——那不是残留，是这类锚点的固有形状，所以单独标出来不判红。
        deletion = bool(new) and new in old
        residue = -1 if deletion else (text.count(new) if new else 0)
        ok = hit == 1 and residue <= 0
        if not ok:
            bad += 1
        print(f"{'OK  ' if ok else 'BAD '}anchor={hit} "
              f"residue={'deletion-shaped' if residue < 0 else residue} {label}")

        # 变异腿**自己**必须是合法 Python。run14b 的 M21 替换串少了一个冒号，
        # 落盘后 pytest 在 collection 阶段 SyntaxError ⇒ RC=2、0 条 FAILED，
        # 台账上显示成"没咬住"（假红，与 run13 的 N11 空变异同族：都是量具坏）。
        # 同理 new==old 的空变异永远咬不住。这两条在出网前就该量掉。
        if new == old:
            syntax_bad += 1
            print(f"SYNTAX BAD-NOOP {label}")
        elif hit == 1:
            try:
                compile(text.replace(old, new, 1), str(path), "exec")
            except SyntaxError as exc:
                syntax_bad += 1
                print(f"SYNTAX BAD {label} -> {type(exc).__name__}: {exc.msg} @line {exc.lineno}")

    # 兜底：整棵 src/ 扫一遍 harness 会写进源码的"死门形状"。上面只核对已知锚点，
    # 抓不到"anchor 和 residue 都不在本批表里"的第三种泄漏（比如上一批 harness 留的）。
    scan_bad = 0
    for py in sorted((ROOT / "src").rglob("*.py")):
        body = py.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(body.splitlines(), 1):
            code = line.split("#")[0]
            if re.search(r"^\s*if False\s*:", code) or re.search(r"\bin \(\)\s*:", code):
                scan_bad += 1
                print(f"DEADSHAPE {py.relative_to(ROOT)}:{i} {line.strip()}")
    print(f"DEADSHAPE_COUNT={scan_bad}")
    print(f"SYNTAX_BAD={syntax_bad}")
    print(f"VERIFY_BAD={bad}")
    return 1 if (bad or scan_bad or syntax_bad) else 0


if __name__ == "__main__":
    sys.exit(main())
