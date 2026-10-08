"""裁5 追加 Q-A 的变异台账：每条变异必须咬到自己那把锁，跑完逐条还原并核验。"""
import os
import subprocess
import sys

ROOT = os.environ.get("MUT_ROOT") or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.environ.get("MUT_SRC") or os.path.join(ROOT, "src")
PY = sys.executable

STORE = os.path.join(SRC, "memory_agent", "store.py")
API = os.path.join(SRC, "memory_agent", "insights", "api.py")
TARGETS = ("tests/test_vma_dcd_20261004b_event_total.py "
           "tests/test_vma_insights_callsite_binding.py tests/test_vma120_scene_graph.py")

MUTS = [
    ("M1 member 退回 LIMIT 之后的 Python 过滤", STORE,
     '''        if member:
            conds.append(
                "EXISTS (SELECT 1 FROM json_each("''',
     '''        if False and member:
            conds.append(
                "EXISTS (SELECT 1 FROM json_each("'''),
    ("M2 字符串元素的 type 写成 string（生产库实测的漏法）", STORE,
     " OR (j.type='text' AND j.value=?))",
     " OR (j.type='string' AND j.value=?))"),
    ("M3 计数查询带上 LIMIT（不再是全量）", STORE,
     '        sql = "SELECT COUNT(*) FROM behavior_events"',
     '        sql = "SELECT COUNT(*) FROM behavior_events" + " LIMIT 3"'),
    ("M4 精确等值换成子串 LIKE", STORE,
     "WHERE (j.type='object' AND json_extract(j.value,'$.name')=?)",
     "WHERE (j.type='object' AND json_extract(j.value,'$.name') LIKE '%'||?||'%')"),
    ("M5 去掉 json_valid 护栏（脏数据把查询带崩）", STORE,
     '"CASE WHEN json_valid(behavior_events.persons_json)=1 "',
     '"CASE WHEN 1=1 "'),
    ("M6 门面把 total 冒充成本页条数", API,
     '                out["total"] = self._behavior_event_total(out)',
     '                out["total"] = out.get("count")'),
    ("M7 _search 不覆盖切片 total", API,
     "            out[\"total\"] = counted\n",
     "            out[\"slice_total\"] = counted\n"),
    ("M8 annotate_scan 忽略 total_exact 入参（沿用切片推断）", API,
     '    payload["total_exact"] = (not truncated) if total_exact is None else bool(total_exact)',
     '    payload["total_exact"] = not truncated'),
    ("M9 计数失败时退回 count 而不是 None", API,
     "            LOG.warning(\"count_behavior_events（行为事件总数）失败: %s\", exc)\n            return None",
     "            LOG.warning(\"count_behavior_events（行为事件总数）失败: %s\", exc)\n            return 0"),
]


def run(files):
    env = dict(os.environ, PYTHONPATH=SRC)
    p = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"] + files.split(),
                       cwd=ROOT, env=env, capture_output=True, text=True)
    lines = [l for l in p.stdout.splitlines() if l.startswith("FAILED")]
    return p.stdout[-400:], sorted({l.split("::")[0].split()[-1] for l in lines}), lines


def counts(out):
    for token in ("passed", "failed", "error"):
        for chunk in out.replace("=", " ").split():
            pass
    import re
    m = re.search(r"(\d+) failed", out)
    f = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) passed", out)
    return f, int(m.group(1)) if m else 0


print("baseline ...")
tail, failed, raw = run(TARGETS)
bf, bp = counts(tail)
print("BASELINE failed=%d passed=%d FAILED=%s" % (bf, bp, failed))
if bf:
    print(tail)
    print("ABORT：基线不绿，变异读数无意义")
    sys.exit(2)

results = []
reds = []
for name, path, old, new in MUTS:
    data = open(path, encoding="utf-8", newline="").read()
    hits = data.count(old)
    if hits != 1:
        results.append((name, "ANCHOR!=1(%d)" % hits, "-"))
        reds.append(False)
        print("%s -> ABORT 锚点命中 %d 次" % (name, hits))
        continue
    open(path, "w", encoding="utf-8", newline="").write(data.replace(old, new))
    tail, failed, _ = run(TARGETS)
    f, p = counts(tail)
    open(path, "w", encoding="utf-8", newline="").write(data)
    restored = open(path, encoding="utf-8", newline="").read() == data
    results.append((name, "%d failed / %d passed" % (f, p),
                   ",".join(os.path.basename(x) for x in failed) or "-"))
    reds.append(bool(f > 0 and restored))
    print("%-52s RED=%s RESTORED=%s 红在: %s" % (name, f > 0, restored,
                                                ",".join(failed) or "-"))
    if f == 0:
        print("  !! 该变异没咬到任何锁：", tail[-300:])
    if not restored:
        print("  !! 还原核验失败（文件与变异前不一致）:", path)

print("\n汇总")
for r in results:
    print("  %s | %s | %s" % r)
print("BITTEN=%d/%d" % (sum(1 for x in reds if x), len(reds)))
tail, failed, _ = run(TARGETS)
print("RESTORED_FINAL=%s 末态=%s" % (not failed, counts(tail)))
print("MUT_RC=%d" % (0 if all(reds) and not failed else 1))
