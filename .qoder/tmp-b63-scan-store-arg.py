"""量具盲区探针：把「同步调用里把 DB 句柄当参数传进去」这一格在 HEAD 上的存量数出来。

r5 卸载量具只认 `rt.store.<m>()` 形状，`build_profile(rt.store, ...)` 这种不进统计。
先看全仓有多少这种形状，再决定量具能不能收这条。
"""
import ast
import pathlib
import sys

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "src/memory_agent")
DB_RECEIVERS = ("store", "ha_db")


def _is_to_thread(node):
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr == "to_thread"
    return isinstance(fn, ast.Name) and fn.id == "to_thread"


def _db_handle_arg(node):
    """表达式里出现 `X.store` / `X.ha_db`（含 `store` 这个名字本身）。"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr in DB_RECEIVERS:
            return True
        if isinstance(sub, ast.Name) and sub.id in DB_RECEIVERS:
            return True
    return None


def scan(text):
    tree = ast.parse(text)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        guarded = set()
        nested = {}
        for sub in ast.walk(node):
            if isinstance(sub, ast.FunctionDef):
                nested[sub.name] = sub
            if isinstance(sub, ast.Await):
                for c in ast.walk(sub.value):
                    if isinstance(c, ast.Call):
                        guarded.add(id(c))
            if isinstance(sub, ast.Call) and _is_to_thread(sub):
                guarded.add(id(sub))          # to_thread 自己不算同步调用
                for a in sub.args:
                    if isinstance(a, ast.Call):
                        guarded.add(id(a))
                    if isinstance(a, ast.Name) and a.id in nested:
                        for c in ast.walk(nested[a.id]):
                            if isinstance(c, ast.Call):
                                guarded.add(c)
                                guarded.add(id(c))
                for kw in sub.keywords:
                    if isinstance(kw.value, ast.Call):
                        guarded.add(id(kw.value))
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call) or id(sub) in guarded:
                continue
            for a in list(sub.args) + [k.value for k in sub.keywords]:
                if _db_handle_arg(a):
                    fn = sub.func
                    nm = getattr(fn, "attr", None) or getattr(fn, "id", "?")
                    hits.append((sub.lineno, nm, ast.dump(a)[:70]))
                    break
    return hits


def main():
    total = 0
    for path in sorted(ROOT.glob("**/*.py")):
        if "test" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, nm, arg in scan(text):
            total += 1
            print(f"{path}:{lineno} {nm}( {arg} )")
    print(f"TOTAL={total}")


main()
