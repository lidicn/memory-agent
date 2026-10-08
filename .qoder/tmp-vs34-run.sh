#!/usr/bin/env bash
# 在 NAS 上为 HEAD 建快照 vs34，容器内跑 pyflakes 门禁 + 全量回归（只读生产库，不碰 /app）
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)

git archive --format=tar.gz HEAD > .qoder/tmp-vs34.tar.gz
echo ARCHIVE_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-vs34.tar.gz "$NAS:/tmp/ma_vs34.tar.gz"
echo SCP_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/vs34_stage && mkdir -p /tmp/vs34_stage && tar -xzf /tmp/ma_vs34.tar.gz -C /tmp/vs34_stage && rm -f /tmp/ma_vs34.tar.gz && docker exec memory-agent sh -c 'rm -rf /tmp/vs34' && docker cp /tmp/vs34_stage memory-agent:/tmp/vs34 && docker exec -u root memory-agent chown -R 10001:10001 /tmp/vs34 && rm -rf /tmp/vs34_stage; echo STAGE_RC=\$?"
echo SYNC_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/vs34 && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh' > /tmp/vs34_gate.log 2>&1; echo GATE_RC=\$?; tail -4 /tmp/vs34_gate.log"
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/vs34 && PYTHONPATH=/tmp/vs34:/tmp/vs34/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs' > /tmp/vs34_suite.log 2>&1; echo SUITE_RC=\$?; tail -6 /tmp/vs34_suite.log"
rm -f .qoder/tmp-vs34.tar.gz
