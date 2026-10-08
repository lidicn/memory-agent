"""变异证红：把 _sanitize_pii 的三条规则逐条退回旧形状，确认新锁各自判红。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"E:/NAS/memory-agent")
TARGET = ROOT / "src" / "memory_agent" / "store.py"
PY = r"C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe"
PRISTINE = TARGET.read_bytes()

NEW_PHONE = b'        text = re.sub(r"(?<!\\d)1[3-9]\\d[\\s-]?\\d{4}[\\s-]?\\d{4}(?!\\d)", "****", text)'
OLD_PHONE = b'        text = re.sub(r"(1[3-9]\\d)\\d{4}(\\d{4})", r"****", text)'
NEW_EMAIL = b'        text = re.sub(r"[A-Za-z0-9._%+-]+@", "***@", text)'
OLD_EMAIL = b'        text = re.sub(r"([\\w.]{2})[\\w.]*@", r"***@", text)'
NEW_ID = (b'        text = re.sub(r"(?<!\\d)[1-9]\\d{5}(?:19|20)\\d{2}(?:0[1-9]|1[0-2])"\n'
          b'                      r"(?:0[1-9]|[12]\\d|3[01])\\d{3}[\\dXx](?!\\d)", "********", text)')
OLD_ID = b'        text = re.sub(r"(\\d{6})\\d{8}(\\d{4})", r"********", text)'

MUTS = [("A 手机号规则退回无边界守卫", OLD_PHONE, NEW_PHONE),
        ("B 邮箱规则退回 \\w 贪婪写法", OLD_EMAIL, NEW_EMAIL),
        ("C 身份证规则退回裸 18 位数字", OLD_ID, NEW_ID)]


def run(label, mutated, original):
    hits = PRISTINE.count(original)
    if hits != 1:
        print(f"[{label}] 锚点命中 {hits} 次，不是 1 次 —— 拒绝把空变异当红证")
        return 3
    new = PRISTINE.replace(original, mutated)
    if new == PRISTINE:
        print(f"[{label}] 空变异")
        return 3
    TARGET.write_bytes(new)
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["GATES_REQUIRE"] = "1"
    try:
        proc = subprocess.run(
            [PY, "-m", "pytest", "tests/test_vma121_intent_and_pii.py",
             "tests/test_vma_r2_pii_backfill.py", "-q", "-p", "no:cacheprovider",
             "--no-header", "-rf"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=900, env=env)
    finally:
        TARGET.write_bytes(PRISTINE)
    lines = proc.stdout.splitlines()
    summary = [ln for ln in lines if " passed" in ln or " failed" in ln][-1:]
    print(f"[{label}] RC={proc.returncode} {summary}")
    for ln in lines:
        if ln.startswith("FAILED"):
            print("   ", ln)
    return proc.returncode


codes = [run(*m) for m in MUTS]
restored = TARGET.read_bytes() == PRISTINE
print(f"RESTORED={restored}")
sys.exit(0 if all(c != 0 for c in codes) and restored else 1)
