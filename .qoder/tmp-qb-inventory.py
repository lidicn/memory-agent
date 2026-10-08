# 量具（任务表 #40 ⑤ / #58 Q-D）：把"13 个工具在新引擎无同名实现"这条静态结论
# 换成 HEAD 现读。逐方法量四件事：
#   1) 门面（insights/api.py::InsightService）有没有这个方法；
#   2) 方法体是不是转发给 self.legacy.*（= 签名归门面、计算归 legacy）；
#   3) 门面形参里哪些在体内**从未被引用**（= 接收但不生效的候选）；
#   4) legacy 同名方法有而门面签名没有的形参（= 切换时会静默丢的入参）。
# 只用 AST，不 import，避免本机依赖不全把量具自身跑崩。
import ast
import os
import sys

ROOT = os.environ.get("QBR", ".")
FACADE = os.path.join(ROOT, "src/memory_agent/insights/api.py")
LEGACY = os.path.join(ROOT, "src/memory_agent/insights_legacy.py")

# 台账 :2418（第六格）点名的 13 个 + DCD 20261005 Q2 排序里的 4 个 + Q-B 相关的门面名
TOOLS = ("search_events device_usage climate_sessions get_last_event query_behavior_events "
         "explain_insight data_coverage get_user_persona ask_memory route_question "
         "define_activity get_behavior_insights_compare get_data_quality entity_catalog "
         "behavior_insights device_health water_purifier_usage plan_question "
         "compare_insights anomaly_report data_quality_issues infer_activities").split()

# 裁5 Q3 第一条点名的六个"门面收下但 _search 丢弃"的过滤/排序位
SIX = "category query state order summarize domain".split()


def methods(path):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = {}
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        for fn in cls.body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[fn.name] = fn
    return out


def params(fn):
    a = fn.args
    names = [p.arg for p in a.posonlyargs + a.args]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    return names + [p.arg for p in a.kwonlyargs]


def used_names(fn):
    return {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}


def attr_calls(fn):
    """self.legacy.X / self.core.X / self.repo.X / self.nl.X 的 X 集合。"""
    buckets = {"legacy": set(), "core": set(), "repo": set(), "nl": set(), "other": set()}
    for n in ast.walk(fn):
        if not isinstance(n, ast.Attribute):
            continue
        v = n.value
        if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) \
                and v.value.id == "self" and v.attr in buckets:
            buckets[v.attr].add(n.attr)
        elif isinstance(v, ast.Name) and v.id == "self" and n.attr not in ("legacy",):
            buckets["other"].add(n.attr)
    return buckets


def kwarg_keys(fn):
    """转发调用里显式传出的关键字（判断"入参有没有落到 legacy 的同名形参"）。"""
    keys = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            for kw in n.keywords:
                if kw.arg:
                    keys.add(kw.arg)
    return keys


f, l = methods(FACADE), methods(LEGACY)

print("FACADE_METHODS=%d LEGACY_METHODS=%d" % (len(f), len(l)))
print()
hdr = ("tool", "facade", "fwd->legacy", "facade_dropped_params", "legacy_only_params")
print("%-26s %-7s %-22s %-26s %s" % hdr)
print("-" * 110)
n_absent = n_fwd = n_drop = 0
for t in TOOLS:
    if t not in f:
        n_absent += 1
        print("%-26s %-7s %-22s %-26s %s" % (t, "ABSENT", "-", "-",
                                             ",".join(params(l[t]) if t in l else []) or "-"))
        continue
    fn = f[t]
    fp = [p for p in params(fn) if p != "self"]
    dropped = [p for p in fp if p not in used_names(fn) and not p.startswith("*")]
    fwd = sorted(attr_calls(fn)["legacy"])
    lop = []
    if t in l:
        lop = [p for p in params(l[t]) if p != "self" and p not in fp]
    if fwd:
        n_fwd += 1
    if dropped:
        n_drop += 1
    print("%-26s %-7s %-22s %-26s %s" % (
        t, "OK", ",".join(fwd)[:22] or "(none)",
        ",".join(dropped)[:26] or "-", ",".join(lop) or "-"))

print()
print("SUMMARY absent=%d forwards=%d has_dropped_param=%d of %d" %
      (n_absent, n_fwd, n_drop, len(TOOLS)))

print()
print("---- 裁5 Q3-1 六个过滤/排序位：门面签名在场？体内被引用？ ----")
for m in ("search_events", "_search", "query_behavior_events", "entity_catalog",
          "device_usage", "device_health", "data_quality_issues", "anomaly_report"):
    fn = f.get(m)
    if not fn:
        print("%-22s ABSENT" % m)
        continue
    ps = params(fn)
    un = used_names(fn)
    row = []
    for s in SIX:
        if s in ps:
            row.append("%s%s" % (s, "" if s in un else "=DROP"))
        else:
            row.append("%s=no-param" % s)
    print("%-22s %s" % (m, " ".join(row)))

print()
print("---- 裁5 Q3-4 legacy 分页/窗口键在门面的在场计数（源码字面量） ----")
with open(FACADE, encoding="utf-8") as fh:
    src = fh.read()
for k in ("count", "next_offset", "window", '"ok"', "total_exact", "truncated",
          "scan_limit", "has_more"):
    print("%-12s facade=%d" % (k, src.count(k)))
with open(LEGACY, encoding="utf-8") as fh:
    lsrc = fh.read()
for k in ("count", "next_offset", "window"):
    print("%-12s legacy=%d" % (k, lsrc.count('"%s"' % k)))

print()
print("---- 门面里显式传出的关键字是否覆盖 legacy 形参（抽样三个高优先工具） ----")
for t in ("get_user_persona", "data_coverage", "get_data_quality"):
    fn = f.get(t)
    if not fn or t not in l:
        print("%-20s skip" % t)
        continue
    lp = [p for p in params(l[t]) if p != "self"]
    print("%-20s legacy_params=%s facade_params=%s body_kwargs=%s fwd=%s" % (
        t, ",".join(lp), ",".join(params(fn)),
        ",".join(sorted(kwarg_keys(fn) & set(lp))) or "-",
        ",".join(sorted(attr_calls(fn)["legacy"])) or "(none)"))
