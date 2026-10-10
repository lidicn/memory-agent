#!/bin/bash
# MA WebUI 自更新宿主侧执行脚本（DCD 20261010 Q1=B）
#
# 由宿主 cron 每分钟轮询：
#   检查 /vol1/1000/docker/memory-agent/data/.update_request.json
#   如果存在：git fetch → checkout → py_compile → docker restart
#   失败则回滚并写结果

set -euo pipefail

REPO_DIR="/vol1/1000/docker/memory-agent"
DATA_DIR="$REPO_DIR/data"
MARKER="$DATA_DIR/.update_request.json"
RESULT="$DATA_DIR/.update_result.json"
LOG="$DATA_DIR/update.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

export GIT_SSH_COMMAND="ssh -i /home/lidicn/.ssh/id_ed25519_github -o StrictHostKeyChecking=no"

[ ! -f "$MARKER" ] && exit 0

log "=== 收到更新请求 ==="

REF=$(python3 -c "import json; print(json.load(open('$MARKER')).get('ref','main'))" 2>/dev/null || echo "main")
log "目标 ref: $REF"

FROM_SHA=$(cd "$REPO_DIR" && git rev-parse HEAD 2>/dev/null || echo "")
FROM_TAG=$(cd "$REPO_DIR" && git describe --tags --always 2>/dev/null || echo "$FROM_SHA")
log "当前: $FROM_SHA"

# 备份
BACKUP="$DATA_DIR/update-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
tar czf "$BACKUP" -C "$REPO_DIR" --exclude='.git' --exclude='data' src/ docker-compose.yml 2>/dev/null || true
log "备份: $BACKUP"

# fetch + checkout
cd "$REPO_DIR"
git fetch --force origin "$REF" 2>&1 | log
git checkout -f "$REF" 2>&1 | log
TO_SHA=$(git rev-parse HEAD)
TO_TAG=$(git describe --tags --always 2>/dev/null || echo "$TO_SHA")
log "更新到: $TO_SHA ($TO_TAG)"

# 抓取 changelog（从 FROM_SHA 到 TO_SHA 的 commit 标题）
CHANGELOG=$(git log --oneline "$FROM_SHA..$TO_SHA" --no-merges 2>/dev/null | head -20 | python3 -c "
import sys, json
commits = [l.strip() for l in sys.stdin if l.strip()]
print(json.dumps(commits, ensure_ascii=False))
" 2>/dev/null || echo "[]")

# py_compile
if ! python3 -m py_compile src/memory_agent/*.py; then
    log "py_compile 失败，回滚"
    git checkout -f "$FROM_SHA" 2>/dev/null || true
    python3 -c "
import json
r = {'ok': False, 'error': '语法检查失败，已自动回滚', 'from': '$FROM_SHA', 'from_tag': '$FROM_TAG', 'to': '$TO_SHA', 'to_tag': '$TO_TAG'}
json.dump(r, open('$RESULT','w'), ensure_ascii=False)
"
    rm -f "$MARKER"
    exit 1
fi
log "py_compile 通过"

# 写成功结果
python3 -c "
import json
r = {'ok': True, 'from': '$FROM_SHA', 'from_tag': '$FROM_TAG', 'to': '$TO_SHA', 'to_tag': '$TO_TAG', 'changelog': json.loads('''$CHANGELOG'''), 'restarting': True}
json.dump(r, open('$RESULT','w'), ensure_ascii=False, indent=2)
"
rm -f "$MARKER"
log "=== 更新完成，重启容器 ==="

sleep 2
docker restart memory-agent
