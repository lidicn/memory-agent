import hashlib
import subprocess
import sys

PY = r"C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe"
MODELS = r"E:/NAS/memory-agent/src/memory_agent/insights/models.py"
REPO = r"E:/NAS/memory-agent/src/memory_agent/insights/repository.py"
TEST = "tests/test_vma_a8_insights_clock_and_tags.py"

FIXED_DT = b"        return house_dt(float(self.ts))"
OLD_DT = b"        return datetime.fromtimestamp(float(self.ts))"
LOG_OK = b'                    self.log.warning("activity_rules rule_id=%s \xe7\x9a\x84 tags_json'
# repository 变异：把整段 except 体退回成只有 tags = []
MUT_REPO_NEW = (
    b"                except (TypeError, ValueError) as exc:\n"
    b"                    self.log.warning(\"activity_rules rule_id=%s \xe7\x9a\x84 tags_json \xe8\xa7\xa3\xe6\x9e\x90\xe5\xa4\xb1\xe8\xb4\xa5(%s)\xef\xbc\x8c\"\n"
    b"                                     \"\xe6\x9c\xac\xe6\x9d\xa1\xe8\xa7\x84\xe5\x88\x99\xe6\x8c\x89\xe6\x97\xa0\xe6\xa0\x87\xe7\xad\xbe\xe8\xbf\x90\xe8\xa1\x8c\",\n"
    b"                                     r.get(\"rule_id\"), type(exc).__name__)\n"
    b"                    tags = []\n")
MUT_REPO_OLD = b"                except (TypeError, ValueError):\n                    tags = []\n"


def read(p):
    with open(p, "rb") as fh:
        return fh.read()


def write(p, data):
    with open(p, "wb") as fh:
        fh.write(data)


def sha(p):
    return hashlib.sha256(read(p)).hexdigest()[:8]


def run(label):
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = r"E:/NAS/memory-agent/src"
    proc = subprocess.run([PY, "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider"],
                          cwd=r"E:/NAS/memory-agent", capture_output=True, text=True, env=env)
    tail = [l for l in proc.stdout.splitlines() if " passed" in l or " failed" in l or "error" in l]
    print("%-22s RC=%d  %s" % (label, proc.returncode, tail[-1] if tail else proc.stdout[-160:]))
    return proc.returncode


base_m, base_r = sha(MODELS), sha(REPO)
print("BASELINE sha models=%s repo=%s" % (base_m, base_r))
print("基线（什么都不改）:", run("baseline"))
bites = 0

d = read(MODELS)
assert FIXED_DT in d, "models 锚点没找到"
write(MODELS, d.replace(FIXED_DT, OLD_DT))
rc = run("M1 dt=机器本地")
bites += 1 if rc == 1 else 0
write(MODELS, read(MODELS).replace(OLD_DT, FIXED_DT))
print("  RESTORED models:", sha(MODELS) == base_m)

d = read(REPO)
assert MUT_REPO_NEW in d, "repository 锚点没找到"
write(REPO, d.replace(MUT_REPO_NEW, MUT_REPO_OLD))
rc = run("M2 tags 静默回空")
bites += 1 if rc == 1 else 0
write(REPO, read(REPO).replace(MUT_REPO_OLD, MUT_REPO_NEW))
print("  RESTORED repo:", sha(REPO) == base_r)

print("MUT_BASELINE_RC=0? 见上；BITTEN=%d/2" % bites)
rc = run("末态复跑")
print("FINAL_RC=%d  sha models=%s repo=%s" % (rc, sha(MODELS), sha(REPO)))
