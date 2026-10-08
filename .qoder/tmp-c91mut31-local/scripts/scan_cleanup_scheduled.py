#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「清理族函数的调用点必须落在周期形状里」的静态量具
（第十九轮 MA-35 建议门禁，报告 :153-154 原句）。

审计员原话：**「`check_cleanup_scheduled.py`：断言所有 `purge_*` / `prune_*` / `expire_*` 函数，
其调用点中至少有一个位于 `while True` 循环内（或显式豁免登记）。」**

MA-35 的真实形状：`_run_retention_cleanup` 原来一轮跑完就 return，唯一调用点在 `startup()` ⇒
声明的 90 天保留期只在开机那一刻生效一次；长跑不重启的容器里 events 跨度远大于 90 天。
所以这门盯的不是"有没有清理函数"，而是**"清理有没有被反复排期"**。

命名偏离登记：审计员给的是 `check_*` 前缀，本仓量具族实际是 `scan_*`（`scripts/` 现读
4 个 `check_*.py` + 8 个 `scan_*.py`；第二十轮 :322 说"你们已经有 15 个 `check_*.py`"是错的）。
新量具随仓规，不随报告里的名字，并把这一条偏离如实登记。

口径（三档算已排期，两档判红）：
  DIRECT_LOOP —— 引用出现在某函数体的 `while` 内（最直白）。本仓常见形状是
                 `await asyncio.to_thread(self.store.purge_old, …)`：**目标名以 Attribute
                 出现（函数当参数递出去），不是 Call** —— 所以"调用点"必须同时认
                 Call 的被调名和作为实参出现的 Name/Attribute；只认 Call 会把整族读成"没人调用"。
  RESIDENT_TASK —— 引用是任务挂载调用的位置参数（`create_task` / `ensure_future` /
                 `task_registry.create` / `register` / `spawn`），且目标函数自己体内有 `while`
                 ⇒ 常驻任务（MA-35 的修法正是 `task_registry.create(self._run_retention_cleanup(), …)`）。
                 `to_thread` 故意不算挂载：它只回答"这次把同步函数搬到线程上跑"，
                 搬不搬与周期无关；当成挂载会把 `purge_old` 内部分批 DELETE 的 `while True`
                 误读成"常驻任务"。
  VIA_CALLER —— 引用所在的那个函数**自己**在循环里被调（或被挂载成常驻任务）⇒ 算排期，并可多层传递。
                 形状如 `store.purge_rule_triggers` ← `device_feed.purge_trigger_history`
                 （后者在 `_periodic_device_feed` 的 while 里被 `to_thread` 调）。
                 真实仓里还有两层的链条：`store.expire_overdue_agent_memories`
                 ← `AgentMemoryService.sweep_promote_candidates` ← `sweep_and_reconcile`
                 ← `while True`（runtime.py 的常驻 sweep 任务）—— 单跳判据在这一条上会**假红**，
                 所以按不动点最多传到第 5 层。
                 实现口径：排期集合的种子是"在 while 内被调 / 被挂载"的**被调名**；
                 若某引用点的所在函数名已在集合里，则该引用点的目标名并入集合。
                 已知取舍：链条按函数的**末段名字**匹配，不同类里的同名方法会被一并算进
                 （假绿风险）——因此 --verbose 把链条（层数 + 证据文件行号 + 所在函数）逐条打印，
                 人工可审；不做全限定名解链，因为跨文件按名字解调用链本身就更不可靠
                 （[[静态量具的盲区]] 那一族：这里选"能审的近似"而不是"审不动的精确"）。
  跨函数边界不继承循环身份：`while` 里定义的嵌套函数体内部按"循环外"计，
                 否则一个 `while: def helper(): purge_x()` 会把未排期的调用洗白。
  判红一 NOT_SCHEDULED —— 有引用但三档都不成立（"只在 startup 调一次"的形状，就是 MA-35）。
  判红二 NO_CALLSITE —— 本扫描根内一个引用都没有（写了清理函数却没人排期）。
  豁免 = `EXEMPT` 在册并写明依据；失效豁免（在册却不再是清理族成员）反向判红，语义同"基线只准减"。
  边界 = 只看 AST 节点，不匹配注释/文档串里的名字（文案锚点会罩住 docstring）。

用法：
    python scripts/scan_cleanup_scheduled.py                 # 默认扫 src/memory_agent
    python scripts/scan_cleanup_scheduled.py --self-test
    python scripts/scan_cleanup_scheduled.py --verbose       # 逐条打印每支的档位与落点
退出码：0 干净 / 1 有判红 / 2 量具自检失败
"""
import argparse
import ast
import io
import os

TARGET_PREFIXES = ("purge_", "prune_", "expire_", "_run_retention_")
#: 只有这些调用名把参数当"常驻任务"挂载；`to_thread` 不在列（理由见模块 docstring）。
MOUNT_NAMES = ("create_task", "ensure_future", "create", "register", "spawn")
RED_RANKS = ("NOT_SCHEDULED", "NO_CALLSITE")


def _callee_name(func):
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_target(name):
    return bool(name) and any(name.startswith(p) for p in TARGET_PREFIXES)


class _Collector(ast.NodeVisitor):
    """一次 DFS 同时收集：清理族定义、每个引用点的（所在函数、是否在 while 内、是否挂载）。"""

    def __init__(self, rel):
        self.rel = rel
        self.defs = []        # [(name, file, line, qual, has_own_while)]
        self.refs = []        # 清理族引用点 [{"name","file","line","qual","in_loop","mounted"}]
        self.all_refs = []    # 一切被调名（供 VIA_CALLER 判"调用方自己在循环里被调"）
        self._stack = []      # 限定名栈（ClassDef 也进栈，但不打断循环计数）
        self._whiles = [0]    # 每层函数的 while 计数；栈底那格是模块层（嵌套函数重置为 0）
        self._parents = {}

    def _qual(self):
        return ".".join(self._stack) or "<module>"

    # ── 定义面 ────────────────────────────────────────────────────────
    def _visit_scoped(self, node, is_func):
        self._stack.append(node.name)
        saved = self._whiles
        self._whiles = [0] if is_func else saved
        if is_func:
            # 记录这一支自己的 while 数量（决定 RESIDENT_TASK 的"自带循环"）
            own = any(isinstance(sub, ast.While)
                      for sub in _walk_excluding_nested_funcs(node))
            if _is_target(node.name):
                self.defs.append((node.name, self.rel, node.lineno, self._qual(), own))
        for child in ast.iter_child_nodes(node):
            self.visit(child)
        self._whiles = saved
        self._stack.pop()

    def visit_FunctionDef(self, node):
        self._visit_scoped(node, is_func=True)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._visit_scoped(node, is_func=False)

    def visit_While(self, node):
        self._whiles[-1] += 1
        for child in ast.iter_child_nodes(node):
            self.visit(child)
        self._whiles[-1] -= 1

    def _note(self, name, node, mounted=None):
        """所有被调名都进 all_refs；清理族再进 refs。"""
        if mounted is None:
            parent = self._parents.get(node)
            mounted = False
            if isinstance(parent, ast.Call) and _callee_name(parent.func) in MOUNT_NAMES:
                mounted = node in parent.args
        rec = {"name": name, "file": self.rel, "line": node.lineno,
               "qual": self._qual(), "in_loop": self._whiles[-1] > 0, "mounted": mounted}
        self.all_refs.append(rec)
        if _is_target(name):
            self.refs.append(rec)
        return rec

    # ── 引用面 ────────────────────────────────────────────────────────
    def visit_Call(self, node):
        name = _callee_name(node.func)
        if name:
            parent = self._parents.get(node)
            mounted = (isinstance(parent, ast.Call)
                       and _callee_name(parent.func) in MOUNT_NAMES
                       and node in parent.args)
            self._note(name, node, mounted=mounted)
        for child in ast.iter_child_nodes(node):
            self.visit(child)

    def visit_Attribute(self, node):
        # 实参位置的引用：`to_thread(self.store.purge_old)` 里 `purge_old` 是 Attribute
        # 而不是 Call.func —— 只认 Call 会把本仓最常见的一族读成"没人调用"。
        # 收**全部**名字（不只清理族）：VIA_CALLER 的链条要认得出
        # `to_thread(self.agent_memory.sweep_and_reconcile)` 这类非清理名的中间跳，
        # 否则 `expire_overdue_agent_memories` 会被判成未排期（真假红，本机现读复现过）。
        # 普通属性读（`self.store`、`config.data_retention_days`）不进 all_refs，免得链条噪声过大。
        if not self._is_call_func(node) and self._is_call_arg(node):
            self._note(node.attr, node)
        for child in ast.iter_child_nodes(node):
            self.visit(child)

    def visit_Name(self, node):
        if not self._is_call_func(node) and self._is_call_arg(node):
            self._note(node.id, node)

    def _is_call_func(self, node):
        parent = self._parents.get(node)
        return isinstance(parent, ast.Call) and parent.func is node

    def _is_call_arg(self, node):
        parent = self._parents.get(node)
        return isinstance(parent, ast.Call) and node in parent.args


def _walk_excluding_nested_funcs(func_node):
    stack = list(ast.iter_child_nodes(func_node))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _parents_of(tree):
    out = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def scan(root):
    defs, refs, all_refs = [], [], []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            rel = path.replace(os.sep, "/")
            try:
                tree = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())
            except SyntaxError:
                continue
            col = _Collector(rel)
            col._parents = _parents_of(tree)
            # 从每个顶层语句进入：函数/类由覆写的 visit_* 处理，其余语句由 generic_visit 下钻，
            # 所以模块级引用（所在函数记为 <module>）在第一趟就已经收全，不再补第二趟。
            for top in ast.iter_child_nodes(tree):
                col.visit(top)
            defs.extend(col.defs)
            refs.extend(col.refs)
            all_refs.extend(col.all_refs)
    return defs, refs, all_refs


def classify(defs, refs, all_refs):
    """返回 ({name: (rank, detail)}, targets, by_name)。"""
    targets = {}
    for name, f, ln, qual, own in defs:
        targets.setdefault(name, []).append((f, ln, qual, own))
    by_name = {}
    for r in refs:
        by_name.setdefault(r["name"], []).append(r)

    def plain(qual):
        return qual.split(".")[-1]

    # 种子：直接被"在 while 内调用"或"挂载成常驻任务"的被调名
    sched = {}          # 被调名 -> (层数, 证据字符串)
    for r in all_refs:
        if r["in_loop"] or r["mounted"]:
            sched.setdefault(r["name"], (1, "%s:%d 在%s内被调（所在函数 %s）"
                                         % (r["file"], r["line"],
                                            "循环" if r["in_loop"] else "常驻挂载",
                                            r["qual"])))
    # 不动点：某个引用点的**所在函数**自己已排期 ⇒ 该引用点指向的名字也算排期（可多层）
    for depth in range(2, 6):
        changed = False
        for r in all_refs:
            if r["name"] in sched:
                continue
            owner = plain(r["qual"])
            if owner in sched and owner != r["name"]:
                sched[r["name"]] = (depth, "%s:%d 的调用方 %s 第 %d 层已排期"
                                     % (r["file"], r["line"], r["qual"], depth - 1))
                changed = True
        if not changed:
            break

    ranks = {}
    for name in targets:
        sites = by_name.get(name, [])
        if not sites:
            ranks[name] = ("NO_CALLSITE", "本扫描根内一个引用都没有")
            continue
        loop_sites = [s for s in sites if s["in_loop"]]
        if loop_sites:
            s = loop_sites[0]
            ranks[name] = ("DIRECT_LOOP", "%d 个引用点，首个在 while 内：%s:%d(%s)"
                           % (len(sites), s["file"], s["line"], s["qual"]))
            continue
        own_loop = any(d[3] for d in targets[name])
        if own_loop and any(s["mounted"] for s in sites):
            m = [s for s in sites if s["mounted"]][0]
            ranks[name] = ("RESIDENT_TASK", "挂载为常驻任务（%s:%d）且自带 while"
                           % (m["file"], m["line"]))
            continue
        if name in sched:
            depth, why = sched[name]
            ranks[name] = ("VIA_CALLER", "链条层数=%d :: %s" % (depth, why))
            continue
        ranks[name] = ("NOT_SCHEDULED", "%d 个引用点全在循环外，首个=%s:%d(%s)"
                       % (len(sites), sites[0]["file"], sites[0]["line"], sites[0]["qual"]))
    return ranks, targets, by_name


EXEMPT = {
    # "函数名": "为什么这支不需要周期排期（写依据，不写空话）"
}


def problems_of(ranks, targets, by_name):
    probs = []
    for name in sorted(ranks):
        rank, detail = ranks[name]
        if name in EXEMPT or rank not in RED_RANKS:
            continue
        f, ln, qual, _own = targets[name][0]
        where = ""
        if rank == "NOT_SCHEDULED":
            s = by_name[name][0]
            where = " 调用点=%s:%d(%s)" % (s["file"], s["line"], s["qual"])
        probs.append("CLEANUP_%s %s 定义=%s:%d(%s)%s :: %s"
                     % ("NOT_SCHEDULED" if rank == "NOT_SCHEDULED" else "NO_CALLSITE",
                        name, f, ln, qual, where, detail))
    return probs


def run_scan(root):
    return classify(*scan(root))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="*", default=["src/memory_agent"])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    ranks, targets, by_name = {}, {}, {}
    for root in args.root:
        r, t, b = run_scan(root)
        ranks.update(r)
        targets.update(t)
        by_name.update(b)
    probs = problems_of(ranks, targets, by_name)
    stale = sorted(k for k in EXEMPT if k not in ranks)
    cnt = {}
    for rank in ranks.values():
        cnt[rank[0]] = cnt.get(rank[0], 0) + 1
    print("ROOT=%s TARGETS=%d DIRECT_LOOP=%d RESIDENT_TASK=%d VIA_CALLER=%d NOT_SCHEDULED=%d NO_CALLSITE=%d PROBLEM=%d EXEMPT_STALE=%d"
          % (",".join(args.root), len(ranks), cnt.get("DIRECT_LOOP", 0),
             cnt.get("RESIDENT_TASK", 0), cnt.get("VIA_CALLER", 0),
             cnt.get("NOT_SCHEDULED", 0), cnt.get("NO_CALLSITE", 0), len(probs), len(stale)))
    if args.verbose:
        for name in sorted(ranks):
            rank, detail = ranks[name]
            f, ln, qual, own = targets[name][0]
            print("  %-14s %-34s %s:%d self_while=%s refs=%d :: %s"
                  % (rank, name, f, ln, own, len(by_name.get(name, [])), detail))
    for k in stale:
        print("EXEMPT_STALE %s :: %s" % (k, EXEMPT[k]))
    for p in probs:
        print(p)
    if probs or stale:
        print("SCAN_RC=1 判红 %d 条 / 失效豁免 %d 条" % (len(probs), len(stale)))
        return 1
    print("SCAN_RC=0 清理族都落在周期形状内（或有在册豁免）")
    return 0


SEED_RED = {
    "p1_startup_only.py": (
        "class Store:\n"
        "    def purge_stale(self):\n"
        "        return delete_rows()\n\n\n"
        "class RT:\n"
        "    def startup(self):\n"
        "        self.store = Store()\n"
        "        self.store.purge_stale()\n"
    ),
    "p2_never_called.py": (
        "class Store:\n"
        "    def prune_orphans(self):\n"
        "        return 0\n"
    ),
}
SEED_GREEN = {
    # 直白形状：调用点在 while 内（且目标名以 Attribute 形式递给 to_thread —— 本仓最常见）
    "n1_call_in_loop.py": (
        "import asyncio\n\n\n"
        "class Store:\n"
        "    def prune_old(self):\n"
        "        return 0\n\n\n"
        "async def cycle(store):\n"
        "    while True:\n"
        "        await asyncio.to_thread(store.prune_old)\n"
        "        await asyncio.sleep(1)\n"
    ),
    # MA-35 的修法：任务挂载 + 目标自带 while
    "n2_resident_task.py": (
        "import asyncio\n\n\n"
        "class Store:\n"
        "    def purge_events(self):\n"
        "        return 0\n\n\n"
        "class RT:\n"
        "    async def _run_retention_cleanup(self):\n"
        "        while True:\n"
        "            await asyncio.sleep(1)\n"
        "            await asyncio.to_thread(self.store.purge_events)\n\n\n"
        "def startup(rt, task_registry):\n"
        "    rt._retention_task = task_registry.create(rt._run_retention_cleanup(), name='ret')\n"
    ),
    # 传递一层：外层函数在循环里被调 ⇒ 它内部的清理调用也算排期
    "n3_via_caller.py": (
        "import asyncio\n\n\n"
        "class Store:\n"
        "    def purge_rule_history(self, cutoff):\n"
        "        return 0\n\n\n"
        "class Feed:\n"
        "    def trim(self):\n"
        "        return self.store.purge_rule_history('2026-01-01')\n\n\n"
        "async def device_cycle(feed):\n"
        "    while True:\n"
        "        feed.trim()\n"
        "        await asyncio.sleep(1)\n"
    ),
    # 两层链条（本仓真实形状：expire ← sweep_promote_candidates ← sweep_and_reconcile ← while）
    # 单跳判据在这一条上会假红，所以它必须锁在负例里。
    "n5_two_hops.py": (
        "import asyncio\n\n\n"
        "class Store:\n"
        "    def purge_deep(self):\n"
        "        return 0\n\n\n"
        "class Svc:\n"
        "    def inner(self):\n"
        "        return self.store.purge_deep()\n\n"
        "    def outer(self):\n"
        "        return self.inner()\n\n\n"
        "async def cycle(svc):\n"
        "    while True:\n"
        "        await asyncio.to_thread(svc.outer)\n"
        "        await asyncio.sleep(1)\n"
    ),
    # 不属于清理族的名字 ⇒ 连候选都不该进
    "n4_not_cleanup_name.py": (
        "class Store:\n"
        "    def fetch_old(self):\n"
        "        return 0\n\n\n"
        "def startup(store):\n"
        "    store.fetch_old()\n"
    ),
    # 反例（必须判红）：while 里定义的嵌套函数体内部调用 ⇒ 跨函数边界不继承循环身份
    "p3_nested_def_in_loop.py": (
        "import asyncio\n\n\n"
        "class Store:\n"
        "    def purge_nested(self):\n"
        "        return 0\n\n\n"
        "async def cycle(store):\n"
        "    while True:\n"
        "        def helper():\n"
        "            store.purge_nested()\n"
        "        await asyncio.sleep(1)\n"
    ),
}


def _self_test():
    import tempfile
    ok = True
    want_red = {"purge_stale": "p1 只在 startup 调一次", "prune_orphans": "p2 一个引用都没有",
                "purge_nested": "p3 循环里定义的嵌套函数体内部调用"}
    want_green = {"prune_old": "n1 循环内 to_thread 传引用",
                  "_run_retention_cleanup": "n2 任务挂载 + 自带 while",
                  "purge_events": "n2 常驻循环体内的清理调用",
                  "purge_rule_history": "n3 调用方已在循环里被调",
                  "purge_deep": "n5 两层链条（expire 那一族的形状）"}
    with tempfile.TemporaryDirectory() as tmp:
        for name, body in list(SEED_RED.items()) + list(SEED_GREEN.items()):
            with io.open(os.path.join(tmp, name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
        ranks, targets, by_name = run_scan(tmp)
        probs = problems_of(ranks, targets, by_name)
        red_names = {p.split(" ", 1)[1].split(" ")[0] for p in probs}
        for fname, label in want_red.items():
            if fname not in ranks:
                print("SELFTEST_MISS %s（%s）没进候选表" % (fname, label))
                ok = False
            elif ranks[fname][0] in RED_RANKS and fname in red_names:
                print("SELFTEST_HIT %s（%s）档位=%s ⇒ 判红" % (fname, label, ranks[fname][0]))
            else:
                print("SELFTEST_MISS %s（%s）应判红却没进判红：档位=%s %s"
                      % (fname, label, ranks[fname][0], ranks[fname][1]))
                ok = False
        for fname, label in want_green.items():
            if fname not in ranks:
                print("SELFTEST_MISS %s（%s）没进候选表" % (fname, label))
                ok = False
            elif ranks[fname][0] in RED_RANKS:
                print("SELFTEST_FALSE %s（%s）被判成未排期：档位=%s %s"
                      % (fname, label, ranks[fname][0], ranks[fname][1]))
                ok = False
            else:
                print("SELFTEST_CLEAN %s（%s）档位=%s" % (fname, label, ranks[fname][0]))
        if "fetch_old" in ranks:
            print("SELFTEST_FALSE fetch_old 不属清理族却进了候选表")
            ok = False
        else:
            print("SELFTEST_CLEAN fetch_old 名字不在清理族前缀里 ⇒ 不进候选")
        # 链条必须真的传到第二层之外：n5 里 purge_deep 距循环有两跳
        deep = ranks.get("purge_deep", ("-", "-"))
        if "层数=3" not in deep[1]:
            print("SELFTEST_FALSE purge_deep 链条层数不是 3（实际=%s %s）" % (deep[0], deep[1]))
            ok = False
        else:
            print("SELFTEST_CLEAN purge_deep 链条层数=3 ⇒ 多跳传递有效")
        if red_names != set(want_red):
            print("SELFTEST_FALSE 判红集合=%s 期望=%s" % (sorted(red_names), sorted(want_red)))
            ok = False
        else:
            print("SELFTEST_CLEAN 判红恰好来自三条种子（PROBLEM=%d）" % len(probs))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
