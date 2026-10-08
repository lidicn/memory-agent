r"""DCD 20261005 落码批次的门自证：五条变异各咬各的锁（改完逐字还原）。

每档只改一处语义，跑对应那条锁，要求它**判红**；「什么都不改」档由改动前那份
全绿读数（134 passed / RC=0）充当。脚本只在 .qoder/ 下活着，不进仓。
"""
import os
import subprocess
import sys
from pathlib import Path

# 容器（Python 3.11）也跑同一份：ROOT 交给环境变量，别在容器里指到 E 盘。
ROOT = Path(os.environ.get("MUT_ROOT", "E:/NAS/memory-agent"))
PY = sys.executable

# (标签, 文件, 原文, 变异, 该咬住的锁)
MUTS = [
    ("M1 ignore_trigger 失效（判定链从不跑）",
     "src/memory_agent/rule_engine.py",
     "            if result and not ignore_trigger and trigger_type != \"absence\":",
     "            if result and not True and trigger_type != \"absence\":",
     "test_full_chain_counts_firings_not_condition_hits"),
    ("M2 回放复用线上 count 状态（不再独立）",
     "src/memory_agent/rule_engine.py",
     "                        hits.append(now_dt)",
     "                        hits = self._event_windows[rule.get('rule_id', '')]\n"
     "                        hits.append(now_dt)",
     "test_replay_leaves_live_engine_state_untouched"),
    ("M3 缺时间戳退回当前墙钟（凭空凑窗）",
     "src/memory_agent/rule_engine.py",
     "        raw = str(event.get(\"ts\") or event.get(\"server_ts\") or \"\").strip()\n"
     "        if not raw:\n            return None",
     "        raw = str(event.get(\"ts\") or event.get(\"server_ts\") or \"\").strip()\n"
     "        if not raw:\n            return self._now()",
     "test_events_without_timestamp_are_not_silently_fired"),
    ("M4 manual_add 直写 active_rules（绕过四红线）",
     "src/memory_agent/rule_lifecycle.py",
     "        if not rid:\n            return {\"ok\": False, \"error\": f\"候选质量闸门拒绝录入: {action}\"}",
     "        self.engine.add_rule(name, {\"kind\": \"device\", \"tag\": \"door\"},\n"
     "                         {\"type\": \"log\"})\n"
     "        if not rid:\n            return {\"ok\": False, \"error\": f\"候选质量闸门拒绝录入: {action}\"}",
     "test_manual_add_lands_in_candidate_rules_not_active_rules"),
    ("M5 只读面从 ROUTES 摘掉",
     "src/memory_agent/api/behavior_routes.py",
     "    Route(\"/api/behaviors/active-rules\", behaviors_list_rules, methods=[\"GET\"]),",
     "",
     "test_all_rule_writes_are_mounted_through_the_channel"),
]

TARGET = {
    "test_full_chain_counts_firings_not_condition_hits":
        "tests/test_vma_dcd_20261005_rules.py::test_full_chain_counts_firings_not_condition_hits",
    "test_replay_leaves_live_engine_state_untouched":
        "tests/test_vma_dcd_20261005_rules.py::test_replay_leaves_live_engine_state_untouched",
    "test_events_without_timestamp_are_not_silently_fired":
        "tests/test_vma_dcd_20261005_rules.py::test_events_without_timestamp_are_not_silently_fired",
    "test_manual_add_lands_in_candidate_rules_not_active_rules":
        "tests/test_vma_dcd_20261005_rules.py::test_manual_add_lands_in_candidate_rules_not_active_rules",
    "test_all_rule_writes_are_mounted_through_the_channel":
        "tests/test_vma_p32_day_bounds.py::test_all_rule_writes_are_mounted_through_the_channel",
}


def run(target: str) -> int:
    proc = subprocess.run([PY, "-m", "pytest", target, "-q", "--no-header",
                           "-p", "no:cacheprovider"],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    lines = [ln for ln in proc.stdout.splitlines()
             if " passed" in ln or " failed" in ln or " error" in ln]
    print("    pytest:", lines[-1] if lines else "(no summary line)")
    return proc.returncode


bad = 0
for label, rel, old, new, key in MUTS:
    path = ROOT / rel
    original = path.read_bytes()
    # 本机 core.autocrlf=true：签出的工作树可能是 CRLF（提交时 git 会换回 LF）。
    # 补丁串按文件自己的行尾走，否则 "PATCH_NOT_FOUND" 会被误读成"这处语义改不动"。
    crlf = b"\r\n" in original
    enc = (lambda s: s.replace("\n", "\r\n").encode("utf-8")) if crlf else \
          (lambda s: s.encode("utf-8"))
    old_b, new_b = enc(old), enc(new)
    if old_b not in original:
        print(f"{label}: PATCH_NOT_FOUND (crlf={crlf})")
        bad += 1
        continue
    try:
        patched = original.replace(old_b, new_b, 1)
        path.write_bytes(patched)
        print(label)
        rc = run(TARGET[key])
        print(f"    RC={rc} -> " + ("咬住了" if rc != 0 else "没咬住：门是假的"))
        if rc == 0:
            bad += 1
    finally:
        path.write_bytes(original)
    print("    restored_identical=" +
          ("OK" if path.read_bytes() == original else "MISMATCH"))
    if path.read_bytes() != original:
        bad += 1

print(f"MUTATION_BAD={bad}")
