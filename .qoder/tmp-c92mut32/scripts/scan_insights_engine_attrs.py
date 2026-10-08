"""对 insights 门面的「引擎成员指向」做静态对账（审计 §十六 的第二半，裁5 复现器）。

`scripts/scan_insights_callsites.py` 管的是**外面调进来**的那一半：调用点的参数形状
能不能被门面接住。这个脚本管**门面调出去**的那一半：门面体里 `self.core.X(...)` /
`self.nl.X(...)` / `self.repo.X(...)` / `self.legacy.X(...)` 指向的成员在目标类上
到底存不存在。

为什么这一半必须单独扫：这类缺陷在运行时读不出来——`_degrade` 会把
`AttributeError: 'BehaviorService' object has no attribute 'climate_sessions'`
静默收成空页，工具在目录里、参数能绑上、返回永远是空的。对 HEAD 的实测读数是
35 个引擎指向里 **6 处空指向**（`get_last_event`→`core.load_events`、
`climate_sessions`、`water_purifier_usage`、`data_quality_issues`、
`explain_insight`×2——共 5 个门面方法，其中 3 个是对外 MCP 工具）。
扫描器按名字报告，`--strict` 判红。

**两跳以上的链也要扫**（run13d 补的口径，起因是一条真实的漏网现行）：
`api.py` 的四条报告面写成 `self.core.reports.<x>_report(...)`，而 `BehaviorService`
从未挂载 `reports`——调用即 `AttributeError: 'BehaviorService' object has no
attribute 'reports'`。改前的版本只认 `self.<引擎>.<成员>(…)` 这一种形状
（判据是 `call.func.value.value` 必须是 `ast.Name`），`self.core.reports.anomaly_report(…)`
的中间节点是 `Attribute` 而不是 `Name`，四条深链**一条都不进统计**：实测读数
「35 指向 / 0 空指向」，全绿，同时这四条方法全是坏的。这与 N6 变异戳出来的是同一族缺口
——量具停在某一层，缺陷就在下一层原样重演。现在链的每一跳都要有归属：中间成员在目标类
（或其 `__init__` 的实例槽位）上不存在 → `MISS`；存在但类型没登记（`SUBOWNERS`）→
`UNREGISTERED`，同样判红，不允许「链太长所以看一眼算了」。

`getattr(self.repo, "scan_limit", 0)` 这类**显式容错读取**不算指向：脚本只看真正的
调用点（`self.<引擎>.<链>(…)`），带默认值的探测由人判断。

用法：
    PYTHONPATH=src python scripts/scan_insights_engine_attrs.py [api.py 路径]
    --strict 时有空指向即 EXIT=1（供门禁调用）
    --self-test 喂四种合成的链形状，逐个验证判红口径（含「真 api.py 无问题」这一档）
"""

import argparse
import ast
import importlib
import inspect
import os
import sys
import textwrap

#: 门面体里允许出现的引擎成员名 -> (导入路径, 类名)
OWNERS = {
    "core": ("memory_agent.insights.service", "BehaviorService"),
    "nl": ("memory_agent.insights.nlquery", "NLQueryEngine"),
    "repo": ("memory_agent.insights.repository", "StoreRepository"),
    "legacy": ("memory_agent.insights_legacy", "InsightService"),
}

#: 深链的中间成员 -> (导入路径, 类名)。缺登记会让 `self.core.reports.x()` 这类
#: 两跳调用只量到第一跳，而第二跳恰恰是审计里活下来的那半个缺陷。
SUBOWNERS = {
    ("core", "reports"): ("memory_agent.insights.report", "ReportBuilder"),
}

_SLOTS_CACHE = {}


def load_targets():
    out = {}
    for owner, (module, cls) in OWNERS.items():
        out[owner] = getattr(importlib.import_module(module), cls)
    return out


def _load_cls(module, name):
    return getattr(importlib.import_module(module), name)


def init_slots(cls):
    """`__init__` 里 `self.X = …` 形状的实例槽位。

    类属性面 `hasattr(cls, 'reports')` 读不到它们（要实例才有），所以判「成员存在」
    必须补这一路；只看 `co_names` 不行——那里分不清「赋值」和「读一个不存在的成员」，
    而后者正是本件要抓的形状。
    """
    if cls.__name__ in _SLOTS_CACHE:
        return _SLOTS_CACHE[cls.__name__]
    slots = set()
    try:
        src = textwrap.dedent(inspect.getsource(cls.__init__))
    except (OSError, TypeError):
        src = ""
    if src:
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for t in targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                        and t.value.id == "self":
                    slots.add(t.attr)
    out = frozenset(slots)
    _SLOTS_CACHE[cls.__name__] = out
    return out


def outward_methods():
    """方法名 -> 对外工具名（有 ToolSpec 的才有；其余是仓内消费点）。"""
    try:
        from memory_agent.tool_schema import TOOL_SPECS
    except Exception:  # noqa: BLE001 - 只为打印标签，拿不到就都标 internal
        return {}
    return {s.method: s.name for s in TOOL_SPECS if s.service == "insights" and s.method}


def collect(path, targets):
    """产出 (method, lineno, owner, chain)。chain 是引擎成员之后逐跳的属性名元组。

    一跳调用 `self.core.load_events(...)` -> chain=("load_events",)；
    两跳 `self.core.reports.anomaly_report(...)` -> chain=("reports", "anomaly_report")。
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    hits = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for call in ast.walk(fn):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            parts = []
            node = call.func
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if not (isinstance(node, ast.Name) and node.id == "self"):
                continue
            parts.reverse()
            if len(parts) < 2 or parts[0] not in targets:
                continue
            hits.append((fn.name, call.lineno, parts[0], tuple(parts[1:])))
    return hits


def resolve_chain(owner, cls, chain):
    """可解析返回 `None`；否则返回 `(kind, hop)`，kind ∈ {"MISS", "UNREGISTERED"}。"""
    mid, rest = chain[0], chain[1:]
    if not hasattr(cls, mid) and mid not in init_slots(cls):
        return ("MISS", mid)
    if not rest:
        return None
    entry = SUBOWNERS.get((owner, mid))
    if entry is None:
        return ("UNREGISTERED", mid)
    sub = _load_cls(*entry)
    for hop in rest:
        if not hasattr(sub, hop):
            return ("MISS", hop)
    return None


def missing(hits, targets):
    """按链解析，返回有问题的那些 (method, lineno, owner, chain, kind, hop)。"""
    out = []
    for method, lineno, owner, chain in hits:
        verdict = resolve_chain(owner, targets[owner], chain)
        if verdict:
            out.append((method, lineno, owner, chain, verdict[0], verdict[1]))
    return out


def _self_test():
    """门自证：四种合成的链形状各判一类，真 api.py 那一档必须一条问题都没有。"""
    import tempfile

    src = (
        "class InsightService:\n"
        "    def fine(self):\n"
        "        return self.core.reports.anomaly_report({})\n"
        "    def direct(self):\n"
        "        return self.core.anomaly_report({})\n"
        "    def no_member(self):\n"
        "        return self.core.nope.thing()\n"
        "    def no_method(self):\n"
        "        return self.core.reports.nope_too()\n"
        "    def unregistered(self):\n"
        "        return self.core.repo.load_events()\n"
    )
    targets = load_targets()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "synthetic.py")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(src)
        hits = collect(path, targets)
        got = {m: (kind, hop) for m, _ln, _o, _c, kind, hop in missing(hits, targets)}
    assert len(hits) == 5, hits
    assert got == {"no_member": ("MISS", "nope"),
                   "no_method": ("MISS", "nope_too"),
                   "unregistered": ("UNREGISTERED", "repo")}, got
    assert "fine" not in got and "direct" not in got, got
    real = collect(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src", "memory_agent", "insights", "api.py"), targets)
    deep = [h for h in real if len(h[3]) > 1]
    problems = missing(real, targets)
    print("SELFTEST_SYNTHETIC hits=%d problems=%d %s" % (len(hits), len(got), sorted(got)))
    print("SELFTEST_REAL 指向=%d 深链=%d 问题=%d" % (len(real), len(deep), len(problems)))
    if problems:
        for m, ln, o, chain, kind, hop in problems:
            print("SELFTEST_BAD %s (api.py:%s) -> self.%s.%s %s"
                  % (m, ln, o, ".".join(chain), kind))
        return 1
    print("SELFTEST=OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description="insights 门面引擎成员静态对账")
    ap.add_argument("path", nargs="?",
                    default=os.path.join("src", "memory_agent", "insights", "api.py"),
                    help="门面文件（默认 src/memory_agent/insights/api.py）")
    ap.add_argument("--strict", action="store_true", help="有空指向即 EXIT=1")
    ap.add_argument("--show-ok", action="store_true", help="附带打印可解析的指向")
    ap.add_argument("--self-test", action="store_true", help="合成链形状自咬（判据不是空转）")
    ns = ap.parse_args()

    if ns.self_test:
        return _self_test()

    targets = load_targets()
    tools = outward_methods()
    hits = collect(ns.path, targets)
    problems = missing(hits, targets)
    deep = sum(1 for h in hits if len(h[3]) > 1)

    print(f"扫描 {ns.path}：门面引擎指向 {len(hits)}（含多跳链 {deep}），"
          f"空指向 {sum(1 for p in problems if p[4] == 'MISS')}，"
          f"未登记 {sum(1 for p in problems if p[4] == 'UNREGISTERED')}")
    for method, lineno, owner, chain, kind, hop in sorted(problems, key=lambda x: x[1]):
        tag = tools.get(method, "internal")
        label = "不存在" if kind == "MISS" else "类型未登记（SUBOWNERS）"
        print(f"[{kind[:3]}] {method} ({ns.path}:{lineno}) -> self.{owner}."
              f"{'.'.join(chain)}（断在 {hop}，{label}）　对外工具={tag}")
    if ns.show_ok:
        bad = {(p[0], p[1]) for p in problems}
        for method, lineno, owner, chain in sorted(set(hits) - bad, key=lambda x: x[1]):
            print(f"[OK  ] {method} (api.py:{lineno}) -> self.{owner}.{'.'.join(chain)}")
    return 1 if (ns.strict and problems) else 0


if __name__ == "__main__":
    sys.exit(main())
