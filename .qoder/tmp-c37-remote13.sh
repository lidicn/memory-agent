#!/usr/bin/env bash
# run13（20261006 批次 c37，任务表 #62：Q-B 参数级落点收口）的**远端本体**。
# 形状沿用 run11（两层引号：NAS bash -> 容器 sh；容器内是 dash，一律 `cmd > file; echo RC=$?`）。
#
# 本批改的是"入参收下了但不生效/不往下传"这一族，三跳 + 一把量具：
#   第一跳  insights/api.py::anomaly_report —— `days` 进死赋值（窗口恒 default_days）、
#           `query` 全函数从未引用；现按 resolve_range 落窗口、按 _search 口径解析实体、
#           解析不出时 fail-closed 答 0 条。
#   第二跳  insights/service.py::anomaly_report/_anomaly_report —— 收 entity_id 并交给 _filters。
#           （这一跳是 N6 变异戳出来的：只量到 core 边界的锁，允许同一族形状在下一层重演。）
#   第三跳  insights/nlquery.py 的 anomaly 路由 —— 规划阶段已解析出 plan.entity_ids，
#           usage/behavior 两条都下推了，唯独 anomaly 漏了 ⇒ 问「灯的异常」答全屋异常。
#   第四跳  insights/api.py 的四条 `self.core.reports.<x>_report(…)` 文本面 —— 顺着第（9）条
#           落点口径查到报告面时抓到的现行：`BehaviorService` 从未挂载 `reports`
#           （实测 `hasattr(BehaviorService, 'reports') == False`），四条方法调用即
#           AttributeError。而负责成员存在性的 `scan_insights_engine_attrs.py` 当时只认
#           `self.<引擎>.<成员>(…)` 一跳形状，这四条**连"指向"都没计上**：读数「35 指向 /
#           0 空指向」全绿。所以这一跳同时改两处：service.py 挂上 `ReportBuilder()`，
#           扫描器按整条链解析（断成员=MISS、类型未登记=UNREGISTERED，两样都判红）。
#   量具    scripts/scan_qb_param_landing.py（DCD 20261005 §三 Q2「不允许静默忽略」的第三半）。
#
# 与 run11 的差异（读数不能沿用）：
#   1) 换了树：#61 三件出网后又落本批 4 个源文件改动 + 1 个新测试。SUITE/TARGETED/TOUCHED/
#      GATE 四格都是整树口径。
#   2) 本批**有** PROBEANOM 格（与 run11「没有 PROBE 格」相反）：静态结论只能证明"没读这个
#      变量"，证明不了"读数因此变了"。这一格在生产库上出成对读数（带实体 vs 不带实体的事件
#      总数、days=2/7/14/30 的窗口天数、解析不出时的 0 条），只打数量与布尔，不外露实体 id。
#      load_events 的分层扫描基准不在本批口径内（repository.py 一字未动，见 SOURCES 上半），
#      仍沿用 §6.18.1 的 0.67× / 3.28×。
#   3) SOURCES 上半 = run11 在册零件 + run10 的读路径零件，全部必须一字不动；下半是本批 13 条
#      新指纹 + 5 个文件的 wc。
#   4) 变异三套：MUT13（本批 12 条：N1..N3 缺陷本身 / N4..N6 量具零件与第二跳 /
#      N7 第三跳 / N8..N9 本批引入的 ok 位那一格 / N10..N12 第四跳与深链量具）
#      + MUT11（8）+ MUT10（10）。
#      MUT11/MUT10 是**自证**：本批没动 intent/predictor/utils/attic，也没动 insights 读路径，
#      读数应与 run11/run10 一字不差（M1..M10 = 5/6/5/8/3/2/4/1/1/24）。
#
# SOURCES 期望读数（本机同一组 grep 先量出来，2026-10-06 19:0x，对不上=快照带的是旧树）：
#   run10 读路径零件：SCAN_DAY=3 HOUR_BUCKET=3 OFFSET_BIND=1 TS_GE=1 SCAN_HOURS=4 WINDOW_HI=5
#     SUBSTR_WHOLE=7 SUBSTR_IN_HOUR_STMT=0 BRANCH_FROM_FILT=1 HOUR_STMT_LINES=32 TEST_DEFS=13
#     SCAN_CLASS_CONST=2 SCAN_RECEIVERS=1 SCAN_DUP_RETURN=1
#   run11/#61 零件：  INTENT_NOW=0 INTENT_EVENT_DT=2 UTILS_KD_DEF=1 UTILS_CD_DEF=2 SRC_LEARNING=0
#     ATTIC_LEARNING=8 BOM_PY=0 P28_TESTDEFS=6 P25_TESTDEFS=6 BOM_TESTDEFS=3
#   本批新指纹：      API_ANOM_DAYS=1 API_ANOM_QUERY=1 API_ANOM_PUSHDOWN=1 API_ANOM_UNRES=1
#     API_ANOM_D2R=0 SVC_ANOM_SIG=1 SVC_ANOM_FILTERS_EID=1 SVC_ANOM_BODY_EID=16
#     NL_ANOM_CALL=1 NL_PLAN_EID=3 API_ANOM_CLOSED_OK=1 API_ANOM_LITERAL_OK=0
#     QBLAND_TESTDEFS=9
#   第四跳/深链量具指纹（run13d 新增）：SVC_REPORTS_MOUNT=1 API_CORE_REPORTS=4
#     ENG_SUBOWNER=1 ENG_SLOTS=1 ENG_RESOLVE=1 CALLSITE_TESTDEFS=18
#     格 1b 的 ATTRS 面板行 = 「门面引擎指向 39（含多跳链 4），空指向 0，未登记 0」
#     （改前 HEAD 同一支扫描器读「35 指向 / 0 空指向」——四条坏链根本没进统计，
#      所以这里要比"空指向 0"多两个读数才算把缺口补上）；ATTRS_SELFTEST_RC=0。
#   wc -l：api 936 / service 1347 / nlquery 202 / scan_qb 272 / qb 测试 298 /
#     scan_insights_engine_attrs 244 / callsite 测试 556（合计 3855）
#     （这组数在 20:0x 重测过：第一次量到 937/3856 是在变异自咬**正在改 api.py** 的时候读的，
#      N1 那条把一行换成两行 ⇒ +1。教训：读数不能与变异harness 并发。）
#   改前/改后成对（本机 3.13 口径，HEAD 三份源拷出来单独扫，`FINDINGS=2`）：
#     API_ANOM_DAYS 0->1 / API_ANOM_PUSHDOWN 0->1 / API_ANOM_D2R 1->0 / NL_ANOM_CALL 0->1 /
#     SVC_ANOM_FILTERS_EID 0->1；量具对 HEAD 的 api.py 报 `anomaly_report(days)=DEAD-RESULT(:711)`
#     与 `anomaly_report(query)=DROPPED`，对工作区同一文件报 FINDINGS=0。
#   API_ANOM_D2R=0 是"死赋值已撤"的现读：区域内 `start, end = self._days_to_range(days, start, end)`
#   归零；docstring 里那句改前记载写作 `days, …)`，不匹配这条全串指纹，所以 0 是真的 0。
#   SVC_ANOM_SIG=1 用的是 `def anomaly_report(self, tr: Any` 前缀（门面与 core 同名但形状不同，
#   门面那支是 `def anomaly_report(self, days: int = 0, …`），带引号的整串在两层转义里容易走形。
#   判据链的自证分三半：量具 `--self-test`（broken 三类各咬住 + clean 档不判红）、
#   MUT13 的 N4/N5（把量具自己的元组展开、def 节点跳过两处拆掉，锁必须响）、
#   MUT13 的 N10..N12 + 扫描器 `--self-test`（第四跳：挂载、深链统计、类型登记表各拆一处）。
#   第四跳的改前现读（本机 3.13，对 HEAD 的 service.py）：`hasattr(BehaviorService,
#   'reports') == False` ⇒ 四条文本面调用即 AttributeError，`SVC_REPORTS_MOUNT 0->1`。
set -uo pipefail
SNAP=${1:-c37snap20261006b}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

# 格 -1：TOOLCHAIN —— 先量工具链，量不到就整跑作废（run8 的教训：/tmp/pylibs 在容器可写层，
# 任何一次 recreate 都会把它清空，届时 pytest 不在 => "非零 RC + 无 FAILED 行"会被读成"咬住了"）。
ex "cd $L && PYTHONPATH=/tmp/pylibs python -c 'import pytest, pyflakes; print(\"PYTEST=\" + pytest.__version__); print(\"PYFLAKES=\" + pyflakes.__version__)' && python -V"
echo TOOLCHAIN_RC=$?

# 格 0：SOURCES —— 上半是"前两批的零件没被动"，下半是本批自己的指纹。
ex "cd $L && R=src/memory_agent/insights/repository.py && echo SCAN_DAY=\$(grep -c _scan_day \$R) && echo HOUR_BUCKET=\$(grep -c hour_bucket \$R) && echo OFFSET_BIND=\$(grep -c 'OFFSET ?' \$R) && echo TS_GE=\$(grep -c 'ts >= ?' \$R) && echo SCAN_HOURS=\$(grep -c SCAN_HOURS \$R) && echo WINDOW_HI=\$(grep -c window_hi \$R) && echo SUBSTR_WHOLE=\$(grep -c 'substr(' \$R) && echo SUBSTR_IN_HOUR_STMT=\$(awk '/def _hour_statement/,/return .*UNION/' \$R | grep -c substr || true) && echo BRANCH_FROM_FILT=\$(awk '/def _hour_statement/,/return .*UNION/' \$R | grep -c 'filt + \[') && echo HOUR_STMT_LINES=\$(awk '/def _hour_statement/,/return .*UNION/' \$R | wc -l) && echo TEST_DEFS=\$(grep -c '^def test_' tests/test_vma_q62_daily_batch_scan.py) && echo SCAN_CLASS_CONST=\$(grep -c _class_int_const scripts/scan_day_bounds.py) && echo SCAN_RECEIVERS=\$(grep -c 'RECEIVERS = (' scripts/scan_day_bounds.py) && echo SCAN_DUP_RETURN=\$(grep -c 'return 0 if ok else 2' scripts/scan_day_bounds.py || true) && I=src/memory_agent/intent_inference.py && U=src/memory_agent/insights/utils.py && echo INTENT_NOW=\$(grep -c 'datetime.now()' \$I || true) && echo INTENT_EVENT_DT=\$(grep -c _event_dt \$I) && echo UTILS_KD_DEF=\$(grep -c '^KEYWORD_DOMAINS' \$U) && echo UTILS_CD_DEF=\$(grep -c '^CATEGORY_DOMAINS' \$U) && echo SRC_LEARNING=\$(ls src/memory_agent/learning_*.py 2>/dev/null | wc -l) && echo ATTIC_LEARNING=\$(ls attic/learning/learning_*.py 2>/dev/null | wc -l) && echo BOM_PY=\$(python -c 'import os;print(sum(1 for b in (\"src\",\"tests\",\"scripts\",\"attic\") for r,d,fs in os.walk(b) for f in fs if f.endswith(\".py\") and open(os.path.join(r,f),\"rb\").read(3)==b\"\xef\xbb\xbf\"))') && echo P28_TESTDEFS=\$(grep -c '^def test_' tests/test_vma_p28_learning_attic_unreachable.py) && echo P25_TESTDEFS=\$(grep -c '^def test_' tests/test_vma_p25_keyword_domains_single_definition.py) && echo BOM_TESTDEFS=\$(grep -c '^def test_' tests/test_vma_gate_scanners_read_every_py.py) && A=src/memory_agent/insights/api.py && S=src/memory_agent/insights/service.py && N=src/memory_agent/insights/nlquery.py && echo API_ANOM_DAYS=\$(grep -c 'tr = self._tr(start, end, days=days)' \$A) && echo API_ANOM_QUERY=\$(grep -c 'resolve_ids(room=room, category=category, query=query) if query else' \$A) && echo API_ANOM_PUSHDOWN=\$(awk '/def anomaly_report/,/def device_health/' \$A | grep -c 'join(ids))') && echo API_ANOM_UNRES=\$(awk '/def anomaly_report/,/def device_health/' \$A | grep -c '\"unresolved\"') && echo API_ANOM_D2R=\$(awk '/def anomaly_report/,/def device_health/' \$A | grep -c 'start, end = self._days_to_range(days, start, end)' || true) && echo SVC_ANOM_SIG=\$(grep -c 'def anomaly_report(self, tr: Any' \$S) && echo SVC_ANOM_BODY_EID=\$(awk '/def _anomaly_report/,/def behavior_insights/' \$S | grep -c entity_id) && echo SVC_ANOM_FILTERS_EID=\$(awk '/def _anomaly_report/,/def behavior_insights/' \$S | grep -c '_filters(room, category, entity_id)') && echo NL_ANOM_CALL=\$(awk '/route == Intent.ANOMALY.value/,/route == Intent.RHYTHM.value/' \$N | grep -c 'entity_id=') && echo NL_PLAN_EID=\$(grep -c 'join(plan.entity_ids)' \$N) && echo API_ANOM_CLOSED_OK=\$(awk '/def anomaly_report/,/def device_health/' \$A | grep -c 'self._closed_ok(anomalies, summary, filters)') && echo API_ANOM_LITERAL_OK=\$(awk '/def anomaly_report/,/def device_health/' \$A | grep -c '\"ok\": True' || true) && echo QBLAND_TESTDEFS=\$(grep -c '^def test_' tests/test_vma_qb_param_landing.py) && E=scripts/scan_insights_engine_attrs.py && C=tests/test_vma_insights_callsite_binding.py && echo SVC_REPORTS_MOUNT=\$(grep -cF 'self.reports = ReportBuilder()' \$S) && echo API_CORE_REPORTS=\$(grep -cF 'self.core.reports.' \$A) && echo ENG_SUBOWNER=\$(grep -cF insights.report \$E) && echo ENG_SLOTS=\$(grep -c 'def init_slots' \$E) && echo ENG_RESOLVE=\$(grep -c 'def resolve_chain' \$E) && echo CALLSITE_TESTDEFS=\$(grep -c '^def test_' \$C) && wc -l \$A \$S \$N scripts/scan_qb_param_landing.py tests/test_vma_qb_param_landing.py \$E \$C" > ${L}_sources.log 2>&1
echo SOURCES_RC=$?
cat ${L}_sources.log

# 格 P：PROBEANOM —— 生产库只读的三跳成对读数（本批的"运行时读数"，不是静态结论）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_probe_anom.py" > ${L}_probe_anom.log 2>&1
echo PROBEANOM_RC=$?
cat ${L}_probe_anom.log

# 格 1：QBLAND —— 本批新量具的权威口径（--strict：有 DROPPED/DEAD-RESULT 就非零）
ex "cd $L && PYTHONPATH=/tmp/pylibs python scripts/scan_qb_param_landing.py --self-test; echo QBSELFTEST_RC=\$?; PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 python scripts/scan_qb_param_landing.py --strict; echo QBLAND_RC=\$?" > ${L}_qbland.log 2>&1
echo QBLAND_RC=$?
cat ${L}_qbland.log

# 格 1b：另两支同族量具（本批改了门面签名、NL 调用点与深链解析，三格都必须仍是绿）
#   ATTRS_SELFTEST 那一格是 run13d 补的：改前这支扫描器对四条坏链回答「一条没扫到」，
#   光看 `ATTRS_RC=0` 分不清「扫了没问题」与「没扫」。
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 python scripts/scan_insights_callsites.py --strict; echo CALLSITE_RC=\$?; PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 python scripts/scan_insights_engine_attrs.py --strict; echo ATTRS_RC=\$?; PYTHONPATH=$L:$L/src:/tmp/pylibs python scripts/scan_insights_engine_attrs.py --self-test; echo ATTRS_SELFTEST_RC=\$?" > ${L}_scans2.log 2>&1
echo SCANS2_RC=$?
grep -E '^(CALLSITE_RC|ATTRS_RC|ATTRS_SELFTEST_RC|SELFTEST|\[HARD|\[ADV|\[MISS|\[UNR|扫描 |门面|空指向|对账|共 |EXIT)' ${L}_scans2.log | tail -14

# 门 1：pyflakes（口径 src/memory_agent；attic 不参与，用计数自证）
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -3 ${L}_gate.log
echo GATE_MENTIONS_ATTIC=$(grep -c attic ${L}_gate.log)

# 门 2：全量回归（容器 Python 3.11 是本仓权威口径）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs" > ${L}_suite.log 2>&1
echo SUITE_RC=$?
tail -6 ${L}_suite.log
# run13b 教训：`tail -6` 只给得出"1 failed"，给不出是哪一条，于是每跑一次都要二次 SSH
# 去捞 _suite.log。这一行把失败用例名直接打到面板上（无匹配时 RC=1 是正常读数）。
grep -E '^_____ ' ${L}_suite.log | head -5; echo SUITE_FAILNAMES_RC=$?

# 定向 1：本批的判据链（九条落点锁 + 门面死工具清单 + 深链的两把锁）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_qb_param_landing.py tests/test_vma_insights_facade_dead_tools.py tests/test_vma_insights_callsite_binding.py -q -p no:cacheprovider" > ${L}_targeted.log 2>&1
echo TARGETED_RC=$?
tail -3 ${L}_targeted.log

# 定向 2：上一批（run11/#61）的判据链必须仍然绿——本批刻意没碰 intent/predictor/utils/attic
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma121_intent_and_pii.py tests/test_vma_behavior_predictor_shapes.py tests/test_vma_p25_keyword_domains_single_definition.py tests/test_vma_p28_learning_attic_unreachable.py tests/test_vma_gate_scanners_read_every_py.py -q -p no:cacheprovider" > ${L}_prevbatch.log 2>&1
echo PREVBATCH_RC=$?
tail -3 ${L}_prevbatch.log

# 定向 3：凡引用 insights 门面/NL 路由/扫描面的既有测试（run11 的 28 个 + 本批 1 个）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_activity_inference.py tests/test_drift.py tests/test_insights_facade_contract.py tests/test_insights_house_timezone.py tests/test_process_mining.py tests/test_rule_recall.py tests/test_signal_learning.py tests/test_vma_a8_insights_clock_and_tags.py tests/test_vma_activity_semantic.py tests/test_vma_activity_window_reachability.py tests/test_vma_audit20261002_fixes.py tests/test_vma_dcd_20261004b_event_total.py tests/test_vma_insights_callsite_binding.py tests/test_vma_insights_facade_dead_tools.py tests/test_vma_insights_matrix_hour.py tests/test_vma_insights_nlquery_routes.py tests/test_vma_insights_search_filters.py tests/test_vma_insights_window_echo.py tests/test_vma_p32_day_bounds.py tests/test_vma_p37_degrade_list_trace.py tests/test_vma_q62_daily_batch_scan.py tests/test_vma_r7_seam_fixes.py tests/test_vma_step0_homesdk_time.py tests/test_vma121_intent_and_pii.py tests/test_vma_behavior_predictor_shapes.py tests/test_vma_p25_keyword_domains_single_definition.py tests/test_vma_p28_learning_attic_unreachable.py tests/test_vma_gate_scanners_read_every_py.py tests/test_vma_qb_param_landing.py tests/test_quality_gates.py -q -p no:cacheprovider" > ${L}_touched.log 2>&1
echo TOUCHED_RC=$?
tail -3 ${L}_touched.log

# 量具①：day bounds 自证 + 门禁口径（src）+ 登记口径（attic）——本批未动这两处，属自证
ex "cd $L && python -V && PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py --self-test; echo SELFTEST_RC=\$?; echo ---SRC---; PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py src; echo DAYSCAN_RC=\$?; echo ---ATTIC---; PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py attic/learning; echo ATTICSCAN_RC=\$?" > ${L}_scan-days.log 2>&1
echo DAYBATCH_RC=$?
tail -6 ${L}_scan-days.log

# 量具②：route mount 自证 + 全仓扫描——本批未动路由，应仍为绿
ex "cd $L && PYTHONPATH=/tmp/pylibs python scripts/scan_route_mount.py --self-test; echo SELFTEST_RC=\$?; PYTHONPATH=/tmp/pylibs python scripts/scan_route_mount.py src; echo ROUTESCAN_RC=\$?"
echo ROUTEBATCH_RC=$?

# 门 3（自证）：三套变异各咬各的锁，容器 Python 3.11 口径
#   N1..N12 = 本批（第一跳 3 条 / 量具自身 2 条 / 第二跳 1 条 / 第三跳 1 条 /
#             ok 位 2 条 / 第四跳与深链量具 3 条）
#     本机 3.13 预跑（20261006 19:0x）：N1..N9 全咬住、restored_identical=OK、MUTATION_BAD=0；
#     其中 N6 首跑**存活**（RC=0），据此补了 test_anomaly_report_entity_id_lands_on_repo_scans。
#   MUT11 的 N1..N8 = 上一批（run11）#61 的八条；M1..M10 = run10 的十条（5/6/5/8/3/2/4/1/1/24）。
#   后两套本批没碰对应文件，重跑是**自证**——对不上就说明本批改动渗到了那些面。
#   注意本批的 Svc/ENG 两处改动会让 MUT11 的口径多出 service.py，但它八条锚点都不在
#   `__init__`/报告面区域，读数应与 run11 一字不差。
ex "cd $L && MUT_ROOT=$L PYLIBS=/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut13.py" > ${L}_mut13.log 2>&1
echo MUT13_RC=$?
grep -E '^(N[0-9]+ |    RC=|    异常|    无汇总行|    [0-9]+ (passed|failed)|MUTATION_BAD|restored_identical|.*锚点命中)' ${L}_mut13.log
ex "cd $L && MUT_ROOT=$L PYLIBS=/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut11.py" > ${L}_mut11.log 2>&1
echo MUT11_RC=$?
grep -E '^(N[1-8] |    RC=|    异常|    无汇总行|    [0-9]+ (passed|failed)|MUTATION_BAD|restored_identical|.*锚点命中)' ${L}_mut11.log
ex "cd $L && MUT_ROOT=$L PYLIBS=/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut.py" > ${L}_mut10.log 2>&1
echo MUT10_RC=$?
grep -E '^(M[1-9]|M10 |    RC=|    异常|    无汇总行|    [0-9]+ (passed|failed)|MUTATION_BAD|restored_identical|.*PATCH_NOT_FOUND)' ${L}_mut10.log

echo REMOTE_BATCH_RC=0
