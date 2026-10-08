#!/usr/bin/env bash
# 20261005 批次 c32（run5，终树含 #53 之后新收的同族第三处：`config` 假豁免）：容器权威门。
# 与 c31（run4）的差别：本批只动了 3 个文件——
#   scripts/scan_day_bounds.py（新增两条 `config` 归属前提 + 真源对照自检）、
#   src/memory_agent/vision_service.py（HTTP 可写保留期收口）、
#   src/memory_agent/learning_api.py（两处非应用配置的 dataclass 窗口内联收敛），
#   外加 tests/test_vma_p32_day_bounds.py 的 4 条新锁。
# 这是**代码改动**不是注释改动，所以 run4 的读数不能顶替本轮：交付面读数必须在容器（Python 3.11）重取，
# 尤其因为量具现在要读两份真源表（config_routes.WRITABLE_FIELDS 与 config.Config 字段），
# 快照里少一份文件就会让整个 `config` 档翻成 external 判红——这条在自检里有独立读数。
# 探针（c31 的 A~E 段）本批不重跑：三处改动里只有 vision 有运行时面，已由 p32 的 vision 锁量过，
# 探针表达式级 C 段不含新站点。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c32snap20261005a
TGZ=.qoder/tmp-c32-snap.tgz

echo HEAD=$(git rev-parse --short HEAD)

{ git ls-files -- src tests scripts benchmarks .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c32-filelist.txt
echo FILES=$(wc -l < .qoder/tmp-c32-filelist.txt)
tar -czf "$TGZ" -T .qoder/tmp-c32-filelist.txt
echo TAR_RC=$? SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; echo SCP_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage; echo STAGE_RC=\$?"
echo SYNC_RC=$?

# 快照完整性：本批量具要读的两份真源必须真在树里（少一份 = 整册判红，先量出来再跑门）
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && wc -l src/memory_agent/api/config_routes.py src/memory_agent/config.py && grep -c WRITABLE_FIELDS src/memory_agent/api/config_routes.py'; echo SOURCES_RC=\$?"

# 门 1：pyflakes（含 scripts/scan_day_bounds.py 与两个改动的 src 模块）
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh' > /tmp/${SNAP}_gate.log 2>&1; echo GATE_RC=\$?; tail -3 /tmp/${SNAP}_gate.log"

# 门 2：全量回归
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP:/tmp/$SNAP/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs' > /tmp/${SNAP}_suite.log 2>&1; echo SUITE_RC=\$?; tail -20 /tmp/${SNAP}_suite.log"

# 定向 1：本批新锁（vision 保留期三条 + 量具归属规则一条）
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_p32_day_bounds.py tests/test_vma_p37_degrade_list_trace.py -q -p no:cacheprovider' > /tmp/${SNAP}_targeted.log 2>&1; echo TARGETED_RC=\$?; tail -3 /tmp/${SNAP}_targeted.log"

# 定向 2：被改了交付面的模块，既有测试必须照旧绿（c31 的 8 条 + 本批 vision 4 条）
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_activity_inference.py tests/test_change_attribution.py tests/test_daily_profile.py tests/test_summary_queries.py tests/test_vma_insights_window_echo.py tests/test_rule_trigger_retention.py tests/test_insights_house_timezone.py tests/test_mcp_surface_parity.py tests/test_vlm_gate.py tests/test_p1_14_15_16_vision_trust.py tests/test_vision_conv.py tests/test_vma120_scene_graph.py -q -p no:cacheprovider' > /tmp/${SNAP}_touched.log 2>&1; echo TOUCHED_RC=\$?; tail -3 /tmp/${SNAP}_touched.log"

# 量具①：day bounds 自证 + 全仓扫描（容器 Python 3.11）
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && python -V && PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py --self-test; echo SELFTEST_RC=\$?; PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py src' > /tmp/${SNAP}_scan-days.log 2>&1; echo DAYSCAN_RC=\$?; cat /tmp/${SNAP}_scan-days.log"

# 量具②：route mount 自证 + 全仓扫描。按设计仍红 4 条（呈 DCD），这里取读数量不取门
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/pylibs python scripts/scan_route_mount.py --self-test; echo SELFTEST_RC=\$?; PYTHONPATH=/tmp/pylibs python scripts/scan_route_mount.py src || true' > /tmp/${SNAP}_scan-routes.log 2>&1; echo ROUTESCAN_RC=\$?; cat /tmp/${SNAP}_scan-routes.log"

echo CONTAINER_BATCH_RC=0
