"""裁6 新增锁的变异红证：逐条改源、只跑对应锁、复原并校验。

纪律：锚点必须恰好命中 1 次，否则该例 ABORT（不跑）；复原后 write/read 必须与原字节全等。
"""
import subprocess
import sys

PY = sys.executable
SRC = "src/memory_agent/"
CASES = [
    ("语义引擎不再产出规则片段", SRC + "insights/service.py",
     '        matches = engine.infer(events, tr, rooms=",".join(room_list),',
     '        matches = [] if True else engine.infer(events, tr, rooms=",".join(room_list),',
     "tests/test_vma_activity_semantic.py::test_semantic_rule_fires_on_the_live_path"),
    ("时段启发式兜底被删", SRC + "insights/service.py",
     "        heuristic, seg_meta = self._heuristic_segments(tr, room_list, allow, exclude_ids)",
     '        heuristic, seg_meta = [], {"days": 0}',
     "tests/test_vma_activity_semantic.py::test_hour_segment_heuristic_survives_as_fallback_output"),
    ("现行引擎不读 activity_rules", SRC + "insights/service.py",
     "        engine.custom = custom",
     "        engine.custom = {}",
     "tests/test_vma_activity_semantic.py::test_engine_reads_the_activity_rules_table"),
    ("停用中的规则也被套用", SRC + "insights/service.py",
     '            return list(self.repo.activity_rules(enabled_only=True) or []), ""',
     '            return list(self.repo.activity_rules(enabled_only=False) or []), ""',
     "tests/test_vma_activity_semantic.py::test_disabled_rule_rows_are_not_applied"),
    ("activities 入参再次被吞", SRC + "insights/service.py",
     "                               activities=allow or None)",
     "                               activities=None)",
     "tests/test_vma_activity_semantic.py::test_activities_input_reaches_the_engine_instead_of_being_dropped"),
    ("旁挂依赖读数（rule_sources）缺席", SRC + "insights/service.py",
     '            "rule_sources": rules_meta,\n',
     "",
     "tests/test_vma_activity_semantic.py::test_three_readings_legacy_vs_new_are_all_present"),
    ("规则表读数恒为 0", SRC + "insights/service.py",
     "        rules, rules_error = self._activity_rule_rows()",
     '        rules, rules_error = [], "mutated"',
     "tests/test_vma_insights_facade_dead_tools.py::test_registered_rule_is_read_by_the_current_engine"),
    ("NOT IN 变成空操作", SRC + "insights/repository.py",
     '        vals = cls._as_tuple(values)\n        if not vals:\n            return\n        where.append(column + " NOT IN ("',
     '        vals = ()\n        if not vals:\n            return\n        where.append(column + " NOT IN ("',
     "tests/test_vma_activity_semantic.py::test_not_in_drops_only_the_named_entities"),
    ("硬排除没接到事件读取", SRC + "insights/repository.py",
     '        self._add_not_in(where, params, "entity_id", exclude_entity_ids)',
     "        pass",
     "tests/test_vma_activity_semantic.py::test_hard_exclusion_drops_events_and_is_visible"),
    ("分类标注被当成排除", SRC + "insights/repository.py",
     '                if row["exclusion_type"] != "exclude":',
     "                if False:",
     "tests/test_vma_activity_semantic.py::test_classification_exclusions_do_not_drop_events"),
    ("回执退回旧文案", SRC + "insights/api.py",
     '                "规则已注册进 activity_rules（rule_id=%s）；"',
     '                "规则已注册，下次 infer_activities 自动套用；"',
     "tests/test_vma_insights_facade_dead_tools.py::test_define_activity_receipt_promise_matches_what_the_engine_now_reads"),
    ("回执吞掉覆盖预检告警", SRC + "insights/api.py",
     '            ) + ((" ⚠️ " + warn) if warn else "")',
     "            )",
     "tests/test_vma_insights_facade_dead_tools.py::test_define_activity_receipt_promise_matches_what_the_engine_now_reads"),
    ("NL 问答退回 data['total']", SRC + "insights/nlquery.py",
     '            int(data.get("total_activities") or 0), "、".join(names) or "无"), data)',
     '            int(data["total"]), "、".join(names) or "无"), data)',
     "tests/test_vma_activity_semantic.py::test_nl_activity_route_answers_instead_of_degrading"),
    ("窗口锚回裁剪起点", SRC + "insights/activity.py",
     "                start, end = self._window_range(_day_midnight(day), rule.window)",
     "                start, end = self._window_range(_day_lo, rule.window)",
     "tests/test_vma_activity_semantic.py::test_rule_window_anchors_at_house_midnight_not_the_tr_start"),
]
# 容器（UTC）专用，本机 +8 与家庭墙钟同侧，变异后等价、判不出红：
EQUIV_LOCAL = [
    ("日界退回机器本地时区", SRC + "insights/models.py",
     "        cur = house_dt(self.start_ts).replace(hour=0, minute=0, second=0, microsecond=0)",
     "        cur = datetime.fromtimestamp(self.start_ts).replace(hour=0, minute=0, second=0, microsecond=0)",
     "tests/test_vma_activity_semantic.py::test_split_days_cuts_at_house_wall_clock_midnight"),
]


def run(node):
    p = subprocess.run([PY, "-m", "pytest", node, "-q", "--no-header", "-p", "no:cacheprovider"],
                       capture_output=True, text=True, cwd=".")
    return p.returncode, (p.stdout or "").strip().splitlines()[-1:]


def mutate(path, anchor, repl):
    raw = open(path, "rb").read()
    text = raw.decode("utf-8")
    n = text.count(anchor)
    if n != 1:
        return None, "ANCHOR_HITS=%d" % n
    return raw, text.replace(anchor, repl).encode("utf-8")


rows = []
for label, path, anchor, repl, node in CASES:
    pristine, new = mutate(path, anchor, repl)
    if new is None:
        rows.append((label, "ABORT", new))
        continue
    open(path, "wb").write(new)
    try:
        rc, tail = run(node)
    finally:
        open(path, "wb").write(pristine)
    ok_restore = open(path, "rb").read() == pristine
    rows.append((label, "RED rc=%s RESTORED=%s" % (rc, ok_restore), " ".join(tail)))

print("== 变异红证（本机 %s）==" % PY)
for label, verdict, detail in rows:
    print("%-34s %s" % (label, verdict))
    print("    %s" % detail)
print("\n-- 本机等价、留待容器 UTC 判红 --")
for label, path, a, b, node in EQUIV_LOCAL:
    print("%-34s %s" % (label, node))
