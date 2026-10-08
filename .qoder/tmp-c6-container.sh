#!/usr/bin/env bash
# 裁6 工作区快照 → 容器全量回归 + pyflakes 门禁（只读，不碰 /app）
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c6snap20261004c            # 全新唯一名，不复用别人的 vsNN
TGZ=.qoder/tmp-c6-snap.tgz

{ git ls-files -- src tests scripts benchmarks .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  echo tests/test_vma_activity_semantic.py; \
  echo scripts/probe_activity_semantic_readings.py; \
  echo scripts/probe_activity_coverage_gap.py; } | sort -u > .qoder/tmp-c6-filelist.txt
echo FILES=$(wc -l < .qoder/tmp-c6-filelist.txt)
tar -czf "$TGZ" -T .qoder/tmp-c6-filelist.txt
echo TAR_RC=$?  SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; echo SCP_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage; echo STAGE_RC=\$?"
echo SYNC_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh' > /tmp/${SNAP}_gate.log 2>&1; echo GATE_RC=\$?; tail -4 /tmp/${SNAP}_gate.log"
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP:/tmp/$SNAP/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs' > /tmp/${SNAP}_suite.log 2>&1; echo SUITE_RC=\$?; tail -8 /tmp/${SNAP}_suite.log"
rm -f "$TGZ"
