r"""把 scan_claimed_semantics 的 41 条声明逐条对到"测试体里真的提到这个名字的用例"。

只做定位，不做判断：命中的用例是否**断言了那条声明语义**要人读——量具登记时必须挑真断言的那条，
不能拿"提到过名字"冒充验证用例（那正是本门要判红的假登记）。

    python .qoder/tmp-claims-map.py
"""
import ast
import io
import os

SRC = "src/memory_agent"
TESTS = "tests"
CLAIMS = ("自增", "递增", "唯一真源", "fail-closed", "幂等")


def decls():
    out = []
    for dp, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            try:
                tree = ast.parse(io.open(os.path.join(dp, fn), encoding="utf-8",
                                         errors="replace").read())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    doc = ast.get_docstring(node) or ""
                    hits = [w for w in CLAIMS if w in doc]
                    if hits:
                        out.append((fn, node.name, node.lineno, hits))
    return sorted(out)


def tests_index():
    idx = []
    for dp, dirs, files in os.walk(TESTS):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            text = io.open(os.path.join(dp, fn), encoding="utf-8", errors="replace").read()
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            lines = text.split("\n")
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
                    seg = "\n".join(lines[node.lineno - 1:(node.end_lineno or node.lineno)])
                    idx.append((fn, node.name, seg))
    return idx


def main():
    idx = tests_index()
    d = decls()
    print("DECL=%d TESTFUNCS=%d" % (len(d), len(idx)))
    nomap = []
    for fn, name, line, hits in d:
        hits_list = [(tf, tn) for tf, tn, seg in idx if name in seg]
        if not hits_list:
            nomap.append((fn, name, line, hits))
            print("%-22s:%-4d %-34s 声明=%-18s HITS=0" % (fn, line, name, "/".join(hits)))
        else:
            files = sorted({tf for tf, _ in hits_list})
            print("%-22s:%-4d %-34s 声明=%-18s TESTFILES=%s  用例=%s"
                  % (fn, line, name, "/".join(hits), files,
                     [tn for _tf, tn in hits_list][:4]))
    print("NOMAP=%d" % len(nomap))
    for fn, name, line, hits in nomap:
        print("   %s:%d %s (%s)" % (fn, line, name, "/".join(hits)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
