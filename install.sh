#!/usr/bin/env bash
# Memory Agent 一键安装脚本
# 用法：
#   bash <(curl -fsSL https://raw.githubusercontent.com/lidicn/memory-agent/main/install.sh)
# 自定义安装目录：
#   INSTALL_DIR=my-agent bash <(curl -fsSL https://raw.githubusercontent.com/lidicn/memory-agent/main/install.sh)
set -euo pipefail

REPO="https://github.com/lidicn/memory-agent.git"
BRANCH="main"
INSTALL_DIR="${INSTALL_DIR:-memory-agent}"

echo "== Memory Agent 一键安装 =="

# 1) 依赖检查
command -v docker >/dev/null 2>&1 || { echo "错误：未检测到 docker，请先安装 Docker（含 compose 插件）。"; exit 1; }
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  echo "错误：未检测到 docker compose 插件，请升级 Docker 桌面版或安装 compose。"; exit 1
fi

# 2) 克隆（已存在则复用，不覆盖）
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "目标目录 ./$INSTALL_DIR 已存在，跳过克隆，直接启动。"
else
  echo "克隆仓库到 ./$INSTALL_DIR ..."
  git clone -b "$BRANCH" "$REPO" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# 3) 生成 .env（不覆盖已有）
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    echo "已根据 .env.example 生成 .env —— 启动前请编辑填入 HASS_TOKEN / JWT_SECRET 等关键项。"
  else
    echo "警告：未找到 .env.example，将使用代码内默认值启动（部分功能受限）。"
  fi
else
  echo ".env 已存在，保持不变。"
fi

# 4) 构建并启动
echo "构建并启动服务（memory-agent / redis / chroma / caddy）..."
$COMPOSE up -d --build

cat <<EOF

== 安装完成 ==
WebUI 地址：  http://<本机IP>:8086
首次访问会引导注册管理员账号，登录后在「设置」页填写 Home Assistant、LLM 与视觉识别配置。

常用命令（在 $INSTALL_DIR 目录内执行）：
  查看日志：  $COMPOSE logs -f
  重启服务：  $COMPOSE restart
  停止服务：  $COMPOSE down
  在线更新：  WebUI「设置 → 系统 / 在线更新」一键从 GitHub 拉取并重启

注意：.env 与 data/ 均落在 $INSTALL_DIR 内，更新代码不会触碰你的配置与数据。
EOF
