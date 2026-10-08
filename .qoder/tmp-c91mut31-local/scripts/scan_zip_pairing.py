#!/usr/bin/env python3
"""zip 配对口径扫描（A2/A7 P2-1）：每个 `zip()` 必须落在三类之一，否则判红。

审计 A2 §五 / A7 §六 把「19 处 `zip()` 无 `strict`」列为**静态结论、未做运行时验证**。
本脚本把它变成可复核的门，并挡住两个相反方向的错：

| 分类 | 判据 | 结论 |
|---|---|---|
| `strict` | 带 `strict=True` | 放行——长度不等 = 判红 |
| `window` | 形如 `zip(x, x[1:])`（第二个参数是第一个的 `[1:]` 切片） | 放行——**长度天生不等 1，加了 strict 每次必崩** |
| `marked` | 调用所在行（含跨行）有 `zip-pair-ok` 标记 | 放行——人工确认过「比到短的尽头即止」是设计 |
| `unclassified` | 以上都不是 | **判红（RC=1）**：静默截断 = 读数形状合法、样本被削 |
| `window/marked + strict` | 滑窗或标记位却带 strict | **判红**：那是把设计改崩 |

为什么不"全量加 strict"：`zip(silence_events, silence_events[1:])` 这类相邻比对
（本仓 5 处）加 strict 会每次都抛 ValueError——审计原文「19 处无 strict」如果照字面
一刀切，修一次就引入 5 个必崩点。所以判据是**逐站点分诊**，不是 `strict` 的个数。

用法：
    python scripts/scan_zip_pairing.py [--root src/memory_agent] [--self-test]
退出码：0=全部分类完毕；1=有未分类/矛盾站点；2=脚本自检不通过（读数作废）。
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import sys
import tempfile

MARKER = "zip-pair-ok"
WINDOW_LABEL = "window"
STRICT_LABEL = "strict"
MARKED_LABEL = "marked"
BARE_LABEL = "unclassified"


def _is_shifted_tail(node, base_name: str) -> bool:
    """`x[1:]`：对同名变量的、下界为 1、无上界无步长的切片。"""
    if not isinstance(node, ast.Subscript):
        return False
    if not (isinstance(node.value, ast.Name) and node.value.id == base_name):
        return False
    sl = node.slice
    if isinstance(sl, ast.Slice):
        return (isinstance(sl.lower, ast.Constant) and sl.lower.value == 1
                and sl.upper is None and sl.step is None)
    return False


def _has_strict(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "strict":
            return isinstance(kw.value, ast.Constant) and kw.value.value is True
    return False


def _is_window(call: ast.Call) -> bool:
    """任一参数是另一同名参数的 `[1:]` ⇒ 相邻两两比对（滑窗）。"""
    for a in call.args:
        if not isinstance(a, ast.Name):
            continue
        for b in call.args:
            if b is not a and _is_shifted_tail(b, a.id):
                return True
    return False


def _markers_in_range(lines: list[str], call: ast.Call) -> bool:
    """标记可以打在调用行（行尾注释）或紧邻上一行，两种写法都认。"""
    lo = getattr(call, "lineno", 1)
    hi = getattr(call, "end_lineno", lo) or lo
    start = max(1, lo - 1)
    return any(MARKER in lines[i - 1] for i in range(start, min(hi, len(lines)) + 1))


def classify_call(call: ast.Call, lines: list[str]) -> str:
    strict, window, marked = _has_strict(call), _is_window(call), _markers_in_range(lines, call)
    if (window or marked) and strict:
        # 设计成不等长的位置却加了 strict：这不是修，这是引入崩溃
        return f"CONFLICT:{'window' if window else 'marked'}+strict"
    if window:
        return WINDOW_LABEL
    if marked:
        return MARKED_LABEL
    if strict:
        return STRICT_LABEL
    return BARE_LABEL


def scan_file(path: str) -> tuple[dict, list]:
    with io.open(path, encoding="utf-8", newline="") as fh:
        src = fh.read()
    lines = src.splitlines()
    counts: dict[str, int] = {}
    problems: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return {}, [f"{path}: 解析失败 {exc}"]
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "zip"):
            continue
        label = classify_call(node, lines)
        counts[label] = counts.get(label, 0) + 1
        if label.startswith(BARE_LABEL) or label.startswith("CONFLICT"):
            problems.append(f"{path}:{node.lineno}: zip {label}")
    return counts, problems


def scan_root(root: str) -> tuple[dict, list]:
    counts: dict[str, int] = {}
    problems: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            c, p = scan_file(os.path.join(dirpath, fn))
            for k, v in c.items():
                counts[k] = counts.get(k, 0) + v
            problems.extend(p)
    return counts, problems


# ── 自检：正例（该响）与反例（该不响）各一组，脚本自己不成立就不许出门 ──────────
POSITIVE = {
    # 裸 zip：长度不等会静默截短 ⇒ 必须被抓
    "p1_bare.py": "def f(a, b):\n    return list(zip(a, b))\n",
    # 滑窗加了 strict ⇒ 每次必崩，必须被抓
    "p2_window_strict.py": "def f(a):\n    return list(zip(a, a[1:], strict=True))\n",
    # 标记位加了 strict ⇒ 同样必崩
    "p3_marked_strict.py":
        f"def f(a, b):\n    # {MARKER}: 变长是设计\n    return list(zip(a, b, strict=True))\n",
    # 多参数裸 zip
    "p4_bare3.py": "def f(a, b, c):\n    return list(zip(a, b, c))\n",
}
NEGATIVE = {
    "n1_strict.py": "def f(a, b):\n    return list(zip(a, b, strict=True))\n",
    "n2_window.py": "def f(a):\n    return list(zip(a, a[1:]))\n",
    "n3_window_named.py": "def f(events):\n    return list(zip(events, events[1:]))\n",
    "n4_marked.py": f"def f(a, b):\n    # {MARKER}: 两串前缀比对\n    return list(zip(a, b))\n",
    "n5_marked_inline.py":
        f"def f(a, b):\n    return list(zip(a, b))  # {MARKER}: 短的尽头即止\n",
}


def self_test() -> int:
    pos_hits = 0
    with tempfile.TemporaryDirectory(prefix="zipscan_pos_") as d:
        for name, src in POSITIVE.items():
            with io.open(os.path.join(d, name), "w", encoding="utf-8", newline="") as fh:
                fh.write(src)
        _, problems = scan_root(d)
        pos_hits = len(problems)
    neg_flags = []
    with tempfile.TemporaryDirectory(prefix="zipscan_neg_") as d:
        for name, src in NEGATIVE.items():
            with io.open(os.path.join(d, name), "w", encoding="utf-8", newline="") as fh:
                fh.write(src)
        counts, neg_flags = scan_root(d)
    print(f"SELFTEST pos_expected={len(POSITIVE)} pos_hit={pos_hits} "
          f"neg_files={len(NEGATIVE)} neg_flagged={len(neg_flags)} "
          f"neg_counts={counts}")
    if pos_hits < len(POSITIVE):
        print("SELFTEST_FAIL: 正例没被抓全（量具会漏放静默截断）")
        return 2
    if neg_flags:
        print("SELFTEST_FAIL: 反例被判红（量具会把正确写法也毙掉）")
        return 2
    print("SELFTEST_OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="src/memory_agent")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    rc = self_test()
    if rc:
        return rc
    if args.self_test:
        return 0

    if not os.path.isdir(args.root):
        print(f"ROOT_MISSING {args.root}")
        return 2
    counts, problems = scan_root(args.root)
    total = sum(counts.values())
    print(f"ROOT={args.root} total_zip={total} " +
          " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for p in problems:
        print("UNCLASSIFIED " + p)
    if problems:
        print(f"SCAN_RC=1 未分类/矛盾站点={len(problems)}")
        return 1
    print("SCAN_RC=0 每个 zip 站点都有归属")
    return 0


if __name__ == "__main__":
    sys.exit(main())
