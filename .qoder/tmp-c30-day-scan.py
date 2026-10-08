"""一次性摸底：全仓 timedelta(days=...) 的上界防护分档（bounded / lo_only / unguarded）。"""
import ast
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "memory_agent")


def has_call(node, name):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Name) and f.id == name:
                return True
    return False


rows = []
for dirpath, _dirs, files in os.walk(ROOT):
    if "__pycache__" in dirpath:
        continue
    for fn in sorted(files):
        if not fn.endswith(".py"):
            continue
        p = os.path.join(dirpath, fn)
        try:
            tree = ast.parse(open(p, encoding="utf-8").read())
        except Exception as exc:
            print(f"PARSE_FAIL {p}: {exc}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            is_td = (isinstance(f, ast.Name) and f.id == "timedelta") or \
                    (isinstance(f, ast.Attribute) and f.attr == "timedelta")
            if not is_td:
                continue
            for kw in node.keywords:
                if kw.arg != "days":
                    continue
                expr = ast.unparse(kw.value)
                if "seconds" in expr:
                    continue
                bucket = ("bounded" if has_call(kw.value, "min")
                          else "lo_only" if has_call(kw.value, "max") else "unguarded")
                rows.append((bucket, os.path.relpath(p, ROOT).replace("\\", "/"), node.lineno, expr))

from collections import Counter
print("TOTAL=" + str(len(rows)) + " " + " ".join(f"{k}={v}" for k, v in sorted(Counter(b for b, *_ in rows).items())))
for b, f, ln, e in sorted(rows):
    print(f"{b:10s} {f}:{ln}  timedelta(days={e[:70]})")
