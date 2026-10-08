"""Q6-2 四条门禁的变异自证（改源码 → 跑测试 → 回写还原 → 校验 RESTORED）。

每处变异都必须让至少一条锁判红；否则该锁是装饰。基线先跑，FAILED 名单做差。
"""
import subprocess
import sys

PY = r"C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe"
TARGET = "src/memory_agent/insights/repository.py"
TEST = "tests/test_vma_q62_daily_batch_scan.py"

MUTS = [
    ("M1 分批失活（n_days 恒 1 = 走旧单次查询）",
     b"            n_days = max(1, (d1 - d0).days + 1)",
     b"            n_days = 1"),
    ("M2 日配额判据漏咬（>= 改 >）",
     b"                if len(rows) >= day_limit:",
     b"                if len(rows) > day_limit:"),
    ("M3 日配额判据恒真（truncated 常亮）",
     b"                if len(rows) >= day_limit:",
     b"                if True:"),
    ("M4 分摊预算变整份预算（daily_limit 不再除 n_days）",
     b"        daily_limit = max(1, limit // n_days)",
     b"        daily_limit = max(1, limit)"),
    ("M5 截断位退回实例状态（跨线程串味）",
     b"        self._scan_state = threading.local()",
     b"        class _Shared:\n            pass\n        self._scan_state = _Shared()"),
]


def run_tests():
    p = subprocess.run(
        [PY, "-m", "pytest", TEST, "-q", "--no-header", "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=".", env={"JWT_SECRET": "ci-test",
                                                      "PATH": __import__("os").environ["PATH"],
                                                      "TEMP": __import__("os").environ.get("TEMP", "."),
                                                      "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "C:/Windows")},
    )
    failed = [ln.split("::")[1].split(" ")[0]
              for ln in p.stdout.splitlines() if ln.startswith("FAILED ")]
    tail = [ln for ln in p.stdout.splitlines() if " passed" in ln or " failed" in ln]
    return p.returncode, sorted(set(failed)), (tail[-1] if tail else p.stdout[-200:])


def read():
    with open(TARGET, "rb") as f:
        return f.read()


def write(data):
    with open(TARGET, "wb") as f:
        f.write(data)


orig = read()
rc, failed, line = run_tests()
print("BASELINE rc=%s %s failed=%s" % (rc, line, failed))
if rc != 0:
    print("基线不绿，终止（不在红基线上做变异）")
    sys.exit(2)

overall = 0
for label, old, new in MUTS:
    data = read()
    n = data.count(old)
    if n != 1:
        print("ABORT %s：锚点命中 %d 次（应为 1）" % (label, n))
        overall = 1
        break
    write(data.replace(old, new, 1))
    rc2, failed2, line2 = run_tests()
    restored_ok = None
    write(read().replace(new, old, 1))
    restored = read() == orig
    print("MUT %-52s rc=%s %s | red=%s | RESTORED=%s" % (
        label, rc2, line2, failed2, restored))
    if rc2 == 0 or not restored:
        overall = 1
    if not restored:
        write(orig)
        print("  已强制回写原始内容")

print("MUT_RC=%d" % overall)
