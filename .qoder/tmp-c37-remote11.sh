#!/usr/bin/env bash
# run11（20261006 批次 c37，任务表 #61：A2/A7 静态结论核销第三批）的**远端本体**。
# 形状沿用 run10（两层引号：NAS bash -> 容器 sh；容器内是 dash，一律 `cmd > file; echo RC=$?`）。
#
# 本批改的是三处"只有静态结论、没有运行时读数"的在册项 + 一处量具能读不动的文件形状：
#   P4-3  intent_inference.py 的 `now` 兜底（一条坏 ts 就能把窗口基准推到容器墙钟）
#   P2-4  behavior_predictor 同日交错重复计天（原实现只跟上一条比日期）
#   P2-5  insights/utils.py 的 KEYWORD_DOMAINS 单定义（此前只有一句注释在守）
#   P2-8  learning_* 移出 src 后的运行时"不可导入"读数
#   BOM   三个在册 .py 带 UTF-8 BOM，ast.parse 直读会 SyntaxError（量具静默漏审的形状）
#
# 与 run10 的差异（读数不能沿用）：
#   1) **没有 PROBE 格**：本批不碰 insights 读路径，生产库成对读数与 run10 同一棵树，
#      沿用 §6.18.1 的正式基准（0.67x / 3.28x），不重贴。
#   2) SOURCES 一格量两件事：乙′/#60 的零件指纹必须**一字不动**（证明本批没碰它们），
#      外加本批自己的 5 条新指纹。
#   3) TARGETED/TOUCHED 的口径换到本批的 5 个测试文件 + 上一批的判据链。
#
# SOURCES 期望读数（本机同一组 grep 先量出来，2026-10-05 18:0x，对不上=快照带的是旧树）：
#   上一批零件（必须一字不动）：SCAN_DAY=3 HOUR_BUCKET=3 OFFSET_BIND=1 TS_GE=1 SCAN_HOURS=4
#     WINDOW_HI=5 SUBSTR_WHOLE=7 SUBSTR_IN_HOUR_STMT=0 BRANCH_FROM_FILT=1 HOUR_STMT_LINES=32
#     TEST_DEFS=13 SCAN_CLASS_CONST=2 SCAN_RECEIVERS=1 SCAN_DUP_RETURN=1
#   本批新指纹：INTENT_NOW=0 INTENT_EVENT_DT=2 UTILS_KD_DEF=1 UTILS_CD_DEF=2
#     SRC_LEARNING=0 ATTIC_LEARNING=8 BOM_PY=0 P28_TESTDEFS=6 P25_TESTDEFS=6 BOM_TESTDEFS=3
#   wc -l：intent_inference 375 / insights/utils 1309 / test_vma121 595 / predictor_shapes 280
#     / p25 127 / p28 140 / gate_scanners 77（合计 2903）
#   UTILS_CD_DEF=2 是新登记的现场：`CATEGORY_DOMAINS` 在 utils.py 顶层有 :271 与 :1091 两份，
#   逐键逐值**相同** ⇒ 后一份只是冗余重复、运行时行为不变（不是 P2-5 那种覆盖分叉）。
#   冗余本身不自主删（内置词表，#38 裁定），改由 N8 变异证明锁会响。
set -uo pipefail
SNAP=${1:-c37snap20261006a}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

# 格 -1：TOOLCHAIN —— 先量工具链，量不到就整跑作废（run8 的教训：/tmp/pylibs 在容器可写层，
# 任何一次 recreate 都会把它清空，届时 pytest 不在 => "非零 RC + 无 FAILED 行"会被读成"咬住了"）。
ex "cd $L && PYTHONPATH=/tmp/pylibs python -c 'import pytest, pyflakes; print(\"PYTEST=\" + pytest.__version__); print(\"PYFLAKES=\" + pyflakes.__version__)' && python -V"
echo TOOLCHAIN_RC=$?

# 格 0：SOURCES —— 上半是"上一批的零件没被动"，下半是本批自己的指纹。
# 期望值由本机同一组 grep 先量出来（对不上 = 快照带的不是这棵树，一切读数作废）。
ex "cd $L && R=src/memory_agent/insights/repository.py && echo SCAN_DAY=\$(grep -c _scan_day \$R) && echo HOUR_BUCKET=\$(grep -c hour_bucket \$R) && echo OFFSET_BIND=\$(grep -c 'OFFSET ?' \$R) && echo TS_GE=\$(grep -c 'ts >= ?' \$R) && echo SCAN_HOURS=\$(grep -c SCAN_HOURS \$R) && echo WINDOW_HI=\$(grep -c window_hi \$R) && echo SUBSTR_WHOLE=\$(grep -c 'substr(' \$R) && echo SUBSTR_IN_HOUR_STMT=\$(awk '/def _hour_statement/,/return .*UNION/' \$R | grep -c substr || true) && echo BRANCH_FROM_FILT=\$(awk '/def _hour_statement/,/return .*UNION/' \$R | grep -c 'filt + \[') && echo HOUR_STMT_LINES=\$(awk '/def _hour_statement/,/return .*UNION/' \$R | wc -l) && echo TEST_DEFS=\$(grep -c '^def test_' tests/test_vma_q62_daily_batch_scan.py) && echo SCAN_CLASS_CONST=\$(grep -c _class_int_const scripts/scan_day_bounds.py) && echo SCAN_RECEIVERS=\$(grep -c 'RECEIVERS = (' scripts/scan_day_bounds.py) && echo SCAN_DUP_RETURN=\$(grep -c 'return 0 if ok else 2' scripts/scan_day_bounds.py || true) && I=src/memory_agent/intent_inference.py && echo INTENT_NOW=\$(grep -c 'datetime.now()' \$I || true) && echo INTENT_EVENT_DT=\$(grep -c _event_dt \$I) && echo UTILS_KD_DEF=\$(grep -c '^KEYWORD_DOMAINS' src/memory_agent/insights/utils.py) && echo UTILS_CD_DEF=\$(grep -c '^CATEGORY_DOMAINS' src/memory_agent/insights/utils.py) && echo SRC_LEARNING=\$(ls src/memory_agent/learning_*.py 2>/dev/null | wc -l) && echo ATTIC_LEARNING=\$(ls attic/learning/learning_*.py 2>/dev/null | wc -l) && echo BOM_PY=\$(python -c 'import os;print(sum(1 for b in (\"src\",\"tests\",\"scripts\",\"attic\") for r,d,fs in os.walk(b) for f in fs if f.endswith(\".py\") and open(os.path.join(r,f),\"rb\").read(3)==b\"\xef\xbb\xbf\"))') && echo P28_TESTDEFS=\$(grep -c '^def test_' tests/test_vma_p28_learning_attic_unreachable.py) && echo P25_TESTDEFS=\$(grep -c '^def test_' tests/test_vma_p25_keyword_domains_single_definition.py) && echo BOM_TESTDEFS=\$(grep -c '^def test_' tests/test_vma_gate_scanners_read_every_py.py) && wc -l \$I src/memory_agent/insights/utils.py tests/test_vma121_intent_and_pii.py tests/test_vma_behavior_predictor_shapes.py tests/test_vma_p25_keyword_domains_single_definition.py tests/test_vma_p28_learning_attic_unreachable.py tests/test_vma_gate_scanners_read_every_py.py" > ${L}_sources.log 2>&1
echo SOURCES_RC=$?
cat ${L}_sources.log

# 门 1：pyflakes（口径 src/memory_agent；attic 不参与，用计数自证）
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -3 ${L}_gate.log
echo GATE_MENTIONS_ATTIC=$(grep -c attic ${L}_gate.log)

# 门 2：全量回归（容器 Python 3.11 是本仓权威口径）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs" > ${L}_suite.log 2>&1
echo SUITE_RC=$?
tail -6 ${L}_suite.log

# 定向 1：本批的判据链（P4-3 时钟无关 / P2-4 同日交错 / P2-5 单定义 / P2-8 不可导入 / BOM 量具可读）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma121_intent_and_pii.py tests/test_vma_behavior_predictor_shapes.py tests/test_vma_p25_keyword_domains_single_definition.py tests/test_vma_p28_learning_attic_unreachable.py tests/test_vma_gate_scanners_read_every_py.py -q -p no:cacheprovider" > ${L}_targeted.log 2>&1
echo TARGETED_RC=$?
tail -3 ${L}_targeted.log

# 定向 2：上一批的判据链必须仍然绿（本批没动 insights 读路径，这一格是自证）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_q62_daily_batch_scan.py tests/test_vma_activity_semantic.py tests/test_vma_dcd_20261004b_event_total.py tests/test_vma_p32_day_bounds.py -q -p no:cacheprovider" > ${L}_prevbatch.log 2>&1
echo PREVBATCH_RC=$?
tail -3 ${L}_prevbatch.log

# 定向 3：凡引用 insights 扫描面/门面/intent 面的既有测试（run10 的 23 个 + 本批 5 个）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_activity_inference.py tests/test_drift.py tests/test_insights_facade_contract.py tests/test_insights_house_timezone.py tests/test_process_mining.py tests/test_rule_recall.py tests/test_signal_learning.py tests/test_vma_a8_insights_clock_and_tags.py tests/test_vma_activity_semantic.py tests/test_vma_activity_window_reachability.py tests/test_vma_audit20261002_fixes.py tests/test_vma_dcd_20261004b_event_total.py tests/test_vma_insights_callsite_binding.py tests/test_vma_insights_facade_dead_tools.py tests/test_vma_insights_matrix_hour.py tests/test_vma_insights_nlquery_routes.py tests/test_vma_insights_search_filters.py tests/test_vma_insights_window_echo.py tests/test_vma_p32_day_bounds.py tests/test_vma_p37_degrade_list_trace.py tests/test_vma_q62_daily_batch_scan.py tests/test_vma_r7_seam_fixes.py tests/test_vma_step0_homesdk_time.py tests/test_vma121_intent_and_pii.py tests/test_vma_behavior_predictor_shapes.py tests/test_vma_p25_keyword_domains_single_definition.py tests/test_vma_p28_learning_attic_unreachable.py tests/test_vma_gate_scanners_read_every_py.py -q -p no:cacheprovider" > ${L}_touched.log 2>&1
echo TOUCHED_RC=$?
tail -3 ${L}_touched.log

# 量具①：day bounds 自证 + 门禁口径（src）+ 登记口径（attic）
ex "cd $L && python -V && PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py --self-test; echo SELFTEST_RC=\$?; echo ---SRC---; PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py src; echo DAYSCAN_RC=\$?; echo ---ATTIC---; PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py attic/learning; echo ATTICSCAN_RC=\$?" > ${L}_scan-days.log 2>&1
echo DAYBATCH_RC=$?
cat ${L}_scan-days.log

# 量具②：route mount 自证 + 全仓扫描（本批未动路由，应仍为绿）
ex "cd $L && PYTHONPATH=/tmp/pylibs python scripts/scan_route_mount.py --self-test; echo SELFTEST_RC=\$?; PYTHONPATH=/tmp/pylibs python scripts/scan_route_mount.py src; echo ROUTESCAN_RC=\$?"
echo ROUTEBATCH_RC=$?

# 格 P28RUNTIME：P2-8 的"不可导入"取**在役环境**的读数，不只取快照
# （快照是工作树拷贝，在役 /app 才是线上那份代码；两处都得是 ModuleNotFoundError）
ex "cd /app && PYTHONPATH=/app/src python -c 'import memory_agent, importlib
for m in (\"learning_api\",\"learning_store\",\"learning_models\",\"learning_analyzer\",\"learning_evaluator\",\"learning_feedback\",\"learning_optimizer\",\"learning_report\"):
    try:
        importlib.import_module(\"memory_agent.\" + m); print(m, \"IMPORTED\")
    except BaseException as e:
        print(m, type(e).__name__, e)' && echo LIVE_APP_OK"
echo P28_LIVE_RC=$?
ex "cd $L && PYTHONPATH=$L/src python -c 'import importlib
try:
    importlib.import_module(\"memory_agent.learning_api\"); print(\"IMPORTED\")
except BaseException as e:
    print(type(e).__name__, e)' && cd $L && PYTHONPATH=$L/src python -c 'import importlib.util
spec = importlib.util.spec_from_file_location(\"memory_agent.learning_analyzer\", \"attic/learning/learning_analyzer.py\")
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod); print(\"ATTIC_IMPORTED\")
except BaseException as e:
    print(\"ATTIC_\" + type(e).__name__, e)'"
echo P28_SNAP_RC=$?

# 门 3（自证）：两套变异各咬各的锁，容器 Python 3.11 口径
#   N1..N8 = 本批五族新锁（P4-3×2 / P2-4 / P2-5×2 / P2-8×2 / BOM）
#   M1..M10 = 上一批（run10）乙′ 分层扫描的十条：本批没碰 repository.py，
#            这一格重跑是**自证**——读数应与 run10 一字不差（5/6/5/8/3/2/4/1/1/24），
#            对不上就说明本批的改动渗到了读路径。
# 本机 3.13 预跑读数（只是预检，权威口径在这格）：N1..N8 全咬住、restored_identical=OK、MUTATION_BAD=0。
ex "cd $L && MUT_ROOT=$L PYLIBS=/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut11.py" > ${L}_mut11.log 2>&1
echo MUT11_RC=$?
grep -E '^(N[1-8] |    RC=|    异常|    无汇总行|    [0-9]+ (passed|failed)|MUTATION_BAD|restored_identical|.*锚点命中)' ${L}_mut11.log
ex "cd $L && MUT_ROOT=$L PYLIBS=/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut.py" > ${L}_mut10.log 2>&1
echo MUT10_RC=$?
grep -E '^(M[1-9]|M10 |    RC=|    异常|    无汇总行|    [0-9]+ (passed|failed)|MUTATION_BAD|restored_identical|.*PATCH_NOT_FOUND)' ${L}_mut10.log

echo REMOTE_BATCH_RC=0
