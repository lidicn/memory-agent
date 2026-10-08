#!/usr/bin/env bash
# run29（20261008 档，任务表 #85 收尾）：四把门禁量具 + 那把锁，与既有全量套件**共处一树**的容器权威档。
#
# ── 为什么还要再跑一档，run28 不算数 ──
#   run28 的快照树 = `800c17b`，里面**没有**这五件（当时它们是未跟踪文件，塞进去就等于改了被测树）。
#   本机旁证只量了五件自己，没量"它们进树之后与既有全量套件（2038 passed + 10 skipped = 2048 收集）同跑会不会打架"。
#   这一格补的就是那一半。
#
# ── 变异腿为什么整体跳过（判据当场量，不沿用上一档结论）──
#   三档 47 腿（MUT85/MUT81/MUT82）的靶面全在 `src/memory_agent/**`；
#   driver 尺4 现读 `git diff --name-only 800c17b..HEAD -- src` 的命中数 ⇒ 为 0 才登记跳过，
#   不为 0 就整跑作废（不发容器）。本轮两格只动 `scripts/`（量具）与 `tests/`（锁），不是任何一条腿的靶面。
#
# ── 这一档的格 ──
#  -1 TOOLCHAIN：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ 永远排第一。
#    0 SNAP：容器树 = 本机树（同一份 `_hashes.py`、同一份 filelist，聚合摘要逐字对账）。
#   0N NEWFIVE：五件真在树里 + 逐件字节数（与本机对账在 driver 侧做）。
#   0A AST311：五件在容器 3.11 下可解析。
#   0G G1..G4：四把门各自 `--self-test`（正负例都在里面）+ 对这棵被测树现扫（门扫的是容器里的树，不是本机）。
#    1/2 门：pyflakes（基线只准减）+ 全量回归（容器 3.11 权威口径，含那把锁的六条）。
#      3 LOCK：锁文件单跑一格，拿到它自己的 pass/skip 读数（套件那格只给总数）。
#   POST：二次哈希（与格 0 逐字同）+ 四把门复扫（跑完全量回归之后树还能读出同一组读数）。
#
# 教训照旧在册：权威门在飞期间不动工作树；远端 `ex "…"` 内层双引号必须 `\"`，锚点挑不含引号的那一截；
# 四把门的格子逐条写开而不是 for 循环（循环变量会被两层引号 lint 判未escaped，展开时机本身没错，但门要能被 lint 罩住）；
# 失败名单 grep 用 `^_{3,} `；CR 一律按字节量；容器里的读数只能从容器里 grep。
set -uo pipefail
SNAP=${1:-c89snap29}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

date -Iseconds
echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

ex "cd $L && PYTHONPATH=/tmp/pylibs python -c 'import pytest, pyflakes; print(\"PYTEST=\" + pytest.__version__); print(\"PYFLAKES=\" + pyflakes.__version__)' && python -V"
echo TOOLCHAIN_RC=$?

# 格 0：SNAP —— 容器树 = 本机树？
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_snap.log 2>&1
echo SNAP_RC=$?
cat ${L}_snap.log

# 格 0N：五件新量具/锁真在树里，逐件字节数（本机↔容器对账在 driver 侧）
ex "cd $L && wc -c scripts/scan_stub_claims_success.py scripts/scan_source_of_truth_sync.py scripts/scan_cleanup_scheduled.py scripts/scan_claimed_semantics.py tests/test_vma_phase2_claims_gauge.py && echo NEWFILE_LISTED=\$(grep -c 'scan_' ${L}_filelist.txt)"
echo NEWFIVE_RC=$?

# 格 0A：容器 3.11 下五件可解析
ex "cd $L && python -c \"import ast,io;ps=['scripts/scan_stub_claims_success.py','scripts/scan_source_of_truth_sync.py','scripts/scan_cleanup_scheduled.py','scripts/scan_claimed_semantics.py','tests/test_vma_phase2_claims_gauge.py'];[ast.parse(io.open(p,encoding='utf-8').read(),filename=p) for p in ps];print('AST311_ALL_OK')\""
echo AST311_RC=$?

# 格 0G：四把门（G1 stub 假成功 / G2 唯一真源 / G3 清理排期 / G4 声明语义台账）
ex "cd $L && python scripts/scan_stub_claims_success.py --self-test" > ${L}_g1_self.log 2>&1
echo SELFTEST_G1_RC=$?
echo SELFTEST_G1_MISS=$(grep -cE '^SELFTEST_MISS|^SELFTEST_FALSE' ${L}_g1_self.log)
ex "cd $L && python scripts/scan_stub_claims_success.py" > ${L}_g1_scan.log 2>&1
echo SCAN_G1_RC=$?
grep -E '^ROOT=' ${L}_g1_scan.log | head -1
grep -E 'PROBLEM=[1-9]' ${L}_g1_scan.log | head -3

ex "cd $L && python scripts/scan_source_of_truth_sync.py --self-test" > ${L}_g2_self.log 2>&1
echo SELFTEST_G2_RC=$?
echo SELFTEST_G2_MISS=$(grep -cE '^SELFTEST_MISS|^SELFTEST_FALSE' ${L}_g2_self.log)
ex "cd $L && python scripts/scan_source_of_truth_sync.py" > ${L}_g2_scan.log 2>&1
echo SCAN_G2_RC=$?
grep -E '^DECLARED=' ${L}_g2_scan.log | head -1
grep -E 'PROBLEM=[1-9]' ${L}_g2_scan.log | head -3

ex "cd $L && python scripts/scan_cleanup_scheduled.py --self-test" > ${L}_g3_self.log 2>&1
echo SELFTEST_G3_RC=$?
echo SELFTEST_G3_MISS=$(grep -cE '^SELFTEST_MISS|^SELFTEST_FALSE' ${L}_g3_self.log)
ex "cd $L && python scripts/scan_cleanup_scheduled.py" > ${L}_g3_scan.log 2>&1
echo SCAN_G3_RC=$?
grep -E '^TARGETS=' ${L}_g3_scan.log | head -1
grep -E 'PROBLEM=[1-9]' ${L}_g3_scan.log | head -3

ex "cd $L && python scripts/scan_claimed_semantics.py --self-test" > ${L}_g4_self.log 2>&1
echo SELFTEST_G4_RC=$?
echo SELFTEST_G4_MISS=$(grep -cE '^SELFTEST_MISS|^SELFTEST_FALSE' ${L}_g4_self.log)
ex "cd $L && python scripts/scan_claimed_semantics.py" > ${L}_g4_scan.log 2>&1
echo SCAN_G4_RC=$?
grep -E '^SRC=' ${L}_g4_scan.log | head -1
grep -E 'PROBLEM=[1-9]' ${L}_g4_scan.log | head -3

# 门 1：pyflakes（GATES_REQUIRE=1，基线只准减）
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -4 ${L}_gate.log

# 门 2：全量回归（容器 Python 3.11 权威口径；锁文件那六条就在这一格里）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs" > ${L}_suite.log 2>&1
echo SUITE_RC=$?
tail -6 ${L}_suite.log
grep -E '^_{3,} |^FAILED ' ${L}_suite.log | head -10; echo SUITE_FAILNAMES_RC=$?
grep -c 'test_vma_phase2_claims_gauge' ${L}_suite.log | sed 's/^/SUITE_GAUGE_LOCK_LINES=/'

# 格 3：锁文件单跑（它自己的读数；套件那格只给了总数）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_phase2_claims_gauge.py -q -rs" > ${L}_lock.log 2>&1
echo LOCK_RC=$?
tail -4 ${L}_lock.log

# 变异腿：靶面与本轮 diff 零交集 ⇒ 显式跳过，判据在 driver 尺4 的现读里，不当通过也不当缺失
echo MUT_SKIPPED_IN_RUN29=1

# 还原自证：二次哈希 + 四把门复扫（跑完全量回归之后树还能读出同一组读数）
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_post_hashes.log 2>&1
echo POST_HASHES_RC=$?
cat ${L}_post_hashes.log

ex "cd $L && python scripts/scan_stub_claims_success.py" > ${L}_post_g1.log 2>&1
echo POST_SCAN_G1_RC=$?
grep -E 'PROBLEM=' ${L}_post_g1.log | head -1
ex "cd $L && python scripts/scan_source_of_truth_sync.py" > ${L}_post_g2.log 2>&1
echo POST_SCAN_G2_RC=$?
grep -E 'PROBLEM=' ${L}_post_g2.log | head -1
ex "cd $L && python scripts/scan_cleanup_scheduled.py" > ${L}_post_g3.log 2>&1
echo POST_SCAN_G3_RC=$?
grep -E 'PROBLEM=' ${L}_post_g3.log | head -1
ex "cd $L && python scripts/scan_claimed_semantics.py" > ${L}_post_g4.log 2>&1
echo POST_SCAN_G4_RC=$?
grep -E 'PROBLEM=' ${L}_post_g4.log | head -1

date -Iseconds
echo REMOTE_DONE
