#!/usr/bin/env bash
# 20261005 批次 c30：#53（P3-2 days 极值 + P3-4 连接池无界 + P3-1 未挂载路由量具）的容器权威门。
# 与 c29 的差别：本批改了 18 个 src 文件并新增 2 个量具 + 1 个新模块（day_bounds.py），
# 所以量具必须在容器（Python 3.11）里自证 + 全仓扫描，读数不假定与本机 3.13 等价；
# 路由量具按设计仍报 4 条失联 handler（呈 DCD 的既有缺口），那里取的是**读数**不是门。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c30snap20261005a
TGZ=.qoder/tmp-c30-snap.tgz

echo HEAD=$(git rev-parse --short HEAD)

{ git ls-files -- src tests scripts benchmarks .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c30-filelist.txt
echo FILES=$(wc -l < .qoder/tmp-c30-filelist.txt)
tar -czf "$TGZ" -T .qoder/tmp-c30-filelist.txt
echo TAR_RC=$? SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; echo SCP_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage; echo STAGE_RC=\$?"
echo SYNC_RC=$?

# 门 1：pyflakes（含新增 scripts/scan_day_bounds.py、scan_route_mount.py、src/memory_agent/day_bounds.py）
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh' > /tmp/${SNAP}_gate.log 2>&1; echo GATE_RC=\$?; tail -3 /tmp/${SNAP}_gate.log"

# 门 2：全量回归
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP:/tmp/$SNAP/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs' > /tmp/${SNAP}_suite.log 2>&1; echo SUITE_RC=\$?; tail -20 /tmp/${SNAP}_suite.log"

# 定向 1：本批新锁
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_p32_day_bounds.py -q -p no:cacheprovider' > /tmp/${SNAP}_targeted.log 2>&1; echo TARGETED_RC=\$?; tail -3 /tmp/${SNAP}_targeted.log"

# 定向 2：被改了交付面的模块，既有测试必须照旧绿
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_activity_inference.py tests/test_change_attribution.py tests/test_daily_profile.py tests/test_summary_queries.py tests/test_vma_insights_window_echo.py tests/test_rule_trigger_retention.py tests/test_insights_house_timezone.py tests/test_mcp_surface_parity.py -q -p no:cacheprovider' > /tmp/${SNAP}_touched.log 2>&1; echo TOUCHED_RC=\$?; tail -3 /tmp/${SNAP}_touched.log"

# 量具①：day bounds 自证 + 全仓扫描（容器 Python 3.11）
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && python -V && PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py --self-test; echo SELFTEST_RC=\$?; PYTHONPATH=/tmp/pylibs python scripts/scan_day_bounds.py src' > /tmp/${SNAP}_scan-days.log 2>&1; echo DAYSCAN_RC=\$?; cat /tmp/${SNAP}_scan-days.log"

# 量具②：route mount 自证 + 全仓扫描。按设计仍红 4 条（呈 DCD），这里取读数量不取门
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/pylibs python scripts/scan_route_mount.py --self-test; echo SELFTEST_RC=\$?; PYTHONPATH=/tmp/pylibs python scripts/scan_route_mount.py src || true' > /tmp/${SNAP}_scan-routes.log 2>&1; echo ROUTESCAN_RC=\$?; cat /tmp/${SNAP}_scan-routes.log"

# 运行时读数探针（改后态）：容器侧独立取一份，不拿本机 3.13 的读数冒充
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c30-days-pool-probe.py "$NAS:/tmp/ma_dayprobe_c30.py"; echo PROBESCRIPT_SCP_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "docker cp /tmp/ma_dayprobe_c30.py memory-agent:/tmp/dayprobe_c30.py && rm -f /tmp/ma_dayprobe_c30.py; echo PROBE_DOCKER_CP_RC=\$?"
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'sha256sum /tmp/dayprobe_c30.py | cut -c1-16'; echo PROBE_SHA_RC=\$?"
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'MA_SRC=/tmp/$SNAP/src python /tmp/dayprobe_c30.py' > /tmp/${SNAP}_probe.log 2>&1; echo PROBE_RC=\$?; cat /tmp/${SNAP}_probe.log"

echo CONTAINER_BATCH_RC=0
