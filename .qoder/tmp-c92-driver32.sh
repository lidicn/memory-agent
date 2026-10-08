#!/usr/bin/env bash
# run32 本机侧编排：出网前四把尺，任一不过就 exit 2 不发容器。远端本体 = tmp-c92-remote32.sh。
#
#   尺1 语法与形状：远端 bash -n、两层引号 lint、七件文件 ast + CR 按字节 = 0、九条腿锚点静态现数
#        （ANCHOR_BAD=0 / ANCHOR_MISSING=0 / ANCHOR_OK=9）；
#   尺2 本机读数档必须真在盘上且判据对得上：锁 7 passed、新用例 13 passed、既有断言 130 passed、
#        四把门 self/scan RC=0、G4 台账当场量 = 29/12/48 且 CAP == len(UNVERIFIED)、
#        本机九腿 killed=9 / survived=0 / invalid=0（九腿的靶全在本仓可读面，本机就该全杀）；
#   尺3 树 = HEAD：代码面工作树干净、filelist 含本批三件、聚合哈希当场量；
#   尺4 旧四档这一批**可以**跳过，但前提要当场量：`git diff --name-only b150000..HEAD -- src` = 0
#        （run31 那 60 条腿的靶全在 src，本批没动 src 才谈得上跳过），同时核四份 harness 的腿数未漂移。
# 期望值全部当场从文件量，不抄上一档的数（这条是 run29 那格 PREFLIGHT_FAILED 换来的）。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c92snap32
TGZ=.qoder/tmp-c92-snap32.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
BAD=0
guard() { if [ "$2" != "$3" ]; then echo "GUARD_FAIL $1 期望=$2 实际=$3"; BAD=1; else echo "GUARD_OK $1=$3"; fi; }
AST() { "$PY" -c "import ast,io,sys;ast.parse(io.open(sys.argv[1],encoding='utf-8').read(),filename=sys.argv[1])" "$1"; }

THREE="scripts/scan_claimed_semantics.py tests/test_vma_phase2_claims_gauge.py tests/test_vma_phase4_claims_direct.py"
HELPERS=".qoder/tmp-c92-mut32.py .qoder/tmp-c91-anchor-check.py .qoder/tmp-c68-hashes.py"

echo HEAD=$(git rev-parse --short HEAD)
CODE_DIFF=$(git status --porcelain --untracked-files=no -- src tests scripts benchmarks attic doc .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh Dockerfile vendor)
guard CODE_CLEAN 0 "$(printf '%s' "$CODE_DIFF" | grep -c . )"
guard HEAD_IS_32 e0e684c "$(git rev-parse --short HEAD)"

# ── 尺1：语法 / 引号 / 行尾 / 锚点 ──
bash -n .qoder/tmp-c92-remote32.sh; guard REMOTE_SYNTAX_RC 0 $?
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c92-remote32.sh > .qoder/tmp-c92-lint.out 2>&1; guard LINT_RC 0 $?
guard LINT_UNESCAPED 0 "$(grep -oE 'UNESCAPED=[0-9]+' .qoder/tmp-c92-lint.out | head -1 | cut -d= -f2)"
for f in $THREE $HELPERS; do
  # 先存 rc：命令替换 $(basename …) 会覆盖 $?，不存就先存再拼名字。
  AST "$f"; rc=$?; guard AST_$(basename $f) 0 "$rc"
  guard CR_$(basename $f) 0 "$(tr -dc '\r' < "$f" | wc -c | tr -d ' ')"
done
for f in $THREE; do guard TRACKED_$(basename $f) 1 "$(git ls-files -- "$f" | grep -c . )"; done
"$PY" .qoder/tmp-c91-anchor-check.py . .qoder/tmp-c92-mut32.py > .qoder/tmp-c92-anchor-local.out 2>&1
guard ANCHOR_RC 0 $?
cat .qoder/tmp-c92-anchor-local.out
guard ANCHOR_BAD 0 "$(grep -oE 'ANCHOR_BAD=[0-9]+' .qoder/tmp-c92-anchor-local.out | cut -d= -f2)"
guard ANCHOR_MISSING 0 "$(grep -oE 'ANCHOR_MISSING=[0-9]+' .qoder/tmp-c92-anchor-local.out | cut -d= -f2)"
guard ANCHOR_OK 9 "$(grep -oE 'ANCHOR_OK=[0-9]+' .qoder/tmp-c92-anchor-local.out | cut -d= -f2)"

# ── 尺2：本机读数档必须真在盘上 ──
for g in scan_stub_claims_success scan_source_of_truth_sync scan_cleanup_scheduled scan_claimed_semantics; do
  "$PY" scripts/$g.py --self-test > .qoder/tmp-c92-${g}-self.out 2>&1; guard SELFTEST_$g 0 $?
  guard SELFTEST_MISS_$g 0 "$(grep -cE '^SELFTEST_MISS|^SELFTEST_FALSE' .qoder/tmp-c92-${g}-self.out)"
  "$PY" scripts/$g.py > .qoder/tmp-c92-${g}-scan.out 2>&1; guard SCAN_$g 0 $?
  guard SCAN_PROBLEM_$g 0 "$(grep -oE 'PROBLEM=[0-9]+' .qoder/tmp-c92-${g}-scan.out | head -1 | cut -d= -f2)"
done
"$PY" -c "import importlib.util as u;s=u.spec_from_file_location('g','scripts/scan_claimed_semantics.py');m=u.module_from_spec(s);s.loader.exec_module(m);print(len(m.REGISTRY),len(m.UNVERIFIED),len(m.REGISTRY)+len(m.UNVERIFIED),sum(len(e['cases']) for e in m.REGISTRY.values()))" > .qoder/tmp-c92-g4-ledgers.out 2>&1
# 本机 python 的重定向会写成 CRLF：`read` 会把行尾 CR 一起读进变量（而 grep -o 只取匹配片段所以别的格子没这问题）
read REG_N UNV_N SUM_N CASE_N < <(tr -d '\r' < .qoder/tmp-c92-g4-ledgers.out)
echo LEDGERS REG=$REG_N UNV=$UNV_N SUM=$SUM_N CASES=$CASE_N
guard G4_REGISTERED 29 "$REG_N"
guard G4_UNVERIFIED 12 "$UNV_N"
guard G4_CASES 48 "$CASE_N"
CAP_LIVE=$(grep -oE '^BASELINE_CAP = [0-9]+' tests/test_vma_phase2_claims_gauge.py | awk '{print $3}')
echo CAP_LIVE=$CAP_LIVE
guard G4_CAP_EQUALS_BASELINE 1 "$([ "$UNV_N" = "$CAP_LIVE" ] && echo 1 || echo 0)"
guard G4_DECLARED_EQUALS_LEDGERS 1 "$([ "$SUM_N" = "$(grep -oE 'DECLARED=[0-9]+' .qoder/tmp-c92-scan_claimed_semantics-scan.out | head -1 | cut -d= -f2)" ] && echo 1 || echo 0)"

"$PY" -m pytest tests/test_vma_phase2_claims_gauge.py -q -p no:cacheprovider > .qoder/tmp-c92-lock-local.out 2>&1; guard LOCK_LOCAL_RC 0 $?
guard LOCK_LOCAL_PASSED "7 passed" "$(grep -oE '[0-9]+ passed' .qoder/tmp-c92-lock-local.out | tail -1)"
"$PY" -m pytest tests/test_vma_phase4_claims_direct.py -q -p no:cacheprovider > .qoder/tmp-c92-ph4-local.out 2>&1; guard PH4_LOCAL_RC 0 $?
guard PH4_LOCAL_PASSED "13 passed" "$(grep -oE '[0-9]+ passed' .qoder/tmp-c92-ph4-local.out | tail -1)"
"$PY" -m pytest tests/test_vma_qb_param_landing.py tests/test_vma_phase2_batch3_shared_ruler.py -q -rs -p no:cacheprovider > .qoder/tmp-c92-pair-local.out 2>&1; guard PAIR_LOCAL_RC 0 $?
guard PAIR_LOCAL_PASSED "130 passed" "$(grep -oE '[0-9]+ passed' .qoder/tmp-c92-pair-local.out | tail -1)"
guard PAIR_LOCAL_SKIPPED 0 "$(grep -cE '^SKIPPED' .qoder/tmp-c92-pair-local.out)"
MUT_ROOT="E:/NAS/memory-agent" MUT_DST="E:/NAS/memory-agent/.qoder/tmp-c92mut32-local" MUT_OUT="E:/NAS/memory-agent/.qoder/tmp-c92-mut32-local.out" MUT_PY="$PY" "$PY" .qoder/tmp-c92-mut32.py > /dev/null 2>&1
guard MUT_LOCAL_RC 0 $?
guard MUT_LOCAL_KILLED "9" "$(grep -oE 'killed=[0-9]+' .qoder/tmp-c92-mut32-local.out | tail -1 | cut -d= -f2)"
guard MUT_LOCAL_SURVIVED "0" "$(grep -oE 'survived=[0-9]+' .qoder/tmp-c92-mut32-local.out | tail -1 | cut -d= -f2)"
guard MUT_LOCAL_INVALID "0" "$(grep -oE 'invalid=[0-9]+' .qoder/tmp-c92-mut32-local.out | tail -1 | cut -d= -f2)"
guard COVERAGE_ARTIFACT 0 "$([ -f .coverage ] && echo 1 || echo 0)"

# ── 尺3：树 = HEAD，本批三件必在清单里，聚合哈希当场量 ──
{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c92-filelist32.txt
echo FILES32=$(wc -l < .qoder/tmp-c92-filelist32.txt)
for f in $THREE; do guard INLIST_$(basename $f) 1 "$(grep -c "^$f\$" .qoder/tmp-c92-filelist32.txt)"; done
echo BASE_LIVE=$(wc -l < .gates-baseline.txt | tr -d ' ')
"$PY" .qoder/tmp-c68-hashes.py .qoder/tmp-c92-filelist32.txt > .qoder/tmp-c92-hashes-local32.out 2>&1
guard HASH_LOCAL_MISSING 0 "$(grep -oE 'HASH_MISSING=[0-9]+' .qoder/tmp-c92-hashes-local32.out | cut -d= -f2)"
grep -E '^HASH_' .qoder/tmp-c92-hashes-local32.out
for f in $THREE $HELPERS; do echo "LOCAL_BYTES_$(basename $f)=$(wc -c < "$f" | tr -d ' ')"; done

# ── 尺4：跳过旧四档的前提要当场量（本批没动 src），并核四份 harness 腿数未漂移 ──
SRC_TOUCHED=$(git diff --name-only b150000..HEAD -- src | tr -d '\r')
printf 'SRC_TOUCHED[%s]\n' "$SRC_TOUCHED"
guard SRC_UNTOUCHED 0 "$(printf '%s' "$SRC_TOUCHED" | grep -c . )"
guard MUT32_LEGS 9 "$(grep -cE '^[[:space:]]+\(\"N[0-9]+\", ' .qoder/tmp-c92-mut32.py)"
guard MUT31_LEGS 13 "$(grep -cE '^[[:space:]]+\(\"T[0-9]+\", ' .qoder/tmp-c91-mut31.py)"
guard MUT81_LEGS 22 "$(grep -cE '^[[:space:]]+\(\"M[0-9]+\", ' .qoder/tmp-c81-mut.py)"
guard MUT85_LEGS 11 "$(grep -cE '^[[:space:]]+\(\"Q[0-9]+\", ' .qoder/tmp-c85-mut.py)"
guard MUT82_LEGS 14 "$(grep -cE '^[[:space:]]+\(\"N[0-9]+\", ' .qoder/tmp-c82-mut.py)"

if [ "$BAD" != "0" ]; then echo GUARDS_FAILED=1 未出网; exit 2; fi
echo GUARDS_ALL_OK=1

tar -czf "$TGZ" -T .qoder/tmp-c92-filelist32.txt; guard TAR_RC 0 $?
echo TAR_SIZE=$(stat -c %s "$TGZ")
"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; guard SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c92-remote32.sh "$NAS:/tmp/ma_c92_remote32.sh"; guard REMOTE_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c92-filelist32.txt "$NAS:/tmp/ma_c92_filelist.txt"; guard FILELIST_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c92-mut32.py "$NAS:/tmp/ma_c92_mut32.py"; guard MUT32_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c91-anchor-check.py "$NAS:/tmp/ma_c92_anchor.py"; guard ANCHOR_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-hashes.py "$NAS:/tmp/ma_c92_hashes.py"; guard HASHES_SCP_RC 0 $?

# 远端本体先在 NAS 侧去 CR 再 bash -n：Windows 写的 CRLF 在这一层最容易漏（run30 实测过形状）
"$SSH" "${OPTS[@]}" "$NAS" "tr -d '\r' < /tmp/ma_c92_remote32.sh > /tmp/ma_c92_remote32_unix.sh && bash -n /tmp/ma_c92_remote32_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?; rm -f /tmp/ma_c92_remote32.sh"
guard NAS_NORMALIZE 0 $?

"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage; echo STAGE_SNAP_RC=\$?"
# 四件 helper 走同一套规范化（去 CR → docker cp → chown 10001），并当场报字节数供逐件对账
"$SSH" "${OPTS[@]}" "$NAS" "for pair in 'ma_c92_filelist.txt:${SNAP}_filelist.txt' 'ma_c92_hashes.py:${SNAP}_hashes.py' 'ma_c92_mut32.py:${SNAP}_mut32.py' 'ma_c92_anchor.py:${SNAP}_anchor.py'; do src=/tmp/\${pair%%:*}; dst=/tmp/\${pair##*:}; tr -d '\r' < \$src > /tmp/norm.tmp && docker cp /tmp/norm.tmp memory-agent:\$dst && docker exec -u root memory-agent chown 10001:10001 \$dst && rm -f /tmp/norm.tmp \$src || echo NORM_FAIL_\$src; done; docker exec memory-agent sh -c 'wc -l < /tmp/${SNAP}_filelist.txt; wc -c /tmp/${SNAP}_hashes.py /tmp/${SNAP}_mut32.py /tmp/${SNAP}_anchor.py'; echo HELPER_PRESENCE_RC=\$?"
SYNC_RC=$?
echo SYNC_RC=$SYNC_RC
if [ "$SYNC_RC" != "0" ]; then echo SYNC_FAILED=1 未出网; exit 2; fi

"$SSH" "${OPTS[@]}" "$NAS" "nohup bash /tmp/ma_c92_remote32_unix.sh $SNAP > /tmp/ma_c92_run32.log 2>&1 < /dev/null & echo CONTAINER_BATCH_LAUNCHED=1; sleep 2; wc -l < /tmp/ma_c92_run32.log"
echo LAUNCH_RC=$?
cat > .qoder/tmp-c92-poll32.sh <<'POLL'
#!/usr/bin/env bash
set -uo pipefail
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
"$SSH" -i "$KEY" -o StrictHostKeyChecking=no lidicn@192.168.2.200 \
  "grep -E '_RC=|_DONE|ANCHOR_|SKIPPED|totals|KILLED_LINES|passed|failed' /tmp/ma_c92_run32.log | tail -45"
POLL
echo POLL_SCRIPT=.qoder/tmp-c92-poll32.sh
echo DRIVER_STAGE_DONE=1
