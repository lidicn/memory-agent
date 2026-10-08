"""§五十五 核销索引表里每一个 file:line 的现读校验：把引用行原样打出来，
让"落点/锁号"这类存在性结论在落册前对得上盘上内容（引用行号是编译期快照，会腐）。"""
import io
import os
import sys

WT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REFS = [
    ("src/memory_agent/llm_client.py", 438, "for p in stale:"),
    ("src/memory_agent/llm_client.py", 442, None),
    ("src/memory_agent/mcp_server.py", 2716, "prev_version"),
    ("src/memory_agent/mcp_server.py", 219, "seed_builtin_skills"),
    ("src/memory_agent/mcp_server.py", 1749, "dry_run"),
    ("src/memory_agent/agent_memory.py", 525, "member_id"),
    ("src/memory_agent/agent_memory.py", 526, "member_scope"),
    ("src/memory_agent/signal_learning.py", 119, None),
    ("src/memory_agent/signal_learning.py", 141, None),
    ("src/memory_agent/api/nr_routes.py", 56, "nr_execute_action"),
    ("src/memory_agent/api/nr_routes.py", 70, "501"),
    ("src/memory_agent/runtime.py", 137, "_run_retention_cleanup"),
    ("src/memory_agent/runtime.py", 166, None),
    ("src/memory_agent/runtime.py", 298, "_periodic_self_diary"),
    ("src/memory_agent/runtime.py", 318, "_self_diary_round"),
    ("src/memory_agent/config.py", 107, "data_retention_interval_seconds"),
    ("src/memory_agent/acp_server.py", 85, "owner_token"),
    ("src/memory_agent/acp_server.py", 86, "SessionOwnerConflict"),
    ("src/memory_agent/acp_server.py", 377, "SessionOwnerConflict"),
    ("src/memory_agent/acp_server.py", 433, "check_owner"),
]

LOCKS = [58, 78, 104, 160, 169, 203, 211, 219, 229, 236, 248, 264, 295, 305,
         328, 346, 358, 401, 411, 421, 430, 441, 486, 498, 509]
LOCK_FILE = "tests/test_rounds11_19_ma25_35_fixes.py"

bad = 0


def get(rel, n):
    with io.open(os.path.join(WT, rel), "r", encoding="utf-8", newline="") as fh:
        lines = fh.read().split("\n")
    if n > len(lines):
        return None
    return lines[n - 1].strip()


for rel, n, want in REFS:
    line = get(rel, n)
    if line is None:
        print(f"MISSING {rel}:{n} 文件只有更少行")
        bad += 1
        continue
    ok = want is None or want in line
    if not ok:
        bad += 1
        print(f"WRONG   {rel}:{n} 期望含 {want!r}，实得 {line[:90]!r}")
    else:
        print(f"OK      {rel}:{n} :: {line[:78]}")

for n in LOCKS:
    line = get(LOCK_FILE, n)
    if line is None or not (line.startswith("def test_") or line.startswith("@pytest.mark")):
        bad += 1
        print(f"WRONG   {LOCK_FILE}:{n} 不是用例/参数化起始行：{line!r}")
    else:
        print(f"OK      lock:{n} :: {line[:70]}")

print(f"LINECHECK_BAD={bad}")
sys.exit(1 if bad else 0)
