"""「规则标签条件」这把锁的变异红证：把 `_signal_ids` 的标签过滤摘掉，锁必须判红。

改源 → 只跑那一把锁 → 复原 → 校验字节全等。锚点必须恰好命中 1 次，否则 ABORT。
"""
import os
import subprocess
import sys

ROOT = os.environ.get("MUT_ROOT", "E:/NAS/memory-agent")
SRC_SUB = os.environ.get("MUT_SRC", "src")
P = os.path.join(ROOT, SRC_SUB, "memory_agent", "insights", "activity.py")
A = ('        wanted = {str(t).strip() for t in (rule.require_tags or []) if str(t).strip()}\n'
     '        if wanted:\n'
     '            # 任一声明标签命中即计入（legacy 的 `set(rule.tags) & set(e.tags)` 口径）\n'
     '            info = [e for e in info if tags_of(e.entity_id, e.label) & wanted]\n')
NODE = ("tests/test_vma_activity_semantic.py::"
        "test_rule_tag_condition_filters_events_instead_of_being_decorative")

env = dict(os.environ)
env["PYTHONPATH"] = os.pathsep.join(
    [ROOT, os.path.join(ROOT, SRC_SUB), env.get("PYTHONPATH", "")])
env.setdefault("JWT_SECRET", "ci-test")

print("root:", ROOT, "| python:", sys.executable)
raw = open(P, "rb").read()
text = raw.decode("utf-8")
hits = text.count(A)
if hits != 1:
    print("ABORT ANCHOR_HITS=%d" % hits)
    sys.exit(2)
mutated = text.replace(A, "").encode("utf-8")
open(P, "wb").write(mutated)
r = subprocess.run([sys.executable, "-m", "pytest", NODE, "-q", "--no-header",
                    "-p", "no:cacheprovider"], capture_output=True, text=True,
                   cwd=ROOT, env=env)
open(P, "wb").write(raw)
restored = open(P, "rb").read() == raw
print("last_lines:", [l for l in (r.stdout or "").splitlines() if l.strip()][-3:])
print("MUT_RC=%s RESTORED=%s" % (r.returncode, restored))
