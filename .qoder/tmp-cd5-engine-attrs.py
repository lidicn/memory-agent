"""静态核对：门面体里 `self.core.X(` / `self.nl.X(` / `self.repo.X(` 调的成员是否真实存在。"""
import ast
import inspect
import os
import sys

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from memory_agent.insights import InsightService
from memory_agent.insights.repository import StoreRepository
from memory_agent.insights.service import BehaviorService
from memory_agent.insights.nlquery import NLQueryEngine

TARGETS = {"core": BehaviorService, "nl": NLQueryEngine, "repo": StoreRepository,
           "legacy": None}

path = os.path.join(_SRC, "memory_agent", "insights", "api.py")
tree = ast.parse(open(path, encoding="utf-8").read())

missing = []
by_method = {}
for node in ast.walk(tree):
    if not isinstance(node, ast.FunctionDef):
        continue
    by_method.setdefault(node.name, node)
for name, fn in by_method.items():
    for call in ast.walk(fn):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        base = call.func.value
        if not (isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
                and base.value.id == "self"):
            continue
        owner = base.attr
        if owner not in TARGETS or TARGETS[owner] is None:
            continue
        attr = call.func.attr
        if not hasattr(TARGETS[owner], attr):
            missing.append((fn.name, call.lineno, f"self.{owner}.{attr}"))

print("门面方法数:", len([n for n in by_method]))
print("调用不存在的引擎成员:", len(missing))
for m in missing:
    print("  [MISS]", m[0], "line", m[1], "->", m[2])

# 反向：门面里每个 self.legacy.X 都要 legacy 真有
from memory_agent.insights_legacy import InsightService as Legacy
bad = [(n, f"self.legacy.{a}") for n, fn in by_method.items() for a in
       [c.func.attr for c in ast.walk(fn) if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Attribute)
        and isinstance(c.func.value, ast.Attribute)
        and isinstance(c.func.value.value, ast.Name) and c.func.value.value.id == "self"
        and c.func.value.attr == "legacy"] if not hasattr(Legacy, a)]
print("legacy 转发指空:", len(bad), bad)
