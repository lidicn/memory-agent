#!/usr/bin/env bash
# 部署 main 上相对已部署基线的变更文件到 NAS volume mount，并重启容器。
# 用法：
#   bash scripts/deploy_nas.sh                # 自动 diff HEAD 与基线（默认 a879dd3）
#   bash scripts/deploy_nas.sh <ref>          # 指定基线 ref
#   bash scripts/deploy_nas.sh --no-restart   # 只 scp 不重启
#   bash scripts/deploy_nas.sh --full         # 全量同步 HEAD（git archive 一次推平，不依赖 diff 基线）
# 注意：按目录分组推送并保留相对路径（嵌套子目录不会被平铺到仓库根）。
# 契约门禁：两条路径都在**重启前**跑 tests/contract（MA↔DB 调用面），红了就中止、不重启；
#           逃生口 MA_SKIP_CONTRACT=1 仅限回滚窗口，用了要补跑并在交付记录里写明。
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
FULL=0
for arg in "$@"; do
    case "$arg" in
        --no-restart) RESTART=0 ;;
        --full) FULL=1 ;;
        *) BASE="$arg" ;;
    esac
done

SSH_OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SCP_OPTS=(-i "$KEY" -o StrictHostKeyChecking=no -q)

# ── 部署前必跑：MA↔DB 契约测试（计划卡 第 5 步 ②）────────────────────────────
# 红了就不重启：prod 继续跑旧版本，比"新码上线但 DB 调用面断了"好。
# 事实源是 DB 的真实调用点，所以这一跑能拦住改名/删参/返回键漂移——
# 这类断裂在 DB 侧表现为"静默空召回"，等下一轮回归才发现就等于没测。
# tests/ 没有挂进容器（只挂 src/data/.env），所以这里 docker cp 一份进去跑，
# 被跑的 src 就是刚同步到挂载目录的 HEAD 代码。
# 逃生口 MA_SKIP_CONTRACT=1：仅限回滚窗口，用了要在交付记录里写明补跑时间。
run_contract_gate() {
    if [ "${MA_SKIP_CONTRACT:-}" = "1" ]; then
        echo "[deploy] ⚠ MA_SKIP_CONTRACT=1，跳过契约门禁（事后必须补跑）"
        return 0
    fi
    local gtar
    gtar="$(mktemp)"
    git archive --format=tar HEAD tests > "$gtar"
    if ! "$SCP" "${SCP_OPTS[@]}" "$gtar" "$NAS:/tmp/ma_contract.tar"; then
        rm -f "$gtar"
        echo "[deploy] ✗ 契约测试包上传失败，中止部署（未重启）"
        exit 1
    fi
    rm -f "$gtar"
    if ! "$SSH" "${SSH_OPTS[@]}" "$NAS" "docker cp /tmp/ma_contract.tar memory-agent:/tmp/"; then
        echo "[deploy] ✗ 契约测试包送入容器失败，中止部署（未重启）"
        exit 1
    fi
    if ! "$SSH" "${SSH_OPTS[@]}" "$NAS" "docker exec memory-agent sh -lc 'rm -rf /tmp/ma_gate && mkdir -p /tmp/ma_gate && tar -C /tmp/ma_gate -xf /tmp/ma_contract.tar'"; then
        echo "[deploy] ✗ 契约测试包解包失败，中止部署（未重启）"
        exit 1
    fi
    if ! "$SSH" "${SSH_OPTS[@]}" "$NAS" "docker exec -w /tmp/ma_gate -e PYTHONPATH=/app/src:/tmp/pylibs -e JWT_SECRET=deploy-gate memory-agent python -m pytest tests/contract -q"; then
        echo "[deploy] ✗ MA↔DB 契约测试红，中止部署（未重启）"
        exit 1
    fi
    # 清理不参与判红：docker cp 落盘的属主是宿主机 uid，容器用户（实测 uid 10001）在
    # sticky /tmp 里删不掉它——上一版把 rm 串进判定链，把 42 passed 的绿跑成了假红。
    "$SSH" "${SSH_OPTS[@]}" "$NAS" "docker exec memory-agent sh -lc 'rm -rf /tmp/ma_gate || true; rm -f /tmp/ma_contract.tar || true'; rm -f /tmp/ma_contract.tar" || true
    echo "[deploy] 契约门禁绿（tests/contract 对刚同步的 /app/src）"
}

# 两条分支都以 HEAD 为源：--full 走 git archive HEAD，增量走 git diff BASE...HEAD。
# 未提交的工作区改动不会有任何一条路径带上——实测过：改完 src 直接 --full，
# NAS 上落的是 HEAD 版本，本地 md5 与 NAS md5 不一致却打印"全量同步完成"。
DIRTY="$(git status --porcelain -- src tests benchmarks .gates)"
if [ -n "$DIRTY" ]; then
    echo "[deploy] ✗ 工作区有未提交的可部署改动，git archive/diff 只看 HEAD，不会带上："
    echo "$DIRTY" | sed 's/^/[deploy]   /'
    echo "[deploy] 先 commit 再部署；确认就是要只发 HEAD 时 MA_ALLOW_DIRTY=1 重来。"
    if [ -z "${MA_ALLOW_DIRTY:-}" ]; then
        exit 1
    fi
    echo "[deploy] MA_ALLOW_DIRTY=1，继续按 HEAD 部署（未提交改动留在工作区）"
fi

if [ "$FULL" = "1" ]; then
    # 全量：git archive 的包内路径就是 src/ tests/ benchmarks/ .gates/，
    # 直接解到 NAS_SRC 根，路径天然对齐，不需要 basename 猜测。
    TAR="$(mktemp)"
    trap 'rm -f "$TAR"' EXIT
    git archive --format=tar HEAD src tests benchmarks .gates > "$TAR"
    "$SCP" "${SCP_OPTS[@]}" "$TAR" "$NAS:/tmp/ma_full.tar"
    "$SSH" "${SSH_OPTS[@]}" "$NAS" "cd '$NAS_SRC' && tar -xf /tmp/ma_full.tar && rm -f /tmp/ma_full.tar && find src -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; echo '[deploy] 全量同步完成'"
    if [ "$RESTART" = "1" ]; then
        run_contract_gate
        "$SSH" "${SSH_OPTS[@]}" "$NAS" "docker restart memory-agent"
        echo "[deploy] 容器已重启；chroma 冷启动约需 12 分钟后再做健康检查"
    fi
    exit 0
fi

# 收集变更/新增文件（仅可部署目录）
mapfile -t FILES < <(git diff --name-only "$BASE"...HEAD -- src/ tests/ benchmarks/ .gates/ | grep -E '\.(py|txt|sh|toml|sql|json|js|css|html)$' || true)
if [ "${#FILES[@]}" -eq 0 ]; then
    echo "[deploy] 相对 $BASE 无可部署变更，跳过 scp"
    exit 0
fi

# bucket -> 仓库内前缀 : NAS 内目标根
# src/memory_agent/** -> $NAS_SRC/src/memory_agent/**
declare -A BUCKET_ROOT=(
    [src]="$NAS_SRC/src"
    [tests]="$NAS_SRC/tests"
    [benchmarks]="$NAS_SRC/benchmarks"
    [.gates]="$NAS_SRC/.gates"
)

# 按「目标目录」分组，保留相对路径
declare -A GROUP  # dest_dir -> 空格分隔的本地相对路径
for f in "${FILES[@]}"; do
    top="${f%%/*}"
    root="${BUCKET_ROOT[$top]:-}"
    if [ -z "$root" ]; then
        echo "[deploy] 跳过非部署目录: $f"; continue
    fi
    rel="${f#*/}"                       # 去掉 bucket 顶层目录名
    dir="$(dirname "$rel")"
    dest="$root"
    [ "$dir" != "." ] && dest="$root/$dir"
    GROUP["$dest"]+="$f"$'\n'
done

# 校验：bucket 子集内的文件必须真实存在于工作树（diff 可能含已删除文件）
PUSHED=()
for dest in "${!GROUP[@]}"; do
    mapfile -t candidates < <(printf '%s' "${GROUP[$dest]}" | sed '/^$/d')
    local_files=()
    for f in "${candidates[@]}"; do
        [ -f "$f" ] && local_files+=("$f")
    done
    [ "${#local_files[@]}" -eq 0 ] && continue
    "$SSH" "${SSH_OPTS[@]}" "$NAS" "mkdir -p '$dest'"
    "$SCP" "${SCP_OPTS[@]}" "${local_files[@]}" "$NAS:$dest/"
    echo "[deploy] scp ${#local_files[@]} 个文件 -> $dest"
    for f in "${local_files[@]}"; do PUSHED+=("$f"); done
done

# 逐个确认到位（按完整相对路径校验，不是 basename）
missing=0
for f in "${PUSHED[@]}"; do
    top="${f%%/*}"; rel="${f#*/}"
    remote="${BUCKET_ROOT[$top]}/$rel"
    if ! "$SSH" "${SSH_OPTS[@]}" "$NAS" "test -f '$remote'"; then
        echo "[deploy] ❌ NAS 未见 $remote"; missing=1
    fi
done
[ "$missing" -eq 0 ] || exit 1
echo "[deploy] ${#PUSHED[@]} 个文件确认到位（路径保留）"

if [ "$RESTART" = "1" ]; then
    run_contract_gate
    "$SSH" "${SSH_OPTS[@]}" "$NAS" "docker restart memory-agent"
    echo "[deploy] 容器已重启；chroma 冷启动约需 12 分钟后再做健康检查"
fi
