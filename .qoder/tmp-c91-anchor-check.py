"""静态锚点预检：把四份变异 harness 的 LEGS 锚点逐条在**被测树**上数一遍命中次数。

为什么要有这一格：run31 因为 `src/memory_agent/app.py` 被改过，旧三档（c81/c85/c82）的
变异腿不再能跳过；而腿一旦锚点漂移，harness 只会报 INVALID（既不算杀也不算活），
整档读数就废在这里。这一格在出网前先把四份 harness 的全部腿锚点当场数一遍：
文件键取自 harness 自己的 `FILES` 表（不猜文件名），命中 != 1 一律算 BAD，
`ANCHOR_BAD=0 且 ANCHOR_MISSING=0` 才允许发容器。

用法：`python tmp-c91-anchor-check.py <被测树根>`（根 = 工作树或快照树，只读）。
"""
import ast
import io
import os
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else "."
#: 容器里没有 `.qoder/`（快照清单只含 src/tests/scripts/…），所以 harness 路径由参数给全；
#: 本机默认走 `.qoder/`。两种口径都当场数，不靠"我记得这些锚点还在"。
HARNESS_FILES = sys.argv[2:] or [
    os.path.join(".qoder", "tmp-c91-mut31.py"),
    os.path.join(".qoder", "tmp-c81-mut.py"),
    os.path.join(".qoder", "tmp-c85-mut.py"),
    os.path.join(".qoder", "tmp-c82-mut.py"),
]


def tables(path):
    """模块级常量表：FILES / LEGS 的值都是 `os.path.join(...)` 或常量拼接，
    `ast.literal_eval` 读不出来（实测：四份 harness 全读成 0 条腿 ⇒ 假绿），
    所以逐条在 {os + 已收集常量} 的命名空间里 eval 该赋值表达式；读不出的跳过。
    """
    tree = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())
    ns = {"os": os}
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            try:
                value = eval(compile(ast.Expression(node.value), path, "eval"), dict(ns))  # noqa: S307
            except Exception:  # noqa: BLE000 — 非常量赋值（如函数调用结果）不是锚点表
                continue
            ns[name] = value
            out[name] = value
    return out


def check(hp):
    t = tables(hp)
    files, legs = t.get("FILES") or {}, t.get("LEGS") or []
    hn = os.path.basename(hp)
    res = []
    for leg in legs:
        tag, key, anchor = leg[0], leg[1], leg[2]
        rel = files.get(key)
        if rel is None:
            res.append(("MISSING", tag, f"{hn} 文件键 {key} 不在 FILES 里", 0))
            continue
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            res.append(("MISSING", tag, f"{hn}::{tag} 树里没有 {rel}", 0))
            continue
        text = io.open(path, encoding="utf-8", newline="").read()
        eol = "\r\n" if "\r\n" in text else "\n"
        n = text.count(anchor.replace("\n", eol))
        res.append(("OK" if n == 1 else "BAD", tag, f"{hn}::{tag} {rel} 命中={n}", n))
    return res


def main():
    counts = {"OK": 0, "BAD": 0, "MISSING": 0}
    per_harness = []
    for hn in HARNESS_FILES:
        rows = check(hn)
        for state, tag, msg, _n in rows:
            counts[state] += 1
        per_harness.append((hn, len(rows), sum(1 for r in rows if r[0] == "OK")))
    for hn, total, okn in per_harness:
        print(f"ANCHOR_HARNESS {hn} legs={total} anchor_ok={okn}")
    print("ANCHOR_OK=%d ANCHOR_BAD=%d ANCHOR_MISSING=%d"
          % (counts["OK"], counts["BAD"], counts["MISSING"]))
    return 0 if counts["BAD"] == 0 and counts["MISSING"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
