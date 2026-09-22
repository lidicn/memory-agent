#!/usr/bin/env bash
# 把 GitHub 最新版部署到 NAS（覆盖式同步，保留 .env / data / certs 等本地文件）
set -e
cd /vol1/1000/docker/memory-agent

# P0-A3: 部署前备份（保留最近 5 份）
BACKUP_DIR="/vol1/1000/docker/_backups/memory-agent"
mkdir -p "$BACKUP_DIR"
BACKUP_NAME="deploy_$(date +%Y%m%d_%H%M%S)"
echo "== 备份当前版本到 $BACKUP_DIR/$BACKUP_NAME =="
tar czf "$BACKUP_DIR/$BACKUP_NAME.tar.gz" --exclude='./data' --exclude='./.git' . 2>/dev/null || true
# 只保留最近 5 份备份
ls -t "$BACKUP_DIR"/deploy_*.tar.gz 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null || true
echo "BACKUP_DONE"

# 1) 叠加最新代码（只更新被 git 跟踪的文件，不动本地私有文件）
git init -q
git remote remove origin 2>/dev/null || true
git remote add origin https://github.com/lidicn/memory-agent.git
git fetch --depth 1 origin main
git checkout -f origin/main -- .
echo "SYNC_DONE"

# 2) 核验关键改动已落地
echo "== Dockerfile git =="; grep -n git Dockerfile
echo "== compose /repo =="; grep -n 'repo:rw' docker-compose.yml
echo "== config vlm_endpoint_path =="; grep -n vlm_endpoint_path src/memory_agent/config.py

# 3) 重建并启动（新 Dockerfile 装了 git；compose 新增 .:/repo 挂载）
docker compose up -d --build

# 4) 查看状态
echo "== ps =="; docker compose ps
