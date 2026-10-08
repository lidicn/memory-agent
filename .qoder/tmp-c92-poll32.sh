#!/usr/bin/env bash
set -uo pipefail
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
"$SSH" -i "$KEY" -o StrictHostKeyChecking=no lidicn@192.168.2.200 \
  "grep -E '_RC=|_DONE|ANCHOR_|SKIPPED|totals|KILLED_LINES|passed|failed' /tmp/ma_c92_run32.log | tail -45"
