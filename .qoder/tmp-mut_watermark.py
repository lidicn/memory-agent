"""变异证红：把 device_feed 的水位线改回缺陷形态，确认新锁各自判红、且只红自己那一条。

A) 整段退回 `self._watermark = end_iso`（旧缺陷：两端闭区间 + 截断即跳尾）
B) 只去掉截断续读分支（读完才推 end+1s，没读完也推 end+1s）
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"E:/NAS/memory-agent")
TARGET = ROOT / "src" / "memory_agent" / "device_feed.py"
PY = r"C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe"

PRISTINE = TARGET.read_bytes()

OLD_BLOCK = b'''        if stats["truncated"]:
            resume_at = parse_wall(str(rows[-1].get("ts") or "")) if rows else None
            stats["pending_tail"] = True
        else:
            resume_at = parse_wall(end_iso)
            stats["pending_tail"] = False
        if resume_at is not None:
            self._watermark = (resume_at + timedelta(seconds=1)).isoformat(
                timespec="seconds", sep="T")
'''

MUT_A = b'''        self._watermark = end_iso
'''

MUT_B = b'''        resume_at = parse_wall(end_iso)
        stats["pending_tail"] = bool(stats["truncated"])
        if resume_at is not None:
            self._watermark = (resume_at + timedelta(seconds=1)).isoformat(
                timespec="seconds", sep="T")
'''


def run(label: str, mutated: bytes) -> int:
    hits = PRISTINE.count(OLD_BLOCK)
    if hits != 1:
        print(f"[{label}] 锚点命中 {hits} 次，不是 1 次 —— 拒绝把空变异当红证")
        return 3
    new = PRISTINE.replace(OLD_BLOCK, mutated)
    if new == PRISTINE:
        print(f"[{label}] 变异没有改动任何字节（空变异）")
        return 3
    TARGET.write_bytes(new)
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["GATES_REQUIRE"] = "1"
    try:
        proc = subprocess.run(
            [PY, "-m", "pytest", "tests/test_device_event_feed.py", "-q",
             "-p", "no:cacheprovider", "--no-header", "-rf"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=600, env=env)
    finally:
        TARGET.write_bytes(PRISTINE)
    tail = [ln for ln in proc.stdout.splitlines() if ln.startswith(("FAILED", "assert"))][:12]
    summary = [ln for ln in proc.stdout.splitlines() if " passed" in ln or " failed" in ln][-1:]
    print(f"[{label}] RC={proc.returncode} {summary}")
    for ln in tail:
        print("   ", ln)
    return proc.returncode


code_a = run("A 退回 end_iso（旧缺陷形态）", MUT_A)
code_b = run("B 去掉截断续读", MUT_B)
restored = TARGET.read_bytes() == PRISTINE
print(f"RESTORED={restored}")
sys.exit(0 if (code_a != 0 and code_b != 0 and restored) else 1)
