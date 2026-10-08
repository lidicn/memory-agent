#!/usr/bin/env bash
# run29 本机侧编排：出网前四把尺，任一不过就 exit 2 不发容器。远端本体 = tmp-c89-remote29.sh。
#
#   尺1 语法与形状：远端 bash -n、两层引号 lint、五件 ast、五件 CR 按字节 = 0；
#   尺2 本机读数档必须真在盘上且判据对得上：四把门 self-test RC=0 + 实扫 PROBLEM=0、锁 6 passed；
#   尺3 树 = HEAD：工作树在代码面必须干净（HEAD `8c16b06`）、filelist 含五件、本机聚合哈希当场量；
#   尺4 变异腿可否跳过：`git diff --name-only 800c17b..HEAD -- src` 命中数必须 = 0，否则不发（要整跑）。
# 期望值全部当场从文件量，不抄上一档的数。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c89snap29
TGZ=.qoder/tmp-c89-snap29.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
BAD=0
guard() { if [ "$2" != "$3" ]; then echo "GUARD_FAIL $1 期望=$2 实际=$3"; BAD=1; else echo "GUARD_OK $1=$3"; fi; }

FIVE="scripts/scan_stub_claims_success.py scripts/scan_source_of_truth_sync.py scripts/scan_cleanup_scheduled.py scripts/scan_claimed_semantics.py tests/test_vma_phase2_claims_gauge.py"

echo HEAD=$(git rev-parse --short HEAD)
CODE_DIFF=$(git status --porcelain --untracked-files=no -- src tests scripts benchmarks attic doc .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh Dockerfile vendor)
guard CODE_CLEAN 0 "$(printf '%s' "$CODE_DIFF" | grep -c . )"
UNTRACKED_SCRIPTS=$(git ls-files --others --exclude-standard -- src tests scripts | grep -c . )
echo UNTRACKED_PREEXISTING=$UNTRACKED_SCRIPTS
guard UNTRACKED_NOT_MY_FIVE 0 "$(git ls-files --others --exclude-standard -- src tests scripts | grep -cE 'scan_stub_claims_success|scan_source_of_truth_sync|scan_cleanup_scheduled|scan_claimed_semantics|test_vma_phase2_claims_gauge')"
guard HEAD_IS_29 8c16b06 "$(git rev-parse --short HEAD)"

# ── 尺1：语法 / 引号 / 行尾 ──
bash -n .qoder/tmp-c89-remote29.sh; guard REMOTE_SYNTAX_RC 0 $?
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c89-remote29.sh; guard LINT_RC 0 $?
"$PY" -c "import ast,io,sys;ast.parse(io.open(sys.argv[1],encoding='utf-8').read())" .qoder/tmp-c68-hashes.py; guard AST_HASHES 0 $?
for f in $FIVE; do
  "$PY" -c "import ast,io,sys;ast.parse(io.open(sys.argv[1],encoding='utf-8').read())" "$f"; guard AST_$(basename $f) 0 $?
  guard CR_$(basename $f) 0 "$(tr -dc '\r' < "$f" | wc -c | tr -d ' ')"
  guard TRACKED_$(basename $f) 1 "$(git ls-files -- "$f" | grep -c . )"
done

# ── 尺2：本机读数档必须真在盘上 ──
for g in scan_stub_claims_success scan_source_of_truth_sync scan_cleanup_scheduled scan_claimed_semantics; do
  "$PY" scripts/$g.py --self-test > .qoder/tmp-c89-${g}-self.out 2>&1; guard SELFTEST_$g 0 $?
  guard SELFTEST_MISS_$g 0 "$(grep -cE '^SELFTEST_MISS|^SELFTEST_FALSE' .qoder/tmp-c89-${g}-self.out)"
  "$PY" scripts/$g.py > .qoder/tmp-c89-${g}-scan.out 2>&1; guard SCAN_$g 0 $?
  guard SCAN_PROBLEM_$g 0 "$(grep -oE 'PROBLEM=[0-9]+' .qoder/tmp-c89-${g}-scan.out | head -1 | cut -d= -f2)"
done
"$PY" -m pytest tests/test_vma_phase2_claims_gauge.py -q > .qoder/tmp-c89-lock-local.out 2>&1; guard LOCK_LOCAL_RC 0 $?
guard LOCK_LOCAL_PASSED "6 passed" "$(grep -oE '[0-9]+ passed' .qoder/tmp-c89-lock-local.out | tail -1)"
guard COVERAGE_ARTIFACT 0 "$([ -f .coverage ] && echo 1 || echo 0)"

# ── 尺3：树 = HEAD，五件必在清单里，聚合哈希当场量 ──
{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c89-filelist29.txt
echo FILES29=$(wc -l < .qoder/tmp-c89-filelist29.txt)
for f in $FIVE; do guard INLIST_$(basename $f) 1 "$(grep -c "^$f\$" .qoder/tmp-c89-filelist29.txt)"; done
BASE_NOW=$(wc -l < .gates-baseline.txt | tr -d ' ')
echo BASE_LIVE=$BASE_NOW
"$PY" .qoder/tmp-c68-hashes.py .qoder/tmp-c89-filelist29.txt > .qoder/tmp-c89-hashes-local29.out 2>&1
guard HASH_LOCAL_MISSING 0 "$(grep -oE 'HASH_MISSING=[0-9]+' .qoder/tmp-c89-hashes-local29.out | cut -d= -f2)"
grep -E '^HASH_' .qoder/tmp-c89-hashes-local29.out

# 五件逐件字节数（远端 0N 格对账用）
for f in $FIVE; do echo "LOCAL_BYTES_$(basename $f)=$(wc -c < "$f" | tr -d ' ')"; done

# ── 尺4：变异腿靶面与本轮 diff 的交集（不为 0 就别跳过）──
SRC_TOUCHED=$(git diff --name-only 800c17b..HEAD -- src | grep -c . )
guard SRC_TOUCHED 0 "$SRC_TOUCHED"
guard MUT85_LEGS 11 "$(grep -cE '^[[:space:]]+\(\"Q[0-9]+\", ' .qoder/tmp-c85-mut.py)"
guard MUT81_LEGS 22 "$(grep -cE '^[[:space:]]+\(\"M[0-9]+\", ' .qoder/tmp-c81-mut.py)"
guard MUT82_LEGS 14 "$(grep -cE '^[[:space:]]+\(\"N[0-9]+\", ' .qoder/tmp-c82-mut.py)"

if [ "$BAD" != "0" ]; then echo GUARDS_FAILED=1 未出网; exit 2; fi
echo GUARDS_ALL_OK=1

tar -czf "$TGZ" -T .qoder/tmp-c89-filelist29.txt; guard TAR_RC 0 $?
echo TAR_SIZE=$(stat -c %s "$TGZ")
"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; guard SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c89-remote29.sh "$NAS:/tmp/ma_c89_remote29.sh"; guard REMOTE_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-hashes.py "$NAS:/tmp/ma_c89_hashes.py"; guard HASHES_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c89-filelist29.txt "$NAS:/tmp/ma_c89_filelist.txt"; guard FILELIST_SCP_RC 0 $?

"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage; echo STAGE_SNAP_RC=\$?"
"$SSH" "${OPTS[@]}" "$NAS" "tr -d '\r' < /tmp/ma_c89_filelist.txt > /tmp/ma_c89_filelist_unix.tmp && docker cp /tmp/ma_c89_filelist_unix.tmp memory-agent:/tmp/${SNAP}_filelist.txt && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_filelist.txt && rm -f /tmp/ma_c89_filelist_unix.tmp /tmp/ma_c89_filelist.txt; echo STAGE_FILELIST_RC=\$?; tr -d '\r' < /tmp/ma_c89_hashes.py > /tmp/ma_c89_hashes_unix.tmp && docker cp /tmp/ma_c89_hashes_unix.tmp memory-agent:/tmp/${SNAP}_hashes.py && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_hashes.py && rm -f /tmp/ma_c89_hashes_unix.tmp /tmp/ma_c89_hashes.py; echo NORM_HASHES_RC=\$?; tr -d '\r' < /tmp/ma_c89_remote29.sh > /tmp/ma_c89_remote29_unix.sh && bash -n /tmp/ma_c89_remote29_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?; docker exec memory-agent sh -c 'wc -l < /tmp/${SNAP}_filelist.txt'; echo SNAP_FILELIST_LINES_RC=\$?"
SYNC_RC=$?
echo SYNC_RC=$SYNC_RC
if [ "$SYNC_RC" != "0" ]; then echo SYNC_FAILED=1 未出网; exit 2; fi

# 远端整跑 ≈ 12 分钟（全量回归单格 9 分钟量级），nohup 脱开 ssh 生命周期，读数从 NAS 侧日志取
"$SSH" "${OPTS[@]}" "$NAS" "nohup bash /tmp/ma_c89_remote29_unix.sh $SNAP > /tmp/ma_c89_run29.log 2>&1 < /dev/null & echo REMOTE_LAUNCHED_PID=\$!; sleep 2; head -3 /tmp/ma_c89_run29.log"

echo CONTAINER_BATCH_LAUNCHED=1
echo POLL_CMD="\"\$SSH\" \"\${OPTS[@]}\" \"\$NAS\" \"tail -40 /tmp/ma_c89_run29.log\""
