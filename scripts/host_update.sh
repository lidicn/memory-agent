#!/bin/bash
# MA WebUI 自更新宿主侧执行脚本（DCD 20261010 Q1=B）
# crontab: * * * * * /vol1/1000/docker/memory-agent/scripts/host_update.sh

set -uo pipefail

REPO_DIR="/vol1/1000/docker/memory-agent"
DATA_DIR="$REPO_DIR/data"
MARKER="$DATA_DIR/.update_request.json"
RESULT="$DATA_DIR/.update_result.json"
LOG="$DATA_DIR/update.log"

export GIT_SSH_COMMAND="ssh -i /home/lidicn/.ssh/id_ed25519_github -o StrictHostKeyChecking=no"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

write_result() {
    # $1=ok(true/false)  $2=error_message
    export _UR_OK="$1"
    export _UR_ERR="$2"
    export _UR_FROM="$FROM_SHA"
    export _UR_FROM_TAG="$FROM_TAG"
    export _UR_TO="$TO_SHA"
    export _UR_TO_TAG="$TO_TAG"
    export _UR_CHANGELOG="$CHANGELOG"
    export _UR_RESULT="$RESULT"
    python3 << 'PYEOF' >> "$LOG" 2>&1
import json, time, os
r = {
    "ok": os.environ.get("_UR_OK", "False") == "True",
    "from": os.environ.get("_UR_FROM", "unknown"),
    "from_tag": os.environ.get("_UR_FROM_TAG", "unknown"),
    "to": os.environ.get("_UR_TO", "unknown"),
    "to_tag": os.environ.get("_UR_TO_TAG", "unknown"),
    "error": os.environ.get("_UR_ERR", ""),
    "changelog": json.loads(os.environ.get("_UR_CHANGELOG", "[]")),
    "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
}
with open(os.environ["_UR_RESULT"], "w") as f:
    json.dump(r, f, ensure_ascii=False, indent=2)
PYEOF
}

[ ! -f "$MARKER" ] && exit 0
log "=== 收到更新请求 ==="

REF=$(python3 -c "import json; print(json.load(open('$MARKER')).get('ref','main'))" 2>/dev/null || echo "main")
log "目标 ref: $REF"

cd "$REPO_DIR"
FROM_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
FROM_TAG=$(git describe --tags --always 2>/dev/null || echo "$FROM_SHA")
log "当前: $FROM_SHA"

# 备份
BACKUP="$DATA_DIR/update-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
tar czf "$BACKUP" -C "$REPO_DIR" --exclude='.git' --exclude='data' src/ docker-compose.yml >> "$LOG" 2>&1
log "备份: $BACKUP"

TO_SHA=""
TO_TAG=""
CHANGELOG="[]"

# fetch
if ! git fetch --force origin "$REF" >> "$LOG" 2>&1; then
    log "ERROR: git fetch 失败"
    TO_SHA="$FROM_SHA"; TO_TAG="$FROM_TAG"
    write_result False "git fetch 失败，请检查网络或 SSH 密钥"
    rm -f "$MARKER"
    exit 1
fi
log "fetch 成功"

# reset to origin/$REF
if ! git reset --hard "origin/$REF" >> "$LOG" 2>&1; then
    log "ERROR: git reset 失败"
    TO_SHA="$FROM_SHA"; TO_TAG="$FROM_TAG"
    write_result False "git reset 失败，代码可能有冲突"
    rm -f "$MARKER"
    exit 1
fi
TO_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
TO_TAG=$(git describe --tags --always 2>/dev/null || echo "$TO_SHA")
log "更新到: $TO_SHA ($TO_TAG)"

# changelog
CHANGELOG=$(git log --oneline "$FROM_SHA..$TO_SHA" --no-merges 2>/dev/null | head -20 | python3 -c "
import sys, json
commits = [l.strip() for l in sys.stdin if l.strip()]
print(json.dumps(commits, ensure_ascii=False))
" 2>/dev/null || echo "[]")

# py_compile
if ! python3 -m py_compile src/memory_agent/*.py >> "$LOG" 2>&1; then
    log "ERROR: py_compile 失败，回滚"
    git reset --hard "$FROM_SHA" >> "$LOG" 2>&1
    write_result False "语法检查失败，已自动回滚"
    rm -f "$MARKER"
    exit 1
fi
log "py_compile 通过"

write_result True ""
rm -f "$MARKER"
log "=== 更新完成，重启容器 ==="

sleep 2
docker restart memory-agent >> "$LOG" 2>&1
