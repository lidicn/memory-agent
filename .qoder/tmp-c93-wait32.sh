#!/usr/bin/env bash
# 等 run32 出 REMOTE_DONE；最多 30×20s。远端命令整条单引号，本机不做任何展开。
set -uo pipefail
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
"$SSH" -i "$KEY" -o StrictHostKeyChecking=no lidicn@192.168.2.200 \
  'for i in $(seq 1 25); do grep -q REMOTE_DONE /tmp/ma_c92_run32.log && { echo DONE_AT_TICK_$i; break; }; sleep 20; done; echo LINECOUNT=$(wc -l < /tmp/ma_c92_run32.log); grep -E "_RC=|SKIPPED|totals|passed|failed|UNVERIFIED|REGISTERED|gates|HASH_|REMOTE_DONE" /tmp/ma_c92_run32.log | tail -30'
echo WAIT_RC=$?
