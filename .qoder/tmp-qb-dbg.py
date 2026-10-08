import ast
import importlib.util
import os
import sys

spec = importlib.util.spec_from_file_location(
    "scl", os.path.join(os.getcwd(), "scripts", "scan_qb_param_landing.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

path = os.path.join("src", "memory_agent", "insights", "api.py")
with open(path, encoding="utf-8") as fh:
    tree = ast.parse(fh.read())
cls = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "InsightService"][0]
fn = [f for f in cls.body if getattr(f, "name", "") == "anomaly_report"][0]
dead = m._dead_store_lines(fn)
print("DEAD_STORES=%s" % dead)
for nm in ("days", "start", "end", "room", "category", "query"):
    print("%-9s owners=%s loads=%s" % (nm, m._owner_lines(fn, nm), m._loads(fn).get(nm)))
