"""MA-裁1 Q1 新锁的变异红证：每条变异必须咬到自己那把锁，改完立刻还原。"""
import subprocess
import sys
from pathlib import Path

PY = r"C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe"
ROOT = Path(r"E:/NAS/memory-agent")

MUTATIONS = [
    ("M1 晋升不再写冷却值（退回 add_rule 形参默认）",
     "src/memory_agent/rule_lifecycle.py",
     "            cooldown_seconds=gate[\"cooldown_seconds\"],\n",
     "",
     "tests/test_rule_cooldown.py"),
    ("M2 取消「缺省即拒」判据",
     "src/memory_agent/rule_lifecycle.py",
     "        elif cooldown is None:\n            blockers.append(COOLDOWN_UNSET_BLOCKER)\n",
     "",
     "tests/test_rule_cooldown.py"),
    ("M3 参数值不回写候选行",
     "src/memory_agent/rule_lifecycle.py",
     "        if gate.get(\"cooldown_source\") == \"argument\":\n"
     "            self.store.set_candidate_rule_cooldown(candidate_id, gate[\"cooldown_seconds\"])\n",
     "",
     "tests/test_rule_cooldown.py"),
    ("M4 坏值不再判拒（当成未设定）",
     "src/memory_agent/rule_lifecycle.py",
     "        if explicit_why or stored_why:\n"
     "            blockers.append(explicit_why or stored_why)\n",
     "        if False:\n            pass\n",
     "tests/test_rule_cooldown.py"),
]


def run(tests):
    import os
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([PY, "-m", "pytest", tests, "-q", "-rf"],
                       cwd=ROOT, capture_output=True, text=True, env=env)
    return r.returncode, r.stdout[-2500:]


def failed_names(out):
    return sorted(ln.split(" ")[1].split(" -")[0] for ln in out.splitlines()
                  if ln.startswith("FAILED"))


def summary(out):
    return [ln for ln in out.splitlines() if " passed" in ln or " failed" in ln][-1:]


base_rc, base_out = run("tests/test_rule_cooldown.py")
print(f"[基线 未变异] RC={base_rc} 摘要={summary(base_out)} 红={failed_names(base_out)}")

for label, rel, anchor, repl, tests in MUTATIONS:
    path = ROOT / rel
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    hits = text.count(anchor)
    if hits != 1:
        print(f"[{label}] ANCHOR_HITS={hits} -> SKIP(rc=3)")
        sys.exit(3)
    path.write_bytes(text.replace(anchor, repl, 1).encode("utf-8"))
    rc, out = run(tests)
    path.write_bytes(raw)
    restored = path.read_bytes() == raw
    red = [n for n in failed_names(out) if n not in failed_names(base_out)]
    print(f"[{label}] RC={rc} RESTORED={restored} 摘要={summary(out)}")
    for ln in red:
        print("    新红:", ln)
print("DONE")
