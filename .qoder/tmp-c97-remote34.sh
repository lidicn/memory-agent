#!/usr/bin/env bash
# run34（20261012 档，任务表 #89/#90）：审计 20261012 的 D1/D2/D3 + 契约 §7.1 status JSON +
# run32 之后 21 个未过门提交打红的 28 条既有用例翻新，全在这一棵容器树上认账。
# 本批**动了 src**（store / mqtt_bridge / tool_schema / env_cfg / api/system_routes / runtime）
# ⇒ 旧五档 88 腿必须当场复跑（跳过的前提在这一档不成立，由 driver 尺4 现量）。
#
# ── 这一档的格 ──
#  -1 TOOLCHAIN：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ 永远排第一。
#     0 SNAP：容器树 = 本机树（同一份 _hashes.py + 同一份 filelist，聚合摘要逐字对账）。
#   0M MUTFILE：六份 harness + 本批靶文件真在树里。
#   0P ANCHOR：100 条变异腿（新 12 + 旧 88）锚点在容器 3.11 + 这棵树上现数，命中 != 1 记 INVALID。
#   0A AST311：本批改过的文件在 3.11 下可解析。
#     1 AUDIT12：tests/test_vma_audit_20261012_fixes.py 单跑（D1/D2/D3 的 15 条，容器里一条都不许 skip）。
#     2 PRESENCE：§7.1 status JSON + 载荷版本不变式两文件单跑。
#     3 KEYS：承诺键 ↔ 运行时契约 + 文案点名键两文件单跑（本批翻面的两条旧锁在这里）。
#   4/4b/4c R6+DB、VACUUM 锁、STARTUP：D3 相关既有锁。
#     4d CONTRACT：tests/contract 单跑（计划 §8.1 件 3 的三块 schema 锁；本机这一档跳 11 条，
#                  容器有 mcp ⇒ CONTRACT_SKIPPED 必须为 0，否则那 11 条是假登记）。
#     5 MUT34：本批 12 腿，副本树 /tmp/c97mut34，快照树只读。
#  6..10 MUT33/31/81/85/82：旧五档 28+13+22+11+14 = 88 腿全量复跑。
#    11 G1..G6：六把门 self-test + 现扫（G6 = 导入期 env 转换，本轮新入门）。
#    12 GATE：pyflakes（GATES_REQUIRE=1，基线只准减）。
#    13 SUITE：全量回归（容器 3.11 权威口径）。
#    POST：二次哈希 + G4/G5/G6 复扫 + 只删自己命名的副本树。
#
# 教训照旧在册：两层引号 ⇒ `ex "…"` 里只有 $L/$SNAP 允许裸展开，其余一律 `\$`；
# 格子逐条写开；CR 按字节量；副本树必须连 scripts/ 一起拷。
set -uo pipefail
SNAP=${1:-c97snap34}
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

# 格 0M：六份 harness + 本批靶文件真在树里
ex "cd $L && wc -c ${L}_mut34.py ${L}_mut33.py ${L}_mut31.py ${L}_mut81.py ${L}_mut85.py ${L}_mut82.py ${L}_anchor.py ${L}_hashes.py src/memory_agent/env_cfg.py src/memory_agent/store.py src/memory_agent/mqtt_bridge.py src/memory_agent/tool_schema.py src/memory_agent/runtime.py src/memory_agent/api/system_routes.py scripts/scan_import_time_env_cast.py tests/test_vma_audit_20261012_fixes.py"
echo MUTFILE_RC=$?

# 格 0P：100 条锚点现数
ex "cd $L && python ${L}_anchor.py $L ${L}_mut34.py ${L}_mut33.py ${L}_mut31.py ${L}_mut81.py ${L}_mut85.py ${L}_mut82.py" > ${L}_anchor.log 2>&1
echo ANCHOR_RC=$?
cat ${L}_anchor.log

# 格 0A：容器 3.11 下可解析
ex "cd $L && python -c \"import ast,io;ps=['src/memory_agent/env_cfg.py','src/memory_agent/store.py','src/memory_agent/mqtt_bridge.py','src/memory_agent/tool_schema.py','src/memory_agent/runtime.py','src/memory_agent/api/system_routes.py','scripts/scan_import_time_env_cast.py','tests/test_vma_audit_20261012_fixes.py','${L}_mut34.py','${L}_mut33.py','${L}_mut31.py','${L}_mut81.py','${L}_mut85.py','${L}_mut82.py','${L}_anchor.py','${L}_hashes.py'];[ast.parse(io.open(p,encoding='utf-8').read(),filename=p) for p in ps];print('AST311_ALL_OK')\""
echo AST311_RC=$?

# 格 1：AUDIT12 —— 本批三份缺陷的直断言（有 skip 就是假登记）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_audit_20261012_fixes.py -q -rs" > ${L}_audit12.log 2>&1
echo AUDIT12_RC=$?
tail -3 ${L}_audit12.log
grep -c '^SKIPPED' ${L}_audit12.log | sed 's/^/AUDIT12_SKIPPED=/'

# 格 2：PRESENCE —— §7.1 载荷形状与版本单源
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_step1_adm_presence.py tests/test_vma_dcd_20261002_payload.py -q -rs" > ${L}_presence.log 2>&1
echo PRESENCE_RC=$?
tail -3 ${L}_presence.log
grep -c '^SKIPPED' ${L}_presence.log | sed 's/^/PRESENCE_SKIPPED=/'

# 格 3：KEYS —— 本批翻面的两条旧锁（recommended_tool 指向真工具 / 文案点名键）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_promised_keys_runtime_contract.py tests/test_tool_prose_promised_keys.py -q -rs" > ${L}_keys.log 2>&1
echo KEYS_RC=$?
tail -3 ${L}_keys.log
grep -c '^SKIPPED' ${L}_keys.log | sed 's/^/KEYS_SKIPPED=/'

# 格 4：DBLOCK —— D3 的既有锁（锁纪律 + 长跑修复 + 启动不阻塞）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_r6_db_lock_discipline.py tests/test_vma_r6_longrun_fixes.py tests/test_startup_identity_nonblocking.py -q -rs" > ${L}_dblock.log 2>&1
echo DBLOCK_RC=$?
tail -3 ${L}_dblock.log
grep -c '^SKIPPED' ${L}_dblock.log | sed 's/^/DBLOCK_SKIPPED=/'

# 格 4b：BATCH3 —— 共用尺子那批（本批假钟补 monotonic 的落点）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_phase2_batch3_shared_ruler.py tests/test_vma_login_rate_limit.py tests/test_mqtt_bridge.py -q -rs" > ${L}_batch3.log 2>&1
echo BATCH3_RC=$?
tail -3 ${L}_batch3.log
grep -c '^SKIPPED' ${L}_batch3.log | sed 's/^/BATCH3_SKIPPED=/'

# 格 4c：OFFLOAD —— B-01 派发权翻面（record_face_event 不再自行派发）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_r5_event_loop_offload.py tests/test_db_integrity_check_mode.py tests/test_rounds11_19_ma25_35_fixes.py tests/test_device_event_feed.py -q -rs" > ${L}_offload.log 2>&1
echo OFFLOAD_RC=$?
tail -3 ${L}_offload.log
grep -c '^SKIPPED' ${L}_offload.log | sed 's/^/OFFLOAD_SKIPPED=/'

# 格 4d：CONTRACT —— MA↔DB 契约面（本机 11 条因 mcp 旧 SDK 静默跳过，容器里必须一条不跳）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/contract -q -rs" > ${L}_contract.log 2>&1
echo CONTRACT_RC=$?
tail -3 ${L}_contract.log
grep -c '^SKIPPED' ${L}_contract.log | sed 's/^/CONTRACT_SKIPPED=/'

# 格 5：MUT34 —— 本批 12 腿
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c97mut34 MUT_OUT=/tmp/c97mut34.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut34.py" > ${L}_mut34.log 2>&1
echo MUT34_RC=$?
tail -3 ${L}_mut34.log
ex "cat /tmp/c97mut34.out" > ${L}_mut34out.log 2>&1
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED|Q-0' ${L}_mut34out.log | head -8
grep -c 'KILLED' ${L}_mut34out.log | sed 's/^/MUT34_KILLED_LINES=/'

# 格 6：MUT33 —— 上一档 28 腿
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c97mut33 MUT_OUT=/tmp/c97mut33.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut33.py" > ${L}_mut33.log 2>&1
echo MUT33_RC=$?
tail -3 ${L}_mut33.log
ex "cat /tmp/c97mut33.out" > ${L}_mut33out.log 2>&1
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED|Q-0' ${L}_mut33out.log | head -6

# 格 7：MUT31 —— 旧档 13 腿
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c97mut31 MUT_OUT=/tmp/c97mut31.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut31.py" > ${L}_mut31.log 2>&1
echo MUT31_RC=$?
ex "cat /tmp/c97mut31.out" > ${L}_mut31out.log 2>&1
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED' ${L}_mut31out.log | head -6

# 格 8：MUT81 —— 旧档 22 腿
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c97mut81 MUT_OUT=/tmp/c97mut81.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut81.py all" > ${L}_mut81.log 2>&1
echo MUT81_RC=$?
ex "cat /tmp/c97mut81.out" > ${L}_mut81out.log 2>&1
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED' ${L}_mut81out.log | head -6

# 格 9：MUT85 —— 旧档 11 腿
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c97mut85 MUT_OUT=/tmp/c97mut85.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut85.py all" > ${L}_mut85.log 2>&1
echo MUT85_RC=$?
ex "cat /tmp/c97mut85.out" > ${L}_mut85out.log 2>&1
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED' ${L}_mut85out.log | head -6

# 格 10：MUT82 —— 旧档 14 腿
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/c97mut82 MUT_OUT=/tmp/c97mut82.out MUT_PY=python PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut82.py all" > ${L}_mut82.log 2>&1
echo MUT82_RC=$?
ex "cat /tmp/c97mut82.out" > ${L}_mut82out.log 2>&1
grep -E 'SURVIVED|INVALID|totals|WT_UNTOUCHED' ${L}_mut82out.log | head -6

# 格 11：六把门 —— self-test（正负例都在里面）+ 对这棵被测树现扫
ex "cd $L && python scripts/scan_stub_claims_success.py --self-test" > ${L}_g1self.log 2>&1
echo SELFTEST_G1_RC=$?
ex "cd $L && python scripts/scan_stub_claims_success.py" > ${L}_g1scan.log 2>&1
echo SCAN_G1_RC=$?
grep -E 'STUBS=|PROBLEM=' ${L}_g1scan.log | head -2
ex "cd $L && python scripts/scan_source_of_truth_sync.py --self-test" > ${L}_g2self.log 2>&1
echo SELFTEST_G2_RC=$?
ex "cd $L && python scripts/scan_source_of_truth_sync.py" > ${L}_g2scan.log 2>&1
echo SCAN_G2_RC=$?
grep -E 'DECLARED=|PROBLEM=' ${L}_g2scan.log | head -2
ex "cd $L && python scripts/scan_cleanup_scheduled.py --self-test" > ${L}_g3self.log 2>&1
echo SELFTEST_G3_RC=$?
ex "cd $L && python scripts/scan_cleanup_scheduled.py" > ${L}_g3scan.log 2>&1
echo SCAN_G3_RC=$?
grep -E 'TARGETS=|PROBLEM=' ${L}_g3scan.log | head -2
ex "cd $L && python scripts/scan_claimed_semantics.py --self-test" > ${L}_g4self.log 2>&1
echo SELFTEST_G4_RC=$?
grep -E '^SELFTEST_MISS|^SELFTEST_FALSE' ${L}_g4self.log | head -3
ex "cd $L && python scripts/scan_claimed_semantics.py" > ${L}_g4scan.log 2>&1
echo SCAN_G4_RC=$?
grep -E 'DECLARED=|UNVERIFIED=|PROBLEM=' ${L}_g4scan.log | head -2
ex "cd $L && python scripts/scan_session_owner_parity.py --self-test" > ${L}_g5self.log 2>&1
echo SELFTEST_G5_RC=$?
grep -E '^SELFTEST_MISS|^SELFTEST_FALSE' ${L}_g5self.log | head -3
ex "cd $L && python scripts/scan_session_owner_parity.py" > ${L}_g5scan.log 2>&1
echo SCAN_G5_RC=$?
grep -E 'BRANCHES=|PROBLEM=|STALE=' ${L}_g5scan.log | head -2
ex "cd $L && python scripts/scan_import_time_env_cast.py --self-test" > ${L}_g6self.log 2>&1
echo SELFTEST_G6_RC=$?
grep -E '^SELFTEST_MISS|^SELFTEST_FALSE' ${L}_g6self.log | head -3
ex "cd $L && python scripts/scan_import_time_env_cast.py" > ${L}_g6scan.log 2>&1
echo SCAN_G6_RC=$?
grep -E 'SITES=|PROBLEM=' ${L}_g6scan.log | head -2

# 格 12：pyflakes 门（基线只准减）
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -4 ${L}_gate.log

# 格 13：全量回归（容器 3.11 权威口径）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs" > ${L}_suite.log 2>&1
echo SUITE_RC=$?
tail -6 ${L}_suite.log
grep -c '^SKIPPED' ${L}_suite.log | sed 's/^/SUITE_SKIPPED_LINES=/'
grep -E '^_{3,} |^FAILED ' ${L}_suite.log | head -10; echo SUITE_FAILNAMES_RC=$?

# POST：还原自证 —— 二次哈希 + G4/G5/G6 复扫
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_post_hashes.log 2>&1
echo POST_HASHES_RC=$?
cat ${L}_post_hashes.log
ex "cd $L && python scripts/scan_claimed_semantics.py" > ${L}_post_g4.log 2>&1
echo POST_SCAN_G4_RC=$?
grep -E 'DECLARED=|UNVERIFIED=|PROBLEM=' ${L}_post_g4.log | head -2
ex "cd $L && python scripts/scan_session_owner_parity.py" > ${L}_post_g5.log 2>&1
echo POST_SCAN_G5_RC=$?
grep -E 'BRANCHES=|PROBLEM=|STALE=' ${L}_post_g5.log | head -2
ex "cd $L && python scripts/scan_import_time_env_cast.py" > ${L}_post_g6.log 2>&1
echo POST_SCAN_G6_RC=$?
grep -E 'SITES=|PROBLEM=' ${L}_post_g6.log | head -2

# 只删自己命名的副本树；别人的 vsNN 目录一律不碰
ex "rm -rf /tmp/c97mut34 /tmp/c97mut33 /tmp/c97mut31 /tmp/c97mut81 /tmp/c97mut85 /tmp/c97mut82"
echo MUT_DST_CLEANED_RC=$?
date -Iseconds
echo REMOTE_DONE
