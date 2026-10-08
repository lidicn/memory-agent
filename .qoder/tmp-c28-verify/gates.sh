#!/bin/bash
# WO-MA-013 DoD-2: memory-agent 质量门禁本地运行脚本
#
# 分工：
#   AST 门禁   —— 容器内直接跑（不 import 被扫代码，只 parse）
#   import 冒烟 —— 必须在部署镜像里跑，CI 工作流 gates.yml 另建 job 做
#
# 用法：docker exec memory-agent bash /app/gates.sh
set -e
cd "$(dirname "$0")"
echo "=== memory-agent 质量门禁（AST only）==="
homesdk-gates . --no-smoke
echo "=== 门禁通过 exit=0 ==="
