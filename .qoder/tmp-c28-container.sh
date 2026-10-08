#!/usr/bin/env bash
# 20261005 批次 c28：A3 §八「未卸载的同步 I/O」落到底的容器权威门与全量回归。
# 本批改动：鉴权中间件 `_authenticate` 卸载（app.py）+ 两条免鉴权热路径卸载（auth_routes.py）
# + 量具 scripts/scan_unloaded_async_io.py + 7 条心跳锁。
# 与 c26 的差别：探针换成"鉴权链两格慢操作到底多慢"的时长读数（含不读盘的对照档），
# 并在容器内打印探针 sha256 前 16 位自证"跑的就是这份文件"。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c28snap20261005a
TGZ=.qoder/tmp-c28-snap.tgz

echo HEAD=$(git rev-parse --short HEAD)

{ git ls-files -- src tests scripts benchmarks .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- tests scripts; } | sort -u > .qoder/tmp-c28-filelist.txt
echo FILES=$(wc -l < .qoder/tmp-c28-filelist.txt)
tar -czf "$TGZ" -T .qoder/tmp-c28-filelist.txt
echo TAR_RC=$? SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; echo SCP_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage; echo STAGE_RC=\$?"
echo SYNC_RC=$?

"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh' > /tmp/${SNAP}_gate.log 2>&1; echo GATE_RC=\$?; tail -3 /tmp/${SNAP}_gate.log"

"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP:/tmp/$SNAP/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs' > /tmp/${SNAP}_suite.log 2>&1; echo SUITE_RC=\$?; tail -4 /tmp/${SNAP}_suite.log"

# 定向：本批心跳锁 7 条 + 上一批的 A8 时间/降级 8 条 + A3 P2-6 并发 5 条
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_a3_p22_auth_loop_blocking.py tests/test_vma_a8_insights_clock_and_tags.py tests/test_vma_a3_p26_conv_lock.py -q -p no:cacheprovider' > /tmp/${SNAP}_targeted.log 2>&1; echo TARGETED_RC=\$?; tail -3 /tmp/${SNAP}_targeted.log"

# 鉴权链全面回归（管家/竞技场/服务令牌/安全门 + 本批新锁）：卸载只该换线程，不该换语义
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_butler_auth.py tests/test_arena_auth.py tests/test_service_tokens.py tests/test_wo_ma_012_g1_security.py tests/test_vma_a3_p22_auth_loop_blocking.py -q -p no:cacheprovider' > /tmp/${SNAP}_authchain.log 2>&1; echo AUTHCHAIN_RC=\$?; tail -3 /tmp/${SNAP}_authchain.log"

# 量具在容器解释器（Python 3.11）里的读数：自证 + 全库
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && python scripts/scan_unloaded_async_io.py --self-test; echo SCANNER_SELFTEST_RC=\$?; python scripts/scan_unloaded_async_io.py src/memory_agent; echo SCANNER_SRC_RC=\$?'"

# 成对生效读数：同一探针在 /app（部署码=改前，鉴权在循环上）与快照（改后）各跑一遍
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c28-auth-read-probe.py "$NAS:/tmp/ma_authprobe_c28.py"; echo PROBE_SCP_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "docker cp /tmp/ma_authprobe_c28.py memory-agent:/tmp/authprobe_c28.py; echo PROBE_DOCKER_CP_RC=\$?; rm -f /tmp/ma_authprobe_c28.py"
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'sha256sum /tmp/authprobe_c28.py | cut -c1-16'; echo PROBE_SHA_RC=\$?"
echo "--- 改前（部署码 /app/src）---"
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /app && PYTHONPATH=/app/src:/tmp/pylibs python /tmp/authprobe_c28.py'; echo PRE_RC=\$?"
echo "--- 改后（工作区快照）---"
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /app && PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs python /tmp/authprobe_c28.py'; echo POST_RC=\$?"

echo CONTAINER_BATCH_RC=0
