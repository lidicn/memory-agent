#!/usr/bin/env python3
"""扫「协程里直调的同步慢函数」——审计 A3 §八「少量未卸载的同步 I/O」的可复现量具。

为什么需要它：A3 说"少量未卸载的同步调用在高并发下会造成 P99 劣化"却没给位置，
一句没有位置的结论既不能修也不能验收。本量具把这一格变成逐条 `文件:行号` 的读数。

口径（**只判这些**，不是"全库阻塞调用"的证明）：
- 只进 `async def` 的作用域，且只判**直接**待在协程体里的调用（嵌套同步函数是给
  `to_thread` 准备的料，不算直调）；
- 命中 = 调用名在 `DB_METHODS`（store 的同步查询/写）或 `MODULE_CALLS`（`time.sleep`、
  `requests.*`、`shutil.*` 这类明确阻塞的模块函数）里，且**不在** `to_thread` /
  `run_in_executor` 的实参范围内；
- `bcrypt.*`、`jwt.*`、账号文件读写这类不走 store 的调用不在表里——它们要靠人读命中所在的路由。
  更要紧的是：本量具只看**一层**调用，`/api/auth/login` 那种"路由 → 同步方法 → bcrypt"的
  **传递性阻塞**它看不见（A3 那格的真身恰好是传递性的），所以 HITS=0 ≠ 循环没被冻住。

量具自证（改前必红的那一半）：`--self-test` 拿两份内置样本跑一遍，
未卸载的正例必须被抓住、`to_thread` 包裹的反例必须不响；两者任一不成立就退出 2 且不出表。

用法：
    python scripts/scan_unloaded_async_io.py [路径…]      # 默认 src/memory_agent
    python scripts/scan_unloaded_async_io.py --self-test   # 量具自证
退出码：0=无命中，1=有命中（逐条打印），2=自证不通过或解析失败。
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys
import tempfile

DB_METHODS = {"db_query", "db_execute", "executescript", "query_rows",
              "execute", "commit"}
MODULE_CALLS = {
    "time": {"sleep"},
    "requests": {"get", "post", "put", "delete", "request", "head"},
    "shutil": {"copytree", "rmtree", "move"},
}
OFFLOAD_FUNCS = {"to_thread", "run_in_executor"}

POSITIVE_SAMPLE = '''
class A:
    async def bad(self):
        return self.store.db_query("SELECT 1")

    async def also_bad(self):
        time.sleep(1)
'''
NEGATIVE_SAMPLE = '''
class A:
    async def good(self):
        return await asyncio.to_thread(self.store.db_query, "SELECT 1")

    async def also_good(self):
        await loop.run_in_executor(None, self.store.db_query, "SELECT 1")

    async def nested_good(self):
        def _fetch():                      # 嵌套同步函数是"给线程用的料"，不算协程直调
            return self.store.db_query("SELECT 1")
        return await asyncio.to_thread(_fetch)
'''


def _func_name(node: ast.Call) -> tuple:
    """回 (root, attr)：`a.b.c(...)` ⇒ ("a", "c")；`c(...)` ⇒ ("c", "c")。"""
    f = node.func
    if isinstance(f, ast.Attribute):
        cur = f.value
        while isinstance(cur, ast.Attribute):
            cur = cur.value
        if isinstance(cur, ast.Name):
            return cur.id, f.attr
        if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Name):
            return cur.func.id, f.attr
        return "?", f.attr
    if isinstance(f, ast.Name):
        return f.id, f.id
    return "?", "?"


def _is_blocking(root: str, attr: str) -> bool:
    if attr in DB_METHODS:
        return True
    if root in MODULE_CALLS and attr in MODULE_CALLS[root]:
        return True
    return False


def _span_contains(outer: ast.AST, lineno: int) -> bool:
    return outer.lineno <= lineno <= (outer.end_lineno or outer.lineno)


def scan_file(path: pathlib.Path, lines: list) -> list:
    tree = ast.parse("\n".join(lines), filename=str(path))
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        offload_spans = [c for c in ast.walk(node)
                         if isinstance(c, ast.Call) and _func_name(c)[1] in OFFLOAD_FUNCS]
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            # 只判"直接待在协程体里"的调用：最近的函数层必须是本 async def。
            # 嵌套同步函数是给 to_thread 准备的料，算进去就是假阳性
            # （首跑就是把 `def _fetch()` 里的 conn.execute 当成了直调，HITS 虚报 3）。
            owner = call
            while isinstance(owner, ast.AST) and not isinstance(
                    owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = parents.get(owner)
            if owner is not node:
                continue
            root, attr = _func_name(call)
            if not _is_blocking(root, attr):
                continue
            if any(_span_contains(o, call.lineno) for o in offload_spans):
                continue
            hits.append("%s:%d %s" % (path, call.lineno, lines[call.lineno - 1].strip()))
    return hits


def scan_paths(paths: list) -> list:
    hits = []
    for base in paths:
        root = pathlib.Path(base)
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for p in files:
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
                hits += scan_file(p, lines)
            except (SyntaxError, UnicodeDecodeError, OSError) as exc:
                print("解析失败 %s: %s: %s" % (p, type(exc).__name__, exc))
                raise
    return hits


def self_test() -> int:
    """正例必须被抓住、反例必须不响，否则这把尺子本身是坏的。"""
    with tempfile.TemporaryDirectory() as td:
        pos = pathlib.Path(td) / "pos.py"
        neg = pathlib.Path(td) / "neg.py"
        pos.write_text(POSITIVE_SAMPLE, encoding="utf-8")
        neg.write_text(NEGATIVE_SAMPLE, encoding="utf-8")
        pos_hits = [h for h in scan_paths([str(pos)]) if h.startswith(str(pos))]
        neg_hits = [h for h in scan_paths([str(neg)]) if h.startswith(str(neg))]
    print("SELFTEST_POS_HITS=%d SELFTEST_NEG_HITS=%d" % (len(pos_hits), len(neg_hits)))
    for h in pos_hits:
        print("  命中 %s" % h.split(" ", 1)[1])
    if len(pos_hits) < 2 or neg_hits:
        print("量具自证不通过：未卸载的正例没抓全，或被卸载的反例仍在响。")
        return 2
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="扫协程内直调的同步慢函数")
    ap.add_argument("paths", nargs="*", default=["src/memory_agent"])
    ap.add_argument("--self-test", action="store_true", help="只跑量具自证")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    hits = scan_paths(args.paths)
    print("HITS=%d" % len(hits))
    for h in hits:
        print(h)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
