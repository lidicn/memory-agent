"""变异 A 单发，打出**具体哪几条**锁判红（上一轮只拿到计数，这里补名字）。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path("E:/NAS/memory-agent")
PY = r"C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe"
HIST = ROOT / "src/memory_agent/history.py"
T = "tests/test_vma_r8_chroma_error_masking.py"

pristine = HIST.read_bytes()
old = b"except httpx.HTTPStatusError as exc:"
n = pristine.count(old)
print("anchor_hits=", n)
if n != 1:
    sys.exit(3)
try:
    HIST.write_bytes(pristine.replace(old, b"except ZeroDivisionError as exc:", 1))
    p = subprocess.run([PY, "-m", "pytest", T, "-q", "-p", "no:cacheprovider", "--tb=no", "-rf"],
                       cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    for ln in (p.stdout or "").splitlines():
        if ln.startswith("FAILED") or "passed" in ln or "failed" in ln:
            print(ln)
    print("RC=", p.returncode)
finally:
    HIST.write_bytes(pristine)
    print("RESTORED=", HIST.read_bytes() == pristine)
