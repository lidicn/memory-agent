#!/usr/bin/env bash
# 20261005 批次 c24：A8 落下的两条 insights 修复（Event.dt 走家庭墙钟 / tags_json 静默降级留痕）
# 的容器权威门 + 成对生效读数（同一探针在 /app=改前 与 快照=改后 各跑一遍）。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c24snap20261005a
TGZ=.qoder/tmp-c24-snap.tgz

echo HEAD=$(git rev-parse --short HEAD)

{ git ls-files -- src tests scripts benchmarks .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- tests scripts; } | sort -u > .qoder/tmp-c24-filelist.txt
echo FILES=$(wc -l < .qoder/tmp-c24-filelist.txt)
tar -czf "$TGZ" -T .qoder/tmp-c24-filelist.txt
echo TAR_RC=$? SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; echo SCP_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage; echo STAGE_RC=\$?"
echo SYNC_RC=$?

"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh' > /tmp/${SNAP}_gate.log 2>&1; echo GATE_RC=\$?; tail -3 /tmp/${SNAP}_gate.log"

"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP:/tmp/$SNAP/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs' > /tmp/${SNAP}_suite.log 2>&1; echo SUITE_RC=\$?; tail -4 /tmp/${SNAP}_suite.log"

# 定向：新锁 + 既有时间口径锁一起跑（改了 Event.dt，凡按钟摆的断言都可能被牵动）
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_a8_insights_clock_and_tags.py tests/test_audit_arrival_time.py -q -p no:cacheprovider' > /tmp/${SNAP}_targeted.log 2>&1; echo TARGETED_RC=\$?; tail -3 /tmp/${SNAP}_targeted.log"

# 成对生效读数：同一条事件的 dt / day / hour 三口径（/app=改前，快照=改后）
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c24-dt-check.py "$NAS:/tmp/ma_dtcheck.py"
"$SSH" "${OPTS[@]}" "$NAS" "docker cp /tmp/ma_dtcheck.py memory-agent:/tmp/dtcheck.py && rm -f /tmp/ma_dtcheck.py; echo DTCP_RC=\$?"
echo "--- 改前（部署码 /app，机器时区 UTC）---"
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'PYTHONPATH=/app/src:/tmp/pylibs python /tmp/dtcheck.py'; echo PRE_RC=\$?"
echo "--- 改后（工作区快照）---"
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs python /tmp/dtcheck.py'; echo POST_RC=\$?"

echo CONTAINER_BATCH_RC=0
