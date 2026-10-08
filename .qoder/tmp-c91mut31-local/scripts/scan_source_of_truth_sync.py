#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「声明真源/最新版本的同步函数，跳过条件里必须有版本比较」的静态量具
（第十七轮 MA-33 建议门禁，报告 :153-154 原句）。

审计员原话：**「断言声明"真源/最新版本"的同步函数，其跳过条件中必须出现版本比较
（或显式豁免登记）。」** 分野判据在 :170-177：**「真源应当被读取，而不是被复制。」**

MA-33 的真实形状：`seed_builtin_skills` 的 docstring 写着"真源=最新版本"，同步判据却只有
`os.path.isfile(dst)` ⇒ 旧部署升级后**永久停在旧版且无日志**。这一族的共同点是
**文案承诺一个更强的语义，代码只实现了一个更弱的存在性判断**——函数不是错的，是"不是它说的那件事"。

口径（**只判"跳过条件"，不判"存在性判断"**）：
  候选 = docstring（或 ToolSpec description）含「真源 / 单一真源 / 最新版本 / 权威 / source of truth」
  判据 = 该候选函数体内有一处**跳过式早退**：`ast.If` 的 test 里出现存在性调用
         （`isfile` / `exists` / `is_dir` / `lexists`），且该 if 的 body 里能走到
         `return` / `continue` / `break`（不下钻进嵌套定义——那里的 return 不是本函数的早退）
  判红 = 上述跳过守卫在册、且函数体内**没有任何**"版本比较"形状：
         比较节点（Compare）的任一操作数名字含 `version`（`prev_version`/`disk_version`/`bundled_version`…），
         或调用了名字含 `_parse_version` / `semver` / `packaging.version` 的东西
  豁免 = `EXEMPT` 在册并写明"为什么不需要版本比较"；失效豁免（在册却不命中）反向判红
  边界一 = docstring 里的关键词只用于**挑候选**，绝不用于判红（判红只看代码节点）。
         这是 [[文案锚点会罩住注释与 docstring]] 那一族：CODE 与 PROSE 两个口径必须分开。
  边界二 = **存在性判断有两种用途**，只有第一种是审计员要拦的形状：
         ① 当作跳过条件（"盘上有文件就早退"）⇒ 语义是"存在即最新"，才是 MA-33 的空话；
         ② 当作读盘前置（"文件在才去读它的版本号"）⇒ 这恰恰是修好之后的形状。
         第一版按"函数体内出现存在性调用"判，把 `mcp_server.py:2672 save_skill` 判成假红——
         它的 `os.path.isfile(path)` 在 :2713 是 MA-26 的**版本基数取盘上那份**（`max(prev, int(src["version"]))`），
         不是跳过条件。所以判据必须挂在控制流上（if-test + 早退），不能只挂在取数上。

用法：
    python scripts/scan_source_of_truth_sync.py                # 默认扫 src/memory_agent
    python scripts/scan_source_of_truth_sync.py --self-test
退出码：0 干净 / 1 有判红 / 2 量具自检失败
"""
import argparse
import ast
import io
import os
import re

TRUTH_WORDS = ("真源", "单一真源", "最新版本", "权威", "source of truth")
EXISTS_CALLS = ("isfile", "exists", "is_dir", "lexists")
VERSION_NAME = re.compile(r"version", re.I)
VERSION_HELPER = ("_parse_version", "parse_version", "semver", "packaging.version", "LooseVersion")


def _docstring(node):
    doc = ast.get_docstring(node) or ""
    # 只取声明段：ToolSpec 的 description 也算声明面，这里按 docstring 口径量
    return doc


def _exists_calls_raw(node):
    """信息栏用的粗口径：函数体里出现过存在性调用（不管在不在 `not` 里）。

    与 `_exists_calls_in` 分开是有意的：判据要挑控制流形状，信息栏只要回答"这函数碰过盘吗"。
    两者混成一个就会像第一版那样，把 `if not isfile: return` 的入参守卫也当成跳过守卫。
    """
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if name in EXISTS_CALLS:
                out.append(sub.lineno)
    return sorted(out)


def _exists_calls_in(node):
    """节点里出现的存在性调用行号（升序）。

    遇到 `not …` 就**不下钻**：`if not isfile(x): return` 走的是"文件不在 ⇒ 早退"，
    那是入参有效性守卫，不是"存在即最新"的空话形状。`ast.walk` 会绕开这层规则
    （它照样访问到 `not` 里面的 Call 节点），所以这里用显式栈自持遍历。
    """
    out = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, ast.UnaryOp) and isinstance(cur.op, ast.Not):
            continue
        if isinstance(cur, ast.Call):
            f = cur.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if name in EXISTS_CALLS:
                out.append(cur.lineno)
        stack.extend(ast.iter_child_nodes(cur))
    return sorted(out)


def _early_exit_in(body):
    """body 内能否走到 return/continue/break —— 不下钻进嵌套定义（那里的 return 不算本函数早退）。"""
    stack = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, (ast.Return, ast.Continue, ast.Break)):
            return True
        stack.extend(ast.iter_child_nodes(node))
    return False


def _skip_guards(node):
    """返回 [(if 的行号, 存在性调用行号)] —— 只有"文件在场 ⇒ 本函数早退"才登记为跳过守卫。"""
    guards = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.If):
            continue
        hits = _exists_calls_in(sub.test)
        if hits and _early_exit_in(sub.body):
            guards.append((sub.lineno, hits[0]))
    return guards


def _has_version_compare(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Compare):
            operands = [sub.left] + list(sub.comparators)
            for o in operands:
                if _mentions_version(o):
                    return True
        if isinstance(sub, ast.Call):
            f = sub.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if any(h in name for h in VERSION_HELPER):
                return True
    return False


def _mentions_version(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and VERSION_NAME.search(sub.id):
            return True
        if isinstance(sub, ast.Attribute) and VERSION_NAME.search(sub.attr):
            return True
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and VERSION_NAME.search(sub.value):
            return True
    return False


EXEMPT = {
    # ("文件名.py", "函数名"): "为什么这支不需要版本比较（写了依据，不写空话）"
}


def scan(root):
    rows = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                tree = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                doc = _docstring(node)
                hits = [w for w in TRUTH_WORDS if w in doc]
                if not hits:
                    continue
                guards = _skip_guards(node)
                rows.append({
                    "file": path, "name": node.name, "line": node.lineno,
                    "words": hits,
                    "guards": guards,
                    "exists_any": bool(_exists_calls_raw(node)),
                    "version_compare": _has_version_compare(node),
                    "key": (fn, node.name),
                })
    return rows


def problems_of(rows):
    probs = []
    for r in rows:
        if not r["guards"] or r["version_compare"] or r["key"] in EXEMPT:
            continue
        if_line, ex_line = r["guards"][0]
        probs.append("TRUTH_SKIP_WITHOUT_COMPARE %s:%d %s 跳过守卫=if:%d 存在性:%d 声明词=%s 无版本比较"
                     % (r["file"].replace(os.sep, "/"), r["line"], r["name"],
                        if_line, ex_line, "/".join(r["words"])))
    return probs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="*", default=["src/memory_agent"])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    rows = []
    for root in args.root:
        rows.extend(scan(root))
    probs = problems_of(rows)
    hit = {r["key"] for r in rows if r["guards"]}
    stale = [k for k in EXEMPT if k not in hit]
    print("ROOT=%s DECLARED=%d SKIP_GUARD=%d NEED_COMPARE=%d OK=%d PROBLEM=%d EXEMPT_STALE=%d"
          % (",".join(args.root), len(rows),
             len([r for r in rows if r["guards"]]),
             len([r for r in rows if r["guards"] and not r["version_compare"]]),
             len([r for r in rows if r["version_compare"]]), len(probs), len(stale)))
    if args.verbose:
        for r in rows:
            print("  %s %s:%d %s 声明=%s guard=%s exists_any=%s compare=%s"
                  % ("PROBLEM" if (r["guards"] and not r["version_compare"]) else "OK",
                     r["file"].replace(os.sep, "/"), r["line"], r["name"],
                     "/".join(r["words"]), r["guards"], r["exists_any"], r["version_compare"]))
    for k in stale:
        print("EXEMPT_STALE %s :: %s" % ("/".join(k), EXEMPT[k]))
    for p in probs:
        print(p)
    if probs or stale:
        print("SCAN_RC=1 判红 %d 条 / 失效豁免 %d 条" % (len(probs), len(stale)))
        return 1
    print("SCAN_RC=0 声明真源的同步函数都带版本比较（或有在册豁免）")
    return 0


POSITIVE = {
    "p1_truth_isfile_skip.py": (
        "import os\n\n\n"
        "def seed(a, b):\n"
        "    \"\"\"真源 = bundled 的最新版本，缺了才补。\"\"\"\n"
        "    if os.path.isfile(b):\n"
        "        return 0\n"
        "    return copy(a, b)\n"
    ),
    "p2_truth_isfile_continue.py": (
        "import os\n\n\n"
        "def seed_cycle(files, dst_dir):\n"
        "    \"\"\"把内置技能铺到 skills_dir（内置是权威源）。\"\"\"\n"
        "    for f in files:\n"
        "        dst = os.path.join(dst_dir, f)\n"
        "        if os.path.isfile(dst):\n"
        "            continue\n"
        "        write(dst, f)\n"
    ),
}
NEGATIVE = {
    "n1_truth_with_compare.py": (
        "import os\n\n\n"
        "def seed2(a, b):\n"
        "    \"\"\"真源 = 最新版本。\"\"\"\n"
        "    disk_version = read(b)\n"
        "    bundled_version = read(a)\n"
        "    if os.path.isfile(b) and disk_version >= bundled_version:\n"
        "        return 0\n"
        "    return copy(a, b)\n"
    ),
    "n2_no_truth_word.py": (
        "import os\n\n\n"
        "def plain(a, b):\n"
        "    \"\"\"把文件铺到目标目录。\"\"\"\n"
        "    if os.path.isfile(b):\n"
        "        return 0\n"
        "    return copy(a, b)\n"
    ),
    # MA-26 修好之后的真实形状：存在性判断是**读盘取版本基数**的前置，不是跳过条件。
    # 第一版量具把这条判成假红，所以它必须当负例锁住，防止口径再漂回去。
    "n3_exists_reads_version_base.py": (
        "import os\n\n\n"
        "def save_skill(name, content, path):\n"
        "    \"\"\"写回网关（唯一真源）：自增 version，供 Agent 拉取最新版本。\"\"\"\n"
        "    prev_version = 0\n"
        "    for src in (parse(content), read_meta(path) if os.path.isfile(path) else {}):\n"
        "        if src.get(\"version\"):\n"
        "            prev_version = max(prev_version, int(src[\"version\"]))\n"
        "    new_version = prev_version + 1\n"
        "    return write_skill(name, new_version)\n"
    ),
    # `if not isfile: return` 是入参有效性守卫（文件不在 ⇒ 没活干），不是"存在即最新"。
    "n4_not_isfile_guard.py": (
        "import os\n\n\n"
        "def refresh(path):\n"
        "    \"\"\"按盘上最新版本刷新缓存（权威 = 磁盘）。\"\"\"\n"
        "    if not os.path.isfile(path):\n"
        "        return None\n"
        "    return load(path)\n"
    ),
}


def _self_test():
    import tempfile
    ok = True
    want = {"seed": "p1 存在即 return", "seed_cycle": "p2 存在即 continue"}
    read_usage = {"save_skill": "n3 存在性=读盘取版本基数", "refresh": "n4 not isfile=入参守卫"}
    with tempfile.TemporaryDirectory() as tmp:
        for name, body in list(POSITIVE.items()) + list(NEGATIVE.items()):
            with io.open(os.path.join(tmp, name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
        rows = scan(tmp)
        st = {r["name"]: r for r in rows}
        probs = problems_of(rows)
        got = set()
        for p in probs:
            for fname in list(want) + list(read_usage) + ["seed2"]:
                if (" %s " % fname) in p:
                    got.add(fname)
        for fname, label in want.items():
            if fname not in st or not st[fname]["guards"] or st[fname]["version_compare"]:
                print("SELFTEST_MISS %s（%s）应有跳过守卫且无版本比较，却没抓到" % (fname, label))
                ok = False
            elif fname not in got:
                print("SELFTEST_MISS %s 有跳过守卫却没进判红" % fname)
                ok = False
            else:
                print("SELFTEST_HIT %s（%s）⇒ 判红" % (fname, label))
        for fname, label in read_usage.items():
            if fname not in st:
                print("SELFTEST_MISS %s 声明面有关键词却没进候选表（应当可查）" % fname)
                ok = False
            elif st[fname]["guards"] or not st[fname]["exists_any"]:
                print("SELFTEST_FALSE %s（%s）被当成跳过守卫 ⇒ 假红形状又回来了" % (fname, label))
                ok = False
            else:
                print("SELFTEST_CLEAN %s（%s）exists_any=True 而无跳过守卫 ⇒ 不判红" % (fname, label))
        if "seed2" not in st or not st["seed2"]["version_compare"]:
            print("SELFTEST_FALSE seed2 有版本比较却判红/没登记候选")
            ok = False
        else:
            print("SELFTEST_CLEAN seed2 版本比较在册")
        if "plain" in st:
            print("SELFTEST_FALSE plain 没声明真源却进了候选表")
            ok = False
        else:
            print("SELFTEST_CLEAN plain 声明面没有真源关键词 ⇒ 不进候选")
        if got != set(want):
            print("SELFTEST_FALSE 判红集合=%s 期望=%s" % (sorted(got), sorted(want)))
            ok = False
        else:
            print("SELFTEST_CLEAN 判红恰好来自两条正例（PROBLEM=%d）" % len(probs))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
