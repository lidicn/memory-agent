"""`split_days` 的时区变异红证：本机（+8）等价位判不出红，容器（UTC）必须判红。

改源 → 只跑那一把锁 → 复原 → 校验字节全等。锚点必须恰好命中 1 次，否则 ABORT。
路径与运行目录用环境变量传入，本机与容器跑同一份脚本：
    MUT_ROOT=/tmp/c6snap20261004a                # 容器快照
    MUT_ROOT=E:/NAS/memory-agent                 # 本机（斜杠路径）
"""
import os
import subprocess
import sys

ROOT = os.environ.get("MUT_ROOT", "/tmp/c6snap20261004a")
SRC_SUB = os.environ.get("MUT_SRC", "src")
P = os.path.join(ROOT, SRC_SUB, "memory_agent", "insights", "models.py")
A = ('        cur = house_dt(self.start_ts).replace(hour=0, minute=0, second=0, microsecond=0)\n'
     '        while house_ts(cur) < self.end_ts:\n'
     '            nxt = cur + timedelta(days=1)\n'
     '            s, e = self.clip(house_ts(cur), house_ts(nxt))')
B = ('        cur = datetime.fromtimestamp(self.start_ts).replace(hour=0, minute=0, second=0, microsecond=0)\n'
     '        while cur.timestamp() < self.end_ts:\n'
     '            nxt = cur + timedelta(days=1)\n'
     '            s, e = self.clip(cur.timestamp(), nxt.timestamp())')
NODE = ("tests/test_vma_activity_semantic.py::"
        "test_split_days_cuts_at_house_wall_clock_midnight")

env = dict(os.environ)
env["PYTHONPATH"] = os.pathsep.join(
    [ROOT, os.path.join(ROOT, SRC_SUB), env.get("PYTHONPATH", "")])
env.setdefault("JWT_SECRET", "ci-test")

print("root:", ROOT, "| python:", sys.executable)
print("machine date:", subprocess.run(["date"], capture_output=True, text=True).stdout.strip()
      if os.name != "nt" else __import__("time").strftime("%Z %z"))

raw = open(P, "rb").read()
text = raw.decode("utf-8")
hits = text.count(A)
if hits != 1:
    print("ABORT ANCHOR_HITS=%d" % hits)
    sys.exit(2)
open(P, "wb").write(text.replace(A, B).encode("utf-8"))
r = subprocess.run([sys.executable, "-m", "pytest", NODE, "-q", "--no-header",
                    "-p", "no:cacheprovider"], capture_output=True, text=True,
                   cwd=ROOT, env=env)
open(P, "wb").write(raw)
restored = open(P, "rb").read() == raw
print("last_line:", (r.stdout or "").strip().splitlines()[-1:])
print("MUT_RC=%s RESTORED=%s" % (r.returncode, restored))
