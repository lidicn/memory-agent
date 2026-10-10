#!/bin/bash
# MA WebUI 自更新宿主侧执行脚本（DCD 20261010 Q1=B）
#
# 由宿主 cron 每分钟轮询：
#   检查 /vol1/1000/docker/memory-agent/data/.update_request.json
#   如果存在：git fetch → checkout → py_compile → docker restart
#   失败则回滚并写结果
#
# crontab 安装（宿主）：
#   * * * * * /vol1/1000/docker/memory-agent/scripts/host_update.sh >> /tmp/ma_update.log 2>&1

set -euo pipefail

REPO_DIR="/vol1/1000/docker/memory-agent"
DATA_DIR="$REPO_DIR/data"
MARKER="$DATA_DIR/.update_request.json"
RESULT="$DATA_DIR/.update_result.json"
LOG="$DATA_DIR/update.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# 无标记文件则静默退出
[ ! -f "$MARKER" ] && exit 0

log "=== 收到更新请求 ==="

# 读取目标 ref
REF=$(python3 -c "import json; print(json.load(open('$MARKER')).get('ref','main'))" 2>/dev/null || echo "main")
log "目标 ref: $REF"

# 记录当前 commit（用于回滚）
FROM_SHA=$(cd "$REPO_DIR" && git rev-parse HEAD 2>/dev/null || echo "")
log "当前 commit: $FROM_SHA"

# 1) 备份 src/ 和 docker-compose.yml（不含 .git / data）
BACKUP="$DATA_DIR/update-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
tar czf "$BACKUP" -C "$REPO_DIR" --exclude='.git' --exclude='data' src/ docker-compose.yml 2>/dev/null || true
log "备份: $BACKUP"

# 2) fetch + checkout
cd "$REPO_DIR"
git fetch --force origin "$REF" 2>&1 | log
git checkout -f "$REF" 2>&1 | log
TO_SHA=$(git rev-parse HEAD)
log "更新到: $TO_SHA"

# 3) py_compile 全量语法校验
if ! python3 -m py_compile src/memory_agent/*.py; then
    log "py_compile 失败，回滚到 $FROM_SHA"
    git checkout -f "$FROM_SHA" 2>/dev/null || true
    cat > "$RESULT" <<EOF
{"ok": false, "error": "py_compile failed, rolled back", "from": "$FROM_SHA", "to": "$TO_SHA"}
EOF
    rm -f "$MARKER"
    exit 1
fi
log "py_compile 通过"

# 4) 写成功结果
cat > "$RESULT" <<EOF
{"ok": true, "from": "$FROM_SHA", "to": "$TO_SHA", "restarting": true}
EOF
rm -f "$MARKER"
log "=== 更新完成，重启容器 ==="

# 5) 重启容器（延迟 2 秒让 HTTP 响应送达）
sleep 2
docker restart memory-agent
