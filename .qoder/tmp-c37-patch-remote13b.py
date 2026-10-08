"""重生成 remote13 的 SOURCES 行：逐条 grep/awk 由列表生成，BOM 那段沿用 run11 已验证的字面。

本机 emulate 抓到两处自己的走形（正是要在门上防住的那族"形状合法、静默变形"）：
  1) 手拼的 awk 区间写成 `/def _anomaly_report,/def behavior_insights/`（逗号后少一个 `/`），
     容器里 awk 会把后半截当裸词——判据静默失效，读数还是一个数。
  2) `BOM_PY` 那段在多层转义里把 `\\xef` 写成了 `\\\\xef`。改成直接从 run11 的远端本体里
     整段搬过来（那一份在容器里跑通过，不该重敲）。
"""
import io
import re

P = ".qoder/tmp-c37-remote13.sh"
R11 = ".qoder/tmp-c37-remote11.sh"
PREFIX = 'ex "cd $L && '

r11 = io.open(R11, encoding="utf-8", newline="").read()
m = re.search(r"echo BOM_PY=.*?\)\)'\)", r11)
if not m:
    raise SystemExit("BOM_ANCHOR_NOT_FOUND_IN_RUN11")
BOM = m.group(0)
print("BOM_FROM_RUN11=%s" % BOM[:60])

CHECKS = [
    ("R=src/memory_agent/insights/repository.py", None),
    ("SCAN_DAY", "grep -c _scan_day $R"),
    ("HOUR_BUCKET", "grep -c hour_bucket $R"),
    ("OFFSET_BIND", "grep -c 'OFFSET ?' $R"),
    ("TS_GE", "grep -c 'ts >= ?' $R"),
    ("SCAN_HOURS", "grep -c SCAN_HOURS $R"),
    ("WINDOW_HI", "grep -c window_hi $R"),
    ("SUBSTR_WHOLE", "grep -c 'substr(' $R"),
    ("SUBSTR_IN_HOUR_STMT", "awk '/def _hour_statement/,/return .*UNION/' $R | grep -c substr || true"),
    ("BRANCH_FROM_FILT", "awk '/def _hour_statement/,/return .*UNION/' $R | grep -c 'filt + \\['"),
    ("HOUR_STMT_LINES", "awk '/def _hour_statement/,/return .*UNION/' $R | wc -l"),
    ("TEST_DEFS", "grep -c '^def test_' tests/test_vma_q62_daily_batch_scan.py"),
    ("SCAN_CLASS_CONST", "grep -c _class_int_const scripts/scan_day_bounds.py"),
    ("SCAN_RECEIVERS", "grep -c 'RECEIVERS = (' scripts/scan_day_bounds.py"),
    ("SCAN_DUP_RETURN", "grep -c 'return 0 if ok else 2' scripts/scan_day_bounds.py || true"),
    ("I=src/memory_agent/intent_inference.py", None),
    ("U=src/memory_agent/insights/utils.py", None),
    ("INTENT_NOW", "grep -c 'datetime.now()' $I || true"),
    ("INTENT_EVENT_DT", "grep -c _event_dt $I"),
    ("UTILS_KD_DEF", "grep -c '^KEYWORD_DOMAINS' $U"),
    ("UTILS_CD_DEF", "grep -c '^CATEGORY_DOMAINS' $U"),
    ("SRC_LEARNING", "ls src/memory_agent/learning_*.py 2>/dev/null | wc -l"),
    ("ATTIC_LEARNING", "ls attic/learning/learning_*.py 2>/dev/null | wc -l"),
    (BOM, None),
    ("P28_TESTDEFS", "grep -c '^def test_' tests/test_vma_p28_learning_attic_unreachable.py"),
    ("P25_TESTDEFS", "grep -c '^def test_' tests/test_vma_p25_keyword_domains_single_definition.py"),
    ("BOM_TESTDEFS", "grep -c '^def test_' tests/test_vma_gate_scanners_read_every_py.py"),
    ("A=src/memory_agent/insights/api.py", None),
    ("S=src/memory_agent/insights/service.py", None),
    ("N=src/memory_agent/insights/nlquery.py", None),
    ("API_ANOM_DAYS", "grep -c 'tr = self._tr(start, end, days=days)' $A"),
    ("API_ANOM_QUERY", "grep -c 'resolve_ids(room=room, category=category, query=query) if query else' $A"),
    ("API_ANOM_PUSHDOWN", "awk '/def anomaly_report/,/def device_health/' $A | grep -c 'join(ids))'"),
    ("API_ANOM_UNRES", "awk '/def anomaly_report/,/def device_health/' $A | grep -c '\"unresolved\"'"),
    ("API_ANOM_D2R", "awk '/def anomaly_report/,/def device_health/' $A | "
                      "grep -c 'start, end = self._days_to_range(days, start, end)' || true"),
    ("SVC_ANOM_SIG", "grep -c 'def anomaly_report(self, tr: Any' $S"),
    ("SVC_ANOM_BODY_EID", "awk '/def _anomaly_report/,/def behavior_insights/' $S | grep -c entity_id"),
    ("SVC_ANOM_FILTERS_EID", "awk '/def _anomaly_report/,/def behavior_insights/' $S | "
                              "grep -c '_filters(room, category, entity_id)'"),
    ("NL_ANOM_CALL", "awk '/route == Intent.ANOMALY.value/,/route == Intent.RHYTHM.value/' $N | "
                      "grep -c 'entity_id='"),
    ("NL_PLAN_EID", "grep -c 'join(plan.entity_ids)' $N"),
    ("QBLAND_TESTDEFS", "grep -c '^def test_' tests/test_vma_qb_param_landing.py"),
]
TAIL = ("wc -l $A $S $N scripts/scan_qb_param_landing.py tests/test_vma_qb_param_landing.py"
        ' > ${L}_sources.log 2>&1')

parts = []
for name, cmd in CHECKS:
    parts.append(name if cmd is None else "echo %s=\\$(%s)" % (name, cmd))
line = PREFIX + 'cd $L && ' + " && ".join(parts) + " && " + TAIL

text = io.open(P, encoding="utf-8", newline="").read()
lines = text.split("\n")
hits = [i for i, ln in enumerate(lines) if ln.startswith('ex "cd $L && R=src')]
print("LINE_HITS=%d" % len(hits))
if len(hits) != 1:
    raise SystemExit(1)
lines[hits[0]] = line
data = "\n".join(lines).encode("utf-8")
with open(P, "wb") as fh:
    fh.write(data)
print("NEW_LEN=%d CR=%d LF=%d" % (len(line), data.count(b"\r"), data.count(b"\n")))
