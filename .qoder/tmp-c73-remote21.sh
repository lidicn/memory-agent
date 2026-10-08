#!/usr/bin/env bash
# run21（20261007 档，任务表 #73：`coverage` / `data_quality` 接上 `window` 通用回显的容器权威门）
#
# ── 为什么必须整跑，不能沿用 run19/20 ──
#   run20 之后落了 84f2fca（`src/memory_agent/insights/service.py` 两格改形状 +
#   `tests/test_vma_insights_window_echo.py` 扩展参数化并新增门面锁）。这是**代码**改动 ⇒
#   run19 的聚合摘要 `b750ca63…1a0cdbd` 已不代表这棵树，快照摘要必须重取，
#   全量 SUITE 与 pyflakes 两道门重新走一遍。
#
# ── 这一档的格 ──
#   -1 TOOLCHAIN：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ 永远排第一，量不到整档作废。
#    0 SNAP：本机与容器跑同一份 `_hashes.py`，聚合摘要逐字对账 ⇒ "容器里跑的就是这棵树"。
#   0C ANCHOR：#73 的新形状必须在、改前形状必须归零——
#              `return self._with_window(out, tr)` 全文件 5 次（usage/device_health/
#              behavior_insights + 这次两格）；改前两格走的是 `return self._fail(...)` 直出，
#              那两条字面量必须归零；新锁与参数化各一个数。
#   0D HARNESS：run19 那两条量具坑的锚点（续 PYTHONPATH / 报 stderr）在 `_mut73.py` 里照样要在，
#              否则跑的是没修的那份量具。
#    1/2 门：pyflakes（口径 src/memory_agent，基线只准减）+ 全量回归（容器 3.11 是本仓权威口径）。
#   定向 1：这次改动的主战场（window 回显 27 条 + 门面契约）。
#   定向 2：会读这两格的面——所有提到 `window` 或 `data_quality` 的用例文件。
#    3 MUT73：三条腿（coverage 丢回显 / data_quality 丢回显 / window 键在但恒空壳），
#             控制腿 NOTHING 必须绿；本机 3.13 档已读全（`.qoder/tmp-c73-mut73-local.out`：
#             NOTHING 27 passed、L1 4 failed、L2 4 failed、L3 13 failed、`MUT_COUNT=3 MUTATION_BAD=0`），
#             这一档要的是容器 3.11 的同一份读数。
#   POST：变异跑完原样再跑同一份用例（还原自证）。
#
# 教训照旧在册：变异腿只在快照树里跑（MUT_ROOT=$L），工作树全程零改动；权威门在飞期间不许动树；
# 失败名单的 grep 用 `^_{3,} `，别写 `^_____ `；腿没跑起来时把 stderr 尾巴也报出来。
set -uo pipefail
SNAP=${1:-c73snap20261007}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

date -Iseconds
echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

ex "cd $L && PYTHONPATH=/tmp/pylibs python -c 'import pytest, pyflakes; print(\"PYTEST=\" + pytest.__version__); print(\"PYFLAKES=\" + pyflakes.__version__)' && python -V"
echo TOOLCHAIN_RC=$?

# 格 0：SNAP —— 容器树 = 本机树？逐文件按字节 md5，整表聚合摘要与本机档对逐字。
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_snap.log 2>&1
echo SNAP_RC=$?
cat ${L}_snap.log

# 格 0C：ANCHOR —— #73 的形状，各一个数。
ex "cd $L && echo WITH_WINDOW=\$(grep -cF 'return self._with_window(out, tr)' src/memory_agent/insights/service.py); echo PRE_FIX_COV=\$(grep -cF 'return self._fail(\"coverage\", exc' src/memory_agent/insights/service.py); echo PRE_FIX_DQ=\$(grep -cF 'return self._fail(\"data_quality\", exc' src/memory_agent/insights/service.py); echo NEW_LOCK=\$(grep -cF 'def test_outward_facade_reads_carry_the_window' tests/test_vma_insights_window_echo.py); echo PARAM_LIST=\$(grep -cF '\"coverage\", \"data_quality\"' tests/test_vma_insights_window_echo.py)"
echo ANCHOR_RC=$?

# 格 0D：HARNESS —— 量具那两条坑的锚点必须在（续 PATH / 报 stderr），旧覆盖写法归零。
ex "echo PATH_APPEND=\$(grep -cF 'os.pathsep.join' ${L}_mut73.py); echo OLD_CLOBBER=\$(grep -cF 'PYTHONPATH=os.path.join(root, \"src\"), JWT_SECRET' ${L}_mut73.py); echo STDERR_SHOWN=\$(grep -cF 'NO_PYTEST_OUTPUT' ${L}_mut73.py); echo FAILED_GUARD=\$(grep -cF 'failed > 0' ${L}_mut73.py)"
echo HARNESS_RC=$?

# 门 1：pyflakes（口径 src/memory_agent；基线只准减）
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -4 ${L}_gate.log

# 门 2：全量回归（容器 Python 3.11 是本仓权威口径）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs" > ${L}_suite.log 2>&1
echo SUITE_RC=$?
tail -6 ${L}_suite.log
grep -E '^_{3,} |^FAILED ' ${L}_suite.log | head -8; echo SUITE_FAILNAMES_RC=$?

# 定向 1：这次改动的主战场（window 回显 + 门面契约）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_insights_window_echo.py tests/test_insights_facade_contract.py -q -rs -p no:cacheprovider" > ${L}_targeted.log 2>&1
echo TARGETED_RC=$?
tail -4 ${L}_targeted.log

# 定向 2：会读这两格的面（所有提到 window 或 data_quality 的用例）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_insights_matrix_hour.py tests/test_vma_insights_callsite_binding.py tests/test_vma_phase2_batch3_shared_ruler.py tests/test_vma_insights_nlquery_routes.py tests/test_vma_phase2_batch4_offload.py tests/test_vma_insights_search_filters.py tests/test_device_usage_core.py tests/test_vma_dcd_20261004b_event_total.py tests/test_vma_p21_zip_pairing.py tests/test_vma_phase2_batch1_dirty_rows.py -q -rs -p no:cacheprovider" > ${L}_faces.log 2>&1
echo FACES_RC=$?
tail -4 ${L}_faces.log
grep -E '^_{3,} |^FAILED ' ${L}_faces.log | head -6; echo FACES_FAILNAMES_RC=$?

# 门 3（自证）：先只读预检，再跑四条腿（控制 + L1/L2/L3），每腿按字节还原
ex "cd $L && MUT_ROOT=$L PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut73.py --verify" > ${L}_mut21_verify.log 2>&1
echo VERIFY_RC=$?
grep -E 'MUTANTS_PARSED|VERIFY_BAD|ANCHOR_BAD|SYNTAX_BAD' ${L}_mut21_verify.log

ex "cd $L && MUT_ROOT=$L PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut73.py" > ${L}_mut73.log 2>&1
echo MUT73_RC=$?
grep -E '^(NOTHING|L[0-9]+ |MUT_COUNT|MUTATION_BAD|CONTROL_BAD|.*锚点异常)' ${L}_mut73.log
grep -E 'NO_PYTEST_OUTPUT' ${L}_mut73.log

# 还原自证：变异跑完原样再跑同一份用例
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_insights_window_echo.py tests/test_insights_facade_contract.py -q -p no:cacheprovider" > ${L}_post.log 2>&1
echo POST_RC=$?
tail -3 ${L}_post.log
date -Iseconds
echo REMOTE_BATCH_RC=0
