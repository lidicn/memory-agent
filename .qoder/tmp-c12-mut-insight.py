"""#42 insight_id 锁的变异自证：三个缺陷现场必须各自判红，复原后必须回绿。

M1 摘掉载荷里的 insight_id 键（"没发"）
M2 把 insight_id 做成 uuid4().hex（发了但无稳定身份 —— AF 去重空转）
M3 把日键换成 trace_id（稳定身份被随机数卷走 —— 同样空转）
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = r"C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe"
TARGET = os.path.join(ROOT, "src", "memory_agent", "vision_service.py")
TEST = os.path.join("tests", "test_vma_dcd_20261004_insight_id.py")

ORIGINAL = open(TARGET, encoding="utf-8").read()

MUTS = [
    ("M1", '                "insight_id": insight_id,\n', ''),
    ("M2", '        insight_id = _insight_id(dedup_session, dedup_type, ts_iso[:10])',
     '        insight_id = uuid.uuid4().hex'),
    ("M3", '        insight_id = _insight_id(dedup_session, dedup_type, ts_iso[:10])',
     '        insight_id = _insight_id(dedup_session, dedup_type, trace_id)'),
]


def run_suite():
    p = subprocess.run([PY, "-m", "pytest", TEST, "-q", "--tb=no"], cwd=ROOT,
                       capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    failed = sorted(l.split("::")[-1].split(" ")[0] for l in out.splitlines()
                    if l.startswith("FAILED"))
    last = [l for l in out.splitlines() if "passed" in l or "failed" in l or "error" in l]
    return p.returncode, (last[-1] if last else "?"), failed


rc, line, failed = run_suite()
print(f"BASELINE rc={rc} {line} FAILED={failed}")
if rc != 0:
    print("ABORT: baseline not green")
    sys.exit(2)

results = []
for name, anchor, repl in MUTS:
    n = ORIGINAL.count(anchor)
    if n != 1:
        print(f"ABORT {name}: anchor hits={n}")
        sys.exit(2)
    mutated = ORIGINAL.replace(anchor, repl, 1)
    if mutated == ORIGINAL:
        print(f"ABORT {name}: no-op mutation")
        sys.exit(2)
    open(TARGET, "w", encoding="utf-8", newline="").write(mutated)
    mrc, mline, mfailed = run_suite()
    results.append((name, mrc, mline, mfailed))
    print(f"{name} rc={mrc} {mline} FAILED={mfailed}")

open(TARGET, "w", encoding="utf-8", newline="").write(ORIGINAL)
print("RESTORED=%s" % (open(TARGET, encoding="utf-8").read() == ORIGINAL))
rc2, line2, failed2 = run_suite()
print(f"AFTER_RESTORE rc={rc2} {line2} FAILED={failed2}")
bitten = sum(1 for _, mrc, _, mf in results if mrc != 0 and mf)
print(f"BITTEN={bitten}/{len(results)}")
print("STILL_GREEN=%s" % [r[0] for r in results if r[1] == 0])
