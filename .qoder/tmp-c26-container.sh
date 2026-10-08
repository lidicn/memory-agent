#!/usr/bin/env bash
# 20261005 批次 c26：A8 落下的三条 insights 修复（Event.dt 走家庭墙钟 / tags_json 降级留痕 /
# list_entities 丢行留痕）+ A3 P2-6 并发锁的容器权威门与全量回归。
# 与 c25 的差别：探针文件路径写对（c25 那格 DTCP_RC=1 用的是容器里的 c24 遗留件），
# 并在容器内打印探针 sha256 前 16 位自证"跑的就是这份文件"。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c26snap20261005a
TGZ=.qoder/tmp-c26-snap.tgz

echo HEAD=$(git rev-parse --short HEAD)

{ git ls-files -- src tests scripts benchmarks .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- tests scripts; } | sort -u > .qoder/tmp-c26-filelist.txt
echo FILES=$(wc -l < .qoder/tmp-c26-filelist.txt)
tar -czf "$TGZ" -T .qoder/tmp-c26-filelist.txt
echo TAR_RC=$? SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; echo SCP_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage; echo STAGE_RC=\$?"
echo SYNC_RC=$?

"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh' > /tmp/${SNAP}_gate.log 2>&1; echo GATE_RC=\$?; tail -3 /tmp/${SNAP}_gate.log"

"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP:/tmp/$SNAP/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs' > /tmp/${SNAP}_suite.log 2>&1; echo SUITE_RC=\$?; tail -4 /tmp/${SNAP}_suite.log"

# 定向：本批两把新锁（A8 时间/降级 8 条 + A3 P2-6 并发 5 条）
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_a8_insights_clock_and_tags.py tests/test_vma_a3_p26_conv_lock.py -q -p no:cacheprovider' > /tmp/${SNAP}_targeted.log 2>&1; echo TARGETED_RC=\$?; tail -3 /tmp/${SNAP}_targeted.log"

# 成对生效读数：同一探针在 /app（部署码=改前）与快照（改后）各跑一遍
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c26-dt-check.py "$NAS:/tmp/ma_dtcheck_c26.py"; echo DTSCP_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "docker cp /tmp/ma_dtcheck_c26.py memory-agent:/tmp/dtcheck_c26.py; echo DTCP_RC=\$?; rm -f /tmp/ma_dtcheck_c26.py"
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'sha256sum /tmp/dtcheck_c26.py | cut -c1-16'; echo PROBE_SHA_RC=\$?"
echo "--- 改前（部署码 /app，机器时区 UTC）---"
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'PYTHONPATH=/app/src:/tmp/pylibs python /tmp/dtcheck_c26.py'; echo PRE_RC=\$?"
echo "--- 改后（工作区快照）---"
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs python /tmp/dtcheck_c26.py'; echo POST_RC=\$?"

echo CONTAINER_BATCH_RC=0
