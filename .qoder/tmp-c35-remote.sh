#!/usr/bin/env bash
# run6 的**远端本体**（在 NAS 上跑，由 .qoder/tmp-c35-container.sh 上传后执行）。
# 为什么拆两层：上一版把 5 层引号（本地 bash → ssh → NAS shell → 容器 sh -c）全塞在
# 一个字符串里，`\$(...)` 到底在哪一层展开没人能一眼看出——第一次跑就在
# SOURCES/GATE 两格报 `syntax error near unexpected token '('`，读数全是垃圾。
# 拆开之后这一层只剩「NAS bash → 容器 sh」，容器内的 $? 与 $(...) 都用单引号包住，
# `bash -n` 能当场验语法。
set -uo pipefail
SNAP=${1:-c35snap20261005a}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

# 快照完整性（搬家批次专属）：src 册里 learning_* 必须 0，attic 册里必须 8；
# 两份真源表在场，否则 day bounds 的 config 档整册翻红（run5 立的 SOURCES 这一格）。
ex "cd $L && echo SRC_LEARNING=\$(ls src/memory_agent/learning_*.py 2>/dev/null | wc -l) && echo ATTIC_LEARNING=\$(ls attic/learning/learning_*.py 2>/dev/null | wc -l) && echo SRC_TOTAL=\$(ls src/memory_agent/*.py | wc -l) && wc -l src/memory_agent/api/config_routes.py src/memory_agent/config.py tests/test_vma_dcd_20261005_rules.py tests/test_vma_p21_zip_pairing.py && grep -c WRITABLE_FIELDS src/memory_agent/api/config_routes.py" > ${L}_sources.log 2>&1
echo SOURCES_RC=$?
cat ${L}_sources.log

# 门 1：pyflakes（口径 src/memory_agent；attic 不参与，用计数自证）
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -3 ${L}_gate.log
echo GATE_MENTIONS_ATTIC=$(grep -c attic ${L}_gate.log)

# 门 2：全量回归
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs" > ${L}_suite.log 2>&1
echo SUITE_RC=$?
tail -20 ${L}_suite.log

# 定向 1：本批新锁（test_rule 判定链 + 通道写侧 + HTTP 面 + 挂载口径 + zip 搬家守恒）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_dcd_20261005_rules.py tests/test_vma_p32_day_bounds.py tests/test_vma_p21_zip_pairing.py -q -p no:cacheprovider" > ${L}_targeted.log 2>&1
echo TARGETED_RC=$?
tail -3 ${L}_targeted.log

# 定向 2：被改了交付面的既有测试（rule_engine / rule_lifecycle / store / behavior_routes /
#          insights 门面 / llm_routes / persona 标注 / insights_legacy 删死参）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_rule_cooldown.py tests/test_rule_recall.py tests/test_rule_trigger_retention.py tests/test_vma_r3_rule_channel.py tests/test_perception_rules.py tests/test_rule_atom_vocabulary.py tests/test_vma_behavior_predictor_shapes.py tests/test_vma121_intent_and_pii.py tests/test_insights_facade_contract.py tests/test_vma_insights_callsite_binding.py tests/test_vma_insights_nlquery_routes.py tests/test_vma_r7_seam_fixes.py -q -p no:cacheprovider" > ${L}_touched.log 2>&1
echo TOUCHED_RC=$?
tail -3 ${L}_touched.log

# 量具①：day bounds 自证 + 门禁口径（src）+ 登记口径（attic 单独扫，只量形状不当门）
ex "cd $L && python -V && PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py --self-test; echo SELFTEST_RC=\$?; echo ---SRC---; PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py src; echo DAYSCAN_RC=\$?; echo ---ATTIC---; PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py attic/learning; echo ATTICSCAN_RC=\$?" > ${L}_scan-days.log 2>&1
echo DAYBATCH_RC=$?
cat ${L}_scan-days.log

# 量具②：route mount 自证 + 全仓扫描。本批**应为绿**（unmounted 4→0），
# 所以这里不再 `|| true`——让脚本自己的退出码说话。
ex "cd $L && PYTHONPATH=/tmp/pylibs python scripts/scan_route_mount.py --self-test; echo SELFTEST_RC=\$?; PYTHONPATH=/tmp/pylibs python scripts/scan_route_mount.py src; echo ROUTESCAN_RC=\$?"
echo ROUTEBATCH_RC=$?

# 门 3（自证）：五条变异各咬各的锁，容器 Python 3.11 口径，逐字还原
ex "cd $L && MUT_ROOT=$L PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut.py" > ${L}_mut.log 2>&1
echo MUT_RC=$?
grep -E '^(M[1-5] |    RC=|MUTATION_BAD|    restored_identical|.*PATCH_NOT_FOUND)' ${L}_mut.log

echo REMOTE_BATCH_RC=0
