#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`timedelta(days=...)` 窗口的极值/负值防护分诊量具（审计第八轮 P3-2）。

为什么要分诊而不是"所有 days 都 clamp 一遍"：
全仓 99 处 `timedelta(days=...)` 里，绝大多数是 `days=1`、`days=7`、`now.weekday()`
这类**根本不受外部输入影响**的字面量或日期算式；把上界加到它们身上只是噪音，
而把上界一刀切加到保留期（`data_retention_days`）上会把合法的多年保留裁掉。
真正要防的是**值来自外部**的那些：MCP 工具形参、HTTP 查询参数，以及
"从问句里正则出来的天数"（`最近 999999 天`）——最后这类最容易漏，
因为它的变量名不叫 `days`。

归属（每个站点必须落到一类，否则 `unclassified` 判红）：
  external   —— 表达式的值可追溯到某个函数形参（含"形参→局部变量→days"的传递）
  config     —— 来自 `config.*` / `self.config.*` / `rt.config.*`，且键名**不在**
                `api/config_routes.py` 的 `WRITABLE_FIELDS` 里（运维侧配置，另册登记）。
                白名单里的键从 HTTP 可写 ⇒ 按 `external` 计，见下面 `WRITABLE_SOURCE`
  literal    —— 整数字面量（含负字面量）
  date_math  —— 由 `.weekday()` 一类日期算式导出（构造上有界 0..6）
  local      —— 其它局部派生值（读代码定档后写 `# day-ok:` 或改成上面几类）

防护（guard）：
  bounded    —— 表达式里有 `min(...)`，或调用了 clamp 类助手（`clamp_days(...)`）
  lo_only    —— 只有 `max(...)`，防负值不防极值
  unguarded  —— 两者皆无
`external` 必须 `bounded`，或在同行/上一行有 `# day-ok: 理由`。其余归属允许 unguarded。

**口径外（不是"没问题"，是"本量具不看"）**：只统计 `timedelta(days=…)`。
`timedelta(hours=…)` / `seconds=…` 同族形状未在册，已知两例：
`learning_api.run_evaluation_cycle` 的 `eval_after_hours`、
`vision_service` 门禁的 `vlm_gate_window_sec`。要不要扩册另开一条，别把本册的
99 站点读数与扩册后的读数混成同一个数。

用法：
    python scripts/scan_day_bounds.py [根目录 ...]      # 默认 src/memory_agent
    python scripts/scan_day_bounds.py --self-test
退出码：0 干净 / 1 有违规或冲突 / 2 量具自检失败
"""
import argparse
import ast
import os
import re
import sys

MARKER = "day-ok"
CLAMPS = ("clamp_days", "safe_days", "bounded_days")
CONFIG_ROOTS = ("config", "cfg")
LABELS = ("external", "config", "literal", "date_math", "local")
GUARDS = ("bounded", "lo_only", "unguarded")

#: `config` 归属的前提是"只有运维能改"。这个前提在本项目**不成立**：
#: `api/config_routes.py` 的 `WRITABLE_FIELDS` 里有一批键可以从 HTTP（设置页的 number 输入框）
#: 直接写入，其中就有 `vision_snapshot_retention_days` —— 把它当免检的运维值，
#: 等于让一个输入框把保留期写成 `OverflowError`。所以这些键按 `external` 计。
#: 清单从真源解析，不复制一份：复制的那份会在下一个人加键时悄悄失效。
WRITABLE_SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "src", "memory_agent", "api", "config_routes.py")


def load_writable_config_keys(path=WRITABLE_SOURCE):
    """解析 `WRITABLE_FIELDS = (...)` 里的字符串常量；读不到返回 None（调用侧按保守处置）。"""
    try:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
    except OSError:
        return None
    m = re.search(r"WRITABLE_FIELDS\s*=\s*\(", src)
    if not m:
        return None
    depth, i = 0, m.end() - 1
    while i < len(src):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = src[m.end():i]
    keys = set(re.findall(r'"([^"]+)"', body)) | set(re.findall(r"'([^']+)'", body))
    return keys or None


#: 解析在导入时做一次；self_test 里会临时替换，用来验证规则本身而不是验证文件在场。
WRITABLE_CONFIG_KEYS = load_writable_config_keys()

#: `config` 归属的第二层前提：这条链读的真是**应用配置**（`config.Config` 的字段）。
#: 只按"变量名叫 config/cfg"就豁免会放过另一类假口径：`learning_api.run_learning_cycle(store, config)`
#: 的 `config` 是模块自己的 `LearningConfig`，`window_days` 既不在应用配置里、也不在可写白名单里，
#: 于是"不在白名单"被误读成"运维受控"。**不在白名单 ≠ 不可写**，除非先证明它是应用配置的字段。
#: 字段名从 `config.py` 的 `class Config` 注解赋值解析，同样不复制清单。
APP_CONFIG_SOURCE = os.path.join(os.path.dirname(os.path.dirname(WRITABLE_SOURCE)), "config.py")


def load_app_config_keys(path=APP_CONFIG_SOURCE):
    """返回 `config.Config` 的注解字段名集合；读不到返回 None（调用侧不做外来键判红）。"""
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Config":
            out = {t.id for st in node.body if isinstance(st, ast.AnnAssign)
                   for t in ([st.target] if isinstance(st.target, ast.Name) else [])}
            return out or None
    return None


APP_CONFIG_KEYS = load_app_config_keys()

_SRC_CACHE = {}


def _lines_of(path):
    if path not in _SRC_CACHE:
        try:
            with open(path, encoding="utf-8") as fh:
                _SRC_CACHE[path] = fh.read().splitlines()
        except Exception:  # noqa: BLE001
            _SRC_CACHE[path] = []
    return _SRC_CACHE[path]


def _is_timedelta(node):
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (isinstance(f, ast.Name) and f.id == "timedelta") or \
           (isinstance(f, ast.Attribute) and f.attr == "timedelta")


def _names(node):
    """表达式里出现的自由名字（Attribute 只取根名，如 config.window_days → config）。"""
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            root = sub
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                out.add(root.id)
    return out


def _has_call_named(node, names):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Name) and f.id in names:
                return True
            if isinstance(f, ast.Attribute) and f.attr in names:
                return True
    return False


def _has_weekday(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Attribute) and f.attr == "weekday":
                return True
    return False


def _has_bound(expr_node):
    """表达式自己就带上界：有 min(...) 或 clamp 助手。"""
    return _has_call_named(expr_node, {"min"} | set(CLAMPS))


def _assignments(func_node):
    """name → [(lineno, value_has_bound)]，覆盖 Name/Tuple/List 目标与增广赋值。"""
    out = {}
    for sub in ast.walk(func_node):
        if not isinstance(sub, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            continue
        if getattr(sub, "value", None) is None:
            continue
        bounded = _has_bound(sub.value)
        for name in _target_names(sub):
            out.setdefault(name, []).append((sub.lineno, bounded))
    return out


def _bounded_at(func_node, site_lineno):
    """站点行之前"最后一次赋值是带上界赋值"的名字才算有界。

    不看行号就会误判：`voice_util.resolve_window` 里 `n` 先在字面量元组循环中用、
    之后才在问句正则分支里 `clamp_days` 过——按集合算会认为这一行也有界，
    于是把"标记 + 有界"判成冲突，反而藏住真正裸奔的站点。

    也避免逼人重复 clamp：`cd = clamp_days(days)` 之后 `timedelta(days=cd)` 视为有界。
    """
    assigns = _assignments(func_node)
    for _ in range(8):
        cur = {n for n, hist in assigns.items() for (ln, b) in hist if b and ln < site_lineno}
        grew = False
        for sub in ast.walk(func_node):
            if not isinstance(sub, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                continue
            if getattr(sub, "value", None) is None or sub.lineno >= site_lineno:
                continue
            refs = _names(sub.value)
            if not refs or not refs <= cur:
                continue
            for name in _target_names(sub):
                entry = (sub.lineno, True)
                if entry not in assigns.setdefault(name, []):
                    assigns[name].append(entry)
                    grew = True
        if not grew:
            break
    out = set()
    for name, hist in assigns.items():
        before = [(ln, b) for ln, b in hist if ln < site_lineno]
        if not before:
            continue
        last = max(ln for ln, _ in before)
        if any(b for ln, b in before if ln == last):
            out.add(name)
    return out


def _guard_of(expr_node, bounded_vars=frozenset()):
    if _has_bound(expr_node):
        return "bounded"
    refs = _names(expr_node)
    if refs and refs <= set(bounded_vars):
        return "bounded"
    if _has_call_named(expr_node, {"max"}):
        return "lo_only"
    return "unguarded"


def _config_shaped(node):
    """表达式里是否出现配置链：根名 config/cfg，或属性段 .config/.cfg（self.config.x / rt.config.x）。"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr in CONFIG_ROOTS:
            return True
        if isinstance(sub, ast.Name) and sub.id in CONFIG_ROOTS:
            return True
    return False


def _config_attr_names(node):
    """这条配置链读到的**键名**：`self.config.x` / `cfg.x` 取 `x`；
    `getattr(self.config, "x", …)` 取那个字符串常量（本项目 runtime 的保留期就是这么写的，
    只按 Attribute 认会把键名整个丢掉）。"""
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute):
            base = sub.value
            if (isinstance(base, ast.Attribute) and base.attr in CONFIG_ROOTS) or \
               (isinstance(base, ast.Name) and base.id in CONFIG_ROOTS):
                out.add(sub.attr)
        elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                and sub.func.id == "getattr" and len(sub.args) >= 2 \
                and _config_shaped(sub.args[0]):
            k = sub.args[1]
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                out.add(k.value)
    return out


def _config_key_map(func_node):
    """名字 → 它携带的配置键名集合。种子是 `x = <配置链>`，再按赋值传递（与 `_propagate` 同一套闭包）。
    要这张表是因为站点点的是局部变量名（`timedelta(days=days)`），键名在上一行的赋值里。"""
    keys = {}
    for sub in ast.walk(func_node):
        if isinstance(sub, (ast.Assign, ast.AnnAssign)) and sub.value is not None \
                and _config_shaped(sub.value):
            carried = _config_attr_names(sub.value)
            for t in _target_names(sub):
                keys.setdefault(t, set()).update(carried)
    for _ in range(8):
        before = sum(len(v) for v in keys.values())
        for sub in ast.walk(func_node):
            if isinstance(sub, (ast.Assign, ast.AugAssign, ast.AnnAssign)) and sub.value is not None:
                src = _names(sub.value) & set(keys)
                if not src:
                    continue
                carried = set().union(*(keys[s] for s in src))
                for t in _target_names(sub):
                    keys.setdefault(t, set()).update(carried)
        if sum(len(v) for v in keys.values()) == before:
            break
    return keys


def _target_names(node):
    """赋值语句里"能被再引用"的名字目标：跳过 self.x / d["k"] 这类非 Name 目标，
    并展开 a, b = ... 的元组目标。"""
    out = set()
    targets = list(getattr(node, "targets", None) or [])
    if not targets and getattr(node, "target", None) is not None:
        targets = [node.target]
    for t in targets:
        if isinstance(t, ast.Name):
            out.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                if isinstance(e, ast.Name):
                    out.add(e.id)
    return out


def _propagate(func_node, seeds):
    """从种子名出发做污染闭包：种子，以及被"引用了已污染名字"的赋值目标。"""
    tainted = set(seeds)
    for _ in range(8):
        before = len(tainted)
        for sub in ast.walk(func_node):
            if isinstance(sub, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                if sub.value is None or not _names(sub.value) & tainted:
                    continue
                tainted |= _target_names(sub)
        if len(tainted) == before:
            break
    return tainted


def _param_names(func_node):
    out = set()
    for a in list(func_node.args.posonlyargs) + list(func_node.args.args) + \
            list(func_node.args.kwonlyargs):
        out.add(a.arg)
    if func_node.args.vararg:
        out.add(func_node.args.vararg.arg)
    if func_node.args.kwarg:
        out.add(func_node.args.kwarg.arg)
    return out


def _taint_sets(func_node):
    """返回 (形参污染集, 配置污染集)。配置优先于形参：cfg.window_days 是运维口径，
    不是 LLM 能填的入参——两者的处置不同，不能混成一格。"""
    params = _param_names(func_node)
    cfg_seeds = {n for n in params if n in CONFIG_ROOTS}
    cfg_tainted = set(cfg_seeds)
    for sub in ast.walk(func_node):
        if isinstance(sub, (ast.Assign, ast.AnnAssign)) and sub.value is not None \
                and _config_shaped(sub.value):
            cfg_tainted |= _target_names(sub)
    cfg_tainted = _propagate(func_node, cfg_tainted)
    param_tainted = _propagate(func_node, params) - cfg_tainted
    return param_tainted, cfg_tainted


def _enclosing_func(tree, lineno):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= lineno <= end:
                if best is None or node.lineno > best.lineno:
                    best = node
    return best


def _markers_in_range(lines, lo, hi):
    """同一行或上一行的 `# day-ok:` 说明。"""
    found = []
    for i in range(max(0, lo - 2), min(len(lines), hi)):
        ln = lines[i]
        pos = ln.find(MARKER)
        if pos >= 0:
            found.append(ln[pos + len(MARKER):].lstrip(": ").strip())
    return found


def classify(func_node, expr_node):
    """返回 (归属标签, 依据)。配置链优先于形参污染，字面量与日期算式最先。"""
    if isinstance(expr_node, ast.Constant) and isinstance(expr_node.value, int):
        return "literal", "int literal"
    if isinstance(expr_node, ast.UnaryOp) and isinstance(expr_node.operand, ast.Constant) \
            and isinstance(expr_node.operand.value, int):
        return "literal", "int literal"
    if _has_weekday(expr_node):
        return "date_math", "weekday()"
    names = _names(expr_node)
    tainted, cfg_tainted = _taint_sets(func_node) if func_node else (set(), set())
    if _config_shaped(expr_node) or (names & cfg_tainted):
        read_keys = _config_attr_names(expr_node)
        if not read_keys and func_node is not None:
            kmap = _config_key_map(func_node)
            for n in (names & cfg_tainted):
                read_keys |= kmap.get(n, set())
        if WRITABLE_CONFIG_KEYS is None:
            # 读不到清单就不是"免检"，是"未知"：宁可整册按外部输入计（会红），
            # 也不能让 config 档在信息缺失时自动变成豁免档。
            return "external", "config-list-unreadable:" + (",".join(sorted(read_keys)) or "-")
        hit = read_keys & WRITABLE_CONFIG_KEYS
        if hit:
            return "external", "writable-config:" + ",".join(sorted(hit))
        # 键名不在应用配置字段表里 ⇒ 这条链根本不是运维受控的配置值（同名的局部对象不算）。
        # 读不到字段表时保持旧口径：不能因为量具少一份信息就把整册 config 判红。
        foreign = (read_keys - APP_CONFIG_KEYS) if APP_CONFIG_KEYS is not None else set()
        if foreign:
            return "external", "not-app-config:" + ",".join(sorted(foreign))
        var_only = (names & cfg_tainted) - read_keys
        return "config", ("attr:" + ",".join(sorted(read_keys))
                          + (" var:" + ",".join(sorted(var_only)) if var_only else ""))
    hit = names & tainted
    if hit:
        return "external", "param-tainted:" + ",".join(sorted(hit))
    return "local", "no param taint"


def scan_file(path):
    src_lines = _lines_of(path)
    try:
        tree = ast.parse("".join(l + "\n" for l in src_lines))
    except Exception as exc:  # noqa: BLE001
        return [{"file": path, "line": 0, "label": "parse_fail", "guard": "-",
                 "marker": "", "detail": repr(exc)[:80], "expr": ""}]
    rows = []
    for node in ast.walk(tree):
        if not _is_timedelta(node):
            continue
        func = _enclosing_func(tree, node.lineno)
        bvars = _bounded_at(func, node.lineno) if func else frozenset()
        for kw in node.keywords:
            if kw.arg != "days":
                continue
            expr = kw.value
            label, why = classify(func, expr)
            guard = _guard_of(expr, bvars)
            markers = _markers_in_range(src_lines, node.lineno, node.lineno)
            rows.append({
                "file": path.replace("\\", "/"), "line": node.lineno,
                "label": label, "guard": guard, "marker": markers[0] if markers else "",
                "detail": why, "expr": ast.unparse(expr)[:70],
            })
    return rows


def problems_of(rows):
    """判红：external 无防护、无归属、标记打在不需要防护的站点上、以及窗口上界缺失。"""
    probs = []
    for r in rows:
        if r["label"] == "parse_fail":
            probs.append(f"CONFLICT:parse_fail {r['file']}:{r['line']} {r['detail']}")
            continue
        if r["label"] not in LABELS:
            probs.append(f"unclassified {r['file']}:{r['line']} days={r['expr']}")
            continue
        if r["label"] == "external" and r["guard"] != "bounded" and not r["marker"]:
            probs.append(f"UNGUARDED-EXTERNAL {r['file']}:{r['line']} "
                         f"guard={r['guard']} days={r['expr']} ({r['detail']})")
        if r["marker"] and r["label"] != "external":
            probs.append(f"CONFLICT:marker_on_{r['label']} {r['file']}:{r['line']} "
                         f"'{r['marker']}'")
        if r["marker"] and r["guard"] == "bounded":
            probs.append(f"CONFLICT:marked+bounded {r['file']}:{r['line']} '{r['marker']}'")
    return probs


def counts_of(rows):
    c = {f"label_{k}": sum(1 for r in rows if r["label"] == k) for k in LABELS}
    c.update({f"guard_{k}": sum(1 for r in rows if r["guard"] == k) for k in GUARDS})
    c["marked"] = sum(1 for r in rows if r["marker"])
    c["total_timedelta_days"] = len(rows)
    return c


def scan_root(root):
    rows = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if fn.endswith(".py"):
                rows.extend(scan_file(os.path.join(dirpath, fn)))
    return rows


# ── 量具自证：正样本必须被抓，负样本不许误报 ─────────────────────────────────
POSITIVE = {
    # 形参直传，无上下界（第八轮报的那一格）
    "p1_param_unguarded.py": "def f(days):\n    from datetime import timedelta\n    return timedelta(days=days)\n",
    # 形参→局部变量→days（名字不叫 days 也须判红）
    "p2_param_renamed.py": "def f(days):\n    from datetime import timedelta\n    span = days * 1\n    return timedelta(days=span)\n",
    # 从问句里正则出来的天数（本批新发现的那一格）
    "p3_regex_from_question.py": ("import re\nfrom datetime import timedelta\n\n\n"
                                  "def f(q):\n    m = re.search(r'(\\d+)', q)\n"
                                  "    n = int(m.group(1))\n    return timedelta(days=n)\n"),
    # 只有 max（防负不防极）也要红
    "p4_lo_only.py": "from datetime import timedelta\n\n\ndef f(days):\n    return timedelta(days=max(1, days))\n",
    # 打了标记但仍无防护，且标记打在 external 上是合法的——这条测的是"标记+bounded 冲突"
    "p5_marked_and_bounded.py": ("from datetime import timedelta\n\n\ndef f(days):\n"
                                 "    # day-ok: 说明\n    return timedelta(days=max(1, min(days, 3650)))\n"),
    # 元组解包目标（a, b = days, 1）也必须带上污染——这条是量具自己踩过的坑
    "p6_tuple_target.py": ("from datetime import timedelta\n\n\ndef f(days):\n"
                           "    a, b = days, 1\n    return timedelta(days=a)\n"),
    # 上一行 clamp 过的变量混在未防护变量里 → 仍然要红（防"半有界"蒙混过关）
    "p7_half_bounded.py": ("from datetime import timedelta\n\n\ndef f(days, other):\n"
                           "    cd = max(1, min(days, 365))\n    return timedelta(days=cd + other)\n"),
    # clamp 发生在站点**之后**（同名变量先在裸分支里用）：站点处并没有界，必须红
    "p8_bounded_only_after_site.py": ("from datetime import timedelta\n\n\ndef f(n):\n"
                                      "    first = timedelta(days=n)\n"
                                      "    n = clamp_days(n)\n    return first, timedelta(days=n)\n"),
}
NEGATIVE = {
    "n1_literal.py": "from datetime import timedelta\n\n\ndef f(now):\n    return timedelta(days=1)\n",
    "n2_bounded.py": "from datetime import timedelta\n\n\ndef f(days):\n    return timedelta(days=max(1, min(int(days or 7), 3650)))\n",
    "n3_clamp_helper.py": "from datetime import timedelta\n\n\ndef f(days):\n    return timedelta(days=clamp_days(days))\n",
    "n4_date_math.py": "from datetime import timedelta\n\n\ndef f(now):\n    return timedelta(days=now.weekday())\n",
    "n5_config.py": "from datetime import timedelta\n\n\ndef f(cfg):\n    return timedelta(days=cfg.retention_days)\n",
    "n6_marked_external.py": ("from datetime import timedelta\n\n\ndef f(days):\n"
                              "    return timedelta(days=days)  # day-ok: 上游入口已收敛\n"),
    "n7_module_level_no_func.py": ("from datetime import datetime, timedelta\n\n"
                                   "TODAY = datetime(2026, 1, 1)\n"
                                   "START = TODAY - timedelta(days=1)\n"),
    # 变量在上一行已被 max/min 收敛：站点自身没有 min 也算有界（否则逼人重复 clamp）
    "n8_bounded_var.py": ("from datetime import timedelta\n\n\ndef f(days):\n"
                          "    cd = max(1, min(int(days or 7), 365))\n"
                          "    return timedelta(days=cd)\n"),
}

#: 自检**不依赖** `config_routes.py` / `config.py` 在场：用这两张合成表验证规则本身。
#: 键名故意与真源不同（真源里可写的是 `vision_snapshot_retention_days`），
#: 这样"真源被改名/删掉"不会让自检假绿；真源对照单列在 REAL_* 里。
SYNTH_WRITABLE = {"window_days"}
SYNTH_APP_KEYS = {"window_days", "retention_days", "other_days"}

#: 只在合成白名单下成立的正样本：值来自 HTTP 可写的配置键。
WRITABLE_POSITIVE = {
    # 直接属性链
    "w1_writable_attr.py": ("from datetime import timedelta\n\n\ndef f(cfg):\n"
                            "    return timedelta(days=cfg.window_days)\n"),
    # 链 + 局部变量名（站点只看到 `days`，键名在上一行）
    "w2_writable_via_var.py": ("from datetime import timedelta\n\n\ndef f(config):\n"
                               "    days = self.config.window_days\n"
                               "    return timedelta(days=days)\n"),
    # getattr 字符串常量形式（runtime 的保留期就是这么读的）
    "w3_writable_getattr.py": ("from datetime import timedelta\n\n\ndef f(rt):\n"
                               "    days = getattr(rt.config, 'window_days', 7)\n"
                               "    return timedelta(days=days)\n"),
}
#: 同一条链、键是应用配置字段但不在白名单 ⇒ 仍归 `config`，不许"能读清单就一律判红"。
WRITABLE_NEGATIVE = {
    "w4_nonwritable_attr.py": ("from datetime import timedelta\n\n\ndef f(cfg):\n"
                               "    return timedelta(days=cfg.other_days)\n"),
}
#: 键不在应用配置字段表里 = 这条链读的不是运维受控配置（同名局部对象不是 Config）。
APP_FOREIGN_POSITIVE = {
    "w5_not_app_config.py": ("from datetime import timedelta\n\n\ndef f(config):\n"
                             "    return timedelta(days=config.foreign_days)\n"),
}
#: 同一格外来键，但站点自己 clamp 过 ⇒ 归 external 且有界，干净（"改了就能过"的对照）。
APP_FOREIGN_NEGATIVE = {
    "w6_not_app_config_clamped.py": ("from datetime import timedelta\n\n\ndef f(config):\n"
                                     "    return timedelta(days=clamp_days(config.foreign_days))\n"),
}

#: 真源对照：直接用真白名单里的键与真 `Config` 字段，证明规则接的是活数据。
#: 键名一旦改动这两条会红——那是警报而不是误报（口径表与代码脱钩的正好证据）。
REAL_POSITIVE = {
    "r1_real_writable.py": ("from datetime import timedelta\n\n\ndef f(cfg):\n"
                            "    return timedelta(days=cfg.vision_snapshot_retention_days)\n"),
}
REAL_NEGATIVE = {
    "r2_real_ops_key.py": ("from datetime import timedelta\n\n\ndef f(cfg):\n"
                           "    return timedelta(days=cfg.drift_retention_days)\n"),
}


def self_test():
    import tempfile
    global WRITABLE_CONFIG_KEYS, APP_CONFIG_KEYS
    real_keys, real_app_keys = WRITABLE_CONFIG_KEYS, APP_CONFIG_KEYS
    ok = True

    def _write(tmp, name, body):
        p = os.path.join(tmp, name)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        return p

    def _positives(tmp, cases, tag):
        nonlocal ok
        for name, body in cases.items():
            rows = scan_file(_write(tmp, name, body))
            probs = [x for x in problems_of(rows) if not x.startswith("CONFLICT:parse")]
            if probs:
                print(f"SELFTEST_HIT {tag}/{name} -> {probs[0].split(' ')[0]} "
                      f"({rows[0]['detail']})")
            else:
                print(f"SELFTEST_MISS {tag}/{name} expected a finding, got {rows}")
                ok = False

    def _negatives(tmp, cases, tag):
        nonlocal ok
        for name, body in cases.items():
            rows = scan_file(_write(tmp, name, body))
            probs = problems_of(rows)
            if probs:
                print(f"SELFTEST_FALSE {tag}/{name} -> {probs}")
                ok = False
            else:
                print(f"SELFTEST_CLEAN {tag}/{name} label={rows[0]['label'] if rows else '-'}")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            WRITABLE_CONFIG_KEYS, APP_CONFIG_KEYS = SYNTH_WRITABLE, SYNTH_APP_KEYS
            _positives(tmp, POSITIVE, "core")
            _negatives(tmp, NEGATIVE, "core")
            _positives(tmp, WRITABLE_POSITIVE, "writable")
            _negatives(tmp, dict(WRITABLE_NEGATIVE, **APP_FOREIGN_NEGATIVE), "writable")
            _positives(tmp, APP_FOREIGN_POSITIVE, "app-config")

            # 「读不到清单」档：白名单缺失时必须整体按外部输入计，config 不能自动变成豁免档。
            # 对照组（什么都不改的那一档）：w4 在合成表下干净，这里必须红。
            WRITABLE_CONFIG_KEYS = None
            _positives(tmp, dict(WRITABLE_POSITIVE, **WRITABLE_NEGATIVE,
                                 **APP_FOREIGN_POSITIVE), "unreadable")

            # 真源对照：合成键退出，改用真白名单/真字段表各跑一条。
            WRITABLE_CONFIG_KEYS, APP_CONFIG_KEYS = real_keys, real_app_keys
            if not real_keys or not real_app_keys:
                print(f"SELFTEST_MISS 真源读不到 WRITABLE_KEYS={real_keys is not None} "
                      f"APP_CONFIG_KEYS={real_app_keys is not None} —— 交付面会整册按 external 判红")
                ok = False
            else:
                print(f"SELFTEST_REALSOURCE_KEYS writable={len(real_keys)} "
                      f"app_config_fields={len(real_app_keys)} "
                      f"writable_not_in_config={len(real_keys - real_app_keys)}")
                _positives(tmp, REAL_POSITIVE, "real-source")
                _negatives(tmp, REAL_NEGATIVE, "real-source")
    finally:
        WRITABLE_CONFIG_KEYS, APP_CONFIG_KEYS = real_keys, real_app_keys
    return 0 if ok else 2
    return 0 if ok else 2


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="*", default=["src/memory_agent"])
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    rows = []
    for root in args.root:
        rows.extend(scan_root(root))
    c = counts_of(rows)
    probs = problems_of(rows)
    print("ROOT=" + ",".join(args.root) + " " +
          " ".join(f"{k}={v}" for k, v in sorted(c.items())))
    for p in probs:
        print("PROBLEM " + p)
    if probs:
        print(f"SCAN_RC=1 共 {len(probs)} 处判红")
        return 1
    print("SCAN_RC=0 每个 timedelta(days=) 站点都有归属")
    return 0


if __name__ == "__main__":
    sys.exit(main())
