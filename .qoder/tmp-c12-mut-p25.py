"""任务 #23 翻新的三条锁的变异自证：判据必须能咬现行引擎的源码。

M1 排除集不下发（`exclude_ids` 变空）⇒ "排除了个寂寞"
M2 区间取规则窗口而非会话首尾 ⇒ 全天误报回归
M3 信号时长门槛失效（`ok()` 不比 minutes）⇒ 30 秒 blip 也判学习
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = r"C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe"
TEST = os.path.join("tests", "test_signal_learning.py")
SVC = os.path.join(ROOT, "src", "memory_agent", "insights", "service.py")
ACT = os.path.join(ROOT, "src", "memory_agent", "insights", "activity.py")

SVC_ORIG = open(SVC, encoding="utf-8").read()
ACT_ORIG = open(ACT, encoding="utf-8").read()

MUTS = [
    ("M1", SVC, '        exclude_ids = list(exclusions["entity_ids"])',
     '        exclude_ids = []'),
    ("M2", ACT,
     '        first = min((metrics[i]["first"] for i in hit if metrics[i]["first"]), default=start)\n'
     '        last = max((metrics[i]["last"] for i in hit if metrics[i]["last"]), default=end)',
     '        first = start\n        last = end'),
    ("M3", ACT,
     '            return (met["minutes"] >= sig.min_minutes\n'
     '                    and met["count"] >= (sig.min_count if sig.min_count > 0 else 0))',
     '            return True'),
]

ORIG = {SVC: SVC_ORIG, ACT: ACT_ORIG}


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
if rc != 0 or "5 passed" not in line:
    print("ABORT: baseline 不是 5 passed")
    sys.exit(2)

results = []
for name, path, anchor, repl in MUTS:
    text = ORIG[path]
    n = text.count(anchor)
    if n != 1:
        print(f"ABORT {name}: anchor hits={n} in {os.path.basename(path)}")
        for p2, t2 in ORIG.items():
            open(p2, "w", encoding="utf-8", newline="").write(t2)
        sys.exit(2)
    mutated = text.replace(anchor, repl, 1)
    if mutated == text:
        print(f"ABORT {name}: no-op")
        sys.exit(2)
    open(path, "w", encoding="utf-8", newline="").write(mutated)
    mrc, mline, mfailed = run_suite()
    results.append((name, mrc, mline, mfailed))
    print(f"{name} rc={mrc} {mline} FAILED={mfailed}")
    open(path, "w", encoding="utf-8", newline="").write(text)
    print(f"  {name} 复原 RESTORED={open(path, encoding='utf-8').read() == text}")

rc2, line2, failed2 = run_suite()
print(f"AFTER_RESTORE rc={rc2} {line2} FAILED={failed2}")
print("BITTEN=%d/%d" % (sum(1 for r in results if r[1] != 0 and r[3]), len(results)))
print("STILL_GREEN=%s" % [r[0] for r in results if r[1] == 0])
print("MUT_RC=%d" % (0 if (rc2 == 0 and all(r[1] != 0 and r[3] for r in results)) else 1))
