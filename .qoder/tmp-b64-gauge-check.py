"""量具改造后的牙齿自证：把 `/api/auth/status` 的卸载摘掉，新阈值必须判红。"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "src" / "memory_agent" / "api" / "auth_routes.py"
ORIG = F.read_bytes()
OLD = b'return ok({"initialized": await asyncio.to_thread(rt.auth.has_users)})'
NEW = b'return ok({"initialized": rt.auth.has_users()})'
assert ORIG.count(OLD) == 1, f"锚点命中 {ORIG.count(OLD)} 次，不是 1"
try:
    F.write_bytes(ORIG.replace(OLD, NEW))
    p = subprocess.run([sys.executable, "-m", "pytest",
                        "tests/test_vma_a3_p22_auth_loop_blocking.py", "-q"],
                       cwd=str(ROOT), capture_output=True, text=True)
    print("MUTATED_RC=%s" % p.returncode)
    print("TAIL=" + p.stdout.strip().splitlines()[-1])
    names = [l for l in p.stdout.splitlines() if l.startswith("FAILED")]
    print("FAILEDNAMES=" + "; ".join(names))
finally:
    F.write_bytes(ORIG)
print("RESTORED=" + ("OK" if F.read_bytes() == ORIG else "DIFF"))
