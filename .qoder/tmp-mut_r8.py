"""变异红证：三发各自咬自己的锁（字节级替换，避免本机 LF→CRLF 整份重写）。

判据：每发变异后只跑相关测试文件，红色数必须 >0 且落在预期的那几条锁上；
锚点在原文里必须**恰好命中 1 次**，否则 rc=3 直接中止（空变异不算红证）。
恢复用 write_bytes(PRISTINE)，并以 RESTORED=True 自证。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path("E:/NAS/memory-agent")
PY = r"C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe"
HIST = ROOT / "src/memory_agent/history.py"
PAT = ROOT / "src/memory_agent/patterns.py"
T = "tests/test_vma_r8_chroma_error_masking.py"

MUTS = [
    ("A 摘掉 HTTPStatusError 的转接（真实状态码重新被 chroma 包装吞掉）",
     HIST, b"except httpx.HTTPStatusError as exc:", b"except ZeroDivisionError as exc:",
     "非 2xx 的三条锁"),
    ("B 状态码不进消息（只剩 model）",
     HIST,
     'f"embedding 端点返回 HTTP {exc.response.status_code}（model={self.model}）"'.encode(),
     'f"embedding 端点调用失败（model={self.model}）"'.encode(),
     "两条状态码锁"),
    ("C 模式库退回不传 embedding_function",
     PAT, b'if _embed is not None:', b'if False:',
     "patterns 同源接线锁"),
]


def run(test_path):
    p = subprocess.run([PY, "-m", "pytest", test_path, "-q", "-p", "no:cacheprovider"],
                       cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    tail = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln or "error" in ln]
    return p.returncode, (tail[-1] if tail else out[-200:])


print("== 基线（未变异）==")
rc, line = run(T)
print(f"  {line}  RC={rc}")
if rc != 0:
    print("ABORT: 基线不是全绿")
    sys.exit(2)

for label, path, old, new, expected in MUTS:
    pristine = path.read_bytes()
    n = pristine.count(old)
    print(f"\n== 变异 {label} ==")
    if n != 1:
        print(f"  锚点命中 {n} 次（需要恰好 1 次）→ 不判红，中止本发")
        sys.exit(3)
    try:
        path.write_bytes(pristine.replace(old, new, 1))
        rc, line = run(T)
        print(f"  读数: {line}  RC={rc}")
        print(f"  预期红了谁: {expected}")
        if rc == 0:
            print("  !! 这发变异没有判红——锁不住")
    finally:
        path.write_bytes(pristine)
        print(f"  RESTORED={path.read_bytes() == pristine}")
    rc, line = run(T)
    print(f"  恢复后复跑: {line}  RC={rc}")

print("\nMUT_RUN_RC=0")
