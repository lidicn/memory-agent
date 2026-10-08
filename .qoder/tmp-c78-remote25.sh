#!/usr/bin/env bash
# run25（20261007 档，任务表 #78：DCD 20261007 §一 裁甲——删 439 行死目录镜像 + 三把形状锁 + 一条读者锁）
#
# ── 为什么必须整跑，不能沿用 run24 ──
#   run24 之后落了 `97b7062`：`src/memory_agent/mcp_server.py` 净减 439 行（生产码），
#   `tests/test_mcp_surface_parity.py` 加 4 把锁。树变了 ⇒ run24 的聚合摘要作废，
#   快照摘要重取，pyflakes 与全量回归重新走。**这一档必须带变异档**：
#   进度文档 §十八 自己写过"下批是删 439 行的生产码改动，必须重开变异档"，说到做到。
#
# ── 这一档的格 ──
#  -1 TOOLCHAIN：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ 永远排第一。
#    0 SNAP：本机与容器跑同一份 `_hashes.py`，聚合摘要逐字对账（证明"容器里跑的就是这棵树"）。
#   0C ANCHOR：删干净了没有（字面量起点 0、派生行 0、旧说明 0）＋ 真源收成一处（绑定 1、全文计数 4）
#              ＋ 文件体量（行数/字节数是这一刀最直接的读数）。
#   0C2：锁的形状（17 个 def test_、Load-only 计数在位、describe 读者在位）。
#   0D HARNESS：mut78 的特征锚点（6 腿、只读 --verify 预检、打完补丁先 ast.parse 的语法护栏）。
#    1/2 门：pyflakes（口径 src/memory_agent，基线只准减）+ 全量回归（容器 3.11 权威口径）。
#   RUNTIME：这一档真正要的读数——**带真 SDK 走一遍读者路径**：TOOL_CATALOG/TOOL_NAMES 的条数、
#            与 build_catalog() 是否同值、describe() 给前端的整份目录条数、无 summary 条数。
#            AST 锁只证明"形状对"，运行时读数才证明"删完没人挨饿"。
#   定向 1：本批主战场（目录面三方对账 + schema 一致性）。
#   定向 2：会读目录面的（ACP / presence 的 caps.tools / 文案承诺键 / 调用点绑定 / WebUI 读键尺）。
#    3 MUT78：六条腿（控制 M-0 + L1..L5：镜像放回、真源换成就地拼装、名字退回派生行、
#             import 期读者、接入页截断 48 条），控制腿必须绿。
#             本机 3.13 档已读全（`.qoder/tmp-c78-mut78-local.out`：M-0 15 passed、
#             L1 3 / L2 2 / L3 2 / L4 1 / L5 1，`MUT78_COUNT=6 MUTATION_BAD=0`），
#             这一档要的是容器 3.11 的同一份读数（容器有真 SDK ⇒ 条数比本机多 2 条是正常的，
#             腿的"该响/对照"判定与 MUTATION_BAD 必须一致）。
#   POST：二次哈希（与格 0 逐字同）+ 原样再跑主战场 ⇒ 变异腿没碰到被测树。
#
# 教训照旧在册：变异腿只在快照树里跑；权威门在飞期间不许动树；失败名单 grep 用 `^_{3,} `；
# CRLF 文件里的锚点不许带 `\n`；本机 guard 的模式在单引号里不许多写反斜杠，
# 远端 `ex "…"` 内层双引号必须 `\"`，且 python 单引号串里不许再出现单引号。
set -uo pipefail
SNAP=${1:-c78snap20261007}
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

# 格 0C：ANCHOR —— 这一刀删干净了没有 + 真源收成一处
ex "cd $L && echo MCP_LINES=\$(wc -l < src/memory_agent/mcp_server.py); echo MCP_BYTES=\$(wc -c < src/memory_agent/mcp_server.py); echo CAT_BIND=\$(grep -cF 'TOOL_CATALOG = build_catalog()' src/memory_agent/mcp_server.py); echo CAT_TOTAL=\$(grep -cF 'TOOL_CATALOG' src/memory_agent/mcp_server.py); echo LITERAL_START=\$(grep -cF 'TOOL_CATALOG: list[dict] = [' src/memory_agent/mcp_server.py); echo DERIVED_LINE=\$(grep -cF 'TOOL_NAMES = [t[\"name\"] for t in TOOL_CATALOG]' src/memory_agent/mcp_server.py); echo NEW_COMMENT=\$(grep -cF '目录唯一真源 = ' src/memory_agent/mcp_server.py); echo OLD_COMMENT=\$(grep -cF '为兼容历史保留' src/memory_agent/mcp_server.py)"
echo ANCHOR_MCP_RC=$?

# 格 0C2：锁的形状（整行代码锚点，不读散文）
ex "cd $L && echo PAR_TEST_DEFS=\$(grep -cF 'def test_' tests/test_mcp_surface_parity.py); echo PAR_LOAD_ONLY=\$(grep -cF 'isinstance(node.ctx, ast.Load)' tests/test_mcp_surface_parity.py); echo PAR_READINGS_FN=\$(grep -cF 'def _catalog_source_readings(path):' tests/test_mcp_surface_parity.py); echo PAR_DESCRIBE_READ=\$(grep -cF '\"catalog\": TOOL_CATALOG,' src/memory_agent/mcp_server.py); echo PAR_DESCRIBE_TEST=\$(grep -cF 'def test_describe_page_serves_the_whole_spec_catalog():' tests/test_mcp_surface_parity.py)"
echo ANCHOR_LOCK_RC=$?

# 格 0D：HARNESS —— 量具特征锚点必须在。
ex "echo PATH_APPEND=\$(grep -cF 'os.pathsep' ${L}_mut78.py); echo VERIFY_FLAG=\$(grep -cF '\"--verify\"' ${L}_mut78.py); echo AST_GUARD=\$(grep -cF 'ast.parse(patched' ${L}_mut78.py); echo L_LEGS=\$(grep -cF '(\"L' ${L}_mut78.py); echo FAILED_GUARD=\$(grep -cF 'failed > 0' ${L}_mut78.py); echo SRC_UNCHANGED_FLAG=\$(grep -cF 'SRC_UNCHANGED=' ${L}_mut78.py)"
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

# 格 RUNTIME：带真 SDK 走一遍读者路径（AST 锁证形状，这条证"删完没人挨饿"）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -c 'from memory_agent import mcp_server as m, tool_schema as t; d = m.describe(); print(\"CATALOG_LEN=\" + str(len(m.TOOL_CATALOG))); print(\"NAMES_LEN=\" + str(len(m.TOOL_NAMES))); print(\"SAME_AS_SOURCE=\" + str(m.TOOL_CATALOG == t.build_catalog())); print(\"DESCRIBE_CATALOG=\" + str(len(d[\"catalog\"]))); print(\"DESCRIBE_TOOLS=\" + str(len(d[\"tools\"]))); print(\"NO_SUMMARY=\" + str(sum(1 for x in d[\"catalog\"] if not x.get(\"summary\")))); print(\"MCP_AVAILABLE=\" + str(m.MCP_AVAILABLE))'"
echo RUNTIME_RC=$?

# 定向 1：本批主战场（目录面三方对账 + schema 一致性）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_mcp_surface_parity.py tests/test_tool_schema.py -q -rs -p no:cacheprovider" > ${L}_targeted.log 2>&1
echo TARGETED_RC=$?
tail -4 ${L}_targeted.log

# 定向 2：会读目录面的（ACP / presence caps.tools / 文案承诺键 / 调用点绑定 / WebUI 读键尺）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_acp_server.py tests/test_vma_step1_adm_presence.py tests/test_vma_promised_keys_runtime_contract.py tests/test_vma_insights_callsite_binding.py tests/test_webui_payload_keys.py -q -rs -p no:cacheprovider" > ${L}_faces.log 2>&1
echo FACES_RC=$?
tail -4 ${L}_faces.log
grep -E '^_{3,} |^FAILED ' ${L}_faces.log | head -6; echo FACES_FAILNAMES_RC=$?

# 门 3（自证）：先只读预检，再跑六条腿（控制 + L1..L5），每腿只动一次性副本树
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut78.py --verify $L" > ${L}_mut25_verify.log 2>&1
echo VERIFY_RC=$?
grep -E 'VERIFY_LEGS|VERIFY_BAD' ${L}_mut25_verify.log

ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut78.py $L" > ${L}_mut78.log 2>&1
echo MUT78_RC=$?
grep -E '^(M-0|L[0-9]+|MUT78_COUNT|SRC_UNCHANGED)' ${L}_mut78.log

# 还原自证：二次哈希（与格 0 逐字同）+ 原样再跑主战场
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_post_hashes.log 2>&1
echo POST_HASHES_RC=$?
cat ${L}_post_hashes.log
diff <(sed 's/^/x/' ${L}_snap.log) <(sed 's/^/x/' ${L}_post_hashes.log) > /dev/null && echo HASHES_IDENTICAL=1 || echo HASHES_IDENTICAL=0

ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_mcp_surface_parity.py -q -p no:cacheprovider" > ${L}_post.log 2>&1
echo POST_RC=$?
tail -3 ${L}_post.log
date -Iseconds
echo REMOTE_BATCH_RC=0
