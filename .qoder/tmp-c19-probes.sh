#!/usr/bin/env bash
# c19：只补跑成对探针（c18 的两遍都栽在量具自己的 `sorted(list) - set`，见 probe §Q3-4 注释）。
# 容器里两个快照还在，把修好的探针覆盖进去重跑即可——门禁与全量回归已由 c18 判绿，不重复。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
POST=c18snap20261005a
PRE=c18pre20261005a

"$SCP" "${OPTS[@]}" -q scripts/probe_insights_q3_acceptance.py "$NAS:/tmp/ma_probe_q3b.py"
echo SCP_PROBE_RC=$?

for snap in "$PRE" "$POST"; do
  "$SSH" "${OPTS[@]}" "$NAS" "docker cp /tmp/ma_probe_q3b.py memory-agent:/tmp/$snap/scripts/probe_insights_q3_acceptance.py; echo CP_RC=\$?"
  "$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$snap && PYTHONPATH=/tmp/$snap/src:/tmp/pylibs python -u scripts/probe_insights_q3_acceptance.py' > /tmp/${snap}_q3b.log 2>&1; echo ${snap}_PROBE_RC=\$?; wc -l /tmp/${snap}_q3b.log"
  "$SCP" "${OPTS[@]}" -q "$NAS:/tmp/${snap}_q3b.log" ".qoder/tmp-c21-${snap}-q3.log"
  echo FETCH_${snap}_RC=$?
done
"$SSH" "${OPTS[@]}" "$NAS" "rm -f /tmp/ma_probe_q3b.py; echo CLEAN_RC=\$?"
echo C19_DONE=0
