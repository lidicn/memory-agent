#!/usr/bin/env bash
# 20261005 批次 c35（run6，DCD 20261005 §二.2 四件落码）：容器权威门。
# 本批与 run5（c32）的三处形状差别，决定了快照必须重建、读数不可沿用：
#   1) 交付面搬家：8 个 learning_* 从 src/memory_agent 移到 attic/learning（Q2=乙）。
#      attic 必须**进快照**（p32 的存档形状锁要靠它才不 skip），但**不进任何门禁口径**
#      （pyflakes 门只扫 src/memory_agent，两个扫描量具的参数写死 src）——所以这里
#      既取"门里没有 attic"的读数，也取"attic 单独扫出来仍是 4 条"的登记读数。
#   2) 新增第三个测试文件（test_vma_dcd_20261005_rules.py，32 条），且它是未跟踪文件，
#      filelist 必须靠 ls-files --others 才收得到。
#   3) 路由挂载量具本批**由红转绿**（unmounted 4→0，SCAN_RC 1→0）：那条锁换了断言口径，
#      必须在新代码上真跑一次才算数，旧读数一律作废。
# 变异自证（M1~M5）本轮一并搬进容器跑，Python 3.11 口径，别只在 3.13 上绿过就算。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c35snap20261005a
TGZ=.qoder/tmp-c35-snap.tgz

echo HEAD=$(git rev-parse --short HEAD)

{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c35-filelist.txt
echo FILES=$(wc -l < .qoder/tmp-c35-filelist.txt)
echo IN_SNAP_attic=$(grep -c '^attic/' .qoder/tmp-c35-filelist.txt)
echo IN_SNAP_newtests=$(grep -c 'test_vma_dcd_20261005_rules.py' .qoder/tmp-c35-filelist.txt)
tar -czf "$TGZ" -T .qoder/tmp-c35-filelist.txt
echo TAR_RC=$? SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; echo SCP_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage; echo STAGE_RC=\$?"
echo SYNC_RC=$?

# 变异脚本单独搬进去（它只在 .qoder/ 活着，不属于交付面）
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c34-mut-rulings.py "$NAS:/tmp/ma_mut_c35.py"; echo MUTSCP_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "docker cp /tmp/ma_mut_c35.py memory-agent:/tmp/${SNAP}_mut.py && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut.py && rm -f /tmp/ma_mut_c35.py; echo MUTSTAGE_RC=\$?"

# 快照完整性（搬家批次专属）：src 册里 learning_* 必须为 0，attic 册里必须为 8；
# 两份真源表（config_routes / config）仍要在，否则 day bounds 的 config 档整册翻红。
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && echo SRC_LEARNING=\\\$(ls src/memory_agent/learning_*.py 2>/dev/null | wc -l) && echo ATTIC_LEARNING=\\\$(ls attic/learning/learning_*.py 2>/dev/null | wc -l) && wc -l src/memory_agent/api/config_routes.py src/memory_agent/config.py tests/test_vma_dcd_20261005_rules.py && grep -c WRITABLE_FIELDS src/memory_agent/api/config_routes.py'; echo SOURCES_RC=\$?"

# 门 1：pyflakes（口径 src/memory_agent；attic 不参与，用计数自证）
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh' > /tmp/${SNAP}_gate.log 2>&1; echo GATE_RC=\$?; tail -3 /tmp/${SNAP}_gate.log; echo GATE_MENTIONS_ATTIC=\\\$(grep -c attic /tmp/${SNAP}_gate.log)"

# 门 2：全量回归
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP:/tmp/$SNAP/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs' > /tmp/${SNAP}_suite.log 2>&1; echo SUITE_RC=\$?; tail -20 /tmp/${SNAP}_suite.log"

# 定向 1：本批新锁（test_rule 判定链 + 通道写侧 + HTTP 面 + 挂载口径改写）
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_dcd_20261005_rules.py tests/test_vma_p32_day_bounds.py -q -p no:cacheprovider' > /tmp/${SNAP}_targeted.log 2>&1; echo TARGETED_RC=\$?; tail -3 /tmp/${SNAP}_targeted.log"

# 定向 2：被改了交付面的既有测试（rule_engine / rule_lifecycle / store / behavior_routes /
#          insights 门面 / llm_routes / persona 标注 / insights_legacy 删死参）
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_rule_cooldown.py tests/test_rule_recall.py tests/test_rule_trigger_retention.py tests/test_vma_r3_rule_channel.py tests/test_perception_rules.py tests/test_rule_atom_vocabulary.py tests/test_vma_behavior_predictor_shapes.py tests/test_vma121_intent_and_pii.py tests/test_insights_facade_contract.py tests/test_vma_insights_callsite_binding.py tests/test_vma_insights_nlquery_routes.py tests/test_vma_r7_seam_fixes.py -q -p no:cacheprovider' > /tmp/${SNAP}_touched.log 2>&1; echo TOUCHED_RC=\$?; tail -3 /tmp/${SNAP}_touched.log"

# 量具①：day bounds 自证 + 门禁口径（src）+ 登记口径（attic 单独扫，只量形状不当门）
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && python -V && PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py --self-test; echo SELFTEST_RC=\$?; echo ---SRC---; PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py src; echo DAYSCAN_RC=\$?; echo ---ATTIC---; PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py attic/learning || true' > /tmp/${SNAP}_scan-days.log 2>&1; echo DAYBATCH_RC=\$?; cat /tmp/${SNAP}_scan-days.log"

# 量具②：route mount 自证 + 全仓扫描。本批**应为绿**（unmounted 4→0），不再 || true——
# 让它自己决定退出码，红了就说明挂载锁的口径没真落到位。
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/pylibs python scripts/scan_route_mount.py --self-test; echo SELFTEST_RC=\$?; PYTHONPATH=/tmp/pylibs python scripts/scan_route_mount.py src; echo ROUTESCAN_RC=\$?'"

# 门 3（自证）：五条变异各咬各的锁，容器 Python 3.11 口径，逐字还原
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && export MUT_ROOT=/tmp/$SNAP PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs JWT_SECRET=ci-test && python /tmp/${SNAP}_mut.py' > /tmp/${SNAP}_mut.log 2>&1; echo MUT_RC=\$?; grep -E 'M1 |M2 |M3 |M4 |M5 |RC=|MUTATION_BAD|restored_identical|PATCH_NOT_FOUND' /tmp/${SNAP}_mut.log"

echo CONTAINER_BATCH_RC=0
