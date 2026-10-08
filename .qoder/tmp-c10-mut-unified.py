"""变异自证：把 _counts 从「一条语句读快照」拆回「八条独立 COUNT」。

期望：test_count_contract_survives_writes_between_reads 判红（c9 假红的形状回来了）。
纪律：字节级替换、锚点必须恰好命中 1 次、先跑基线、还原后校验 RESTORED=True。
"""
import subprocess
import sys

P = r"E:\NAS\memory-agent\tests\test_unified_view.py"
PY = r"C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe"

ANCHOR = b"    row = conn.execute(_COUNTS_SQL).fetchone()\n"
MUTANT = (
    b"    row = (\n"
    b"        conn.execute(\"SELECT COUNT(*) FROM unified_events\").fetchone()[0],\n"
    b"        conn.execute(\"SELECT COUNT(*) FROM events\").fetchone()[0],\n"
    b"        conn.execute(\"SELECT COUNT(*) FROM behavior_events WHERE status='ok'\").fetchone()[0],\n"
    b"        conn.execute(\"SELECT COUNT(*) FROM perception_events\").fetchone()[0],\n"
    b"        conn.execute(\"SELECT COUNT(*) FROM unified_events WHERE source='device'\").fetchone()[0],\n"
    b"        conn.execute(\"SELECT COUNT(*) FROM unified_events WHERE source='vision'\").fetchone()[0],\n"
    b"        conn.execute(\"SELECT COUNT(*) FROM unified_events WHERE source='perception'\").fetchone()[0],\n"
    b"        conn.execute(\"SELECT COUNT(*) FROM unified_events WHERE source NOT IN ('device','vision','perception')\").fetchone()[0],\n"
    b"    )\n"
)

original = open(P, "rb").read()
hits = original.count(ANCHOR)
print("ANCHOR_HITS=%d" % hits)
if hits != 1:
    print("ABORT: 锚点命中次数不是 1，未触碰源文件")
    sys.exit(2)


def run(label):
    r = subprocess.run(
        [PY, "-m", "pytest", "tests/test_unified_view.py", "-q", "--no-header", "-rN"],
        cwd=r"E:\NAS\memory-agent", capture_output=True, text=True,
    )
    tail = [ln for ln in (r.stdout + r.stderr).splitlines() if "passed" in ln or "failed" in ln or "error" in ln]
    print("%s RC=%d | %s" % (label, r.returncode, tail[-1] if tail else "(no summary line)"))
    return r.returncode


print("── 基线")
base = run("BASELINE")
if base != 0:
    print("ABORT: 基线不绿，变异结论无意义")
    sys.exit(2)

print("── 变异 M1（拆成八条独立 COUNT）")
mutated = original.replace(ANCHOR, MUTANT)
if mutated == original:
    print("ABORT: 替换后字节与原文相同，变异未生效")
    sys.exit(2)
open(P, "wb").write(mutated)
try:
    mut = run("MUTANT")
finally:
    print("── 还原")
    open(P, "wb").write(original)
    now = open(P, "rb").read()
    print("RESTORED=%s" % (now == original))
print("MUTANT_RED=%s" % (mut != 0))
