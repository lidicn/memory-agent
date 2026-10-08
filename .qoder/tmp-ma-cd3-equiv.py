"""裁3 补充探针：这行代码有没有锁能看见？（预期是"看不见 = 行为等价"，不是红）

M10 去掉 `anon_sanitizer is not None` 判据：sanitizer 缺失时 `None(...)` 抛 TypeError，
被同一段 except 收成 None → 仍然不产 anon。两条路径出口一致，所以它不该有红；
真跑一遍确认"等价"是我读出来的还是实测出来的。
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_PACK = os.path.join(ROOT, "src", "memory_agent", "feedback_pack.py")
OLD = "        if trace_included and anon_sanitizer is not None:"
NEW = "        if trace_included:"


def run_tests() -> tuple[int, list[str]]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-m", "pytest", "tests/test_feedback_pack.py", "-q",
                        "--no-header", "-p", "no:cacheprovider"],
                       cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    failed = [ln.split("::", 1)[1].split(" ")[0] for ln in p.stdout.splitlines()
              if ln.startswith("FAILED")]
    return p.returncode, failed


with open(SRC_PACK, "rb") as fh:
    raw = fh.read()
text = raw.decode("utf-8")
assert text.count(OLD) == 1, f"锚点命中 {text.count(OLD)} 次"
with open(SRC_PACK, "wb") as fh:
    fh.write(text.replace(OLD, NEW).encode("utf-8"))
try:
    rc, failed = run_tests()
finally:
    with open(SRC_PACK, "wb") as fh:
        fh.write(raw)
with open(SRC_PACK, "rb") as fh:
    restored = fh.read() == raw
print(f"[M10 去掉 anon_sanitizer is not None 判据] RC={rc} red={len(failed)} {failed} "
      f"RESTORED={restored}")
print("结论：" + ("无锁可咬（行为等价，登记为不被测试观察）" if rc == 0
                else "有锁咬住了（上面列出的红）"))
