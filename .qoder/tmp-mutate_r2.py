import subprocess
import sys

P = "/tmp/vs20/scripts/pii_backfill_r2.py"
GOOD = open(P, encoding="utf-8").read()

MUTS = [
    ("窗口门去掉", "if not args.window_ok:", "if False:"),
    ("备份步骤跳过", "target, sha = make_backup(store.db_path)", "target, sha = ('none', 'n/a')"),
    ("验证步假报干净", "leftover = count_leftover(conn, roster_names(conn))", "leftover = 0"),
]


def run():
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_vma_r2_pii_backfill.py", "-q"],
                       cwd="/tmp/vs20", capture_output=True, text=True)
    tail = [ln for ln in r.stdout.splitlines() if ln.strip()][-1]
    return r.returncode, tail


for name, old, new in MUTS:
    assert old in GOOD, name
    open(P, "w", encoding="utf-8").write(GOOD.replace(old, new))
    rc, tail = run()
    print(f"MUT[{name}] rc={rc} :: {tail}")

open(P, "w", encoding="utf-8").write(GOOD)
rc, tail = run()
print(f"RESTORED rc={rc} :: {tail}")
