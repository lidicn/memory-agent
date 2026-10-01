#!/usr/bin/env bash
# pyflakes 质量门禁：基线只准减少，不准新增。
# 用法：
#   bash scripts/pyflakes_gate.sh                # 扫描 src/memory_agent，与 .gates/pyflakes-baseline.txt 比较
#   bash scripts/pyflakes_gate.sh --bless        # 把当前扫描结果写为新基线（清理欠账后收编台账）
# 口径：基线是「仓库内 src/memory_agent」的扫描结果（CI 即此口径）。
#       在容器里直接扫 /app/src 会多出 NAS 盘上 git 未跟踪的历史残留
#       （memory_agent/insights.py、memory_agent/static/js/pages/vision_service.py）
#       共 2 条幻影。切勿用容器口径 --bless，否则把不存在的文件写进台账。
#       容器内取仓库口径：把 /app/src 复制到别处删掉这 2 个残留再扫。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BLESS=0
TARGET_DIR="$REPO_ROOT/src/memory_agent"
for arg in "$@"; do
    case "$arg" in
        --bless) BLESS=1 ;;
        *) TARGET_DIR="$arg" ;;
    esac
done

BASELINE="$REPO_ROOT/.gates/pyflakes-baseline.txt"
TMP_SCAN="$(mktemp)"
TMP_NORM="$(mktemp)"
trap 'rm -f "$TMP_SCAN" "$TMP_NORM"' EXIT

PY="${PYTHON:-python}"
"$PY" -m pyflakes "$TARGET_DIR" > "$TMP_SCAN" 2>&1 || true

# 归一化：去掉容器/绝对路径前缀与 CR，统一为 memory_agent/... 相对形式；
# 再去掉行列号 —— 基线键为「文件: 消息」。带行号会让任何一次上方插入都把同一条目
# 同时算成"新增"和"已修"，门禁误判红（实测 71 条去掉行列号后零重复）。
# `from line NNNN` 是 pyflakes 消息自带的话术（redefinition 类），同样会漂移，一并归一。
sed -E 's#^/app/src/##; s#^'"$REPO_ROOT"'/src/##; s#^src/##' "$TMP_SCAN" \
    | sed -E -e 's/:[0-9]+:[0-9]+:/:/' -e 's/(from line )[0-9]+/\1N/' | tr -d '\r' > "$TMP_NORM"

if [ "$BLESS" = "1" ] || [ ! -f "$BASELINE" ]; then
    cp "$TMP_NORM" "$BASELINE"
    echo "[gates] 基线已写入：$BASELINE（$(wc -l < "$BASELINE") 条）"
    exit 0
fi

NEW_COUNT=$(comm -13 <(sort "$BASELINE") <(sort "$TMP_NORM") | wc -l)
FIXED_COUNT=$(comm -23 <(sort "$BASELINE") <(sort "$TMP_NORM") | wc -l)
CUR_COUNT=$(wc -l < "$TMP_NORM")

echo "[gates] pyflakes: 当前 $CUR_COUNT 条，基线 $(wc -l < "$BASELINE") 条，新增 $NEW_COUNT，已修 $FIXED_COUNT"
if [ "$NEW_COUNT" -gt 0 ]; then
    echo "[gates] ❌ 新增 pyflakes 问题（必须为零）："
    comm -13 <(sort "$BASELINE") <(sort "$TMP_NORM")
    exit 1
fi
echo "[gates] ✅ pyflakes 门禁通过（无新增）"
