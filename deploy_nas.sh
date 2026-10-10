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

# 第四轮审计 §五：上面那份 tar 刻意排除 data/，所以它只能回滚「代码」。
# 数据库单独做一次在线一致性快照：VACUUM INTO 走 SQLite 的快照语义，不需要停服务，
# 也不会像 cp 那样把一个还没落盘的 WAL 库拷成半份。先在 ./data 里落地（容器只挂了
# ./data:/data），再 mv 到备份目录——它既不进代码档，也不与 BackupManager 的
# ma-*.db / 恢复逻辑的 *.db.bak* 重名，不会互相轮转掉。
DB_REL="$(grep -E '^[[:space:]]*DB_PATH=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"')"
DB_IN_CONTAINER="${DB_REL:-/data/memory_agent.db}"
SNAP_IN_DATA="./data/deploy-snapshot.tmp.db"
SNAP="$BACKUP_DIR/$BACKUP_NAME.db"
rm -f "$SNAP_IN_DATA" 2>/dev/null || true
if docker compose exec -T memory-agent python -c '
import os, sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
if os.path.exists(dst):
    os.remove(dst)
c = sqlite3.connect(src, timeout=30.0)
try:
    c.execute("VACUUM INTO ?", (dst,))
finally:
    c.close()
print("SNAPSHOT_OK", dst)
' "$DB_IN_CONTAINER" "/data/deploy-snapshot.tmp.db" 2>/dev/null \
   && mv -f "$SNAP_IN_DATA" "$SNAP"; then
  echo "DB_BACKUP_DONE $SNAP"
else
  rm -f "$SNAP_IN_DATA" 2>/dev/null || true
  echo "DB_BACKUP_FAILED（容器未在运行或快照失败：本次部署只有代码可回滚，数据不可回滚）"
fi

# 只保留最近 5 份备份（代码档与数据库快照各自轮转）
ls -t "$BACKUP_DIR"/deploy_*.tar.gz 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null || true
ls -t "$BACKUP_DIR"/deploy_*.db 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null || true
echo "BACKUP_DONE"

# 1) 叠加最新代码（只更新被 git 跟踪的文件，不动本地私有文件）
git init -q
git remote remove origin 2>/dev/null || true
git remote add origin https://github.com/lidicn/memory-agent.git
git fetch --depth 1 origin main
git checkout -f origin/main -- .
echo "SYNC_DONE"

# 2) 核验关键改动已落地（审计 H2：原 grep 'repo:rw' 已过期，set -e 下会卡死部署）
echo "== Dockerfile git =="; grep -n git Dockerfile || echo "(未命中，跳过)"
echo "== config vlm_endpoint_path =="; grep -n vlm_endpoint_path src/memory_agent/config.py || echo "(未命中，跳过)"

# 3) 重建并启动（新 Dockerfile 装了 git；compose 新增 .:/repo 挂载）
docker compose up -d --build

# 4) 查看状态
echo "== ps =="; docker compose ps
