#!/usr/bin/env bash
# run23（20261007 档，任务表 #75：对外文案承诺键的全仓扫描 + 三条活口收口 + 气候温度链补锁）
#
# ── 为什么必须整跑，不能沿用 run22 ──
#   run22 之后落了 `6e2d2e9`（代码提交）：`mcp_server.py` 两处 handler docstring、
#   `tool_schema.py` 三处文案，外加新用例文件 `tests/test_vma_promised_keys_runtime_contract.py`。
#   树变了 ⇒ run22 的聚合摘要作废，快照摘要重取，pyflakes 与全量回归两道门重新走。
#
# ── 这一档的格 ──
#  -1 TOOLCHAIN：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ 永远排第一。
#    0 SNAP：本机与容器跑同一份 `_hashes.py`，聚合摘要逐字对账。
#   0C ANCHOR：#75 的新形状必须在、被改掉的旧许诺必须归零。
#              `data_quality_issues` 在 mcp_server.py 全文仍按 **2** 记账（`:519` 死 TOOL_CATALOG 字面量 +
#              `:1604` device_health 的 legacy 归属文案），不是 0 —— 与 §四十八 同一口径。
#              两条引擎侧锚点（attrs 读法、room_temp_c 落点名）各 1 次，供 L6/L7 对锚。
#   0D HARNESS：mut75 的特征锚点（8 腿 = M-0 + L1..L7、只读 --verify 预检、
#              打完补丁先 ast.parse 的语法护栏必须在）。
#    1/2 门：pyflakes（口径 src/memory_agent，基线只准减）+ 全量回归（容器 3.11 权威口径）。
#   定向 1：这次改动的主战场（新文案锁 10 条 + coverage 文案锁 + window 回显 + 门面契约）。
#   定向 2：会读这四个面的（工具目录/描述、MCP 面同源、ACP、callsite 形状、NL 路由、气候与矩阵）。
#    3 MUT75：八条腿（控制 M-0 + L1..L5 文案回滚 + L6/L7 引擎侧），控制腿必须绿；
#             本机 3.13 档已读全（`.qoder/tmp-c75-mut75-local8b.out`：M-0 10 passed、
#             L1 1 / L2 1 / L3 1 / L4 2 / L5 1 / L6 1 / L7 2，`MUT75_COUNT=8 MUTATION_BAD=0`），
#             这一档要的是容器 3.11 的同一份读数。
#   POST：变异跑完原样再跑同一份用例（还原自证）。
#
# 教训照旧在册：变异腿只在快照树里跑；权威门在飞期间不许动树；失败名单 grep 用 `^_{3,} `；
# CRLF 文件里的锚点不许带 `\n`；本机 guard 的模式在单引号里不许多写反斜杠。
set -uo pipefail
SNAP=${1:-c75snap20261007}
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

# 格 0C：ANCHOR —— 文案面新形状（各一个数）
ex "cd $L && echo QC_NEW_HEADLINE=\$(grep -cF '聚合数据质量：逐项' src/memory_agent/mcp_server.py); echo MCP_DATA_QUALITY_ISSUES_OTHER=\$(grep -cF 'data_quality_issues' src/memory_agent/mcp_server.py); echo MCP_SEMANTIC_HINTS=\$(grep -cF 'semantic_hints' src/memory_agent/mcp_server.py); echo MCP_AGENT_MEMORY_HINTS=\$(grep -cF 'agent_memory_hints' src/memory_agent/mcp_server.py); echo MCP_RECOMMENDED_TOOL=\$(grep -cF 'recommended_tool' src/memory_agent/mcp_server.py); echo MCP_HINTS_BACKTICK=\$(grep -cF '另给顶层键 \`hints\`' src/memory_agent/mcp_server.py)"
echo ANCHOR_MCP_RC=$?

# 格 0C2：ToolSpec 面 —— 旧许诺归零、新点名各 1
ex "cd $L && echo SPEC_SEMANTIC_HINTS=\$(grep -cF 'semantic_hints' src/memory_agent/tool_schema.py); echo SPEC_RECOMMENDED_TOOL=\$(grep -cF 'recommended_tool' src/memory_agent/tool_schema.py); echo SPEC_INTENT_LIST=\$(grep -cF '取值是意图名（device_usage/behavior' src/memory_agent/tool_schema.py); echo SPEC_PARAM_HINTS=\$(grep -cF 'True 时保留顶层 hints' src/memory_agent/tool_schema.py)"
echo ANCHOR_SPEC_RC=$?

# 格 0C3：新锁与两条引擎锚点（L6/L7 对的就是这两行）
ex "cd $L && echo TEST_CLIMATE_DEFS=\$(grep -cF 'def test_climate' tests/test_vma_promised_keys_runtime_contract.py); echo TEST_TOTAL=\$(grep -cF 'def test_' tests/test_vma_promised_keys_runtime_contract.py); echo LEGACY_ATTRS_ANCHOR=\$(grep -cF 'cur_temp = _as_float(attrs.get(\"current_temperature\"))' src/memory_agent/insights_legacy.py); echo UTILS_ROOMTEMP_ANCHOR=\$(grep -cF '\"room_temp_c\": round(sum(rt) / len(rt), 1) if rt else None,' src/memory_agent/insights/utils.py)"
echo ANCHOR_TEST_RC=$?

# 格 0D：HARNESS —— 量具特征锚点必须在，旧覆盖写法归零。
ex "echo PATH_APPEND=\$(grep -cF 'os.pathsep' ${L}_mut75.py); echo VERIFY_FLAG=\$(grep -cF '\"--verify\"' ${L}_mut75.py); echo AST_GUARD=\$(grep -cF 'ast.parse(patched' ${L}_mut75.py); echo L_LEGS=\$(grep -cF '(\"L' ${L}_mut75.py); echo FAILED_GUARD=\$(grep -cF 'failed > 0' ${L}_mut75.py); echo SRC_UNCHANGED_FLAG=\$(grep -cF 'SRC_UNCHANGED=' ${L}_mut75.py)"
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

# 定向 1：这次改动的主战场（新文案锁 + coverage 文案锁 + window 回显 + 门面契约）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_promised_keys_runtime_contract.py tests/test_vma_coverage_docstring_contract.py tests/test_vma_insights_window_echo.py tests/test_insights_facade_contract.py -q -rs -p no:cacheprovider" > ${L}_targeted.log 2>&1
echo TARGETED_RC=$?
tail -4 ${L}_targeted.log

# 定向 2：会读这四个面的（工具目录与描述 / MCP 面同源 / ACP / callsite 形状 / NL 路由 / 气候矩阵）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_tool_schema.py tests/test_mcp_surface_parity.py tests/test_acp_server.py tests/test_vma_insights_callsite_binding.py tests/test_vma_insights_nlquery_routes.py tests/test_vma_insights_matrix_hour.py tests/test_vma_phase2_batch3_shared_ruler.py tests/test_vma_insights_search_filters.py tests/test_device_usage_core.py tests/test_vma_dcd_20261004b_event_total.py tests/test_vma_p21_zip_pairing.py tests/test_vma_phase2_batch1_dirty_rows.py -q -rs -p no:cacheprovider" > ${L}_faces.log 2>&1
echo FACES_RC=$?
tail -4 ${L}_faces.log
grep -E '^_{3,} |^FAILED ' ${L}_faces.log | head -6; echo FACES_FAILNAMES_RC=$?

# 门 3（自证）：先只读预检，再跑八条腿（控制 + L1..L7），每腿只动一次性副本树
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut75.py --verify $L" > ${L}_mut23_verify.log 2>&1
echo VERIFY_RC=$?
grep -E 'VERIFY_LEGS|VERIFY_BAD' ${L}_mut23_verify.log

ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut75.py $L" > ${L}_mut75.log 2>&1
echo MUT75_RC=$?
grep -E '^(M-0|L[0-9]+|MUT75_COUNT|SRC_UNCHANGED)' ${L}_mut75.log

# 还原自证：变异跑完原样再跑同一份用例
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_promised_keys_runtime_contract.py tests/test_vma_coverage_docstring_contract.py -q -p no:cacheprovider" > ${L}_post.log 2>&1
echo POST_RC=$?
tail -3 ${L}_post.log
date -Iseconds
echo REMOTE_BATCH_RC=0
