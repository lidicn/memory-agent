r"""#80 建门前的取数预演（只读，不落测试）：17 个 insights 工具的运行时载荷键集合 + 文案候选差集。

门的形状要求"文案点名的键 ↔ 运行时探针读数"逐条对上，所以先把两件事量出来：
1. 每个 `service="insights"` 的 ToolSpec，按夹具真跑一遍门面方法，递归收载荷里**真实存在**的键；
2. 该工具 summary/description/pitfall 里点名的候选 token，扣掉**入参名**（同一条 spec 的 params）
   与**别的工具名**之后，还剩哪些没被载荷覆盖 —— 这些就是要进登记表的（枚举取值 / 输入侧字段名 /
   子路径 / 散文惯用语）。

    python .qoder/tmp-c80-probe-keys.py
"""
import ast
import keyword
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from memory_agent.config import Config                     # noqa: E402
from memory_agent.insights import InsightService           # noqa: E402
from memory_agent.insights.models import EntityInfo        # noqa: E402
from memory_agent.store import Store                       # noqa: E402

SPEC = os.path.join(ROOT, "src", "memory_agent", "tool_schema.py")
TOKEN = re.compile(r"(?<![A-Za-z0-9_.-])[a-z][a-z0-9_]{1,}(?![A-Za-z0-9_-])")
STOP = set("""a an and any are as at by for from in into is it no not of on or that the to use
with via when which yes can may must be been if then else per etc eg i.e one two three first
next other also only same such very more most than too this those these both each into
""".split())
DAY_1, DAY_2 = "2026-09-21", "2026-09-22"
PC, LIGHT = "binary_sensor.study_pc", "light.study_lamp"
AC = "climate.master_ac"
CATALOG = [EntityInfo(entity_id=PC, friendly_name="书房电脑", room="书房", domain="binary_sensor"),
           EntityInfo(entity_id=LIGHT, friendly_name="书房台灯", room="书房", domain="light"),
           EntityInfo(entity_id=AC, friendly_name="主卧空调", room="主卧", domain="climate")]

ARGS = {"route_question": {"question": "书房昨晚用了多久电脑"},
        "ask_memory": {"question": "书房昨晚用了多久电脑"},
        "explain_insight": {"insight_id": "insight-1"},
        "define_activity": {"name": "看书", "room": "书房", "keywords": "看书"}}


def specs():
    tree = ast.parse(open(SPEC, encoding="utf-8").read(), filename=SPEC)
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ToolSpec"):
            continue
        g = {"params": []}
        for kw in node.keywords:
            if kw.arg in ("name", "service", "method") and isinstance(kw.value, ast.Constant):
                g[kw.arg] = kw.value.value
            elif kw.arg in ("summary", "description", "pitfall"):
                if isinstance(kw.value, ast.Constant):
                    g[kw.arg] = str(kw.value.value)
                elif isinstance(kw.value, ast.JoinedStr):
                    g[kw.arg] = " ".join(str(v.value) for v in kw.value.values
                                         if isinstance(v, ast.Constant))
                else:  # 相邻字符串字面量被 parser 合成 Constant，这里兜 ImplicitConcat 之外的形状
                    g[kw.arg] = ast.unparse(kw.value)
            elif kw.arg == "params" and isinstance(kw.value, (ast.List, ast.Tuple)):
                for el in kw.value.elts:
                    if isinstance(el, ast.Call) and el.args and isinstance(el.args[0], ast.Constant):
                        g["params"].append(el.args[0].value)
        out.append(g)
    return out


def payload_keys(obj, depth=0, bag=None):
    bag = set() if bag is None else bag
    if depth > 6 or isinstance(obj, (str, int, float, bool)) or obj is None:
        return bag
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                bag.add(k)
            payload_keys(v, depth + 1, bag)
    elif isinstance(obj, (list, tuple)):
        for v in obj[:6]:
            payload_keys(v, depth + 1, bag)
    return bag


def probe(facade, name, method):
    kwargs = dict(ARGS.get(name, {}))
    fn = getattr(facade, method, None)
    if fn is None:
        return None, "NO_METHOD"
    try:
        import inspect
        allowed = {p for p in inspect.signature(fn).parameters}
        kwargs = {k: v for k, v in kwargs.items() if k in allowed}
        return fn(**kwargs), None
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, str(exc)[:80])


def ev(entity_id, day, minute, state, domain, room):
    return {"entity_id": entity_id, "ts": "%sT10:%02d:00" % (day, minute), "room": room,
            "domain": domain, "new_state": state, "old_state": "x",
            "attrs_json": '{"friendly_name": "%s", "hvac_action": "%s", "temperature": 26.0, '
                          '"current_temperature": 28.0}' % (entity_id.split(".")[-1], state)}


def rows():
    out = []
    for day in (DAY_1, DAY_2):
        out += [ev(PC, day, i, ("on" if i % 2 else "off"), "binary_sensor", "书房")
                for i in range(6)]
        out += [ev(LIGHT, day, i, "on", "light", "书房") for i in range(3)]
        out += [ev(AC, day, i, s, "climate", "主卧")
                for i, s in ((0, "off"), (10, "cool"), (40, "cool"), (70, "off"))]
    return out


tmp = tempfile.mkdtemp(prefix="ma_c80_")
store = Store(os.path.join(tmp, "c80.db"), tz_offset_hours=0.0)
store.init_schema()
store.insert_events(rows())
facade = InsightService(store, Config())
facade.resolver.refresh(CATALOG)

tool_names = {s["name"] for s in specs() if s.get("name")}
missing, covered = [], []
for s in sorted(specs(), key=lambda x: x.get("name", "")):
    if s.get("service") != "insights":
        continue
    name = s["name"]
    payload, err = probe(facade, name, s.get("method", ""))
    keys = set() if err else payload_keys(payload)
    prose = " ".join(str(s.get(k, "")) for k in ("summary", "description", "pitfall"))
    cand = {t for t in TOKEN.findall(prose)
            if t not in STOP and t not in s["params"] and t not in tool_names
            and not keyword.iskeyword(t) and ("_" in t or len(t) >= 4)}
    hit = sorted(cand & keys)
    miss = sorted(cand - keys)
    covered.append((name, len(keys), len(hit), len(miss), sorted(keys)))
    missing.append((name, hit, miss))

print("PROBED=%d" % len(covered))
for name, nkeys, nhit, nmiss, keys in covered:
    print("\n### %-30s keys=%d prose_hit=%d prose_miss=%d" % (name, nkeys, nhit, nmiss))
    print("    KEYS: " + " ".join(keys))
for name, hit, miss in missing:
    print("\n--- %-30s MISS(%d): %s" % (name, len(miss), " ".join(miss)))
    print("    HIT(%d): %s" % (len(hit), " ".join(hit)))
store.close()
