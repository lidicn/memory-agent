#!/usr/bin/env bash
# run17（20261007 档，任务表 #68 第四批 + 其前 10 个未推提交的**首次**容器权威门）
#
# ── 这一档为什么必须整跑，不能沿用 run16 ──
#   run16（20261006 00:50，HEAD=38192eb）之后又落了 10 个提交（704f603 → 2a14651，`git rev-list
#   --count origin/main..HEAD` = 10 条未推），改到 54 个文件；本批 #68 第四批的 7 个文件还在工作树里
#   未提交。run16 当时读到的 7 条容器侧失败（device_usage 一族 5+1 + 未用导入那格 GATE_RC=1）
#   已由 704f603 收口，但**收口之后没有再进过容器**——所以这一档同时是：
#     ①704f603/86a849f/f4f3108/b3e9665/1753cf0/c400abd/82df0d3/f60811d/2a14651 的补门；
#     ②#68 第四批（32 处协程直调卸载 + 量具 + 新锁）的权威门。
#
# ── 与 run16 的结构差异（读数口径变了，别按 run16 的格名找）──
#   1) 新增格 0 SNAP：容器树对 filelist 逐文件按字节取 md5、整表再取聚合摘要，与本机同名档**逐字对账**。
#      以前靠"几十条 grep 锚点两侧手抄"来证"容器里跑的就是我这棵树"，那一格现在由哈希一票判掉；
#      锚点（格 0C）退回到只证"形状"：落点在不在、改前形状归零没归零、反向腿有没有被卸载。
#   2) 格 0A/0B（run16 的 insights / identity_fusion 指纹）**不再逐字复用**：那些文件在 704f603 之后
#      自己就变了（service.py 去未用导入、nlquery 的 fail-closed、identity_fusion §九 验收），
#      拿 run16 读数当期望值是错的。改由 SUITE + 定向 2 的每批锁文件实跑承担回归证据。
#   3) SCANS 格带全部七支量具：#62 的三支 + day_bounds / route_mount / zip_pairing 的 --self-test
#      + 本批的 scan_unloaded_async_io（--self-test / 一层口径 / --transitive 三读）。
#   4) MUT 两档串跑：MUT68（本批 15 条）+ MUT64（#64 那 24 条，其源文件自 run16 后又动过 ⇒ 必须重跑）。
#      本机 MUT68 档已读全：控制腿 `NOTHING → 49 passed`、15 腿全"咬住了 restored=OK"、
#      `MUT_COUNT=15 MUTATION_BAD=0`（.qoder/tmp-c68-mut-local17.out）。
#      **MUT63 从这一档摘掉，原因是出网前的预检量出来的**：run17 首跑时 `tmp-b63-mut-verify.py`
#      读 `VERIFY_BAD=4`——M1..M4 那四条的锚点命中 **0 次**（`BAD anchor=0 residue=0`）。
#      根因不是脚本坏了，是 #68 第三批把 `_num` 那把尺从 `api/behavior_routes.py` 搬进了
#      `day_bounds.py`（`git diff --stat 38192eb..HEAD -- …behavior_routes.py` = **26 行净删**）：
#      那四条缺陷的**现行锁**在 `tests/test_vma_phase2_batch3_shared_ruler.py` + day_bounds 面，
#      变异档要重跑必须**重新对锚**。"读数应与 run16 逐字相同"这个渗染自证的前提已经不成立——
#      它的前提是"那些文件一个字没动"，而字动了。⇒ 开成一格：#63 变异档需按共用尺重锚后补跑。
#   5) 出网前四把尺照旧：bash -n ×2、引号 lint、两支变异 harness 的 ast 预检（15/24/22）、
#      快照 IN_SNAP 逐格 + 本机哈希档。
#
# 教训照旧在册：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ TOOLCHAIN 排第一，量不到整跑作废；
# 变异串的语法在出网前用 ast 档量掉（SYNTAX_BAD 必须 0）；**权威门在飞的期间不许动树**；
# 失败名单的 grep 别再写 `^_____ `（run16 那一格因此空手而归：分隔线是变长下划线，五个下划线后不是空格）。
set -uo pipefail
SNAP=${1:-c68snap20261007}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

date -Iseconds
echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

# 格 -1：TOOLCHAIN —— 先量工具链，量不到就整跑作废（run8 的教训）。
ex "cd $L && PYTHONPATH=/tmp/pylibs python -c 'import pytest, pyflakes; print(\"PYTEST=\" + pytest.__version__); print(\"PYFLAKES=\" + pyflakes.__version__)' && python -V"
echo TOOLCHAIN_RC=$?

# 格 0：SNAP —— 容器树 = 本机树？逐文件按字节 md5，整表聚合摘要与本机档对逐字。
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_snap.log 2>&1
echo SNAP_RC=$?
cat ${L}_snap.log

# 格 0C：ANCHORS —— 本机与容器跑**同一份**脚本，diff 必须为空。
ex "cd $L && sh ${L}_anchors.sh" > ${L}_anchors.log 2>&1
echo ANCHORS_RC=$?
cat ${L}_anchors.log

# 格 1：SCANS —— 七支量具各就各位；本批那支要三读（自证 / 一层 / 展开），HITS=0 是硬期望。
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 python scripts/scan_qb_param_landing.py --strict; echo QBLAND_RC=\$?; PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 python scripts/scan_insights_callsites.py --strict; echo CALLSITE_RC=\$?; PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 python scripts/scan_insights_engine_attrs.py --strict; echo ATTRS_RC=\$?; PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 python scripts/scan_day_bounds.py --self-test; echo DAYBOUNDS_RC=\$?; PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 python scripts/scan_route_mount.py --self-test; echo ROUTEMOUNT_RC=\$?; PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 python scripts/scan_zip_pairing.py --self-test; echo ZIPPASS_RC=\$?; PYTHONPATH=$L:$L/src python scripts/scan_unloaded_async_io.py --self-test; echo GAUGE_SELFTEST_RC=\$?; PYTHONPATH=$L:$L/src python scripts/scan_unloaded_async_io.py src/memory_agent; echo GAUGE_DIRECT_RC=\$?; PYTHONPATH=$L:$L/src python scripts/scan_unloaded_async_io.py src/memory_agent --transitive; echo GAUGE_TRANSITIVE_RC=\$?" > ${L}_scans.log 2>&1
echo SCANS_RC=$?
grep -E '^(QBLAND_RC|CALLSITE_RC|ATTRS_RC|DAYBOUNDS_RC|ROUTEMOUNT_RC|ZIPPASS_RC|GAUGE_SELFTEST_RC|GAUGE_DIRECT_RC|GAUGE_TRANSITIVE_RC|HITS=|SELFTEST|FINDINGS=|扫描 )' ${L}_scans.log

# 门 1：pyflakes（口径 src/memory_agent；基线只准减，run16 那两格未用导入必须已经没了）
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -4 ${L}_gate.log
echo GATE_MENTIONS_ATTIC=$(grep -c attic ${L}_gate.log)

# 门 2：全量回归（容器 Python 3.11 是本仓权威口径）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs" > ${L}_suite.log 2>&1
echo SUITE_RC=$?
tail -6 ${L}_suite.log
grep -E '^_{3,} |^FAILED ' ${L}_suite.log | head -8; echo SUITE_FAILNAMES_RC=$?

# 定向 1：本批的判据链（49 条 = 20 个 def，含 30 条落点锁、三腿自咬、心跳真响、反向腿）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_phase2_batch4_offload.py -q -rs -p no:cacheprovider" > ${L}_targeted.log 2>&1
echo TARGETED_RC=$?
tail -6 ${L}_targeted.log

# 定向 2：run16 之后落的那 9 个提交各自的锁文件（device_usage / 落点绑定 / 三批 #68 / 联动码 / NL 面 / 词表 / 载荷）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_device_usage_core.py tests/test_vma_insights_callsite_binding.py tests/test_vma_phase2_batch1_dirty_rows.py tests/test_vma_phase2_batch2_numeric_bounds.py tests/test_vma_phase2_batch3_shared_ruler.py tests/test_vma_dcd_20261006_linkage_codes.py tests/test_vma_insights_nlquery_routes.py tests/test_vma_p25_keyword_domains_single_definition.py tests/test_vma_dcd_20261002_payload.py -q -rs -p no:cacheprovider" > ${L}_batches.log 2>&1
echo BATCHES_RC=$?
tail -6 ${L}_batches.log
grep -E '^_{3,} |^FAILED ' ${L}_batches.log | head -8; echo BATCHES_FAILNAMES_RC=$?

# 定向 3：#63/#64 的判据链（本批一格未动那些面 ⇒ 应全绿）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_task63_routes_input_boundary.py tests/test_vma_r5_event_loop_offload.py tests/test_vma_task64_identity_fusion.py tests/test_vma_task64_behavior_predictor.py tests/test_vma_a3_p22_auth_loop_blocking.py tests/test_change_attribution.py tests/test_p5c_counterfactual.py tests/test_vma_p32_day_bounds.py tests/test_feedback_pack.py -q -rs -p no:cacheprovider" > ${L}_prevbatch.log 2>&1
echo PREVBATCH_RC=$?
tail -6 ${L}_prevbatch.log

# 定向 4：凡引用本批六支路由的测试（TOUCHED，本机 grep -l 实测 13 个文件）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_arena_auth.py tests/test_butler_auth.py tests/test_insight_query.py tests/test_service_tokens.py tests/test_source_whitelist.py tests/test_vma120_scene_graph.py tests/test_vma20_feedback_question_entry.py tests/test_vma_insights_callsite_binding.py tests/test_vma_phase2_batch2_numeric_bounds.py tests/test_vma_phase2_batch4_offload.py tests/test_vma_r7_seam_fixes.py tests/test_vma_task64_identity_fusion.py tests/test_wo_ma_012_g1_security.py -q -rs -p no:cacheprovider" > ${L}_touched.log 2>&1
echo TOUCHED_RC=$?
tail -6 ${L}_touched.log
grep -E '^_{3,} |^FAILED ' ${L}_touched.log | head -8; echo TOUCHED_FAILNAMES_RC=$?

# 门 3（自证）：两档变异各咬各的锁，容器 Python 3.11 口径，串行跑、每腿按字节还原
ex "cd $L && MUT_ROOT=$L PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut68.py" > ${L}_mut68.log 2>&1
echo MUT68_RC=$?
grep -E '^(NOTHING|M[0-9]+ |MUT_COUNT|MUTATION_BAD|.*锚点异常|.*restored)' ${L}_mut68.log
ex "cd $L && MUT_ROOT=$L PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut64.py" > ${L}_mut64.log 2>&1
echo MUT64_RC=$?
grep -E '^(NOTHING|M[0-9]+ |MUT_COUNT|MUTATION_BAD|.*锚点异常|CONTROL_BAD)' ${L}_mut64.log
date -Iseconds
echo REMOTE_BATCH_RC=0
