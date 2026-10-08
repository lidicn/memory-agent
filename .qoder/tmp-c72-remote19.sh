#!/usr/bin/env bash
# run19（20261007 档，任务表 #72：`get_data_quality` 丢键自查修复的容器权威门）
#
# ── 这一档为什么还要整跑，不能沿用 run18 ──
#   run18 之后落了 8b11437（`src/memory_agent/insights/api.py` +17/-2、
#   `tests/test_insights_facade_contract.py` 三条新锁）。那是一处**代码**改动，
#   run18 的哈希档（`cafb96a7…581112`）已经不代表这棵树 ⇒ 必须重取 SNAP 摘要，
#   并把全量 SUITE 与 pyflakes 两道门重新走一遍。
#
# ── 格名与 run18 的差别 ──
#   1) 格 0 SNAP：本机与容器跑同一份 `_hashes.py`，聚合摘要逐字对账 ⇒ "容器里跑的就是这棵树"。
#   2) 格 0C ANCHOR：只证形状三件事——合并那行命中 1 次、改前那行（`return self.core.data_quality(tr)`
#      直接落在 `get_data_quality` 里）归零、legacy 的失败形状 `{"ok": False, "error": "agent_memory 不可用"}` 在。
#   3) 门 3 MUT72：三条腿（丢整格 / 恒空 / 摘保护），控制腿 NOTHING 必须绿；
#      本机 3.13 档已读全（`.qoder/tmp-c72-mut72-local.out`：NOTHING 12 passed、
#      L1 3 failed、L2 3 failed、L3 2 failed、`MUT_COUNT=3 MUTATION_BAD=0`），
#      这一档要的是容器 3.11 的同一份读数。
#   4) 定向 2 是"这次改动碰到的面"：insights 门面契约 + 窗口回显 + 落点绑定 + 第三批共用尺。
#
# 教训照旧在册：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ TOOLCHAIN 排第一；
# 变异腿只在快照树里跑（MUT_ROOT=$L），工作树全程零改动；**权威门在飞期间不许动树**；
# 失败名单的 grep 用 `^_{3,} `，别写 `^_____ `。
set -uo pipefail
SNAP=${1:-c72snap20261007}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

date -Iseconds
echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

# 格 -1：TOOLCHAIN —— 量不到就整跑作废。
ex "cd $L && PYTHONPATH=/tmp/pylibs python -c 'import pytest, pyflakes; print(\"PYTEST=\" + pytest.__version__); print(\"PYFLAKES=\" + pyflakes.__version__)' && python -V"
echo TOOLCHAIN_RC=$?

# 格 0：SNAP —— 容器树 = 本机树？逐文件按字节 md5，整表聚合摘要与本机档对逐字。
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_snap.log 2>&1
echo SNAP_RC=$?
cat ${L}_snap.log

# 格 0C：ANCHOR —— 三个形状，各一个数。
ex "cd $L && echo MERGE_LINE=\$(grep -c 'out\[\"agent_memory\"\] = self._agent_memory_health()' src/memory_agent/insights/api.py); echo PRE_FIX_LEGS=\$(grep -c 'return self.core.data_quality(tr)' src/memory_agent/insights/api.py); echo FAIL_SHAPE=\$(grep -c 'agent_memory 不可用' src/memory_agent/insights/api.py); echo NEW_LOCKS=\$(grep -c '^def test_.*agent_memory.*\|^def test_get_data_quality_carries' tests/test_insights_facade_contract.py)"
echo ANCHOR_RC=$?

# 门 1：pyflakes（口径 src/memory_agent；基线只准减）
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -4 ${L}_gate.log

# 门 2：全量回归（容器 Python 3.11 是本仓权威口径）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs" > ${L}_suite.log 2>&1
echo SUITE_RC=$?
tail -6 ${L}_suite.log
grep -E '^_{3,} |^FAILED ' ${L}_suite.log | head -8; echo SUITE_FAILNAMES_RC=$?

# 定向 1：本批那三条锁（12 条 = 9 条既有契约锁 + 3 条新锁）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_insights_facade_contract.py -q -rs -p no:cacheprovider" > ${L}_targeted.log 2>&1
echo TARGETED_RC=$?
tail -4 ${L}_targeted.log

# 定向 2：这次改动碰到的面（窗口回显 / 落点绑定 / 第三批共用尺 / NL 面）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_insights_window_echo.py tests/test_vma_insights_callsite_binding.py tests/test_vma_phase2_batch3_shared_ruler.py tests/test_vma_insights_nlquery_routes.py tests/test_vma_phase2_batch4_offload.py -q -rs -p no:cacheprovider" > ${L}_faces.log 2>&1
echo FACES_RC=$?
tail -4 ${L}_faces.log
grep -E '^_{3,} |^FAILED ' ${L}_faces.log | head -6; echo FACES_FAILNAMES_RC=$?

# 门 3（自证）：三条变异腿只在快照树里改，每腿按字节还原
ex "cd $L && MUT_ROOT=$L PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut72.py" > ${L}_mut72.log 2>&1
echo MUT72_RC=$?
grep -E '^(NOTHING|L[0-9]+ |MUT_COUNT|MUTATION_BAD|CONTROL_BAD|.*锚点异常)' ${L}_mut72.log

# 还原自证：变异跑完原样再跑同一份用例
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_insights_facade_contract.py -q -p no:cacheprovider" > ${L}_post.log 2>&1
echo POST_RC=$?
tail -3 ${L}_post.log
date -Iseconds
echo REMOTE_BATCH_RC=0
