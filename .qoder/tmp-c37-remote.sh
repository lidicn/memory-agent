#!/usr/bin/env bash
# run9/run10（20261005 批次 c37，DCD 20261005 §二.1 乙′ 落码 + **夹紧 ts 半开区间改法**）的**远端本体**。
# run7 的读数登记的是「分层有效但慢 25.82×」，本跑量的是改法收口后的同一棵树。
# run8 的整跑读数**作废**：容器 `/tmp/pylibs` 随 08:01 那次 recreate 被清空，
# pytest/pyflakes 都不在 ⇒ SUITE/TARGETED/TOUCHED 全是 `No module named`，
# 十条变异也被读成「咬住了」。run9 在同一棵树上重跑，并加 TOOLCHAIN 格自证版本。
# 由 .qoder/tmp-c37-driver.sh 上传后执行）。两层引号（NAS bash → 容器 sh）的形状沿用
# run6 的教训：容器内 `$?` 与 `$(...)` 一律用单引号包住，`bash -n` 当场可验语法。
#
# 本批与 run6 的形状差异（读数不能沿用）：
#   1) 只改读路径 4 个源文件 + 1 个测试文件 + 1 个探针脚本，没有搬家、没有新测试文件
#      （test_vma_q62_daily_batch_scan.py 是 #44 那批建的，本批从 8 条长到 12 条）。
#   2) SOURCES 这一格换口径：量的是**乙′ 的零件在不在**（_scan_day / hour_bucket /
#      OFFSET / 小时格 SQL 里不许有 substr），不是搬家守恒。
#   3) 多一格 PROBE：约束② 要的是生产库上的改前/改后成对读数 + 每日可见时段表，
#      只有容器里那份在役库能给（本机没有生产数据）。全程 SELECT。
set -uo pipefail
SNAP=${1:-c37snap20261005a}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

# run9/run10 这一格漏了 `PYTHONPATH=/tmp/pylibs`，于是自己报了 `No module named pytest`
# （TOOLCHAIN_RC=1 两次）——这是量具的假红，不是树的假红：pytest/pyflakes 装在 /tmp/pylibs，
# 只有带 PYTHONPATH 的格子（gate/suite）才看得见它们。已补 PYTHONPATH，并在正文补量一次：
# 现读 PYTEST=9.1.1 / PYFLAKES=4.0.2 / PY=3.11.16，TC_RC=0。
ex "cd $L && PYTHONPATH=/tmp/pylibs python -c 'import pytest, pyflakes; print(\"PYTEST=\" + pytest.__version__); print(\"PYFLAKES=\" + pyflakes.__version__)'"
echo TOOLCHAIN_RC=$?

# 量具自证（run8 的教训）：容器 08:01 被重建过（CVE 网络隔离那次 recreate），
# `/tmp/pylibs` 是**容器可写层**里的东西 ⇒ 当场被清空，`python -m pytest` 变成
# `No module named pytest`。而 pytest 的 RC 非 0 又没有 FAILED 行，变异格把十条"没跑起来"
# 全读成了「咬住了」，`MUTATION_BAD=10` 才把这层假绿戳穿。往后工具链必须在同一个快照里
# 先量一次版本，量不到就整跑作废。
# 格 0：SOURCES —— 乙′ 的零件指纹（口径：快照里的工作树内容，不是 git HEAD）。
# 期望读数由本机同一组 grep 先量出来，两处对得上才算快照带的是新代码。
# SUBSTR_IN_HOUR_STMT 的 awk 区间用 `/return .*UNION/` 收尾：docstring 里也有 "UNION ALL"
# 字样，拿它当结束图案会在第 3 行就截掉，SQL 建设段根本没被量到（本机验过一次才敢写）。
# `|| true` 是必需的：这条**期望 0 命中**，grep 无匹配时退出码 1，会把整条 `&&` 链断掉，
# 让 SOURCES_RC 变成"快照没带新代码"的假红。
ex "cd $L && R=src/memory_agent/insights/repository.py && echo SCAN_DAY=\$(grep -c _scan_day \$R) && echo HOUR_BUCKET=\$(grep -c hour_bucket \$R) && echo OFFSET_BIND=\$(grep -c 'OFFSET ?' \$R) && echo TS_GE=\$(grep -c 'ts >= ?' \$R) && echo SCAN_HOURS=\$(grep -c SCAN_HOURS \$R) && echo WINDOW_HI=\$(grep -c window_hi \$R) && echo SUBSTR_WHOLE=\$(grep -c 'substr(' \$R) && echo SUBSTR_IN_HOUR_STMT=\$(awk '/def _hour_statement/,/return .*UNION/' \$R | grep -c substr || true) && echo BRANCH_FROM_FILT=\$(awk '/def _hour_statement/,/return .*UNION/' \$R | grep -c 'filt + \[') && echo BRANCH_FROM_WHERE=\$(awk '/def _hour_statement/,/return .*UNION/' \$R | grep -c 'where + \[' || true) && echo HOUR_CLAMPS=\$(grep -c 'window_hi' \$R) && echo HOUR_STMT_LINES=\$(awk '/def _hour_statement/,/return .*UNION/' \$R | wc -l) && echo TEST_DEFS=\$(grep -c '^def test_' tests/test_vma_q62_daily_batch_scan.py) && echo PROBE_A5=\$(grep -c '=== A5' scripts/probe_q62_q2_readings.py) && echo SCAN_CLASS_CONST=\$(grep -c _class_int_const scripts/scan_day_bounds.py) && echo SCAN_RECEIVERS=\$(grep -c 'RECEIVERS = (' scripts/scan_day_bounds.py) && echo SCAN_DUP_RETURN=\$(grep -c 'return 0 if ok else 2' scripts/scan_day_bounds.py || true) && wc -l \$R src/memory_agent/insights/api.py src/memory_agent/insights/service.py tests/test_vma_q62_daily_batch_scan.py scripts/probe_q62_q2_readings.py scripts/scan_day_bounds.py" > ${L}_sources.log 2>&1
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

# 定向 1：本批的判据链（乙′ 分层 12 条 + 裁6 语义回归 + 裁5 追加 Q-A 事件总数 + P3-2 日界）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_q62_daily_batch_scan.py tests/test_vma_activity_semantic.py tests/test_vma_dcd_20261004b_event_total.py tests/test_vma_p32_day_bounds.py -q -p no:cacheprovider" > ${L}_targeted.log 2>&1
echo TARGETED_RC=$?
tail -3 ${L}_targeted.log

# 定向 2：凡引用 insights 扫描面/门面的既有测试（23 个文件，按 grep 口径列全）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_activity_inference.py tests/test_drift.py tests/test_insights_facade_contract.py tests/test_insights_house_timezone.py tests/test_process_mining.py tests/test_rule_recall.py tests/test_signal_learning.py tests/test_vma_a8_insights_clock_and_tags.py tests/test_vma_activity_semantic.py tests/test_vma_activity_window_reachability.py tests/test_vma_audit20261002_fixes.py tests/test_vma_dcd_20261004b_event_total.py tests/test_vma_insights_callsite_binding.py tests/test_vma_insights_facade_dead_tools.py tests/test_vma_insights_matrix_hour.py tests/test_vma_insights_nlquery_routes.py tests/test_vma_insights_search_filters.py tests/test_vma_insights_window_echo.py tests/test_vma_p32_day_bounds.py tests/test_vma_p37_degrade_list_trace.py tests/test_vma_q62_daily_batch_scan.py tests/test_vma_r7_seam_fixes.py tests/test_vma_step0_homesdk_time.py -q -p no:cacheprovider" > ${L}_touched.log 2>&1
echo TOUCHED_RC=$?
tail -3 ${L}_touched.log

# 量具①：day bounds 自证 + 门禁口径（src）+ 登记口径（attic 单独扫，只量形状不当门）
ex "cd $L && python -V && PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py --self-test; echo SELFTEST_RC=\$?; echo ---SRC---; PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py src; echo DAYSCAN_RC=\$?; echo ---ATTIC---; PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py attic/learning; echo ATTICSCAN_RC=\$?" > ${L}_scan-days.log 2>&1
echo DAYBATCH_RC=$?
cat ${L}_scan-days.log

# 量具②：route mount 自证 + 全仓扫描（本批未动路由，应仍为绿）
ex "cd $L && PYTHONPATH=/tmp/pylibs python scripts/scan_route_mount.py --self-test; echo SELFTEST_RC=\$?; PYTHONPATH=/tmp/pylibs python scripts/scan_route_mount.py src; echo ROUTESCAN_RC=\$?"
echo ROUTEBATCH_RC=$?

# 门 3（自证）：七条变异各咬各的锁，容器 Python 3.11 口径
ex "cd $L && MUT_ROOT=$L PYLIBS=/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut.py" > ${L}_mut.log 2>&1
echo MUT_RC=$?
grep -E '^(M[1-9]|M10 |    RC=|    异常|    无汇总行|    [0-9]+ (passed|failed)|MUTATION_BAD|restored_identical|.*PATCH_NOT_FOUND)' ${L}_mut.log

# 格 PROBE：生产库上的改前/改后成对读数（约束②）+ 每日可见时段表（判据①/②）。
# 代码取快照那份（新读路径），配置/库取在役的 /app（cwd 让 .env 生效）。全程 SELECT。
ex "cd /app && PYTHONPATH=$L/src python $L/scripts/probe_q62_q2_readings.py" > ${L}_probe.log 2>&1
echo PROBE_RC=$?
cat ${L}_probe.log

echo REMOTE_BATCH_RC=0
