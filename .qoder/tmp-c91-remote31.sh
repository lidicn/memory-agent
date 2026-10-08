#!/usr/bin/env bash
# run31（20261008 档，任务表 #85「UNVERIFIED 只准减」第二批）：容器权威档 + 十三腿新变异 + 旧三档复跑。
#
# ── 为什么要复跑旧三档 ──
#   run30 的尺4 允许跳过 c81/c85/c82，前提是 `git diff --name-only 8c16b06..HEAD -- src` = 0。
#   本批改了 `src/memory_agent/app.py`（_is_trusted_source 按 docstring 收紧为 RFC1918/ULA），
#   前提不成立 ⇒ 三档全部当场复跑（60 条锚点先静态数一遍，命中 != 1 会在 harness 里读成 INVALID）。
#   c85/c82 的靶文件本批没动，但"没动"这件事由这一档自己证明，不由上一档的记忆证明。
#
# ── 这一档的格 ──
#  -1 TOOLCHAIN：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ 永远排第一（顺带报 MCP_IMPORT_OK）。
#    0 SNAP：容器树 = 本机树（同一份 `_hashes.py` + 同一份 filelist，聚合摘要逐字对账）。
#  0M MUTFILE：四份 harness 与五件被测源文件真在树里（字节数与本机逐件对账在 driver 侧做）。
#    0P ANCHOR：60 条变异腿锚点在容器 3.11 + 这棵树上现数，`ANCHOR_BAD=0`。
#    0A AST311：本批改过的文件 + 四份 harness 在 3.11 下可解析。
#     1 PH3：新用例文件单跑 ⇒ 必须 41 passed / PH3_SKIPPED=0（有 skip 就是假登记）。
#     2 LOCK：量具锁单跑（CAP 下调到 18 后仍 7 条全绿）。
#     3 NEWTWO：首批两条 mcp 用例按 nodeid 单跑（容器 2 passed，本机是 skip）。
#     4 MUT31：本批十三腿（T01..T13），副本树 /tmp/c91mut31，快照树只读。
#   5/6/7 MUT81/85/82：旧三档 22/11/14 腿全量复跑，各自副本树。
#     8 G1..G4：四把门 self-test + 现扫（G4 读数应当是 REGISTERED=23 / UNVERIFIED=18）。
#     9 GATE：pyflakes（GATES_REQUIRE=1，基线只准减）。
#    10 SUITE：全量回归（容器 3.11 权威口径）。
#    POST：二次哈希 + G4 复扫 + 只删自己命名的四个副本树。
#
# 教训照旧在册：两层引号 ⇒ `ex "…"` 里只有 $L/$SNAP 允许裸展开，其余一律 `\$`；
# 格子逐条写开（for 循环变量会被 lint 判未 escape）；CR 按字节量；`-r` 是单选项。
set -uo pipefail
SNAP=${1:-c91snap31}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

date -Iseconds
echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

# 格 -1：TOOLCHAIN
ex "cd $L && PYTHONPATH=/tmp/pylibs python -c 'import pytest, pyflakes, mcp; print(\"PYTEST=\" + pytest.__version__); print(\"PYFLAKES=\" + pyflakes.__version__); print(\"MCP_IMPORT_OK\")' && python -V"
echo TOOLCHAIN_RC=$?

# 格 0：SNAP —— 容器树 = 本机树？
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_snap.log 2>&1
echo SNAP_RC=$?
cat ${L}_snap.log

# 格 0M：四份 harness 与本批五件被测源文件真在树里
ex "cd $L && wc -c ${L}_mut31.py ${L}_mut81.py ${L}_mut85.py ${L}_mut82.py src/memory_agent/app.py src/memory_agent/auth.py src/memory_agent/mcp_tokens.py src/memory_agent/task_record.py src/memory_agent/store.py tests/test_vma_phase3_claims_direct.py tests/test_vma_phase2_claims_gauge.py"
echo MUTFILE_RC=$?

# 格 0P：60 条锚点现数（靶文件树根 = $L，harness 路径按参数给全）
ex "cd $L && python ${L}_anchor.py $L ${L}_mut31.py ${L}_mut81.py ${L}_mut85.py ${L}_mut82.py" > ${L}_anchor.log 2>&1
echo ANCHOR_RC=$?
cat ${L}_anchor.log

# 格 0A：容器 3.11 下可解析
ex "cd $L && python -c \"import ast,io;ps=['src/memory_agent/app.py','src/memory_agent/auth.py','src/memory_agent/mcp_tokens.py','src/memory_agent/task_record.py','src/memory_agent/store.py','scripts/scan_claimed_semantics.py','tests/test_vma_phase3_claims_direct.py','tests/test_vma_phase2_claims_gauge.py','${L}_mut31.py','${L}_mut81.py','${L}_mut85.py','${L}_mut82.py','${L}_anchor.py'];[ast.parse(io.open(p,encoding='utf-8').read(),filename=p) for p in ps];print('AST311_ALL_OK')\""
echo AST311_RC=$?

# 格 1：PH3 —— 新用例文件单跑（容器里一条都不许 skip）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_phase3_claims_direct.py -q -rs" > ${L}_ph3.log 2>&1
echo PH3_RC=$?
tail -3 ${L}_ph3.log
grep -c '^SKIPPED' ${L}_ph3.log | sed 's/^/PH3_SKIPPED=/'

# 格 2：LOCK —— 量具锁单跑
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_phase2_claims_gauge.py -q -rs" > ${L}_lock.log 2>&1
echo LOCK_RC=$?
tail -3 ${L}_lock.log

# 格 3：NEWTWO —— 首批两条 mcp 用例（本机旧 SDK 是 skip，只有这一档能读它们）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_rounds11_19_ma25_35_fixes.py::test_retrieve_agent_memories_tool_body_403s_when_scope_is_not_admin tests/test_rounds11_19_ma25_35_fixes.py::test_retrieve_agent_memories_tool_body_refuses_empty_question -q -rs" > ${L}_newtwo.log 2>&1
echo NEWTWO_RC=$?
tail -3 ${L}_newtwo.log
grep -c '^SKIPPED' ${L}_newtwo.log | sed 's/^/NEWTWO_SKIPPED=/'

# 格 4：MUT31 —— 本批十三腿
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c91mut31 MUT_OUT=/tmp/c91mut31.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut31.py" > ${L}_mut31.log 2>&1
echo MUT31_RC=$?
tail -3 ${L}_mut31.log
ex "cat /tmp/c91mut31.out" > ${L}_mut31out.log 2>&1
echo MUT31OUT_RC=$?
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED' ${L}_mut31out.log | head -8
grep -c 'KILLED' ${L}_mut31out.log | sed 's/^/MUT31_KILLED_LINES=/'

# 格 5：MUT81 —— 旧档 22 腿（app.py 被本批改过 ⇒ 这一档必须复跑）
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c91mut81 MUT_OUT=/tmp/c91mut81.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut81.py all" > ${L}_mut81.log 2>&1
echo MUT81_RC=$?
tail -3 ${L}_mut81.log
ex "cat /tmp/c91mut81.out" > ${L}_mut81out.log 2>&1
echo MUT81OUT_RC=$?
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED' ${L}_mut81out.log | head -8

# 格 6：MUT85 —— 旧档 11 腿
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c91mut85 MUT_OUT=/tmp/c91mut85.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut85.py all" > ${L}_mut85.log 2>&1
echo MUT85_RC=$?
tail -3 ${L}_mut85.log
ex "cat /tmp/c91mut85.out" > ${L}_mut85out.log 2>&1
echo MUT85OUT_RC=$?
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED' ${L}_mut85out.log | head -8

# 格 7：MUT82 —— 旧档 14 腿
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c91mut82 MUT_OUT=/tmp/c91mut82.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut82.py all" > ${L}_mut82.log 2>&1
echo MUT82_RC=$?
tail -3 ${L}_mut82.log
ex "cat /tmp/c91mut82.out" > ${L}_mut82out.log 2>&1
echo MUT82OUT_RC=$?
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED' ${L}_mut82out.log | head -8

# 格 8：四把门 —— self-test（正负例都在里面）+ 对这棵被测树现扫
ex "cd $L && python scripts/scan_stub_claims_success.py --self-test" > ${L}_g1self.log 2>&1
echo SELFTEST_G1_RC=$?
grep -c '^SELFTEST_' ${L}_g1self.log | sed 's/^/SELFTEST_G1_LINES=/'
ex "cd $L && python scripts/scan_stub_claims_success.py" > ${L}_g1scan.log 2>&1
echo SCAN_G1_RC=$?
grep -E 'STUBS=|PROBLEM=' ${L}_g1scan.log | head -2
ex "cd $L && python scripts/scan_source_of_truth_sync.py --self-test" > ${L}_g2self.log 2>&1
echo SELFTEST_G2_RC=$?
grep -c '^SELFTEST_' ${L}_g2self.log | sed 's/^/SELFTEST_G2_LINES=/'
ex "cd $L && python scripts/scan_source_of_truth_sync.py" > ${L}_g2scan.log 2>&1
echo SCAN_G2_RC=$?
grep -E 'DECLARED=|PROBLEM=' ${L}_g2scan.log | head -2
ex "cd $L && python scripts/scan_cleanup_scheduled.py --self-test" > ${L}_g3self.log 2>&1
echo SELFTEST_G3_RC=$?
grep -c '^SELFTEST_' ${L}_g3self.log | sed 's/^/SELFTEST_G3_LINES=/'
ex "cd $L && python scripts/scan_cleanup_scheduled.py" > ${L}_g3scan.log 2>&1
echo SCAN_G3_RC=$?
grep -E 'TARGETS=|PROBLEM=' ${L}_g3scan.log | head -2
ex "cd $L && python scripts/scan_claimed_semantics.py --self-test" > ${L}_g4self.log 2>&1
echo SELFTEST_G4_RC=$?
grep -c '^SELFTEST_' ${L}_g4self.log | sed 's/^/SELFTEST_G4_LINES=/'
grep -E '^SELFTEST_MISS|^SELFTEST_FALSE' ${L}_g4self.log | head -3
ex "cd $L && python scripts/scan_claimed_semantics.py" > ${L}_g4scan.log 2>&1
echo SCAN_G4_RC=$?
grep -E 'DECLARED=|UNVERIFIED=|PROBLEM=' ${L}_g4scan.log | head -2

# 格 9：pyflakes 门（基线只准减）
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -4 ${L}_gate.log

# 格 10：全量回归（容器 3.11 权威口径）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs" > ${L}_suite.log 2>&1
echo SUITE_RC=$?
tail -6 ${L}_suite.log
grep -E '^_{3,} |^FAILED ' ${L}_suite.log | head -10; echo SUITE_FAILNAMES_RC=$?

# POST：还原自证 —— 二次哈希 + G4 复扫（跑完全量回归之后树还能读出同一组读数）
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_post_hashes.log 2>&1
echo POST_HASHES_RC=$?
cat ${L}_post_hashes.log
ex "cd $L && python scripts/scan_claimed_semantics.py" > ${L}_post_g4.log 2>&1
echo POST_SCAN_G4_RC=$?
grep -E 'DECLARED=|UNVERIFIED=|PROBLEM=' ${L}_post_g4.log | head -2

# 只删自己命名的副本树；别人的 vsNN 目录一律不碰
ex "rm -rf /tmp/c91mut31 /tmp/c91mut81 /tmp/c91mut85 /tmp/c91mut82"
echo MUT_DST_CLEANED_RC=$?
date -Iseconds
echo REMOTE_DONE
