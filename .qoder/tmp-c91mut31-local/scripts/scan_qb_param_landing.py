"""门面/新引擎的「入参有没有落到调用」静态对账（任务表 #40 ⑤ / #58 Q-D，DCD 20261005 §三 Q2）。

裁定原文（`decisions/20261005-AF-ir_non_reversible与DPP遥测与MA集中五问-裁定.md` §三 Q2 第 2 条）：
「每个入参必须有『新实现落点』或『显式不支持』的登记，**不允许静默忽略**。」
这句话要的是一条能判红的对账，而不是一份人写的表——所以本脚本是它的量具。

与前两支扫描器的分工（三半合成，不重叠）：
- `scripts/scan_insights_callsites.py`：**外面调进来**的一半——调用点的参数形状门面接不接得住；
- `scripts/scan_insights_engine_attrs.py`：**门面调出去**的一半——`self.core.X` 这类成员存不存在；
- 本脚本：门面/新引擎**体内**的一半——形参收下了，究竟有没有进到一个会影响结果的调用里。

为什么这一半必须单独扫（现场读数，本机 AST，`INV_RC=0`）：
`insights/api.py:707 anomaly_report(days=0, start="", end="", room="", category="", query=")`
的体是 `tr = self._tr(start, end)` + `start, end = self._days_to_range(days, start, end)`
——后一条的**返回值没人读**，于是 `days` 进了一个死赋值，窗口悄悄退回 `default_days`；
`query` 更直接：全函数从未引用。两类都不抛、不报错、返回形状完好，
`_degrade` 也管不着（它只吞异常），所以在工具目录里看起来一切正常。

判据（只报两类，宁可少报不误报）：
- `DROPPED`：形参在函数体内**一次都没有被读取**（`ast.Name` 的 Load 上下文）。
- `DEAD-RESULT`：形参的读取点**全部**落在死赋值右侧——即它只参与了一次运算，
  而那次运算的目标（`x = …` 的 `x`）在赋值之后再也没有被读过。
其余一律算 `LANDS`。日志/异常消息里的引用不算落点，但也不判红（由人读表）。

用法：
    PYTHONPATH=src python scripts/scan_qb_param_landing.py [文件…]
    --strict      有 DROPPED / DEAD-RESULT 即 EXIT=1（供门禁调用）
    --self-test   量具自证：两档合成源（两类缺陷都要咬 / 什么都不改不判红），失败 EXIT=2
    --json        机器可读输出

`PARAM_LANDING_EXCEPTIONS` 是"显式不支持"的登记口：某参数确实不打算生效，
必须在这里写明理由，否则判红——登记这件事本身就是裁定的要求。
"""
from __future__ import annotations

import ast
import json
import os
import sys

#: 显式登记「这个入参在新引擎就是不落地」的口子：`{(文件, 类, 方法): {参数: 理由}}`。
#: 空表是常态；要往这里加就得先写清理由，并由 diff 留下裁决出处。
PARAM_LANDING_EXCEPTIONS: dict = {}

DEFAULT_TARGETS = (
    "src/memory_agent/insights/api.py",
    "src/memory_agent/insights/service.py",
    "src/memory_agent/insights/nlquery.py",
    "src/memory_agent/insights/repository.py",
)


def _loads(tree):
    """所有 Load 位置的 `名字 -> 出现行号` 表。"""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            out.setdefault(node.id, []).append(node.lineno)
    return out


def _flat_names(targets):
    """把 `a = …`、`a, b = …`、`a[i], b = …` 的目标名字拍平（元组/列表目标要展开）。"""
    out = []
    for t in targets:
        if isinstance(t, ast.Name):
            out.append(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            out += _flat_names(t.elts)
    return out


def _dead_store_lines(fn):
    """函数里「赋值之后再没被读过的赋值语句」，连同其右侧引用到的名字。"""
    reads = _loads(fn)
    dead = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            names = _flat_names(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names, value = [node.target.id], node.value
        else:
            continue
        if not names or value is None:
            continue
        # 目标里只要有一个在赋值行之后被读过，这条赋值就不是死的
        if any(lineno > node.lineno for n in names for lineno in reads.get(n, [])):
            continue
        dead[node.lineno] = {r.id for r in ast.walk(value)
                             if isinstance(r, ast.Name) and isinstance(r.ctx, ast.Load)}
    return dead


def _owner_lines(fn, name):
    """每次读取该名字的**外层语句行号**列表。

    做法：逐语句扫描，取每条语句子树里该名字出现的次数差——语句自己带的出现算在这条语句上，
    嵌套语句（函数/lambda/类体）里的出现由那条内层语句认领，不会重复计。
    """
    owners = []
    for stmt in ast.walk(fn):
        if not isinstance(stmt, ast.stmt) or isinstance(
                stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # 函数定义节点自身要跳过：它的子树覆盖整段函数体，
            # 否则每个形参都会被算成"在签名行读过一次"，死赋值判据永远打不中。
            continue
        inner = [c for c in ast.walk(stmt)
                 if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                                   ast.ClassDef)) and c is not stmt]
        inner_nodes = set()
        for node in inner:
            inner_nodes |= {id(x) for x in ast.walk(node)}
        for r in ast.walk(stmt):
            if (isinstance(r, ast.Name) and r.id == name
                    and isinstance(r.ctx, ast.Load) and id(r) not in inner_nodes):
                owners.append(stmt.lineno)
    return owners


def scan_file(path, only_public=True):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    findings = []
    rows = []
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        for fn in cls.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if only_public and fn.name.startswith("_"):
                continue
            params = [p.arg for p in fn.args.posonlyargs + fn.args.args
                      + fn.args.kwonlyargs if p.arg != "self"]
            params += ["*" + a.arg for a in [fn.args.vararg] if a]
            params += ["**" + a.arg for a in [fn.args.kwarg] if a]
            if not params:
                continue
            dead = set(_dead_store_lines(fn))
            for p in params:
                if p.startswith("*"):
                    continue
                exc = PARAM_LANDING_EXCEPTIONS.get(
                    (os.path.basename(path), cls.name, fn.name), {})
                if p in exc:
                    rows.append((cls.name, fn.name, p, "REGISTERED"))
                    continue
                owners = _owner_lines(fn, p)
                if not owners:
                    kind = "DROPPED"
                elif all(ln in dead for ln in owners):
                    kind = "DEAD-RESULT"
                else:
                    kind = "LANDS"
                rows.append((cls.name, fn.name, p, kind))
                if kind != "LANDS":
                    findings.append("%s:%s(%s) -> %s  [%s:%d 读取落在死赋值行 %s]" % (
                        cls.name, fn.name, p, kind, os.path.basename(path),
                        fn.lineno, sorted(set(owners)) or "-"))
    return rows, findings


def self_test():
    """门自证（与 `scan_route_mount.py` / `scan_day_bounds.py` 同一口径）：
    两档合成源各验一次——「两类缺陷必须都咬住」与「什么都不改必须不判红」。
    后者是防idle-spin：判红清单为空既可能是干净，也可能是量具根本没在扫。
    """
    import tempfile
    ok = True
    broken = (
        "class InsightService:\n"
        "    def _tr(self, start, end, days=0):\n"
        "        return (start, end, days)\n"
        "    def _days_to_range(self, days, start, end):\n"
        "        return start, end\n"
        "    def broken_days(self, days=0, start='', end=''):\n"
        "        tr = self._tr(start, end)\n"
        "        start, end = self._days_to_range(days, start, end)\n"
        "        return tr\n"
        "    def broken_query(self, room='', query=''):\n"
        "        return self._tr('', '', days=1)\n"
        "    def fine(self, room=''):\n"
        "        return self._tr('', '', days=0) + room\n"
    )
    clean = (
        "class InsightService:\n"
        "    def _tr(self, start, end, days=0):\n"
        "        return (start, end, days)\n"
        "    def _push(self, tr, entity_id):\n"
        "        return entity_id\n"
        "    def good(self, days=0, start='', end='', query=''):\n"
        "        tr = self._tr(start, end, days=days)\n"
        "        return self._push(tr, query)\n"
    )
    want = {("InsightService", "broken_days", "days"): "DEAD-RESULT",
            ("InsightService", "broken_query", "room"): "DROPPED",
            ("InsightService", "broken_query", "query"): "DROPPED"}
    with tempfile.TemporaryDirectory() as tmp:
        for label, src in (("broken", broken), ("clean", clean)):
            path = os.path.join(tmp, label + ".py")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(src)
            rows, findings = scan_file(path)
            kinds = {(r[0], r[1], r[2]): r[3] for r in rows if r[3] != "LANDS"}
            if not rows:
                print("SELFTEST_MISS %s rows=0（一个形参都没扫到 = 量具失效）" % label)
                ok = False
            if label == "broken":
                for key, kind in sorted(want.items()):
                    if kinds.get(key) == kind:
                        print("SELFTEST_HIT %s -> %s" % (".".join(key), kind))
                    else:
                        print("SELFTEST_MISS %s want=%s got=%s" % (
                            ".".join(key), kind, kinds.get(key)))
                        ok = False
                for innocent in (("InsightService", "broken_days", "start"),
                                 ("InsightService", "broken_days", "end"),
                                 ("InsightService", "fine", "room")):
                    if innocent in kinds:
                        print("SELFTEST_FALSE %s -> %s" % (".".join(innocent), kinds[innocent]))
                        ok = False
            elif kinds:
                print("SELFTEST_FALSE clean 判红：%s" % sorted(kinds.items()))
                ok = False
            else:
                print("SELFTEST_HIT clean -> FINDINGS=0（rows=%d）" % len(rows))
    print("SELFTEST=%s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 2


def main(argv):
    if "--self-test" in argv:
        return self_test()
    strict = "--strict" in argv
    as_json = "--json" in argv
    files = [a for a in argv if not a.startswith("--")]
    files = files or [os.path.join(os.environ.get("QBR", "."), t) for t in DEFAULT_TARGETS]
    all_findings = []
    report = {}
    for path in files:
        if not os.path.exists(path):
            print("MISSING %s" % path)
            all_findings.append("MISSING %s" % path)
            continue
        rows, findings = scan_file(path)
        report[os.path.basename(path)] = {
            "methods_params": len(rows),
            "lands": sum(1 for r in rows if r[3] == "LANDS"),
            "dropped": sum(1 for r in rows if r[3] == "DROPPED"),
            "dead_result": sum(1 for r in rows if r[3] == "DEAD-RESULT"),
            "registered": sum(1 for r in rows if r[3] == "REGISTERED"),
        }
        all_findings += findings
    if as_json:
        print(json.dumps({"summary": report, "findings": all_findings},
                         ensure_ascii=False, indent=1))
    else:
        for name, s in report.items():
            print("%-22s params=%4d lands=%4d dropped=%2d dead_result=%2d "
                  "registered=%2d" % (name, s["methods_params"], s["lands"],
                                      s["dropped"], s["dead_result"], s["registered"]))
        print("---- 判红清单（DROPPED / DEAD-RESULT）----")
        for fnd in all_findings:
            print("  ! %s" % fnd)
        print("FINDINGS=%d" % len(all_findings))
    if strict and all_findings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
