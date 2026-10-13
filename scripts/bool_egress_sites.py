# -*- coding: utf-8 -*-
"""分类量具（任务表 #116／#92 的「先分类量具」那半步）：`MqttBridge` 里 `-> bool` 的发布方法，
每一个调用点到底有没有**用**那个返回值。纯 AST，⛔ import 被扫模块。

为什么链上已有的三把尺吃不到这一面：`homesdk-gates` 的 `except-pass-broad`／`fake-ok-const`／
`swallow-and-claim-ok` 量的分别是空体 except、写死的 ok 字面量、以及**同一函数内**两者齐备；
而「`ok = publish_raw(...)` vs `publish_raw(...)`」这种**返回值在调用点被丢**的形状，三条都不咬。

方法名集合从 `mqtt_bridge.py` 的**声明面**现读（`-> bool`），⛔ 手列——手列的清单会随桥加方法而静默变窄。

判据（机械可定）：目标调用的**直接父节点是 `ast.Expr`** ⇒ 这条语句的整值⛔ 被任何人取走＝DISCARDED；
父节点是赋值/条件/返回/实参/比较…… ⇒ USED。

两个已知的口径盲区，都在输出里单列而⛔ 静默吞掉：
① **接收者面**：只按属性名匹配会把同名库函数误记成"丢弃桥的返回值"——`presence.advertise(...)`
   声明是 `-> None`（`site-packages/homesdk/presence.py:83`），与桥的 `advertise() -> bool` 同名。
   所以丢弃点按**接收者文本**分档：只有桥的接收者（`BRIDGE_RECEIVERS` 那串字面量，加
   `mqtt_bridge.py` 内部的 `self`）才进 DISCARDED；其余 ⇒ `NOT_THE_BRIDGE`，逐枚列出、⛔ 进计数。
② **出声档位是函数级的**：`DISCARDED/LOCAL` 只说"这条调用所在函数体内⛔ 只有这一处出声"，
   ⛔ 说出声的那一行报告了这条调用的返回值——那要人读原文。本量具⛔ 判"是不是缺陷"，只出清单与分类。

用法：`python scripts/bool_egress_sites.py`（无参数）。读数⛔ 判范围：范围等 DCD 裁（任务表 #92）。
"""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src", "memory_agent")
BRIDGE = os.path.join(SRC, "mqtt_bridge.py")

BRIDGE_RECEIVERS = {"mqtt", "self.mqtt", "bridge", "self.bridge", "_bridge", "self._bridge",
                    "mqtt_bridge", "self._mqtt"}


def bool_api(path):
    """声明面：模块级类里标了 `-> bool` 的方法名。"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    names = set()
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        for fn in cls.body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and isinstance(fn.returns, ast.Name) and fn.returns.id == "bool":
                names.add(fn.name)
    return names


def has_voice(fn_node):
    """函数体内**存在**出声（print／logger）。档位口径见模块 docstring 盲区②。"""
    for n in ast.walk(fn_node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id == "print":
                return True
            if isinstance(f, ast.Attribute) and (f.attr in ("info", "warning", "error", "debug",
                                                            "exception") or "log" in f.attr.lower()):
                return True
    return False


def receiver_of(src, call):
    txt = ast.get_source_segment(src, call.func.value)
    return (txt or "").strip()


def receiver_class(recv, rel):
    if recv in BRIDGE_RECEIVERS:
        return "BRIDGE"
    if recv == "self" and rel.endswith("mqtt_bridge.py"):
        return "BRIDGE"
    return "NOT_THE_BRIDGE"


def enclosing_fn(node, parent):
    while node is not None and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        node = parent.get(id(node))
    return node


def qualname(fn, parent):
    parts, cur = [], fn
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parts.append(cur.name + "()")
        elif isinstance(cur, ast.ClassDef):
            parts.append(cur.name + ".")
        cur = parent.get(id(cur))
    return ".".join(reversed(parts)) or "<module>"


api = bool_api(BRIDGE)
assert len(api) >= 5, f"声明面只读到 {len(api)} 个 `-> bool` 方法，量具口径失效：{sorted(api)}"

discarded, used = [], []
files = 0
for dirpath, dirnames, filenames in os.walk(SRC):
    dirnames[:] = [d for d in dirnames if d != "__pycache__"]
    for name in sorted(filenames):
        if not name.endswith(".py"):
            continue
        p = os.path.join(dirpath, name)
        rel = os.path.relpath(p, REPO).replace("\\", "/")
        files += 1
        with open(p, encoding="utf-8") as f:
            src = f.read()
        try:
            tree = ast.parse(src, filename=p)
        except SyntaxError as exc:
            print(f"BAD-PARSE {rel}: {exc}")
            continue
        parent = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parent[id(child)] = node

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in api):
                continue
            recv = receiver_of(src, node)
            rcls = receiver_class(recv, rel)
            if isinstance(parent.get(id(node)), ast.Expr):
                fn = enclosing_fn(node, parent)
                voice = "DISCARDED/LOCAL" if fn is not None and has_voice(fn) \
                        else "DISCARDED/SILENT"
                discarded.append((rel, node.lineno, node.func.attr, qualname(fn, parent),
                                  voice, recv, rcls))
            else:
                used.append((rel, node.lineno, node.func.attr, recv, rcls))

bridge_disc = [r for r in discarded if r[6] == "BRIDGE"]
other_disc = [r for r in discarded if r[6] != "BRIDGE"]
bridge_used = [r for r in used if r[4] == "BRIDGE"]
other_used = [r for r in used if r[4] != "BRIDGE"]

print(f"扫描文件= {files}｜API(`-> bool`，声明面现读)= {len(api)} 个：{sorted(api)}")
print(f"ALL_NAME_HITS= {len(discarded) + len(used)}"
      f"｜BRIDGE 面: DISCARDED= {len(bridge_disc)}｜USED= {len(bridge_used)}"
      f"｜NOT_THE_BRIDGE(同名别对象，⛔ 进 DISCARDED 口径)= {len(other_disc) + len(other_used)}")
print("-- 桥接收者上的丢弃点（这一档才是「丢弃 bool 出口」的候选面）--")
for row in sorted(bridge_disc):
    print(f"  {row[0]}:{row[1]}  {row[2]}  @ {row[3]}  [{row[4]}]  recv={row[5]!r}")
print("-- 名字命中但接收者⛔ 是桥（假阳面，逐枚留档供下一只手复核）--")
for row in sorted(other_disc):
    print(f"  (丢) {row[0]}:{row[1]}  {row[2]}  @ {row[3]}  recv={row[5]!r}")
for row in sorted(other_used):
    print(f"  (USED) {row[0]}:{row[1]}  {row[2]}  recv={row[3]!r}")
by_attr = {}
for row in bridge_disc:
    by_attr[row[2]] = by_attr.get(row[2], 0) + 1
print("DISCARDED(桥面) 按方法＝ " + " | ".join(f"{k}={v}" for k, v in sorted(by_attr.items())))
print("NOTE= 丢弃⛔ 等于缺陷。本量具只出清单与分类；范围等 DCD 裁（任务表 #92），本席⛔ 自决扩大改动面。")
sys.exit(0)
