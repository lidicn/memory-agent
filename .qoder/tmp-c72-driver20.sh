#!/usr/bin/env bash
# run20 = #72 的**补腿**：只重跑容器门 3（MUT72）。远端本体 = tmp-c72-remote20.sh。
#
# 与 run19 的差别只在"要不要重烤整棵快照"：run19 除门 3 外全绿，且失败发生在控制腿
# （控制腿不绿 ⇒ 主循环立刻 return，**没有改过树**），所以这一档复用同一棵 /tmp/c72snap20261007，
# 但"没改过"不靠推断——格 0 SNAP 用同一份 `_hashes.py` 重取聚合摘要，与 run19 逐字对账。
#
# 出网前的尺：语法 ×2、两层引号 lint、变异档只读预检、本机那档必须在盘上且 MUTATION_BAD=0、
# 容器快照目录必须还在、五条锚点读数必须与本机一致。任一不过 exit 2 不发容器。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c72snap20261007
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
BAD=0
guard() { if [ "$2" != "$3" ]; then echo "GUARD_FAIL $1 期望=$2 实际=$3"; BAD=1; else echo "GUARD_OK $1=$3"; fi }

echo HEAD=$(git rev-parse --short HEAD)
echo UNPUSHED=$(git rev-list --count origin/main..HEAD)
guard TREE_CLEAN 0 "$(git status --porcelain --untracked-files=no | wc -l | tr -d ' ')"

bash -n .qoder/tmp-c72-remote20.sh; guard REMOTE_SYNTAX_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c72-mut72.py',encoding='utf-8').read())"; guard MUT72_PARSE_RC 0 $?
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c72-remote20.sh; guard LINT_RC 0 $?

# 量具锚点（本机先读一遍，容器里读的是同一份文件，两侧数字必须一致）
guard PATH_APPEND 1 "$(grep -cF 'os.pathsep.join' .qoder/tmp-c72-mut72.py)"
guard OLD_CLOBBER 0 "$(grep -cF 'PYTHONPATH=os.path.join(root, "src"), JWT_SECRET' .qoder/tmp-c72-mut72.py)"
guard STDERR_SHOWN 1 "$(grep -cF 'NO_PYTEST_OUTPUT' .qoder/tmp-c72-mut72.py)"
guard FACADE_ORDER 1 "$(grep -cF 'store: Any = None, config: Optional' src/memory_agent/insights/api.py)"
guard LEGACY_ORDER 1 "$(grep -cF 'def __init__(self, config, store: Store)' src/memory_agent/insights_legacy.py)"

# 修好的量具先在本机重跑一遍：证明这次改动没把绿的腿改坏（副本树，工作树零改动）
MUT_ROOT="$PWD/.qoder/tmp-c72-wt" PYTHONPATH="$PWD/.qoder/tmp-c72-wt/src" \
  "$PY" .qoder/tmp-c72-mut72.py > .qoder/tmp-c72-mut72-local20.out 2>&1
guard MUT72_LOCAL_RC 0 $?
grep -E '^(NOTHING|L[0-9]+ |MUT_COUNT)' .qoder/tmp-c72-mut72-local20.out
guard MUT72_LOCAL_BAD 0 "$(grep -oE 'MUTATION_BAD=[0-9]+' .qoder/tmp-c72-mut72-local20.out | cut -d= -f2)"

"$PY" .qoder/tmp-c72-mut72.py --verify > .qoder/tmp-c72-mutverify20.out 2>&1; guard MUT72_VERIFY_RC 0 $?
guard MUT72_PARSED 3 "$(grep -oE 'MUTANTS_PARSED=[0-9]+' .qoder/tmp-c72-mutverify20.out | cut -d= -f2)"
guard MUT72_VERIFYBAD 0 "$(grep -oE 'VERIFY_BAD=[0-9]+' .qoder/tmp-c72-mutverify20.out | cut -d= -f2)"

if [ "$BAD" != 0 ]; then echo PREFLIGHT_FAILED=1 未出网; exit 2; fi
echo PREFLIGHT_OK=1

"$SCP" "${OPTS[@]}" -q .qoder/tmp-c72-mut72.py "$NAS:/tmp/ma_c72b_mut72.py"; guard MUT72_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c72-remote20.sh "$NAS:/tmp/ma_c72b_remote20.sh"; guard REMOTE_SCP_RC 0 $?

# 快照树必须还在（run19 那棵）；换掉容器里的量具副本，行尾先洗；再验远端脚本语法
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'test -d /tmp/$SNAP && test -f /tmp/${SNAP}_filelist.txt'; echo SNAP_PRESENT_RC=\$?; tr -d '\r' < /tmp/ma_c72b_mut72.py > /tmp/ma_c72b_mut72_unix.py && docker cp /tmp/ma_c72b_mut72_unix.py memory-agent:/tmp/${SNAP}_mut72.py && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut72.py; echo HARNESS_CP_RC=\$?; rm -f /tmp/ma_c72b_mut72.py /tmp/ma_c72b_mut72_unix.py; tr -d '\r' < /tmp/ma_c72b_remote20.sh > /tmp/ma_c72b_remote20_unix.sh && bash -n /tmp/ma_c72b_remote20_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?"
# 先赋值再打印：`echo X=$?` 只报数不存变量，上一版在这里读 $SYNC_RC 被 set -u 打回（那一次没出网）。
SYNC_RC=$?
echo SYNC_RC=$SYNC_RC
if [ "$SYNC_RC" != 0 ]; then echo SYNC_FAILED=1 未出网; exit 2; fi

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c72b_remote20_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"
echo CONTAINER_BATCH_RC=0
