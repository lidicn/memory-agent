#!/bin/bash
# 宿主侧：定期检查远端是否有更新，写预览文件供容器读取
# crontab: */5 * * * * /vol1/1000/docker/memory-agent/scripts/host_check_update.sh

set -euo pipefail

REPO_DIR="/vol1/1000/docker/memory-agent"
DATA_DIR="$REPO_DIR/data"
PREVIEW="$DATA_DIR/.update_available.json"

cd "$REPO_DIR"
git fetch --quiet origin main 2>/dev/null || true

LOCAL=$(git rev-parse HEAD 2>/dev/null || echo "")
REMOTE=$(git rev-parse origin/main 2>/dev/null || echo "")
LOCAL_TAG=$(git describe --tags --always 2>/dev/null || echo "$LOCAL")

if [ -n "$LOCAL" ] && [ -n "$REMOTE" ] && [ "$LOCAL" != "$REMOTE" ]; then
    CHANGELOG=$(git log --oneline "$LOCAL..$REMOTE" --no-merges 2>/dev/null | head -15 || echo "")
    HAS_UPDATE="True"
else
    CHANGELOG=""
    HAS_UPDATE="False"
fi

export PREVIEW LOCAL REMOTE LOCAL_TAG CHANGELOG HAS_UPDATE
python3 <<'PYEOF'
import json, os, datetime
changelog = []
raw = os.environ.get("CHANGELOG", "").strip()
if raw:
    changelog = [l.strip() for l in raw.split("\n") if l.strip()]
r = {
    "has_update": os.environ.get("HAS_UPDATE") == "True",
    "local_commit": os.environ.get("LOCAL", "")[:12],
    "local_tag": os.environ.get("LOCAL_TAG", ""),
    "remote_commit": os.environ.get("REMOTE", "")[:12],
    "changelog": changelog,
    "checked_at": datetime.datetime.now().isoformat()
}
with open(os.environ["PREVIEW"], "w", encoding="utf-8") as f:
    json.dump(r, f, ensure_ascii=False, indent=2)
PYEOF
