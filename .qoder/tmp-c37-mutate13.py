# 变异自咬（run13 / 任务表 #62：Q-B 参数级落点）：把这一批的十二处零件逐个拆掉，
# 每条都必须被指定的锁咬住，跑完逐字还原并校验字节一致。
#
# 与 tmp-c37-mutate11.py 同一套形状：PY=sys.executable（容器 3.11 / 本机 3.13 都能跑），
# 根目录交给 MUT_ROOT，缺省用当前工作目录；「什么都不改」档由改动前的全绿读数充当。
#
# 十二条覆盖五个方向：
#   N1..N3 咬**被修的缺陷本身**（days 死赋值 / fail-closed 分支 / entity_id 下推）
#   N4..N6 咬**量具与下游零件**（元组目标展开 / 函数定义节点跳过 /
#         core._anomaly_report 的 entity_id 落点）——这两类是上一轮真实踩过的坑：
#         量具第一次写出来时元组目标没展开，`start, end = …` 整条看不见，门恒绿。
#         N6 首跑（本机 2026-10-06 03:0x）**存活**：第一跳的 spy core 看不见第二跳，
#         于是补了 test_anomaly_report_entity_id_lands_on_repo_scans 才咬住。
#   N7     咬第三跳（`nlquery` 的 anomaly 路由不交出实体集）——顺着 N6 暴露的口径逐跳
#         查下去抓到的现行，属同一族"收下但不往下传"。
#   N8..N9 咬**本批自己引入的那一格**：run13 首跑 SUITE_RC=1，是仓内门禁 `fake-ok-const`
#         判红了新 fail-closed 分支的字面量 `ok=True`（api.py:724）。改法是回读不变式，
#         而"改了要能被拆回来仍判红"才算满足：N8（字面量放回）由门禁那一格响，
#         N9（回读函数退化成空壳）由 test_vma_qb_param_landing 第 8 条响。
#         为此 TESTS 里加了 tests/test_quality_gates.py——不能门禁判过红、修完却把它挪出口径。
#   N10..N12 咬第四跳（报告文本面）与它的量具：`self.core.reports` 从未挂载（N10）、
#         扫描器退回只认一跳形状（N11）、深链的类型登记表变成摆设（N12）。
#         N11/N12 是对量具本身的要求——同一批里 N4/N5/N6 已经三次证明「锁停在某一层」
#         时缺陷会在下一层原样重演，而这一族的特殊之处是门**从来没绿错**：
#         改前读数「35 指向 / 0 空指向」是真的，那四条坏链一条都没进统计。
import os
import subprocess
import sys

ROOT = os.environ.get("MUT_ROOT", ".")
API = os.path.join(ROOT, "src/memory_agent/insights/api.py")
SVC = os.path.join(ROOT, "src/memory_agent/insights/service.py")
SCAN = os.path.join(ROOT, "scripts/scan_qb_param_landing.py")
ENG = os.path.join(ROOT, "scripts/scan_insights_engine_attrs.py")
NL = os.path.join(ROOT, "src/memory_agent/insights/nlquery.py")
TESTS = ("tests/test_vma_qb_param_landing.py "
         "tests/test_vma_insights_facade_dead_tools.py "
         # 深链的两把锁（成员存在性 + 量具自证）在这个文件里，N10..N12 靠它响。
         "tests/test_vma_insights_callsite_binding.py "
         # 仓内自带的 `fake-ok-const` 门禁就在 tests/test_quality_gates.py 里——本批那条红
         # 是它先报的（run13 首读 SUITE_RC=1）。N8 把字面量 `ok=True` 放回去时，
         # 「算出来的 ok」那把锁根本不会响（它只调纯函数），必须由这一格响，
         # 否则等于"门禁判过红、修完却把门禁挪出口径"。
         "tests/test_quality_gates.py").split()

MUTS = [
    ("N1 days 退回死赋值（改前现场：窗口悄悄用 default_days）", API,
     "        tr = self._tr(start, end, days=days)",
     "        tr = self._tr(start, end)\n        start, end = self._days_to_range(days, start, end)",
     0),
    ("N2 拆掉 query 解析不出时的 fail-closed（查不到设备→答全屋）", API,
     "        if query and not ids:", "        if False:", 0),
    ("N3 entity_id 不再下推（query 解析出来了但没交给 core）", API,
     '                    entity_id=",".join(ids))', '                    entity_id="")', 0),
    ("N4 量具退回「只认 ast.Name 目标」（元组赋值整条看不见）", SCAN,
     "            names = _flat_names(node.targets)",
     "            names = [t.id for t in node.targets if isinstance(t, ast.Name)]", 0),
    ("N5 量具把函数定义节点也算成读取点（死赋值判据永远打不中）", SCAN,
     "        if not isinstance(stmt, ast.stmt) or isinstance(\n"
     "                stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):",
     "        if not isinstance(stmt, ast.stmt):", 0),
    ("N6 core._anomaly_report 收了 entity_id 却不用（落点在第二处调用）", SVC,
     "        rooms, domains, entity_ids = self._filters(room, category, entity_id)",
     "        rooms, domains, entity_ids = self._filters(room, category)", 2),
    ("N7 NL 的 anomaly 路由不交出实体集（第三跳退回改前形状）", NL,
     '            data = self.service.anomaly_report(tr, room=plan.room, category=plan.category,\n'
     '                                               entity_id=",".join(plan.entity_ids))',
     '            data = self.service.anomaly_report(tr, room=plan.room, category=plan.category)',
     0),
    ("N8 fail-closed 的 ok 位退回字面量 True（仓内 fake-ok-const 门禁的正当满足被拆掉）", API,
     '                    "ok": self._closed_ok(anomalies, summary, filters),',
     '                    "ok": True,', 0),
    ("N9 _closed_ok 退化成空壳（写了个「算」字却没真的算）", API,
     '        return bool(filters.get("unresolved"))',
     '        return True', 0),
    ("N10 报告面的成员不挂载（`core.reports` 退回改前的不存在形状）", SVC,
     "        self.reports = ReportBuilder()",
     "        pass  # N10：四条 *_report 文本面指向不存在的成员", 0),
    ("N11 深链量具退回只认一跳（四条坏链再次不进统计）", ENG,
     "            hits.append((fn.name, call.lineno, parts[0], tuple(parts[1:])))",
     "            if len(parts) > 2:\n                continue  # N11\n"
     "            hits.append((fn.name, call.lineno, parts[0], tuple(parts[1:])))", 0),
    ("N12 深链登记表成摆设（链第二跳的类型不再核对）", ENG,
     '    ("core", "reports"): ("memory_agent.insights.report", "ReportBuilder"),',
     '    ("core", "nothing"): ("memory_agent.insights.report", "ReportBuilder"),', 0),
]

originals = {}
for path in (API, SVC, SCAN, ENG, NL):
    with open(path, "rb") as fh:
        originals[path] = fh.read()
texts = {k: v.decode("utf-8") for k, v in originals.items()}

bad = 0
# MUT_ONLY=N11 只跑指定的那几条：首跑（本机 20261006 20:0x）N11 把阈值写成 `len(parts) > 3`
# 而 `self` 不在 parts 里 ⇒ 那条守卫永不成立 ⇒ **空变异**（RC=0、34 passed），
# 重跑那一格就够，不必再花六分钟把十二条全过一遍。缺省跑全部。
only = os.environ.get("MUT_ONLY", "")
for name, path, old, new, nth in MUTS:
    if only and not any(name.split()[0].endswith(t) for t in only.split(",")):
        print("%s -> 按 MUT_ONLY 跳过" % name.split()[0])
        continue
    text = texts[path]
    hits = text.count(old)
    want = max(1, nth)
    if hits != want:
        print("%s -> 锚点命中 %d 次（期望 %d），变异未施加（自咬失败）" % (name, hits, want))
        bad += 1
        continue
    if nth >= 2:
        # 只替换第 nth 次出现：同串在文件里还有别的合法用处时，整串替换会误伤
        idx = -1
        for _ in range(nth):
            idx = text.index(old, idx + 1)
        mutated = (text[:idx] + new + text[idx + len(old):]).encode("utf-8")
    else:
        mutated = text.replace(old, new, 1).encode("utf-8")
    # 先算出字节再写：`open(p,'wb')` 会立刻截断，串在可能失败的表达式前面会清空原文件
    with open(path, "wb") as fh:
        fh.write(mutated)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [os.path.join(ROOT, "src"), os.environ.get("PYLIBS", "/tmp/pylibs"), ROOT]))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header",
                        "-p", "no:cacheprovider"] + TESTS,
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=ROOT, env=env)
    with open(path, "wb") as fh:
        fh.write(originals[path])
    texts[path] = originals[path].decode("utf-8")
    failed = [ln.split("::")[-1].split(" ")[0] for ln in r.stdout.splitlines()
              if ln.startswith("FAILED")]
    tail = [ln for ln in r.stdout.splitlines() if " passed" in ln or " failed" in ln]
    print("%s\n    RC=%d -> %s" % (name, r.returncode,
                                   "咬住了" if r.returncode else "没咬住（变异存活）"))
    print("    %s" % (tail[-1] if tail else "无汇总行"))
    for f in failed:
        print("      x %s" % f)
    if r.returncode == 0:
        bad += 1
    elif not failed:
        print("    异常：RC 非 0 但没有 FAILED 行（可能是收集期崩了）")
        bad += 1

restored = all(open(p, "rb").read() == originals[p] for p in originals)
print("restored_identical=%s" % ("OK" if restored else "FAIL"))
print("MUTATION_BAD=%d" % bad)
