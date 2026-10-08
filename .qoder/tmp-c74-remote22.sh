#!/usr/bin/env bash
# run22（20261007 档，任务表 #74：对外文案不再承诺载荷里没有的键 —— 三处出货面一起收口）
#
# ── 为什么必须整跑，不能沿用 run21 ──
#   run21 之后落了两个**代码**提交：`35fdfa2`（mcp_server.py 的 handler docstring + 新用例文件）
#   与 `fec2c2c`（skills_bundle/insight/SKILL.md、static/js/pages/user_manual.js 同一族死键，
#   外加把 prose 面纳入锁的口径）。树变了 ⇒ run21 的聚合摘要作废，快照摘要必须重取，
#   全量 SUITE 与 pyflakes 两道门重新走一遍。
#
# ── 这一档的格 ──
#  -1 TOOLCHAIN：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ 永远排第一，量不到整档作废。
#    0 SNAP：本机与容器跑同一份 `_hashes.py`，聚合摘要逐字对账 ⇒ "容器里跑的就是这棵树"。
#   0C ANCHOR：#74 的新形状必须在、改前形状必须归零——
#              handler docstring 新标题 1 次、旧标题（承诺 has_data/first-last）0 次；
#              service.py 两条被 L2/L3 咬的载荷行各 1 次；`_with_window` 单行仍 5 次（#73 的形状没被破）；
#              SKILL.md 与 user_manual.js 的 `has_data`/`first/last` 各 0 次、`day_coverage` 各 1 次；
#              新锁的 PROSE_SURFACES / 两条 prose 用例各一个数。
#              注意 `has_data` 在 mcp_server.py 全文仍有 **2** 次（device_health 说的是实体目录字段，
#              那格走 legacy、载荷里真有）⇒ 这一格按 2 记账，不是 0；文案锁管的是 handler 自己的 docstring。
#   0D HARNESS：mut74 的特征锚点（追加 PYTHONPATH / 腿没跑起来时吐 stderr 尾巴 / --verify 只读预检 /
#              旧的覆盖式 PYTHONPATH 写法归零），否则跑的是没修的那份量具。
#    1/2 门：pyflakes（口径 src/memory_agent，基线只准减）+ 全量回归（容器 3.11 是本仓权威口径）。
#   定向 1：这次改动的主战场（文案锁 8 条 + window 回显 + 门面契约）。
#   定向 2：会读这三处面的——coverage 载荷、工具目录/描述、门面契约、NL 路由。
#    3 MUT74：六条腿（控制 M-0 + L1 docstring 回滚 + L2 载荷丢 peak_hours + L3 start_day 越界 +
#             L4 SKILL.md 回滚死键 + L5 手册回滚死键），控制腿必须绿；
#             本机 3.13 档已读全（`.qoder/tmp-c74-mut74-local22.out`：M-0 23 passed、
#             L1 failed=2 / L2 1 / L3 1 / L4 1 / L5 2，全部 restored=OK，`MUT_COUNT=6 MUTATION_BAD=0`），
#             这一档要的是容器 3.11 的同一份读数。
#   POST：变异跑完原样再跑同一份用例（还原自证）。
#
# 教训照旧在册：变异腿只在快照树里跑（腿的 root=$L），工作树全程零改动；权威门在飞期间不许动树；
# 失败名单的 grep 用 `^_{3,} `，别写 `^_____ `；CRLF 文件里的锚点不许带 `\n`。
set -uo pipefail
SNAP=${1:-c74snap20261007}
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

# 格 0C：ANCHOR —— #74 的形状，各一个数。
ex "cd $L && echo NEW_HEADLINE=\$(grep -cF '数据覆盖报告：逐日给出事件量与空日标记' src/memory_agent/mcp_server.py); echo OLD_HEADLINE=\$(grep -cF '数据覆盖报告：明确告诉你' src/memory_agent/mcp_server.py); echo MCP_HAS_DATA_OTHER=\$(grep -cF 'has_data' src/memory_agent/mcp_server.py); echo SVC_START_DAY=\$(grep -cF '\"start_day\": start_day,' src/memory_agent/insights/service.py); echo SVC_PEAK=\$(grep -cF '\"peak_hours\": peak,' src/memory_agent/insights/service.py); echo WITH_WINDOW=\$(grep -cF 'return self._with_window(out, tr)' src/memory_agent/insights/service.py)"
echo ANCHOR_RC=$?

# 格 0C2：prose 两面 —— 死键归零、真键点名。
ex "cd $L && echo SKILL_HAS_DATA=\$(grep -cF 'has_data' src/memory_agent/skills_bundle/insight/SKILL.md); echo SKILL_FIRSTLAST=\$(grep -cF 'first/last' src/memory_agent/skills_bundle/insight/SKILL.md); echo SKILL_DAYCOV=\$(grep -cF 'day_coverage' src/memory_agent/skills_bundle/insight/SKILL.md); echo JS_HAS_DATA=\$(grep -cF 'has_data' src/memory_agent/static/js/pages/user_manual.js); echo JS_FIRSTLAST=\$(grep -cF 'first/last' src/memory_agent/static/js/pages/user_manual.js)"
echo ANCHOR_PROSE_RC=$?

# 格 0C3：新锁自身的形状。
ex "cd $L && echo PROSE_SURFACES=\$(grep -cF 'PROSE_SURFACES' tests/test_vma_coverage_docstring_contract.py); echo PROSE_TESTS=\$(grep -cF 'def test_shipped_prose' tests/test_vma_coverage_docstring_contract.py)"
echo ANCHOR_TEST_RC=$?

# 格 0D：HARNESS —— 量具特征锚点必须在，旧覆盖写法归零。
ex "echo PATH_APPEND=\$(grep -cF 'os.pathsep' ${L}_mut74.py); echo STDERR_TAIL=\$(grep -cF -- '-160' ${L}_mut74.py); echo VERIFY_FLAG=\$(grep -cF '\"--verify\"' ${L}_mut74.py); echo OLD_CLOBBER=\$(grep -cF 'PYTHONPATH=os.path.join(root, \"src\"), JWT_SECRET' ${L}_mut74.py); echo FAILED_GUARD=\$(grep -cF 'failed > 0' ${L}_mut74.py)"
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

# 定向 1：这次改动的主战场（文案锁 + window 回显 + 门面契约）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_coverage_docstring_contract.py tests/test_vma_insights_window_echo.py tests/test_insights_facade_contract.py -q -rs -p no:cacheprovider" > ${L}_targeted.log 2>&1
echo TARGETED_RC=$?
tail -4 ${L}_targeted.log

# 定向 2：会读这三处面的（coverage 载荷 / 工具目录与描述 / 门面 / NL 路由）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_insights_matrix_hour.py tests/test_vma_insights_callsite_binding.py tests/test_vma_phase2_batch3_shared_ruler.py tests/test_vma_insights_nlquery_routes.py tests/test_vma_phase2_batch4_offload.py tests/test_vma_insights_search_filters.py tests/test_device_usage_core.py tests/test_vma_dcd_20261004b_event_total.py tests/test_vma_p21_zip_pairing.py tests/test_vma_phase2_batch1_dirty_rows.py tests/test_tool_schema.py tests/test_mcp_surface_parity.py tests/test_acp_server.py -q -rs -p no:cacheprovider" > ${L}_faces.log 2>&1
echo FACES_RC=$?
tail -4 ${L}_faces.log
grep -E '^_{3,} |^FAILED ' ${L}_faces.log | head -6; echo FACES_FAILNAMES_RC=$?

# 门 3（自证）：先只读预检，再跑六条腿（控制 + L1..L5），每腿按字节还原
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut74.py --verify $L" > ${L}_mut22_verify.log 2>&1
echo VERIFY_RC=$?
grep -E 'MUTANTS_PARSED|VERIFY_BAD|ANCHOR_BAD|SYNTAX_BAD|VERIFY_FAILED' ${L}_mut22_verify.log

ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut74.py $L" > ${L}_mut74.log 2>&1
echo MUT74_RC=$?
grep -E '^(M-0|L[0-9]+ |MUT_COUNT|MUTATION_BAD|CONTROL_BAD|VERIFY)' ${L}_mut74.log

# 还原自证：变异跑完原样再跑同一份用例
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_coverage_docstring_contract.py tests/test_vma_insights_window_echo.py -q -p no:cacheprovider" > ${L}_post.log 2>&1
echo POST_RC=$?
tail -3 ${L}_post.log
date -Iseconds
echo REMOTE_BATCH_RC=0
