#!/usr/bin/env bash
# 部署 main 上相对已部署基线的变更文件到 NAS volume mount，并重启容器。
# 用法：
#   bash scripts/deploy_nas.sh                # 自动 diff HEAD 与 NAS_DEPLOYED_REF（默认 a879dd3），scp src/ tests/ benchmarks/ 内变更
#   bash scripts/deploy_nas.sh <ref>          # 指定基线 ref
#   bash scripts/deploy_nas.sh --no-restart   # 只 scp 不重启
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
NAS_SRC="/vol1/1000/docker/memory-agent"

BASE="a879dd3"
RESTART=1
for arg in "$@"; do
    case "$arg" in
        --no-restart) RESTART=0 ;;
        *) BASE="$arg" ;;
    esac
done

# 收集变更/新增文件（仅可部署目录）
mapfile -t FILES < <(git diff --name-only "$BASE"..HEAD -- src/ tests/ benchmarks/ .gates/ | grep -E '\.(py|txt|sh)$' || true)
if [ "${#FILES[@]}" -eq 0 ]; then
    echo "[deploy] 相对 $BASE 无源码变更，跳过 scp"
fi

SRC_FILES=(); TEST_FILES=(); BENCH_FILES=(); GATE_FILES=()
for f in "${FILES[@]}"; do
    case "$f" in
        src/*)        SRC_FILES+=("$f") ;;
        tests/*)      TEST_FILES+=("$f") ;;
        benchmarks/*) BENCH_FILES+=("$f") ;;
        .gates/*)     GATE_FILES+=("$f") ;;
    esac
done

push() {  # push <目标目录> <文件...>
    local dest="$1"; shift
    [ $# -eq 0 ] && return 0
    "$SCP" -i "$KEY" -o StrictHostKeyChecking=no -q "$@" "$NAS:$dest/"
    echo "[deploy] scp $# 个文件 -> $dest"
}

[ "${#SRC_FILES[@]}" -gt 0 ]  && push "$NAS_SRC/src/memory_agent" "${SRC_FILES[@]}"
[ "${#TEST_FILES[@]}" -gt 0 ] && push "$NAS_SRC/tests" "${TEST_FILES[@]}"
[ "${#BENCH_FILES[@]}" -gt 0 ] && push "$NAS_SRC/benchmarks" "${BENCH_FILES[@]}"
[ "${#GATE_FILES[@]}" -gt 0 ] && push "$NAS_SRC/.gates" "${GATE_FILES[@]}"

# 逐个确认到位（抽样 grep 文件名存在即可）
for f in "${SRC_FILES[@]}"; do
    base="$(basename "$f")"
    "$SSH" -i "$KEY" -o StrictHostKeyChecking=no "$NAS" "test -f $NAS_SRC/src/memory_agent/$base" || { echo "[deploy] ❌ NAS 未见 $base"; exit 1; }
done
echo "[deploy] 文件确认到位"

if [ "$RESTART" = "1" ]; then
    "$SSH" -i "$KEY" -o StrictHostKeyChecking=no "$NAS" "docker restart memory-agent"
    echo "[deploy] 容器已重启；chroma 冷启动约需 12 分钟后再做健康检查"
fi
