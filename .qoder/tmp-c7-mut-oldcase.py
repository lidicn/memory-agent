"""DCD 20261004 裁6 补裁 §二.3 的现读：老回归用例有没有仍在替缺陷站岗。

做法：把「规则标签条件」摘掉（还原成缺陷现场），跑整个 `test_vma_activity_semantic.py`，
对比 FAILED 名单。判读口径：
  ① 新语义锁 `test_rule_tag_condition_filters_events_instead_of_being_decorative` 必须判红；
  ② 曾被冻结的那条 `test_disabled_rule_rows_are_not_applied` 若在缺陷现场**仍然绿**，
     说明它的「不判」来自 `enabled=0` 而不是标签缺陷 —— 缺陷不再被它当成期望行为。
改源 → 跑 → 回写 → 校验字节全等；锚点命中数≠1 即 ABORT。
"""
import os
import re
import subprocess
import sys

ROOT = os.environ.get("MUT_ROOT", "E:/NAS/memory-agent")
SRC_SUB = os.environ.get("MUT_SRC", "src")
P = os.path.join(ROOT, SRC_SUB, "memory_agent", "insights", "activity.py")
FILE = "tests/test_vma_activity_semantic.py"
A = ('        wanted = {str(t).strip() for t in (rule.require_tags or []) if str(t).strip()}\n'
     '        if wanted:\n'
     '            # 任一声明标签命中即计入（legacy 的 `set(rule.tags) & set(e.tags)` 口径）\n'
     '            info = [e for e in info if tags_of(e.entity_id, e.label) & wanted]\n')

env = dict(os.environ)
env["PYTHONPATH"] = os.pathsep.join([ROOT, os.path.join(ROOT, SRC_SUB), env.get("PYTHONPATH", "")])
env.setdefault("JWT_SECRET", "ci-test")


def run():
    r = subprocess.run([sys.executable, "-m", "pytest", FILE, "-q", "--no-header", "-rfb",
                        "-p", "no:cacheprovider"], capture_output=True, text=True,
                       cwd=ROOT, env=env)
    out = (r.stdout or "") + (r.stderr or "")
    failed = sorted(set(re.findall(r"^FAILED [^:]+::(\S+)", out, re.M)))
    tail = [l for l in out.splitlines() if re.search(r"\d+ (passed|failed)", l)]
    return failed, (tail[-1] if tail else "NO_SUMMARY"), r.returncode


b_failed, b_tail, b_rc = run()
print("BASELINE failed=%s | %s | rc=%s" % (b_failed, b_tail, b_rc))

raw = open(P, "rb").read()
text = raw.decode("utf-8")
hits = text.count(A)
if hits != 1:
    print("ABORT ANCHOR_HITS=%d" % hits)
    sys.exit(2)
open(P, "wb").write(text.replace(A, "").encode("utf-8"))
failed, tail, rc = run()
open(P, "wb").write(raw)
restored = open(P, "rb").read() == raw

print("缺陷现场: %s | rc=%s" % (tail, rc))
print("缺陷现场 FAILED=%s" % failed)
print("锁判红=%s" % ("test_rule_tag_condition_filters_events_instead_of_being_decorative" in failed))
print("老用例仍在站岗=%s" % ("test_disabled_rule_rows_are_not_applied" in failed))
f_failed, f_tail, f_rc = run()
print("RESTORED=%s 末态 rc=%s | %s | FAILED=%s" % (restored, f_rc, f_tail, f_failed))
sys.exit(0 if restored and not f_failed else 1)
