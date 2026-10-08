"""变异自证：Q-B 六把过滤位 + 分页键 + fail-closed 的每一条锁都要咬到自己。

baseline（什么都不改）必须先跑绿；逐条变异跑 tests/test_vma_insights_search_filters.py，
记录「红了几条 / 红的用例名」，改回后校验文件字节与改前一致。
"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.join(ROOT, "src", "memory_agent", "insights", "api.py")
TEST = "tests/test_vma_insights_search_filters.py"
PY = r"C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe"

SEM = """        if not ids and any([category, domain, query]):
            ids = self.resolver.resolve_ids(room=room, category=category,
                                            query=query, domain=domain)"""
DOM = """        if category or domain or query:
            domains = self.domains_for(category=category, domain=domain, query=query) or None"""
LOAD = """        events = self.repo.load_events(tr, entity_ids=entities, rooms=rooms,
                                       domains=domains, behavior_only=behavior_only,
                                       states=states, order=direction)"""
CNT = """            return int(self.repo.count_events(tr, entity_ids=ids, rooms=rooms,
                                              domains=domains, states=states,
                                              behavior_only=behavior_only))"""
ECHO = '            "order": direction,'
SUML = "        if summarize:"
OPEN = "        if (category or domain or query) and not ids and not domains:"
KEYS = """        out["window"] = out.get("time_range") or window"""
NEXT = '        out["next_offset"] = (off + count) if out.get("has_more") else None'
TOTC = """        if counted is not None:
            out["total"] = counted
            out["has_more"] = off + count < counted"""
EXACT = "                             total_exact=(counted is not None),"
OKLINE = '        out["ok"] = self._envelope_ok(out, counted)'
OKTOT = "        return counted is None or int(counted) >= len(events)"
OKCNT = """        if int(out.get("count") or 0) != len(events):
            return False"""

MUTS = [
    ("M1 语义实体位失效", [(SEM, "        if False:\n" + SEM.split("\n", 1)[1])]),
    ("M2 domain 位失效", [(DOM, "        if False:\n" + DOM.split("\n", 1)[1])]),
    ("M3 state 位失效（扫描与计数两侧都不下推）",
     [(LOAD, LOAD.replace("states=states, ", "")),
      (CNT, CNT.replace("domains=domains, states=states,", "domains=domains,"))]),
    ("M4 order 位失效（恒 asc）", [(LOAD, LOAD.replace("order=direction)", 'order="asc")'))]),
    ("M5 回声与取数不同源", [(ECHO, '            "order": str(order or "asc"),')]),
    ("M6 summarize 位失效", [(SUML, "        if False:")]),
    ("M7 fail-open（解析不出回落成全量）", [(OPEN, "        if False:")]),
    ("M8 分页/窗口键退化（window/next_offset/ok 下架）",
     [(KEYS, ""), (NEXT, ""), (OKLINE, "")]),
    ("M9 total 被切片行数冒充", [(TOTC, "        if False:\n            pass")]),
    ("M10 total_exact 说谎", [(EXACT, "                             total_exact=False,")]),
    ("M11 ok 恒真（total 与本页矛盾也报 ok）", [(OKTOT, "        return True")]),
    ("M12 ok 不看 count 与样本是否对得上", [(OKCNT, "        pass")]),
]


def read(path):
    with io.open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def run():
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"))
    p = subprocess.run([PY, "-m", "pytest", TEST, "-q", "--no-header",
                        "-p", "no:cacheprovider"], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    tail = [ln for ln in (p.stdout or "").splitlines()
            if " passed" in ln or " failed" in ln or "error" in ln]
    failed = sorted({ln.split("::")[-1].split(" ")[0] for ln in (p.stdout or "").splitlines()
                     if ln.startswith("FAILED")})
    return p.returncode, (tail[-1] if tail else "no-summary"), failed


def apply_mut(text, steps):
    for old, new in steps:
        if text.count(old) != 1:
            return None, "定位不唯一(命中 %d 次)" % text.count(old)
        text = text.replace(old, new)
    return text, ""


original = read(API)
base_rc, base_line, base_failed = run()
print("BASELINE(什么都不改): %s RC=%s FAILED=%s" % (base_line, base_rc, base_failed))

bitten = 0
skipped = []
for name, steps in MUTS:
    mutated, why = apply_mut(original, steps)
    if mutated is None:
        skipped.append((name, why))
        print("%-46s SKIP %s" % (name, why))
        continue
    write(API, mutated)
    rc, line, failed = run()
    write(API, original)
    ok = read(API) == original
    mark = "咬到" if (rc != 0 and failed and ok) else "未咬到"
    if rc != 0 and failed and ok:
        bitten += 1
    print("%-46s %s %s RESTORED=%s" % (name, mark, line, ok))
    print("      红(%d): %s" % (len(failed), ", ".join(failed)))

print("BITTEN=%d/%d SKIPPED=%d" % (bitten, len(MUTS), len(skipped)))
write(API, original)
print("RESTORED_FINAL=%s" % (read(API) == original))
sys.stdout.flush()
