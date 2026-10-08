#!/usr/bin/env bash
# run30（20261008 档，任务表 #85 的「UNVERIFIED 只准减」那一件）：容器权威档 + 六腿变异自证。
#
# ── 为什么要这一档（run29 不算数）──
#   run29 那棵树 = `8c16b06`，里面还是旧账（UNVERIFIED=26、锁 6 条）。
#   本批把三格从基线移进 REGISTRY 并补了直接断言 ⇒ "有断言"这句话必须由**变异腿**证明，
#   不是由用例名字证明：R01..R06 各自把那一格退回旧写法，六腿都必须红。
#   其中 R05/R06 的靶是 `retrieve_agent_memories` 的工具本体 ⇒ 要 mcp>=2.0 才取到已注册工具函数，
#   本机 3.13 旧 SDK 那两腿读成 SURVIVED（用例 skip），**只有容器这一档能读它们**。
#
# ── 这一档的格 ──
#  -1 TOOLCHAIN：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ 永远排第一（这格还顺带报 MCP_IMPORT_OK）。
#    0 SNAP：容器树 = 本机树（同一份 `_hashes.py` + 同一份 filelist，聚合摘要逐字对账）。
#   0M MUTFILE：变异 harness 真在容器里、逐件字节数与本机对账（对账在 driver 侧做）。
#   0A AST311：本批改过的四个文件在容器 3.11 下可解析。
#    1 LOCK：`tests/test_vma_phase2_claims_gauge.py` 单跑（BASELINE_CAP 下调后必须是 7 条且全绿）。
#    2 NEWTWO：两条新 mcp 用例按 nodeid 单跑 ⇒ 容器里必须 **2 passed**（skip 就是假登记）。
#    3 MUT：六腿变异档跑在一次性副本树 `/tmp/c90mut30`，快照树只读。
#    4 G1..G4：四把门 self-test + 对容器里这棵树现扫（G4 的读数应当变成 REGISTERED=18 / UNVERIFIED=23）。
#    5 GATE：pyflakes（GATES_REQUIRE=1，基线只准减）。
#    6 SUITE：全量回归（容器 3.11 权威口径）。
#    POST：二次哈希 + G4 复扫 + 删副本树（只删自己命名的目录）。
#
# 教训照旧在册：两层引号 ⇒ `ex "…"` 里只有 $L/$SNAP 允许裸展开，其余一律 `\$`；
# 四把门的格子逐条写开（for 循环变量会被 lint 判未 escape）；CR 按字节量；
# `-r` 是单选项（`-rf -rs` 会互相覆盖 ⇒ FAIL 名单被吞），harness 里已合并成 `-rfEs`。
set -uo pipefail
SNAP=${1:-c90snap30}
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

# 格 0M：变异 harness 与它要跑的那两个测试文件真在树里
ex "cd $L && wc -c ${L}_mut30.py src/memory_agent/insights/repository.py src/memory_agent/mcp_server.py tests/test_vma_phase2_claims_gauge.py tests/test_vma_a8_insights_clock_and_tags.py tests/test_rounds11_19_ma25_35_fixes.py"
echo MUTFILE_RC=$?

# 格 0A：容器 3.11 下可解析
ex "cd $L && python -c \"import ast,io;ps=['src/memory_agent/insights/repository.py','src/memory_agent/mcp_server.py','tests/test_vma_phase2_claims_gauge.py','tests/test_vma_a8_insights_clock_and_tags.py','tests/test_rounds11_19_ma25_35_fixes.py','${L}_mut30.py'];[ast.parse(io.open(p,encoding='utf-8').read(),filename=p) for p in ps];print('AST311_ALL_OK')\""
echo AST311_RC=$?

# 格 1：LOCK —— 量具锁单跑
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_phase2_claims_gauge.py -q -rs" > ${L}_lock.log 2>&1
echo LOCK_RC=$?
tail -4 ${L}_lock.log

# 格 2：NEWTWO —— 两条新 mcp 用例按 nodeid 单跑（容器必须 2 passed；skip 就是假登记）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_rounds11_19_ma25_35_fixes.py::test_retrieve_agent_memories_tool_body_403s_when_scope_is_not_admin tests/test_rounds11_19_ma25_35_fixes.py::test_retrieve_agent_memories_tool_body_refuses_empty_question -q -rs" > ${L}_newtwo.log 2>&1
echo NEWTWO_RC=$?
tail -3 ${L}_newtwo.log
grep -c '^SKIPPED' ${L}_newtwo.log | sed 's/^/NEWTWO_SKIPPED=/'

# 格 3：MUT —— 六腿变异（副本树，快照树只读）
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c90mut30 MUT_OUT=/tmp/c90mut30.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut30.py" > ${L}_mut.log 2>&1
echo MUT_RC=$?
tail -3 ${L}_mut.log
ex "cat /tmp/c90mut30.out" > ${L}_mutout.log 2>&1
echo MUTOUT_RC=$?
grep -E 'KILLED|SURVIVED|INVALID|totals|WT_UNTOUCHED' ${L}_mutout.log | head -12
grep -c 'SKIPPED' ${L}_mutout.log | sed 's/^/MUT_Q0_SKIPPED_LINES=/'

# 格 4：四把门 —— self-test（正负例都在里面）+ 对这棵被测树现扫
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

# 格 5：pyflakes 门（基线只准减）
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -4 ${L}_gate.log

# 格 6：全量回归（容器 3.11 权威口径）
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
ex "rm -rf /tmp/c90mut30"
echo MUT_DST_CLEANED_RC=$?
date -Iseconds
echo REMOTE_DONE
