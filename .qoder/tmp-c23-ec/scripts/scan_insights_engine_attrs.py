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

`getattr(self.repo, "scan_limit", 0)` 这类**显式容错读取**不算指向：脚本只看真正的
调用点（`self.<owner>.<attr>(…)`），带默认值的探测由人判断。

用法：
    PYTHONPATH=src python scripts/scan_insights_engine_attrs.py [api.py 路径]
    --strict 时有空指向即 EXIT=1（供门禁调用）
"""

import argparse
import ast
import os
import sys

#: 门面体里允许出现的引擎成员名 -> (导入路径, 类名)
OWNERS = {
    "core": ("memory_agent.insights.service", "BehaviorService"),
    "nl": ("memory_agent.insights.nlquery", "NLQueryEngine"),
    "repo": ("memory_agent.insights.repository", "StoreRepository"),
    "legacy": ("memory_agent.insights_legacy", "InsightService"),
}


def load_targets():
    import importlib

    out = {}
    for owner, (module, cls) in OWNERS.items():
        out[owner] = getattr(importlib.import_module(module), cls)
    return out


def outward_methods():
    """方法名 -> 对外工具名（有 ToolSpec 的才有；其余是仓内消费点）。"""
    try:
        from memory_agent.tool_schema import TOOL_SPECS
    except Exception:  # noqa: BLE001 - 只为打印标签，拿不到就都标 internal
        return {}
    return {s.method: s.name for s in TOOL_SPECS if s.service == "insights" and s.method}


def collect(path, targets):
    """产出 (method, lineno, owner, attr)。只看 `self.<owner>.<attr>(…)`。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    hits = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for call in ast.walk(fn):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            base = call.func.value
            if not (isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
                    and base.value.id == "self"):
                continue
            if base.attr not in targets:
                continue
            hits.append((fn.name, call.lineno, base.attr, call.func.attr))
    return hits


def main():
    ap = argparse.ArgumentParser(description="insights 门面引擎成员静态对账")
    ap.add_argument("path", nargs="?",
                    default=os.path.join("src", "memory_agent", "insights", "api.py"),
                    help="门面文件（默认 src/memory_agent/insights/api.py）")
    ap.add_argument("--strict", action="store_true", help="有空指向时 EXIT=1")
    ap.add_argument("--show-ok", action="store_true", help="附带打印可解析的指向")
    ns = ap.parse_args()

    targets = load_targets()
    tools = outward_methods()
    hits = collect(ns.path, targets)
    missing = [h for h in hits if not hasattr(targets[h[2]], h[3])]

    print(f"扫描 {ns.path}：门面引擎指向 {len(hits)}，空指向 {len(missing)}")
    for method, lineno, owner, attr in sorted(missing, key=lambda x: x[1]):
        tag = tools.get(method, "internal")
        print(f"[MISS] {method} (api.py:{lineno}) -> self.{owner}.{attr} "
              f"不存在于 {targets[owner].__name__}　对外工具={tag}")
    if ns.show_ok:
        for method, lineno, owner, attr in sorted(set(hits) - set(missing), key=lambda x: x[1]):
            print(f"[OK  ] {method} (api.py:{lineno}) -> self.{owner}.{attr}")
    return 1 if (ns.strict and missing) else 0


if __name__ == "__main__":
    sys.exit(main())
