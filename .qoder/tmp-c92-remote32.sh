#!/usr/bin/env bash
# run32（20261008 档，任务表 #85「UNVERIFIED 只准减」第三批 18 → 12）：容器权威档 + 九腿新变异。
#
# ── 为什么这一批可以不带旧四档（c91/c81/c85/c82）──
#   本批只动 `scripts/scan_claimed_semantics.py`、`tests/test_vma_phase2_claims_gauge.py` 与新增用例文件，
#   `src/` 一个字节没动（判据在 driver 尺4 现读 `git diff --name-only b150000..HEAD -- src` = 0，
#   不是"我记得没动"）。旧档 60 条腿的靶全在 src，由 run31 那一档当场全杀过。
#   跳过的前提由**这一档**自己量，不写进上一档的记忆。
#
# ── 这一档的格 ──
#  -1 TOOLCHAIN：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ 永远排第一（顺带报 MCP_IMPORT_OK）。
#    0 SNAP：容器树 = 本机树（同一份 `_hashes.py` + 同一份 filelist，聚合摘要逐字对账）。
#  0M MUTFILE：harness 与本批靶件、四个用例文件真在树里（字节数与本机逐件对账在 driver 侧做）。
#    0P ANCHOR：九条腿锚点在容器 3.11 + 这棵树上现数（命中 != 1 会在 harness 里读成 INVALID）。
#    0A AST311：本批改过的文件 + harness + anchor 预检在 3.11 下可解析。
#     1 PH4：新用例文件单跑 ⇒ 必须 13 passed / PH4_SKIPPED=0（有 skip 就是假登记）。
#     2 PAIR：后三格指向的既有断言文件单跑（qb + batch3）⇒ 它们不是"名字像"，是真在跑。
#     3 LOCK：量具锁单跑（CAP 下调到 12 后仍 7 条全绿）。
#     4 MUT32：本批九腿（N01..N09），副本树 /tmp/c92mut32，快照树只读。
#     5 G1..G4：四把门 self-test + 现扫（G4 读数应当是 REGISTERED=29 / CASES=48 / UNVERIFIED=12）。
#     6 GATE：pyflakes（GATES_REQUIRE=1，基线只准减）。
#     7 SUITE：全量回归（容器 3.11 权威口径）。
#     POST：二次哈希 + G4 复扫 + 只删自己命名的副本树。
#
# 教训照旧在册：两层引号 ⇒ `ex "…"` 里只有 $L/$SNAP 允许裸展开，其余一律 `\$`；
# 格子逐条写开（for 循环变量会被 lint 判未 escape）；CR 按字节量；`-r` 是单选项；
# 副本树必须连 `scripts/` 一起拷（run31 的 Q-0 就是缺它不绿）。
set -uo pipefail
SNAP=${1:-c92snap32}
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

# 格 0M：harness 与本批靶件真在树里
ex "cd $L && wc -c ${L}_mut32.py ${L}_anchor.py src/memory_agent/store.py src/memory_agent/insights/api.py src/memory_agent/app.py scripts/scan_claimed_semantics.py tests/test_vma_phase4_claims_direct.py tests/test_vma_phase2_claims_gauge.py tests/test_vma_qb_param_landing.py tests/test_vma_phase2_batch3_shared_ruler.py"
echo MUTFILE_RC=$?

# 格 0P：九条锚点现数（靶文件树根 = $L，harness 路径按参数给全）
ex "cd $L && python ${L}_anchor.py $L ${L}_mut32.py" > ${L}_anchor.log 2>&1
echo ANCHOR_RC=$?
cat ${L}_anchor.log

# 格 0A：容器 3.11 下可解析
ex "cd $L && python -c \"import ast,io;ps=['src/memory_agent/store.py','src/memory_agent/insights/api.py','src/memory_agent/app.py','scripts/scan_claimed_semantics.py','tests/test_vma_phase4_claims_direct.py','tests/test_vma_phase2_claims_gauge.py','tests/test_vma_qb_param_landing.py','tests/test_vma_phase2_batch3_shared_ruler.py','${L}_mut32.py','${L}_anchor.py'];[ast.parse(io.open(p,encoding='utf-8').read(),filename=p) for p in ps];print('AST311_ALL_OK')\""
echo AST311_RC=$?

# 格 1：PH4 —— 新用例文件单跑（容器里一条都不许 skip）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_phase4_claims_direct.py -q -rs" > ${L}_ph4.log 2>&1
echo PH4_RC=$?
tail -3 ${L}_ph4.log
grep -c '^SKIPPED' ${L}_ph4.log | sed 's/^/PH4_SKIPPED=/'

# 格 2：PAIR —— 后三格指向的既有断言（qb + batch3）在容器里真跑
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_qb_param_landing.py tests/test_vma_phase2_batch3_shared_ruler.py -q -rs" > ${L}_pair.log 2>&1
echo PAIR_RC=$?
tail -3 ${L}_pair.log
grep -c '^SKIPPED' ${L}_pair.log | sed 's/^/PAIR_SKIPPED=/'

# 格 3：LOCK —— 量具锁单跑
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_phase2_claims_gauge.py -q -rs" > ${L}_lock.log 2>&1
echo LOCK_RC=$?
tail -3 ${L}_lock.log

# 格 4：MUT32 —— 本批九腿（副本树 = 快照树的一份拷贝，锚点命中 != 1 记 INVALID 不算杀）
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c92mut32 MUT_OUT=/tmp/c92mut32.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut32.py" > ${L}_mut32.log 2>&1
echo MUT32_RC=$?
tail -3 ${L}_mut32.log
ex "cat /tmp/c92mut32.out" > ${L}_mut32out.log 2>&1
echo MUT32OUT_RC=$?
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED|Q-0' ${L}_mut32out.log | head -8
grep -c 'KILLED' ${L}_mut32out.log | sed 's/^/MUT32_KILLED_LINES=/'

# 格 5：四把门 —— self-test（正负例都在里面）+ 对这棵被测树现扫
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

# 格 6：pyflakes 门（基线只准减）
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -4 ${L}_gate.log

# 格 7：全量回归（容器 3.11 权威口径）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs" > ${L}_suite.log 2>&1
echo SUITE_RC=$?
tail -6 ${L}_suite.log
grep -c '^SKIPPED' ${L}_suite.log | sed 's/^/SUITE_SKIPPED_LINES=/'
grep -E '^_{3,} |^FAILED ' ${L}_suite.log | head -10; echo SUITE_FAILNAMES_RC=$?

# POST：还原自证 —— 二次哈希 + G4 复扫（跑完全量回归之后树还能读出同一组读数）
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_post_hashes.log 2>&1
echo POST_HASHES_RC=$?
cat ${L}_post_hashes.log
ex "cd $L && python scripts/scan_claimed_semantics.py" > ${L}_post_g4.log 2>&1
echo POST_SCAN_G4_RC=$?
grep -E 'DECLARED=|UNVERIFIED=|PROBLEM=' ${L}_post_g4.log | head -2

# 只删自己命名的副本树；别人的 vsNN 目录一律不碰
ex "rm -rf /tmp/c92mut32"
echo MUT_DST_CLEANED_RC=$?
date -Iseconds
echo REMOTE_DONE
