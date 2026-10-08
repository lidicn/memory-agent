"""「扫描上限自报」这把锁的变异红证：摘掉 rule_sources 的三个新键，锁必须判红。"""
import os
import subprocess
import sys

ROOT = os.environ.get("MUT_ROOT", "E:/NAS/memory-agent")
SRC_SUB = os.environ.get("MUT_SRC", "src")
P = os.path.join(ROOT, SRC_SUB, "memory_agent", "insights", "service.py")
A = ('            "events_total": events_total,\n'
     '            "scan_limit": scan_limit,\n')
NODE = ("tests/test_vma_activity_semantic.py::"
        "test_rule_sources_reports_the_scan_cap_that_limits_the_semantic_side")

env = dict(os.environ)
env["PYTHONPATH"] = os.pathsep.join(
    [ROOT, os.path.join(ROOT, SRC_SUB), env.get("PYTHONPATH", "")])
env.setdefault("JWT_SECRET", "ci-test")

print("root:", ROOT, "| python:", sys.executable)
raw = open(P, "rb").read()
text = raw.decode("utf-8")
hits = text.count(A)
if hits != 1:
    print("ABORT ANCHOR_HITS=%d" % hits)
    sys.exit(2)
open(P, "wb").write(text.replace(A, "").encode("utf-8"))
r = subprocess.run([sys.executable, "-m", "pytest", NODE, "-q", "--no-header",
                    "-p", "no:cacheprovider"], capture_output=True, text=True,
                   cwd=ROOT, env=env)
open(P, "wb").write(raw)
restored = open(P, "rb").read() == raw
print("last_lines:", [l for l in (r.stdout or "").splitlines() if l.strip()][-3:])
print("MUT_RC=%s RESTORED=%s" % (r.returncode, restored))
