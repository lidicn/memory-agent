"""裁5 新锁的变异红证：逐条制造缺陷，确认对应用例判红，然后逐字节还原。"""
import re
import subprocess
import sys

API = "src/memory_agent/insights/api.py"
PY = sys.executable
PRISTINE = open(API, "rb").read()
assert PRISTINE.count(b"\r") == 0, "working tree should be LF"

MUTS = [
    ("A1 门面 query_behavior_events 退回 Phase-4 形状（丢 member/days）",
     ('def query_behavior_events(self, room: str = "", member: str = "", days: int = 7,\n'
      '                              start: str = "", end: str = "", limit: int = 50) -> Dict[str, Any]:',
      'def query_behavior_events(self, room: str = "", start: str = "", end: str = "",\n'
      '                              limit: int = 50) -> Dict[str, Any]:'),
     ["test_every_insights_toolspec_param_lands_in_a_facade_slot",
      "test_dispatch_would_bind_every_insights_tool"]),
    ("A2 _search 不再上报扫描上限（Q4 三键消失）",
     ("        return annotate_scan(out, len(events), getattr(self.repo, \"scan_limit\", 0))",
      "        return out"),
     ["test_scan_annotation_tells_whether_total_is_a_scan_cap"]),
    ("A3 climate_sessions 指回 BehaviorService 上不存在的成员",
     ("        out = self.legacy.climate_sessions(query=query, room=room, days=days,",
      "        out = self.core.climate_sessions(query=query, room=room, days=days,"),
     ["test_facade_engine_pointers_all_resolve",
      "test_no_insights_tool_dies_on_a_binding_shape",
      "test_forwarded_tools_keep_both_key_generations"]),
    ("A4 get_last_event 不给门面代兼容键（只回 legacy 原样）",
     ('        if isinstance(out, dict):\n'
      '            found = bool(out.get("ok"))\n'
      '            out.setdefault("event", dict(out) if found else None)',
      '        if isinstance(out, dict):\n'
      '            found = bool(out.get("ok"))\n'
      '            out = dict(out)'),
     ["test_get_last_event_returns_both_key_generations",
      "test_forwarded_tools_keep_both_key_generations"]),
    ("A5 登记表漏登 get_last_event",
     ('    "query_behavior_events",\n    "get_last_event",\n',
      '    "query_behavior_events",\n'),
     ["test_ledger_size_matches_the_measured_outward_surface",
      "test_legacy_outward_ledger_is_complete_in_both_directions"]),
    ("A6 把本页条数改名成 total（谎报匹配总数）",
     ('            if "time_range" not in out and out.get("window"):\n'
      '                out["time_range"] = out["window"]\n'
      '        return out\n\n    def _search(',
      '            out["total"] = count\n'
      '            if "time_range" not in out and out.get("window"):\n'
      '                out["time_range"] = out["window"]\n'
      '        return out\n\n    def _search('),
     ["test_query_behavior_events_reads_the_vision_table_and_honors_member"]),
    ("A7 data_quality_issues 指回不存在的引擎成员（仓内消费点那一半）",
     ('        issues = (self.core.data_quality(tr) or {}).get("issues") or []',
      '        issues = (self.core.data_quality_issues(tr) or {}).get("issues") or []'),
     ["test_facade_engine_pointers_all_resolve"]),
]

rows = []
base = subprocess.run([PY, "-m", "pytest", "tests/test_vma_insights_callsite_binding.py",
                       "-q", "-p", "no:cacheprovider", "-rf"],
                      capture_output=True, text=True, encoding="utf-8", errors="replace")
print("[baseline]", base.stdout.strip().splitlines()[-1],
      "FAILED =", sorted(re.findall(r"^FAILED (?:\S+::)?([\w_]+)", base.stdout, re.M)))
print("=" * 100)

for label, (old, new), expected in MUTS:
    body = PRISTINE.decode("utf-8")
    n = body.count(old)
    if n != 1:
        rows.append((label, f"锚点命中 {n} 次（要求恰好 1）", "ABORT"))
        continue
    with open(API, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body.replace(old, new))
    p = subprocess.run([PY, "-m", "pytest", "tests/test_vma_insights_callsite_binding.py",
                        "-q", "-p", "no:cacheprovider", "-rf"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    failed = sorted(set(re.findall(r"^FAILED (?:\S+::)?([\w_]+)", p.stdout, re.M)))
    open(API, "wb").write(PRISTINE)
    ok_restore = open(API, "rb").read() == PRISTINE
    bit = [e for e in expected if e in failed]
    miss = [e for e in expected if e not in failed]
    rows.append((label, f"判红 {failed}",
                 f"咬住 {len(bit)}/{len(expected)} 缺 {miss} RESTORED={ok_restore}"))

print("=" * 100)
for a, b, c in rows:
    print(f"{a}\n  {b}\n  {c}\n")
print("RESTORED_FINAL =", open(API, "rb").read() == PRISTINE)
