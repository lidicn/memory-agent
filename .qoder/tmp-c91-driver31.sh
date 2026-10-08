#!/usr/bin/env bash
# run31 本机侧编排：出网前四把尺，任一不过就 exit 2 不发容器。远端本体 = tmp-c91-remote31.sh。
#
#   尺1 语法与形状：远端 bash -n、两层引号 lint、八件文件 ast + CR 按字节 = 0、四份 harness 的
#        60 条锚点静态现数（ANCHOR_BAD=0 / ANCHOR_MISSING=0 / ANCHOR_OK=60）；
#   尺2 本机读数档必须真在盘上且判据对得上：锁 7 passed、新用例 41 passed、目标文件全绿、
#        四把门 self/scan RC=0、G4 台账当场量 = 23/18/36 且 CAP == len(UNVERIFIED)、
#        本机十三腿 killed=13 / survived=0 / invalid=0（本批五格都不碰 mcp 工具本体，本机就该全杀）；
#   尺3 树 = HEAD：代码面工作树干净、filelist 含本批四件、聚合哈希当场量；
#   尺4 旧三档这一档**必须**跑：本批改了 src/memory_agent/app.py ⇒ run30 那条"可跳过"的前提不成立。
#        这里只核能跑的前提（三档腿数 22/11/14 现读未动 + 被改文件确实只有 app.py 一件），
#        不把"跳过"当默认。
# 期望值全部当场从文件量，不抄上一档的数（这条是 run29 那格 PREFLIGHT_FAILED 换来的）。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c91snap31
TGZ=.qoder/tmp-c91-snap31.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
BAD=0
guard() { if [ "$2" != "$3" ]; then echo "GUARD_FAIL $1 期望=$2 实际=$3"; BAD=1; else echo "GUARD_OK $1=$3"; fi; }
AST() { "$PY" -c "import ast,io,sys;ast.parse(io.open(sys.argv[1],encoding='utf-8').read(),filename=sys.argv[1])" "$1"; }

FOUR="src/memory_agent/app.py scripts/scan_claimed_semantics.py tests/test_vma_phase2_claims_gauge.py tests/test_vma_phase3_claims_direct.py"
HELPERS=".qoder/tmp-c91-mut31.py .qoder/tmp-c81-mut.py .qoder/tmp-c85-mut.py .qoder/tmp-c82-mut.py .qoder/tmp-c91-anchor-check.py .qoder/tmp-c68-hashes.py"

echo HEAD=$(git rev-parse --short HEAD)
CODE_DIFF=$(git status --porcelain --untracked-files=no -- src tests scripts benchmarks attic doc .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh Dockerfile vendor)
guard CODE_CLEAN 0 "$(printf '%s' "$CODE_DIFF" | grep -c . )"
guard HEAD_IS_31 b150000 "$(git rev-parse --short HEAD)"

# ── 尺1：语法 / 引号 / 行尾 / 锚点 ──
bash -n .qoder/tmp-c91-remote31.sh; guard REMOTE_SYNTAX_RC 0 $?
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c91-remote31.sh > .qoder/tmp-c91-lint.out 2>&1; guard LINT_RC 0 $?
guard LINT_UNESCAPED 0 "$(grep -oE 'UNESCAPED=[0-9]+' .qoder/tmp-c91-lint.out | head -1 | cut -d= -f2)"
for f in $FOUR $HELPERS; do
  # 先存 rc：命令替换 $(basename …) 会覆盖 $?，不存就先存再拼名字。
  AST "$f"; rc=$?; guard AST_$(basename $f) 0 "$rc"
  guard CR_$(basename $f) 0 "$(tr -dc '\r' < "$f" | wc -c | tr -d ' ')"
done
for f in $FOUR; do guard TRACKED_$(basename $f) 1 "$(git ls-files -- "$f" | grep -c . )"; done
"$PY" .qoder/tmp-c91-anchor-check.py . > .qoder/tmp-c91-anchor-local.out 2>&1
guard ANCHOR_RC 0 $?
cat .qoder/tmp-c91-anchor-local.out
guard ANCHOR_BAD 0 "$(grep -oE 'ANCHOR_BAD=[0-9]+' .qoder/tmp-c91-anchor-local.out | cut -d= -f2)"
guard ANCHOR_MISSING 0 "$(grep -oE 'ANCHOR_MISSING=[0-9]+' .qoder/tmp-c91-anchor-local.out | cut -d= -f2)"
guard ANCHOR_OK 60 "$(grep -oE 'ANCHOR_OK=[0-9]+' .qoder/tmp-c91-anchor-local.out | cut -d= -f2)"

# ── 尺2：本机读数档必须真在盘上 ──
for g in scan_stub_claims_success scan_source_of_truth_sync scan_cleanup_scheduled scan_claimed_semantics; do
  "$PY" scripts/$g.py --self-test > .qoder/tmp-c91-${g}-self.out 2>&1; guard SELFTEST_$g 0 $?
  guard SELFTEST_MISS_$g 0 "$(grep -cE '^SELFTEST_MISS|^SELFTEST_FALSE' .qoder/tmp-c91-${g}-self.out)"
  "$PY" scripts/$g.py > .qoder/tmp-c91-${g}-scan.out 2>&1; guard SCAN_$g 0 $?
  guard SCAN_PROBLEM_$g 0 "$(grep -oE 'PROBLEM=[0-9]+' .qoder/tmp-c91-${g}-scan.out | head -1 | cut -d= -f2)"
done
"$PY" -c "import importlib.util as u;s=u.spec_from_file_location('g','scripts/scan_claimed_semantics.py');m=u.module_from_spec(s);s.loader.exec_module(m);print(len(m.REGISTRY),len(m.UNVERIFIED),len(m.REGISTRY)+len(m.UNVERIFIED),sum(len(e['cases']) for e in m.REGISTRY.values()))" > .qoder/tmp-c91-g4-ledgers.out 2>&1
# 本机 python 的重定向会写成 CRLF：`read` 会把行尾 CR 一起读进变量（而 grep -o 只取匹配片段所以别的格子没这问题）
read REG_N UNV_N SUM_N CASE_N < <(tr -d '\r' < .qoder/tmp-c91-g4-ledgers.out)
echo LEDGERS REG=$REG_N UNV=$UNV_N SUM=$SUM_N CASES=$CASE_N
guard G4_REGISTERED 23 "$REG_N"
guard G4_UNVERIFIED 18 "$UNV_N"
guard G4_CASES 36 "$CASE_N"
CAP_LIVE=$(grep -oE '^BASELINE_CAP = [0-9]+' tests/test_vma_phase2_claims_gauge.py | awk '{print $3}')
echo CAP_LIVE=$CAP_LIVE
guard G4_CAP_EQUALS_BASELINE 1 "$([ "$UNV_N" = "$CAP_LIVE" ] && echo 1 || echo 0)"
guard G4_DECLARED_EQUALS_LEDGERS 1 "$([ "$SUM_N" = "$(grep -oE 'DECLARED=[0-9]+' .qoder/tmp-c91-scan_claimed_semantics-scan.out | head -1 | cut -d= -f2)" ] && echo 1 || echo 0)"

"$PY" -m pytest tests/test_vma_phase2_claims_gauge.py -q > .qoder/tmp-c91-lock-local.out 2>&1; guard LOCK_LOCAL_RC 0 $?
guard LOCK_LOCAL_PASSED "7 passed" "$(grep -oE '[0-9]+ passed' .qoder/tmp-c91-lock-local.out | tail -1)"
"$PY" -m pytest tests/test_vma_phase3_claims_direct.py -q > .qoder/tmp-c91-ph3-local.out 2>&1; guard PH3_LOCAL_RC 0 $?
guard PH3_LOCAL_PASSED "41 passed" "$(grep -oE '[0-9]+ passed' .qoder/tmp-c91-ph3-local.out | tail -1)"
"$PY" -m pytest tests/test_vma_a8_insights_clock_and_tags.py tests/test_rounds11_19_ma25_35_fixes.py tests/test_wo_ma_012_g1_security.py tests/test_vma_login_rate_limit.py -q -rs > .qoder/tmp-c91-target-local.out 2>&1; guard TARGET_LOCAL_RC 0 $?
tail -1 .qoder/tmp-c91-target-local.out
MUT_ROOT="E:/NAS/memory-agent" MUT_DST="E:/NAS/memory-agent/.qoder/tmp-c91mut31-local" MUT_OUT="E:/NAS/memory-agent/.qoder/tmp-c91-mut31-local.out" MUT_PY="$PY" "$PY" .qoder/tmp-c91-mut31.py > /dev/null 2>&1
guard MUT_LOCAL_RC 0 $?
guard MUT_LOCAL_KILLED "13" "$(grep -oE 'killed=[0-9]+' .qoder/tmp-c91-mut31-local.out | tail -1 | cut -d= -f2)"
guard MUT_LOCAL_SURVIVED "0" "$(grep -oE 'survived=[0-9]+' .qoder/tmp-c91-mut31-local.out | tail -1 | cut -d= -f2)"
guard MUT_LOCAL_INVALID "0" "$(grep -oE 'invalid=[0-9]+' .qoder/tmp-c91-mut31-local.out | tail -1 | cut -d= -f2)"
guard COVERAGE_ARTIFACT 0 "$([ -f .coverage ] && echo 1 || echo 0)"

# ── 尺3：树 = HEAD，本批四件必在清单里，聚合哈希当场量 ──
{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c91-filelist31.txt
echo FILES31=$(wc -l < .qoder/tmp-c91-filelist31.txt)
for f in $FOUR; do guard INLIST_$(basename $f) 1 "$(grep -c "^$f\$" .qoder/tmp-c91-filelist31.txt)"; done
echo BASE_LIVE=$(wc -l < .gates-baseline.txt | tr -d ' ')
"$PY" .qoder/tmp-c68-hashes.py .qoder/tmp-c91-filelist31.txt > .qoder/tmp-c91-hashes-local31.out 2>&1
guard HASH_LOCAL_MISSING 0 "$(grep -oE 'HASH_MISSING=[0-9]+' .qoder/tmp-c91-hashes-local31.out | cut -d= -f2)"
grep -E '^HASH_' .qoder/tmp-c91-hashes-local31.out
for f in $FOUR $HELPERS; do echo "LOCAL_BYTES_$(basename $f)=$(wc -c < "$f" | tr -d ' ')"; done

# ── 尺4：本批动了 src ⇒ 旧三档必须复跑，这里只核"能跑"的前提 ──
SRC_TOUCHED=$(git diff --name-only 8c16b06..HEAD -- src | tr -d '\r')
printf '%s\n' "$SRC_TOUCHED"
guard SRC_TOUCHED_ONE_FILE 1 "$(printf '%s\n' "$SRC_TOUCHED" | grep -c . )"
guard SRC_TOUCHED_NAME src/memory_agent/app.py "$(printf '%s\n' "$SRC_TOUCHED" | head -1)"
guard MUT81_LEGS 22 "$(grep -cE '^[[:space:]]+\(\"M[0-9]+\", ' .qoder/tmp-c81-mut.py)"
guard MUT85_LEGS 11 "$(grep -cE '^[[:space:]]+\(\"Q[0-9]+\", ' .qoder/tmp-c85-mut.py)"
guard MUT82_LEGS 14 "$(grep -cE '^[[:space:]]+\(\"N[0-9]+\", ' .qoder/tmp-c82-mut.py)"
guard MUT31_LEGS 13 "$(grep -cE '^[[:space:]]+\(\"T[0-9]+\", ' .qoder/tmp-c91-mut31.py)"

if [ "$BAD" != "0" ]; then echo GUARDS_FAILED=1 未出网; exit 2; fi
echo GUARDS_ALL_OK=1

tar -czf "$TGZ" -T .qoder/tmp-c91-filelist31.txt; guard TAR_RC 0 $?
echo TAR_SIZE=$(stat -c %s "$TGZ")
"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; guard SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c91-remote31.sh "$NAS:/tmp/ma_c91_remote31.sh"; guard REMOTE_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c91-filelist31.txt "$NAS:/tmp/ma_c91_filelist.txt"; guard FILELIST_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c91-mut31.py "$NAS:/tmp/ma_c91_mut31.py"; guard MUT31_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c81-mut.py "$NAS:/tmp/ma_c91_mut81.py"; guard MUT81_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c85-mut.py "$NAS:/tmp/ma_c91_mut85.py"; guard MUT85_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c82-mut.py "$NAS:/tmp/ma_c91_mut82.py"; guard MUT82_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c91-anchor-check.py "$NAS:/tmp/ma_c91_anchor.py"; guard ANCHOR_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-hashes.py "$NAS:/tmp/ma_c91_hashes.py"; guard HASHES_SCP_RC 0 $?

# 远端本体先在 NAS 侧去 CR 再 bash -n：Windows 写的 CRLF 在这一层最容易漏（run30 实测过形状）
"$SSH" "${OPTS[@]}" "$NAS" "tr -d '\r' < /tmp/ma_c91_remote31.sh > /tmp/ma_c91_remote31_unix.sh && bash -n /tmp/ma_c91_remote31_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?; rm -f /tmp/ma_c91_remote31.sh"
guard NAS_NORMALIZE 0 $?

"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage; echo STAGE_SNAP_RC=\$?"
# 七件 helper 走同一套规范化（去 CR → docker cp → chown 10001），并当场报字节数供逐件对账
"$SSH" "${OPTS[@]}" "$NAS" "for pair in 'ma_c91_filelist.txt:${SNAP}_filelist.txt' 'ma_c91_hashes.py:${SNAP}_hashes.py' 'ma_c91_mut31.py:${SNAP}_mut31.py' 'ma_c91_mut81.py:${SNAP}_mut81.py' 'ma_c91_mut85.py:${SNAP}_mut85.py' 'ma_c91_mut82.py:${SNAP}_mut82.py' 'ma_c91_anchor.py:${SNAP}_anchor.py'; do src=/tmp/\${pair%%:*}; dst=/tmp/\${pair##*:}; tr -d '\r' < \$src > /tmp/norm.tmp && docker cp /tmp/norm.tmp memory-agent:\$dst && docker exec -u root memory-agent chown 10001:10001 \$dst && rm -f /tmp/norm.tmp \$src || echo NORM_FAIL_\$src; done; docker exec memory-agent sh -c 'wc -l < /tmp/${SNAP}_filelist.txt; wc -c /tmp/${SNAP}_hashes.py /tmp/${SNAP}_mut31.py /tmp/${SNAP}_mut81.py /tmp/${SNAP}_mut85.py /tmp/${SNAP}_mut82.py /tmp/${SNAP}_anchor.py'; echo HELPER_PRESENCE_RC=\$?"
SYNC_RC=$?
echo SYNC_RC=$SYNC_RC
if [ "$SYNC_RC" != "0" ]; then echo SYNC_FAILED=1 未出网; exit 2; fi

"$SSH" "${OPTS[@]}" "$NAS" "nohup bash /tmp/ma_c91_remote31_unix.sh $SNAP > /tmp/ma_c91_run31.log 2>&1 < /dev/null & echo CONTAINER_BATCH_LAUNCHED=1; sleep 2; wc -l < /tmp/ma_c91_run31.log"
echo LAUNCH_RC=$?
cat > .qoder/tmp-c91-poll31.sh <<'POLL'
#!/usr/bin/env bash
set -uo pipefail
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
"$SSH" -i "$KEY" -o StrictHostKeyChecking=no lidicn@192.168.2.200 \
  "grep -E '_RC=|_DONE|ANCHOR_|SKIPPED|totals|KILLED_LINES|passed|failed' /tmp/ma_c91_run31.log | tail -45"
POLL
echo POLL_SCRIPT=.qoder/tmp-c91-poll31.sh
echo DRIVER_STAGE_DONE=1
