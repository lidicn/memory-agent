#!/usr/bin/env bash
# run30 本机侧编排：出网前四把尺，任一不过就 exit 2 不发容器。远端本体 = tmp-c90-remote30.sh。
#
#   尺1 语法与形状：远端 bash -n、两层引号 lint、六个文件 ast + CR 按字节 = 0、harness ast；
#   尺2 本机读数档必须真在盘上且判据对得上：锁 7 passed、两个测试文件全绿、四把门 self/scan RC=0、
#        本机变异档 killed=4 / survived=2 / invalid=0（survived 那两条**只能**在容器读，见下）；
#   尺3 树 = HEAD：代码面工作树干净、filelist 含本批四件、聚合哈希当场量、gates 基线行数现读；
#   尺4 旧三档变异腿可否跳过：`git diff --name-only 8c16b06..HEAD -- src` 必须 = 0，
#        且旧 harness 三个腿数锚点（11/22/14）现读未动。
# 期望值全部当场从文件量，不抄上一档的数（这条是 run29 那格 PREFLIGHT_FAILED 换来的）。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c90snap30
TGZ=.qoder/tmp-c90-snap30.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
BAD=0
guard() { if [ "$2" != "$3" ]; then echo "GUARD_FAIL $1 期望=$2 实际=$3"; BAD=1; else echo "GUARD_OK $1=$3"; fi; }
AST() { "$PY" -c "import ast,io,sys;ast.parse(io.open(sys.argv[1],encoding='utf-8').read(),filename=sys.argv[1])" "$1"; }

FOUR="scripts/scan_claimed_semantics.py tests/test_vma_phase2_claims_gauge.py tests/test_vma_a8_insights_clock_and_tags.py tests/test_rounds11_19_ma25_35_fixes.py"

echo HEAD=$(git rev-parse --short HEAD)
CODE_DIFF=$(git status --porcelain --untracked-files=no -- src tests scripts benchmarks attic doc .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh Dockerfile vendor)
guard CODE_CLEAN 0 "$(printf '%s' "$CODE_DIFF" | grep -c . )"
guard HEAD_IS_30 a5ffdde "$(git rev-parse --short HEAD)"

# ── 尺1：语法 / 引号 / 行尾 ──
bash -n .qoder/tmp-c90-remote30.sh; guard REMOTE_SYNTAX_RC 0 $?
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c90-remote30.sh; guard LINT_RC 0 $?
AST .qoder/tmp-c68-hashes.py; guard AST_HASHES_RC 0 $?
AST .qoder/tmp-c90-mut30.py; guard AST_MUT30_RC 0 $?
for f in $FOUR .qoder/tmp-c90-mut30.py; do
  AST "$f"; guard AST_$(basename $f) 0 $?
  guard CR_$(basename $f) 0 "$(tr -dc '\r' < "$f" | wc -c | tr -d ' ')"
done
for f in $FOUR; do guard TRACKED_$(basename $f) 1 "$(git ls-files -- "$f" | grep -c . )"; done

# ── 尺2：本机读数档必须真在盘上 ──
for g in scan_stub_claims_success scan_source_of_truth_sync scan_cleanup_scheduled scan_claimed_semantics; do
  "$PY" scripts/$g.py --self-test > .qoder/tmp-c90-${g}-self.out 2>&1; guard SELFTEST_$g 0 $?
  guard SELFTEST_MISS_$g 0 "$(grep -cE '^SELFTEST_MISS|^SELFTEST_FALSE' .qoder/tmp-c90-${g}-self.out)"
  "$PY" scripts/$g.py > .qoder/tmp-c90-${g}-scan.out 2>&1; guard SCAN_$g 0 $?
  guard SCAN_PROBLEM_$g 0 "$(grep -oE 'PROBLEM=[0-9]+' .qoder/tmp-c90-${g}-scan.out | head -1 | cut -d= -f2)"
done
# G4 的读数必须是"减三格之后"的形状：REGISTERED=18 / UNVERIFIED=23（当场从台账量，不写死记忆）
"$PY" -c "import importlib.util as u;s=u.spec_from_file_location('g','scripts/scan_claimed_semantics.py');m=u.module_from_spec(s);s.loader.exec_module(m);print(len(m.REGISTRY),len(m.UNVERIFIED),len(m.REGISTRY)+len(m.UNVERIFIED))" > .qoder/tmp-c90-g4-ledgers.out 2>&1
# 本机 python 的重定向会写成 CRLF：`read` 会把行尾 CR 一起读进变量（SUM_N=$'41\r'），
# 而 grep -o 只取匹配片段所以别的格子没这问题 ⇒ 这里必须先按字节去掉 CR 再比。
read REG_N UNV_N SUM_N < <(tr -d '\r' < .qoder/tmp-c90-g4-ledgers.out)
guard G4_REGISTERED 18 "$REG_N"
guard G4_UNVERIFIED 23 "$UNV_N"
CAP_LIVE=$(grep -oE '^BASELINE_CAP = [0-9]+' tests/test_vma_phase2_claims_gauge.py | awk '{print $3}')
echo CAP_LIVE=$CAP_LIVE
# 真不变量是「CAP == len(UNVERIFIED)」，不是「CAP == 我记得的那个数」
guard G4_CAP_EQUALS_BASELINE 1 "$([ "$UNV_N" = "$CAP_LIVE" ] && echo 1 || echo 0)"
guard G4_DECLARED_EQUALS_LEDGERS 1 "$([ "$SUM_N" = "$(grep -oE 'DECLARED=[0-9]+' .qoder/tmp-c90-scan_claimed_semantics-scan.out | head -1 | cut -d= -f2)" ] && echo 1 || echo 0)"

"$PY" -m pytest tests/test_vma_phase2_claims_gauge.py -q > .qoder/tmp-c90-lock-local.out 2>&1; guard LOCK_LOCAL_RC 0 $?
guard LOCK_LOCAL_PASSED "7 passed" "$(grep -oE '[0-9]+ passed' .qoder/tmp-c90-lock-local.out | tail -1)"
"$PY" -m pytest tests/test_vma_a8_insights_clock_and_tags.py tests/test_rounds11_19_ma25_35_fixes.py -q -rs > .qoder/tmp-c90-target-local.out 2>&1; guard TARGET_LOCAL_RC 0 $?
tail -1 .qoder/tmp-c90-target-local.out
# 本机变异档：R05/R06 的靶是 mcp 工具本体，本机旧 SDK ⇒ 用例 skip ⇒ 这两腿只能活（下一条格在容器读它们）
MUT_ROOT="E:/NAS/memory-agent" MUT_DST="E:/NAS/memory-agent/.qoder/tmp-c90mut30-local" MUT_OUT="E:/NAS/memory-agent/.qoder/tmp-c90-mut30-local.out" MUT_PY="$PY" "$PY" .qoder/tmp-c90-mut30.py > /dev/null 2>&1
guard MUT_LOCAL_RC 2 $?
guard MUT_LOCAL_KILLED "4" "$(grep -oE 'killed=[0-9]+' .qoder/tmp-c90-mut30-local.out | tail -1 | cut -d= -f2)"
guard MUT_LOCAL_SURVIVED "2" "$(grep -oE 'survived=[0-9]+' .qoder/tmp-c90-mut30-local.out | tail -1 | cut -d= -f2)"
guard MUT_LOCAL_INVALID "0" "$(grep -oE 'invalid=[0-9]+' .qoder/tmp-c90-mut30-local.out | tail -1 | cut -d= -f2)"
guard COVERAGE_ARTIFACT 0 "$([ -f .coverage ] && echo 1 || echo 0)"

# ── 尺3：树 = HEAD，本批四件必在清单里，聚合哈希当场量 ──
{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c90-filelist30.txt
echo FILES30=$(wc -l < .qoder/tmp-c90-filelist30.txt)
for f in $FOUR; do guard INLIST_$(basename $f) 1 "$(grep -c "^$f\$" .qoder/tmp-c90-filelist30.txt)"; done
echo BASE_LIVE=$(wc -l < .gates-baseline.txt | tr -d ' ')
"$PY" .qoder/tmp-c68-hashes.py .qoder/tmp-c90-filelist30.txt > .qoder/tmp-c90-hashes-local30.out 2>&1
guard HASH_LOCAL_MISSING 0 "$(grep -oE 'HASH_MISSING=[0-9]+' .qoder/tmp-c90-hashes-local30.out | cut -d= -f2)"
grep -E '^HASH_' .qoder/tmp-c90-hashes-local30.out
for f in $FOUR; do echo "LOCAL_BYTES_$(basename $f)=$(wc -c < "$f" | tr -d ' ')"; done
echo LOCAL_BYTES_mut30.py=$(wc -c < .qoder/tmp-c90-mut30.py | tr -d ' ')

# ── 尺4：旧三档变异腿与本批 diff 的交集（不为 0 就别跳过）──
SRC_TOUCHED=$(git diff --name-only 8c16b06..HEAD -- src | grep -c . )
guard SRC_TOUCHED 0 "$SRC_TOUCHED"
guard MUT85_LEGS 11 "$(grep -cE '^[[:space:]]+\(\"Q[0-9]+\", ' .qoder/tmp-c85-mut.py)"
guard MUT81_LEGS 22 "$(grep -cE '^[[:space:]]+\(\"M[0-9]+\", ' .qoder/tmp-c81-mut.py)"
guard MUT82_LEGS 14 "$(grep -cE '^[[:space:]]+\(\"N[0-9]+\", ' .qoder/tmp-c82-mut.py)"
guard MUT30_LEGS 6 "$(grep -cE '^[[:space:]]+\(\"R[0-9]+\", ' .qoder/tmp-c90-mut30.py)"

if [ "$BAD" != "0" ]; then echo GUARDS_FAILED=1 未出网; exit 2; fi
echo GUARDS_ALL_OK=1

tar -czf "$TGZ" -T .qoder/tmp-c90-filelist30.txt; guard TAR_RC 0 $?
echo TAR_SIZE=$(stat -c %s "$TGZ")
"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; guard SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c90-remote30.sh "$NAS:/tmp/ma_c90_remote30.sh"; guard REMOTE_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-hashes.py "$NAS:/tmp/ma_c90_hashes.py"; guard HASHES_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c90-mut30.py "$NAS:/tmp/ma_c90_mut30.py"; guard MUT_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c90-filelist30.txt "$NAS:/tmp/ma_c90_filelist.txt"; guard FILELIST_SCP_RC 0 $?

"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP /tmp/c90mut30' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage; echo STAGE_SNAP_RC=\$?"
"$SSH" "${OPTS[@]}" "$NAS" "tr -d '\r' < /tmp/ma_c90_filelist.txt > /tmp/ma_c90_filelist_unix.tmp && docker cp /tmp/ma_c90_filelist_unix.tmp memory-agent:/tmp/${SNAP}_filelist.txt && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_filelist.txt && rm -f /tmp/ma_c90_filelist_unix.tmp /tmp/ma_c90_filelist.txt; echo STAGE_FILELIST_RC=\$?; tr -d '\r' < /tmp/ma_c90_hashes.py > /tmp/ma_c90_hashes_unix.tmp && docker cp /tmp/ma_c90_hashes_unix.tmp memory-agent:/tmp/${SNAP}_hashes.py && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_hashes.py && rm -f /tmp/ma_c90_hashes_unix.tmp /tmp/ma_c90_hashes.py; echo NORM_HASHES_RC=\$?; tr -d '\r' < /tmp/ma_c90_mut30.py > /tmp/ma_c90_mut_unix.py && docker cp /tmp/ma_c90_mut_unix.py memory-agent:/tmp/${SNAP}_mut30.py && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut30.py && rm -f /tmp/ma_c90_mut_unix.py /tmp/ma_c90_mut30.py; echo NORM_MUT_RC=\$?; tr -d '\r' < /tmp/ma_c90_remote30.sh > /tmp/ma_c90_remote30_unix.sh && bash -n /tmp/ma_c90_remote30_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?; rm -f /tmp/ma_c90_remote30.sh; docker exec memory-agent sh -c 'wc -l < /tmp/${SNAP}_filelist.txt; wc -c < /tmp/${SNAP}_mut30.py'; echo HELPER_PRESENCE_RC=\$?"
SYNC_RC=$?
echo SYNC_RC=$SYNC_RC
if [ "$SYNC_RC" != "0" ]; then echo SYNC_FAILED=1 未出网; exit 2; fi

# 远端整跑 ≈ 14 分钟（全量回归单格 9 分钟量级 + 六腿变异各一次定点回归），
# nohup 脱开 ssh 生命周期，读数从 NAS 侧日志取
"$SSH" "${OPTS[@]}" "$NAS" "nohup bash /tmp/ma_c90_remote30_unix.sh $SNAP > /tmp/ma_c90_run30.log 2>&1 < /dev/null & echo REMOTE_LAUNCHED_PID=\$!; sleep 2; head -3 /tmp/ma_c90_run30.log"

echo CONTAINER_BATCH_LAUNCHED=1
echo POLL_CMD="\"\$SSH\" \"\${OPTS[@]}\" \"\$NAS\" \"tail -40 /tmp/ma_c90_run30.log\""
