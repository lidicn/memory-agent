#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导入期裸 env 转换门（稳定性/功能性审计 20261012 · D1 + §四 工具漏报第 2 条）。

判红的形状：模块级（含 class 体、`if`/`try`/`with` 等导入期就会执行的分支）出现
    X = int(os.getenv("N", "5")) / float(os.environ.get(...)) / int(os.environ["N"])
这类赋值。它们在 **import 那一刻** 就跑：
  - env 写成非数字（`MA_VOICE_CACHE_MAX=2000条`、误留引号、空串）→ ValueError → 模块导入失败
    → uvicorn 起不来 → 容器启动失败循环（NAS 侧的 churn 是开发规范 §0 记着的整机冻结路径）；
  - `os.environ["N"]` 键缺失 → KeyError，同一条崩溃链；
  - 传 0 或负值不报错，却把护栏语义整条关掉（上限 0 = 这条护栏不存在）。

正解是 `memory_agent.env_cfg.env_int / env_float`：坏值记 warning 后回落默认值，
一个手滑的 env 不允许杀死进程。所以本门认这两种调用为干净，其余模块级 int/float(env) 一律判红。

口径（写死，避免把自己读红）：
  不进函数体    —— FunctionDef / AsyncFunctionDef / Lambda 内部的转换发生在调用期，不是导入期，
                   由各自的入参校验负责，本门不管（管了就会把 200 处函数内 int() 全读成红的）。
  要进 class 体 —— class 语句体在导入期执行，形状与模块级完全一样。
  要进 if/try/with/for/while —— 这些分支在模块级就是导入期。

用法：
    python scripts/scan_import_time_env_cast.py            # 默认扫 src/memory_agent
    python scripts/scan_import_time_env_cast.py --self-test
退出码：0 干净 / 1 有判红 / 2 量具自检失败
"""
import argparse
import ast
import io
import os
import sys

SRC_DEFAULT = os.path.join("src", "memory_agent")
CASTS = ("int", "float")


def _attr_chain(node):
    """把 `os.getenv(...)` / `os.environ.get(...)` / `os.environ[...]` 归一成可比较的元组链。"""
    if isinstance(node, ast.Call):
        base = _attr_chain(node.func)
        if base is None:
            return None
        return base + ("call",)
    if isinstance(node, ast.Subscript):
        base = _attr_chain(node.value)
        if base is None:
            return None
        return base + ("__getitem__",)
    if isinstance(node, ast.Attribute):
        base = _attr_chain(node.value)
        if base is None:
            return None
        return base + (node.attr,)
    if isinstance(node, ast.Name):
        return (node.id,)
    return None


def _reads_env(node):
    """这个表达式里有没有直接读 os.getenv / os.environ（任意一层，含 `... or "5"` 的左操作数）。"""
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Call, ast.Attribute, ast.Subscript, ast.Name)):
            chain = _attr_chain(sub)
            if not chain:
                continue
            if chain[:2] == ("os", "getenv") or chain[:3] == ("os", "environ", "get"):
                return True
            if chain[:1] == ("os",) and "environ" in chain:
                return True
    return False


def _is_bare_cast_on_env(node):
    if not isinstance(node, ast.Call):
        return False
    if not (isinstance(node.func, ast.Name) and node.func.id in CASTS):
        return False
    return bool(node.args) and _reads_env(node.args[0])


def _scan_body(stmts, path, out, depth=0):
    for st in stmts:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue  # 函数体 = 调用期，不是导入期
        if isinstance(st, ast.Assign):
            for target in st.targets:
                if _is_bare_cast_on_env(st.value):
                    out.append({"file": path, "line": st.lineno,
                                "name": getattr(target, "id", "?"),
                                "expr": ast.dump(st.value)[:120]})
        elif isinstance(st, ast.AnnAssign) and _is_bare_cast_on_env(st.value):
            out.append({"file": path, "line": st.lineno,
                        "name": getattr(st.target, "id", "?"),
                        "expr": ast.dump(st.value)[:120]})
        # if/try/with/for/while/class 的语句体在模块级就是导入期，继续往里找；函数体已在上面排除。
        for field in ("body", "orelse", "finalbody"):
            inner = getattr(st, field, None)
            if isinstance(inner, list) and depth < 6:
                _scan_body(inner, path, out, depth + 1)
        if isinstance(st, ast.Try) and depth < 6:
            for handler in st.handlers:
                _scan_body(handler.body, path, out, depth + 1)


def scan(path):
    src = io.open(path, encoding="utf-8", errors="replace").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    _scan_body(tree.body, path, out)
    return out


def scan_tree(root):
    rows = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "attic")]
        for fn in sorted(files):
            if fn.endswith(".py"):
                rows.extend(scan(os.path.join(dirpath, fn)))
    return rows


def problems_of(rows):
    return ["%s:%d 模块级裸 %s(env) —— 坏 env 会在导入期炸掉进程，改用 env_cfg.env_int/env_float :: %s"
            % (r["file"], r["line"], "int/float", r["name"]) for r in rows]


CLEAN = (
    "from .env_cfg import env_float, env_int\n"
    "\n"
    "CACHE_MAX = env_int(\"X_CACHE_MAX\", 2000, lo=1)\n"
    "SLOW_MS = env_float(\"X_SLOW_MS\", 5000, lo=1)\n"
    "\n"
    "def helper(raw):\n"
    "    return int(os.getenv(\"X_LATE\", \"1\"))  # 调用期转换：本门不管\n"
)

DIRTY = (
    "import os\n"
    "\n"
    "CACHE_MAX = int(os.getenv(\"X_CACHE_MAX\", \"2000\"))\n"
    "DAYS = float(os.environ.get(\"X_DAYS\", 3))\n"
    "\n"
    "class Cfg:\n"
    "    LIMIT = int(os.environ[\"X_LIMIT\"])\n"
)


def _write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _self_test():
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        clean = os.path.join(tmp, "clean.py")
        dirty = os.path.join(tmp, "dirty.py")
        _write(clean, CLEAN)
        _write(dirty, DIRTY)
        got_clean = scan(clean)
        if got_clean:
            print("SELFTEST_FALSE clean.py 里的 env_int/env_float 与函数内转换被误判：%s" % got_clean)
            ok = False
        else:
            print("SELFTEST_CLEAN clean.py 零判红（收口助手 + 调用期转换都不算导入期）")
        got_dirty = scan(dirty)
        names = sorted(r["name"] for r in got_dirty)
        if names != ["CACHE_MAX", "DAYS", "LIMIT"]:
            print("SELFTEST_MISS dirty.py 期望抓到 CACHE_MAX/DAYS/LIMIT，实际 %s" % names)
            ok = False
        else:
            print("SELFTEST_HIT dirty.py 抓到 3 处（模块级 2 + class 体 1）")
        if len(problems_of(got_dirty)) != 3:
            print("SELFTEST_MISS problems_of 与读数不同步")
            ok = False
    return 0 if ok else 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=SRC_DEFAULT)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    if not os.path.isdir(args.root):
        print("PROBLEM=1 目录不存在：%s" % args.root)
        return 1
    rows = scan_tree(args.root)
    probs = problems_of(rows)
    print("SITES=%d PROBLEM=%d" % (len(rows), len(probs)))
    for p in probs:
        print("PROBLEM " + p)
    return 0 if not probs else 1


if __name__ == "__main__":
    sys.exit(main())
