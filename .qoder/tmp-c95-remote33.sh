#!/usr/bin/env bash
# run33（20261008 档，任务表 #88）：DCD 三件落码（MA-29乙 / MA-32甲 / MA-34甲）+ 判例 2 入门，
# 本批**动了 src** ⇒ 旧四档 60 腿必须当场复跑（跳过的前提由上一档证明，这一档前提不成立）。
#
# ── 这一档的格 ──
#  -1 TOOLCHAIN：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ 永远排第一（顺带报 MCP_IMPORT_OK）。
#    0 SNAP：容器树 = 本机树（同一份 `_hashes.py` + 同一份 filelist，聚合摘要逐字对账）。
#  0M MUTFILE：五份 harness + 本批十件靶文件真在树里（字节数与本机逐件对账在 driver 侧做）。
#    0P ANCHOR：88 条变异腿（新 28 + 旧 60）锚点在容器 3.11 + 这棵树上现数，命中 != 1 记 INVALID。
#    0A AST311：本批改过的文件 + 新增两件 + 五份 harness 在 3.11 下可解析。
#     1 RULINGS：新用例文件单跑 ⇒ 必须 27 passed / RULINGS_SKIPPED=0（有 skip 就是假登记）。
#     2 ACP：会话属主隔离两文件单跑（R26 摘闸腿的行为面证据）。
#     3 CHANNEL：规则通道 + 设备 feed + 冷却 + 播报（MA-29 返回形状的消费方）。
#     4 MINING：test_drift + test_process_mining（两份 CRLF 文件里的显式 persist=True 在 3.11 下真跑）。
#     5 MUT33：本批 28 腿（R01..R28），副本树 /tmp/c95mut33，快照树只读。
#   6/7/8/9 MUT31/81/85/82：旧四档 13/22/11/14 腿全量复跑，各自副本树。
#    10 G1..G5：五把门 self-test + 现扫（G1 现读 STUBS=0/EXEMPT_TOTAL=0；G5 是新入门，正负例都在里面）。
#    11 GATE：pyflakes（GATES_REQUIRE=1，基线只准减）。
#    12 SUITE：全量回归（容器 3.11 权威口径）。
#    POST：二次哈希 + G4/G5 复扫 + 只删自己命名的副本树。
#
# 教训照旧在册：两层引号 ⇒ `ex "…"` 里只有 $L/$SNAP 允许裸展开，其余一律 `\$`；
# 格子逐条写开（for 循环变量会被 lint 判未 escape）；CR 按字节量；`-r` 是单选项；
# 副本树必须连 `scripts/` 一起拷（run31 的 Q-0 就是缺它不绿）。
set -uo pipefail
SNAP=${1:-c95snap33}
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

# 格 0M：五份 harness + 本批靶文件真在树里
ex "cd $L && wc -c ${L}_mut33.py ${L}_mut31.py ${L}_mut81.py ${L}_mut85.py ${L}_mut82.py ${L}_anchor.py ${L}_hashes.py src/memory_agent/rule_engine.py src/memory_agent/activity_inference.py src/memory_agent/config.py src/memory_agent/api/config_routes.py src/memory_agent/api/behavior_routes.py src/memory_agent/runtime.py src/memory_agent/static/js/pages/settings.js scripts/scan_stub_claims_success.py scripts/scan_session_owner_parity.py tests/test_vma_dcd_20261008_rulings.py"
echo MUTFILE_RC=$?

# 格 0P：88 条锚点现数（靶文件树根 = $L，harness 路径按参数给全）
ex "cd $L && python ${L}_anchor.py $L ${L}_mut33.py ${L}_mut31.py ${L}_mut81.py ${L}_mut85.py ${L}_mut82.py" > ${L}_anchor.log 2>&1
echo ANCHOR_RC=$?
cat ${L}_anchor.log

# 格 0A：容器 3.11 下可解析
ex "cd $L && python -c \"import ast,io;ps=['src/memory_agent/rule_engine.py','src/memory_agent/activity_inference.py','src/memory_agent/config.py','src/memory_agent/api/config_routes.py','src/memory_agent/api/behavior_routes.py','src/memory_agent/runtime.py','scripts/scan_stub_claims_success.py','scripts/scan_session_owner_parity.py','tests/test_vma_dcd_20261008_rulings.py','tests/test_drift.py','tests/test_process_mining.py','${L}_mut33.py','${L}_mut31.py','${L}_mut81.py','${L}_mut85.py','${L}_mut82.py','${L}_anchor.py','${L}_hashes.py'];[ast.parse(io.open(p,encoding='utf-8').read(),filename=p) for p in ps];print('AST311_ALL_OK')\""
echo AST311_RC=$?

# 格 1：RULINGS —— 新用例文件单跑（容器里一条都不许 skip）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_dcd_20261008_rulings.py -q -rs" > ${L}_rulings.log 2>&1
echo RULINGS_RC=$?
tail -3 ${L}_rulings.log
grep -c '^SKIPPED' ${L}_rulings.log | sed 's/^/RULINGS_SKIPPED=/'

# 格 2：ACP —— 属主隔离用例单跑（摘闸腿要在这里红，不是只在门里红）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_acp_session_cross_owner_denied.py tests/test_acp_round20_owner_isolation.py -q -rs" > ${L}_acp.log 2>&1
echo ACP_RC=$?
tail -3 ${L}_acp.log
grep -c '^SKIPPED' ${L}_acp.log | sed 's/^/ACP_SKIPPED=/'

# 格 3：CHANNEL —— MA-29 返回形状的消费方（规则通道 + 设备 feed + 冷却 + 播报）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_r3_rule_channel.py tests/test_device_event_feed.py tests/test_rule_cooldown.py tests/test_announcer.py -q -rs" > ${L}_channel.log 2>&1
echo CHANNEL_RC=$?
tail -3 ${L}_channel.log
grep -c '^SKIPPED' ${L}_channel.log | sed 's/^/CHANNEL_SKIPPED=/'

# 格 4：MINING —— 两份 CRLF 用例文件在 3.11 下真跑（river 缺失的 skip 要如实报出来）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_drift.py tests/test_process_mining.py -q -rs" > ${L}_mining.log 2>&1
echo MINING_RC=$?
tail -3 ${L}_mining.log
grep -c '^SKIPPED' ${L}_mining.log | sed 's/^/MINING_SKIPPED=/'

# 格 5：MUT33 —— 本批 28 腿（副本树 = 快照树的一份拷贝，锚点命中 != 1 记 INVALID 不算杀）
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c95mut33 MUT_OUT=/tmp/c95mut33.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut33.py" > ${L}_mut33.log 2>&1
echo MUT33_RC=$?
tail -3 ${L}_mut33.log
ex "cat /tmp/c95mut33.out" > ${L}_mut33out.log 2>&1
echo MUT33OUT_RC=$?
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED|Q-0' ${L}_mut33out.log | head -8
grep -c 'KILLED' ${L}_mut33out.log | sed 's/^/MUT33_KILLED_LINES=/'

# 格 6：MUT31 —— 旧档 13 腿（src 被本批改过 ⇒ 不能跳过）
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c95mut31 MUT_OUT=/tmp/c95mut31.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut31.py" > ${L}_mut31.log 2>&1
echo MUT31_RC=$?
tail -3 ${L}_mut31.log
ex "cat /tmp/c95mut31.out" > ${L}_mut31out.log 2>&1
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED' ${L}_mut31out.log | head -6

# 格 7：MUT81 —— 旧档 22 腿
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c95mut81 MUT_OUT=/tmp/c95mut81.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut81.py all" > ${L}_mut81.log 2>&1
echo MUT81_RC=$?
tail -3 ${L}_mut81.log
ex "cat /tmp/c95mut81.out" > ${L}_mut81out.log 2>&1
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED' ${L}_mut81out.log | head -6

# 格 8：MUT85 —— 旧档 11 腿
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c95mut85 MUT_OUT=/tmp/c95mut85.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut85.py all" > ${L}_mut85.log 2>&1
echo MUT85_RC=$?
tail -3 ${L}_mut85.log
ex "cat /tmp/c95mut85.out" > ${L}_mut85out.log 2>&1
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED' ${L}_mut85out.log | head -6

# 格 9：MUT82 —— 旧档 14 腿
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c95mut82 MUT_OUT=/tmp/c95mut82.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut82.py all" > ${L}_mut82.log 2>&1
echo MUT82_RC=$?
tail -3 ${L}_mut82.log
ex "cat /tmp/c95mut82.out" > ${L}_mut82out.log 2>&1
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED' ${L}_mut82out.log | head -6

# 格 10：五把门 —— self-test（正负例都在里面）+ 对这棵被测树现扫
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
ex "cd $L && python scripts/scan_session_owner_parity.py --self-test" > ${L}_g5self.log 2>&1
echo SELFTEST_G5_RC=$?
grep -c '^SELFTEST_' ${L}_g5self.log | sed 's/^/SELFTEST_G5_LINES=/'
grep -E '^SELFTEST_MISS|^SELFTEST_FALSE' ${L}_g5self.log | head -3
ex "cd $L && python scripts/scan_session_owner_parity.py" > ${L}_g5scan.log 2>&1
echo SCAN_G5_RC=$?
grep -E 'BRANCHES=|PROBLEM=|STALE=' ${L}_g5scan.log | head -2

# 格 11：pyflakes 门（基线只准减）
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -4 ${L}_gate.log

# 格 12：全量回归（容器 3.11 权威口径）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs" > ${L}_suite.log 2>&1
echo SUITE_RC=$?
tail -6 ${L}_suite.log
grep -c '^SKIPPED' ${L}_suite.log | sed 's/^/SUITE_SKIPPED_LINES=/'
grep -E '^_{3,} |^FAILED ' ${L}_suite.log | head -10; echo SUITE_FAILNAMES_RC=$?

# POST：还原自证 —— 二次哈希 + G4/G5 复扫（跑完全量回归之后树还能读出同一组读数）
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_post_hashes.log 2>&1
echo POST_HASHES_RC=$?
cat ${L}_post_hashes.log
ex "cd $L && python scripts/scan_claimed_semantics.py" > ${L}_post_g4.log 2>&1
echo POST_SCAN_G4_RC=$?
grep -E 'DECLARED=|UNVERIFIED=|PROBLEM=' ${L}_post_g4.log | head -2
ex "cd $L && python scripts/scan_session_owner_parity.py" > ${L}_post_g5.log 2>&1
echo POST_SCAN_G5_RC=$?
grep -E 'BRANCHES=|PROBLEM=|STALE=' ${L}_post_g5.log | head -2

# 只删自己命名的副本树；别人的 vsNN 目录一律不碰
ex "rm -rf /tmp/c95mut33 /tmp/c95mut31 /tmp/c95mut81 /tmp/c95mut85 /tmp/c95mut82"
echo MUT_DST_CLEANED_RC=$?
date -Iseconds
echo REMOTE_DONE
