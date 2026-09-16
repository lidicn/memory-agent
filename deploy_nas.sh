#!/usr/bin/env bash
# 把 GitHub 最新版部署到 NAS（覆盖式同步，保留 .env / data / certs 等本地文件）
set -e
cd /vol1/1000/docker/memory-agent

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
