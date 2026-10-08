import hashlib
import os
import re
import subprocess

PY = r"C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe"
REPO = r"E:/NAS/memory-agent/src/memory_agent/insights/repository.py"
TEST = "tests/test_vma_a8_insights_clock_and_tags.py"

NEW = re.compile(rb"except \(TypeError, ValueError\) as exc:.*?tags = \[\]\n", re.S)
MUT = b"except (TypeError, ValueError):\n" + b" " * 16 + b"tags = []\n"


def read():
    with open(REPO, "rb") as fh:
        return fh.read()


def write(data):
    with open(REPO, "wb") as fh:
        fh.write(data)


def sha(data):
    return hashlib.sha256(data).hexdigest()[:8]


def run(label):
    env = dict(os.environ)
    env["PYTHONPATH"] = r"E:/NAS/memory-agent/src"
    proc = subprocess.run([PY, "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider"],
                          cwd=r"E:/NAS/memory-agent", capture_output=True, text=True, env=env)
    tail = [l for l in proc.stdout.splitlines() if " passed" in l or " failed" in l]
    print("%-20s RC=%d  %s" % (label, proc.returncode, tail[-1] if tail else proc.stdout[-200:]))
    return proc.returncode


good = read()
print("基线 sha=%s  锚点唯一=%s" % (sha(good), len(NEW.findall(good)) == 1))
print("基线（什么都不改）:", run("baseline"))
write(NEW.sub(MUT, good, count=1))
rc = run("M2 tags 静默回空")
write(good)                       # 原字节整份还原
print("RESTORED=%s  sha=%s" % (read() == good, sha(read())))
print("BITTEN_M2=%s" % (rc == 1))
print("末态复跑:", run("final"))
