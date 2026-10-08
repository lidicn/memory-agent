#!/usr/bin/env bash
# run33 本机侧编排：出网前四把尺，任一不过就 exit 2 不发容器。远端本体 = tmp-c95-remote33.sh。
#
#   尺1 语法与形状：远端 bash -n、两层引号 lint、helper 与本批 LF 件的 ast + CR 按字节 = 0、
#        四件本机 CRLF 旧文件只验可解析（CR 判据交给入库 blob）、
#        88 条腿（新 28 + 旧 60）锚点静态现数（ANCHOR_BAD=0 / ANCHOR_MISSING=0 / ANCHOR_OK=88）；
#   尺2 本机读数档必须真在盘上且判据对得上：各格 passed 数 = **当场 --collect-only 量的条数**
#        （不抄上一档、也不写"我以为的数"），五把门 self/scan RC=0 且 PROBLEM=0，
#        G4 台账当场量且 CAP == len(UNVERIFIED)，28 腿**这一趟现跑**（rc 落 .rc）：
#        Q-0 绿 / legs=28 / killed=28 / survived=0 / invalid=0 / WT_UNTOUCHED=True，
#        本机全量回归无 failed（读的是同批现跑的 .out + .rc，缺一份就判红）；
#   尺3 树 = HEAD：代码面工作树干净（本批十四件已提交）、filelist 含本批件、聚合哈希当场量、
#        入库 blob 逐件验"git show 取到非空内容"再验 CR=0（取空会让 CR 假绿）、
#        改旧文件的 + 行数必须 < 全文件行数（整份重写 = 行尾被改写的信号）；
#   尺4 旧四档这一批**不能**跳过：判据当场量 —— `git diff --name-only a890c4c..HEAD -- src` 非空，
#        于是远端把 60 条旧腿全部复跑；同时核四份旧 harness 的腿数未漂移（13/22/11/14）。
#
# 用法：bash .qoder/tmp-c95-driver33.sh <本批代码提交的 short hash>
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c95snap33
TGZ=.qoder/tmp-c95-snap33.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
EXPECT_HEAD=${1:-}
BAD=0
guard() { if [ "$2" != "$3" ]; then echo "GUARD_FAIL $1 期望=$2 实际=$3"; BAD=1; else echo "GUARD_OK $1=$3"; fi; }
AST() { "$PY" -c "import ast,io,sys;ast.parse(io.open(sys.argv[1],encoding='utf-8').read(),filename=sys.argv[1])" "$1"; }
#: 期望条数当场量：--collect-only 的 "N tests collected"，不是记忆里的数
collected() { "$PY" -m pytest "$@" --collect-only -q -p no:cacheprovider 2>/dev/null | grep -oE '^[0-9]+ test' | grep -oE '^[0-9]+'; }
#: 读本机读数档里的 "N passed"（没有则返回空）
passedof() { grep -oE '[0-9]+ passed' "$1" 2>/dev/null | tail -1 | grep -oE '^[0-9]+'; }

CHANGED="src/memory_agent/rule_engine.py src/memory_agent/activity_inference.py src/memory_agent/config.py src/memory_agent/api/config_routes.py src/memory_agent/api/behavior_routes.py src/memory_agent/runtime.py src/memory_agent/static/js/pages/settings.js scripts/scan_stub_claims_success.py scripts/scan_session_owner_parity.py tests/test_vma_dcd_20261008_rulings.py tests/test_drift.py tests/test_process_mining.py tests/test_vma_phase2_claims_gauge.py .gates-baseline.txt"
#: 本机工作树**本来就是** CRLF 的四件（autocrlf=true：入库 blob 是 LF）。
#: 它们的判据不是"工作树 CR=0"（那永远不成立），而是"入库 blob CR=0" + "不是整份重写"。
CRLF_NATIVE="src/memory_agent/activity_inference.py src/memory_agent/static/js/pages/settings.js tests/test_drift.py tests/test_process_mining.py"
#: 工作树必须是 LF 的本批新增/改动件（CR 按字节量 = 0）
LF_NATIVE="src/memory_agent/rule_engine.py src/memory_agent/config.py src/memory_agent/api/config_routes.py src/memory_agent/api/behavior_routes.py src/memory_agent/runtime.py scripts/scan_stub_claims_success.py scripts/scan_session_owner_parity.py tests/test_vma_dcd_20261008_rulings.py tests/test_vma_phase2_claims_gauge.py .gates-baseline.txt"
HELPERS=".qoder/tmp-c95-mut33.py .qoder/tmp-c95-remote33.sh .qoder/tmp-c91-anchor-check.py .qoder/tmp-c68-hashes.py .qoder/tmp-c91-mut31.py .qoder/tmp-c81-mut.py .qoder/tmp-c85-mut.py .qoder/tmp-c82-mut.py"
NEWFILES="scripts/scan_session_owner_parity.py tests/test_vma_dcd_20261008_rulings.py"

echo HEAD=$(git rev-parse --short HEAD)
CODE_DIFF=$(git status --porcelain --untracked-files=no -- src tests scripts benchmarks attic doc .gates-baseline.txt .gates.toml pytest.ini pyproject.toml gates.sh Dockerfile vendor)
guard CODE_CLEAN 0 "$(printf '%s' "$CODE_DIFF" | grep -c . )"
if [ -n "$EXPECT_HEAD" ]; then guard HEAD_IS_33 "$EXPECT_HEAD" "$(git rev-parse --short HEAD)"; fi

# ── 尺1：语法 / 引号 / 行尾 / 锚点 ──
bash -n .qoder/tmp-c95-remote33.sh; guard REMOTE_SYNTAX_RC 0 $?
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c95-remote33.sh > .qoder/tmp-c95-lint.out 2>&1; guard LINT_RC 0 $?
guard LINT_UNESCAPED 0 "$(grep -oE 'UNESCAPED=[0-9]+' .qoder/tmp-c95-lint.out | head -1 | cut -d= -f2)"
for f in $HELPERS $LF_NATIVE; do
  # 先存 rc：命令替换 $(basename …) 会覆盖 $?，不存就先存再拼名字。
  if [ "${f##*.}" = "sh" ]; then
    guard CR_$(basename $f) 0 "$(tr -dc '\r' < "$f" | wc -c | tr -d ' ')"
    continue
  fi
  if [ "${f##*.}" = "js" ] || [ "${f##*.}" = "txt" ]; then
    guard CR_$(basename $f) 0 "$(tr -dc '\r' < "$f" | wc -c | tr -d ' ')"
    continue
  fi
  AST "$f"; rc=$?; guard AST_$(basename $f) 0 "$rc"
  guard CR_$(basename $f) 0 "$(tr -dc '\r' < "$f" | wc -c | tr -d ' ')"
done
# 本机本来就是 CRLF 的四件：只验"3.11/3.13 都能解析"，CR 判据交给尺3 的入库 blob。
for f in $CRLF_NATIVE; do
  if [ "${f##*.}" = "js" ]; then echo "SKIP_AST_$(basename $f)=js"; continue; fi
  AST "$f"; rc=$?; guard AST_$(basename $f) 0 "$rc"
  echo "WTCR_$(basename $f)=$(tr -dc '\r' < "$f" | wc -c | tr -d ' ')"
done

"$PY" .qoder/tmp-c91-anchor-check.py . .qoder/tmp-c95-mut33.py .qoder/tmp-c91-mut31.py .qoder/tmp-c81-mut.py .qoder/tmp-c85-mut.py .qoder/tmp-c82-mut.py > .qoder/tmp-c95-anchor-local.out 2>&1
guard ANCHOR_RC 0 $?
cat .qoder/tmp-c95-anchor-local.out
guard ANCHOR_BAD 0 "$(grep -oE 'ANCHOR_BAD=[0-9]+' .qoder/tmp-c95-anchor-local.out | cut -d= -f2)"
guard ANCHOR_MISSING 0 "$(grep -oE 'ANCHOR_MISSING=[0-9]+' .qoder/tmp-c95-anchor-local.out | cut -d= -f2)"
guard ANCHOR_OK 88 "$(grep -oE 'ANCHOR_OK=[0-9]+' .qoder/tmp-c95-anchor-local.out | cut -d= -f2)"
guard ANCHOR_HARNESS_LINES 5 "$(grep -c '^ANCHOR_HARNESS' .qoder/tmp-c95-anchor-local.out)"

# ── 尺2：本机读数档必须真在盘上，且 passed 数 == 当场 collect 的条数 ──
for g in scan_stub_claims_success scan_source_of_truth_sync scan_cleanup_scheduled scan_claimed_semantics scan_session_owner_parity; do
  "$PY" scripts/$g.py --self-test > .qoder/tmp-c95-${g}-self.out 2>&1; guard SELFTEST_$g 0 $?
  guard SELFTEST_MISS_$g 0 "$(grep -cE '^SELFTEST_MISS|^SELFTEST_FALSE' .qoder/tmp-c95-${g}-self.out)"
  "$PY" scripts/$g.py > .qoder/tmp-c95-${g}-scan.out 2>&1; guard SCAN_$g 0 $?
  guard SCAN_PROBLEM_$g 0 "$(grep -oE 'PROBLEM=[0-9]+' .qoder/tmp-c95-${g}-scan.out | head -1 | cut -d= -f2)"
done
head -1 .qoder/tmp-c95-scan_session_owner_parity-scan.out
guard G1_STUBS0 1 "$(grep -c 'STUBS=0' .qoder/tmp-c95-scan_stub_claims_success-scan.out)"
guard G1_EXEMPT0 1 "$(grep -c 'EXEMPT_TOTAL=0' .qoder/tmp-c95-scan_stub_claims_success-scan.out)"
guard G5_STALE0 1 "$(grep -c 'STALE=0' .qoder/tmp-c95-scan_session_owner_parity-scan.out)"

"$PY" -c "import importlib.util as u;s=u.spec_from_file_location('g','scripts/scan_claimed_semantics.py');m=u.module_from_spec(s);s.loader.exec_module(m);print(len(m.REGISTRY),len(m.UNVERIFIED),len(m.REGISTRY)+len(m.UNVERIFIED),sum(len(e['cases']) for e in m.REGISTRY.values()))" > .qoder/tmp-c95-g4-ledgers.out 2>&1
# 本机 python 的重定向会写成 CRLF：`read` 会把行尾 CR 一起读进变量（grep -o 只取匹配片段所以别的格子没这问题）
read REG_N UNV_N SUM_N CASE_N < <(tr -d '\r' < .qoder/tmp-c95-g4-ledgers.out)
echo LEDGERS REG=$REG_N UNV=$UNV_N SUM=$SUM_N CASES=$CASE_N
CAP_LIVE=$(grep -oE '^BASELINE_CAP = [0-9]+' tests/test_vma_phase2_claims_gauge.py | awk '{print $3}')
echo CAP_LIVE=$CAP_LIVE
guard G4_CAP_EQUALS_BASELINE 1 "$([ "$UNV_N" = "$CAP_LIVE" ] && echo 1 || echo 0)"
guard G4_UNVERIFIED_NO_GROW 1 "$([ "$UNV_N" -le 12 ] && echo 1 || echo 0)"
guard G4_DECLARED_EQUALS_LEDGERS 1 "$([ "$SUM_N" = "$(grep -oE 'DECLARED=[0-9]+' .qoder/tmp-c95-scan_claimed_semantics-scan.out | head -1 | cut -d= -f2)" ] && echo 1 || echo 0)"

for cell in "lock|tests/test_vma_phase2_claims_gauge.py tests/test_quality_gates.py" \
            "rulings|tests/test_vma_dcd_20261008_rulings.py" \
            "acp|tests/test_acp_session_cross_owner_denied.py tests/test_acp_round20_owner_isolation.py" \
            "channel|tests/test_vma_r3_rule_channel.py tests/test_device_event_feed.py tests/test_rule_cooldown.py tests/test_announcer.py" \
            "mining|tests/test_drift.py tests/test_process_mining.py"; do
  name=${cell%%|*}; files=${cell#*|}
  rcprev=$("$PY" -m pytest $files -q -rs -p no:cacheprovider > .qoder/tmp-c95-${name}-local.out 2>&1; echo $?)
  guard CELL_RC_$name 0 "$rcprev"
  want=$(collected $files)
  got=$(passedof .qoder/tmp-c95-${name}-local.out)
  echo "CELL $name collected=$want passed=$got"
  guard CELL_PASSED_$name "$want" "$got"
done

# 28 腿在本机现跑一遍（靶全在本仓可读面 ⇒ 本机就该全杀）；rc 落到 .rc，读数不靠上一档的旧档。
MUT_ROOT="E:/NAS/memory-agent" MUT_DST="E:/NAS/memory-agent/.qoder/tmp-c95mut33-local" \
  MUT_OUT="E:/NAS/memory-agent/.qoder/tmp-c95-mut33-local.out" MUT_PY="$PY" \
  "$PY" .qoder/tmp-c95-mut33.py > /dev/null 2>&1
echo MUT33_LOCAL_RC=$? | tee .qoder/tmp-c95-mut33-local.rc
MUTL=.qoder/tmp-c95-mut33-local.out
guard MUT_LOCAL_RC 0 "$(grep -oE 'MUT33_LOCAL_RC=[0-9]+' .qoder/tmp-c95-mut33-local.rc | cut -d= -f2)"
guard MUT_LOCAL_Q0 1 "$(grep -c '^Q-0 绿' $MUTL)"
guard MUT_LOCAL_LEGS 28 "$(grep -oE 'legs=[0-9]+' $MUTL | tail -1 | cut -d= -f2)"
guard MUT_LOCAL_KILLED 28 "$(grep -oE 'killed=[0-9]+' $MUTL | tail -1 | cut -d= -f2)"
guard MUT_LOCAL_SURVIVED 0 "$(grep -oE 'survived=[0-9]+' $MUTL | tail -1 | cut -d= -f2)"
guard MUT_LOCAL_INVALID 0 "$(grep -oE 'invalid=[0-9]+' $MUTL | tail -1 | cut -d= -f2)"
guard MUT_LOCAL_WT_UNTOUCHED True "$(grep -oE 'WT_UNTOUCHED=[A-Za-z]+' $MUTL | tail -1 | cut -d= -f2)"
# 全量回归由前置步骤现跑并落档（.out + .rc）；这一格读的就是那两份产物，缺一份就判红。
guard SUITE_LOCAL_FILE 1 "$([ -f .qoder/tmp-c95-suite-local2.out ] && [ -f .qoder/tmp-c95-suite-local2.rc ] && echo 1 || echo 0)"
guard SUITE_LOCAL_FAILED 0 "$(grep -oE '[0-9]+ failed' .qoder/tmp-c95-suite-local2.out | tail -1 | grep -oE '^[0-9]+')"
guard SUITE_LOCAL_RC 0 "$(grep -oE 'SUITE_LOCAL_RC=[0-9]+' .qoder/tmp-c95-suite-local2.rc | cut -d= -f2)"
grep -E 'passed|SUITE_LOCAL_RC' .qoder/tmp-c95-suite-local2.out .qoder/tmp-c95-suite-local2.rc | tail -3
guard COVERAGE_ARTIFACT 0 "$([ -f .coverage ] && echo 1 || echo 0)"

# ── 尺3：树 = HEAD，本批十二件已入库，聚合哈希当场量，入库 blob 无 CR ──
{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c95-filelist33.txt
echo FILES33=$(wc -l < .qoder/tmp-c95-filelist33.txt)
for f in $NEWFILES $CHANGED; do guard INLIST_$(basename $f) 1 "$(grep -c "^$f\$" .qoder/tmp-c95-filelist33.txt)"; done
for f in $CHANGED; do guard TRACKED_$(basename $f) 1 "$(git ls-files -- "$f" | grep -c . )"; done
# 入库 blob 逐件验两件事：git show 真取到了内容（取空会让 CR 假绿 0），且 CR 按字节 = 0。
git diff --numstat a890c4c..HEAD -- $CHANGED > .qoder/tmp-c95-numstat.out 2>&1
while read -r add del path; do
  [ -n "${path:-}" ] || continue
  # 本批新建的文件整份都是"+": 那条判据只对**改旧文件**有意义。
  if ! git cat-file -e a890c4c:"$path" 2>/dev/null; then echo "NUMSTAT $path +$add -$del 新建件（不适用整份重写判据）"; continue; fi
  total=$("$PY" -c "import io,sys;print(len(io.open(sys.argv[1],encoding='utf-8',errors='replace').readlines()))" "$path" 2>/dev/null)
  echo "NUMSTAT $path +$add -$del 现全文件=${total} 行"
  guard NOT_WHOLE_REWRITE_$(basename $path) 1 "$([ "$add" -lt "$total" ] && echo 1 || echo 0)"
done < .qoder/tmp-c95-numstat.out
for f in $CHANGED; do
  git show HEAD:"$f" > .qoder/tmp-c95-blob.tmp; brc=$?
  guard BLOB_SHOW_$(basename $f) 0 "$brc"
  guard BLOB_BYTES_$(basename $f) 1 "$([ -s .qoder/tmp-c95-blob.tmp ] && echo 1 || echo 0)"
  guard BLOB_CR_$(basename $f) 0 "$(tr -dc '\r' < .qoder/tmp-c95-blob.tmp | wc -c | tr -d ' ')"
done
rm -f .qoder/tmp-c95-blob.tmp
echo BASE_LIVE=$(wc -l < .gates-baseline.txt | tr -d ' ')
"$PY" .qoder/tmp-c68-hashes.py .qoder/tmp-c95-filelist33.txt > .qoder/tmp-c95-hashes-local33.out 2>&1
guard HASH_LOCAL_MISSING 0 "$(grep -oE 'HASH_MISSING=[0-9]+' .qoder/tmp-c95-hashes-local33.out | cut -d= -f2)"
grep -E '^HASH_' .qoder/tmp-c95-hashes-local33.out
for f in $HELPERS $CHANGED; do echo "LOCAL_BYTES_$(basename $f)=$(wc -c < "$f" | tr -d ' ')"; done

# ── 尺4：旧四档不能跳过 —— 前提（本批动了 src）当场量，并核四份 harness 腿数未漂移 ──
SRC_TOUCHED=$(git diff --name-only a890c4c..HEAD -- src | tr -d '\r')
printf 'SRC_TOUCHED[%s]\n' "$(printf '%s' "$SRC_TOUCHED" | grep -c . )"
guard SRC_TOUCHED_NONZERO 1 "$([ "$(printf '%s' "$SRC_TOUCHED" | grep -c . )" -gt 0 ] && echo 1 || echo 0)"
guard MUT33_LEGS 28 "$(grep -cE '^[[:space:]]+\(\"R[0-9]+\", ' .qoder/tmp-c95-mut33.py)"
guard MUT31_LEGS 13 "$(grep -cE '^[[:space:]]+\(\"T[0-9]+\", ' .qoder/tmp-c91-mut31.py)"
guard MUT81_LEGS 22 "$(grep -cE '^[[:space:]]+\(\"M[0-9]+\", ' .qoder/tmp-c81-mut.py)"
guard MUT85_LEGS 11 "$(grep -cE '^[[:space:]]+\(\"Q[0-9]+\", ' .qoder/tmp-c85-mut.py)"
guard MUT82_LEGS 14 "$(grep -cE '^[[:space:]]+\(\"N[0-9]+\", ' .qoder/tmp-c82-mut.py)"

if [ "$BAD" != "0" ]; then echo GUARDS_FAILED=1 未出网; exit 2; fi
echo GUARDS_ALL_OK=1

tar -czf "$TGZ" -T .qoder/tmp-c95-filelist33.txt; guard TAR_RC 0 $?
echo TAR_SIZE=$(stat -c %s "$TGZ")
"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; guard SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c95-remote33.sh "$NAS:/tmp/ma_c95_remote33.sh"; guard REMOTE_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c95-filelist33.txt "$NAS:/tmp/ma_c95_filelist.txt"; guard FILELIST_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c95-mut33.py "$NAS:/tmp/ma_c95_mut33.py"; guard MUT33_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c91-mut31.py "$NAS:/tmp/ma_c95_mut31.py"; guard MUT31_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c81-mut.py "$NAS:/tmp/ma_c95_mut81.py"; guard MUT81_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c85-mut.py "$NAS:/tmp/ma_c95_mut85.py"; guard MUT85_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c82-mut.py "$NAS:/tmp/ma_c95_mut82.py"; guard MUT82_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c91-anchor-check.py "$NAS:/tmp/ma_c95_anchor.py"; guard ANCHOR_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-hashes.py "$NAS:/tmp/ma_c95_hashes.py"; guard HASHES_SCP_RC 0 $?

# 远端本体先在 NAS 侧去 CR 再 bash -n：Windows 写的 CRLF 在这一层最容易漏（run30 实测过形状）
"$SSH" "${OPTS[@]}" "$NAS" "tr -d '\r' < /tmp/ma_c95_remote33.sh > /tmp/ma_c95_remote33_unix.sh && bash -n /tmp/ma_c95_remote33_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?; rm -f /tmp/ma_c95_remote33.sh"
guard NAS_NORMALIZE 0 $?

"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage; echo STAGE_SNAP_RC=\$?"
# 九件 helper 走同一套规范化（去 CR → docker cp → chown 10001），并当场报字节数供逐件对账
"$SSH" "${OPTS[@]}" "$NAS" "for pair in 'ma_c95_filelist.txt:${SNAP}_filelist.txt' 'ma_c95_hashes.py:${SNAP}_hashes.py' 'ma_c95_mut33.py:${SNAP}_mut33.py' 'ma_c95_mut31.py:${SNAP}_mut31.py' 'ma_c95_mut81.py:${SNAP}_mut81.py' 'ma_c95_mut85.py:${SNAP}_mut85.py' 'ma_c95_mut82.py:${SNAP}_mut82.py' 'ma_c95_anchor.py:${SNAP}_anchor.py'; do src=/tmp/\${pair%%:*}; dst=/tmp/\${pair##*:}; tr -d '\r' < \$src > /tmp/norm.tmp && docker cp /tmp/norm.tmp memory-agent:\$dst && docker exec -u root memory-agent chown 10001:10001 \$dst && rm -f /tmp/norm.tmp \$src || echo NORM_FAIL_\$src; done; docker exec memory-agent sh -c 'wc -l < /tmp/${SNAP}_filelist.txt; wc -c /tmp/${SNAP}_hashes.py /tmp/${SNAP}_mut33.py /tmp/${SNAP}_mut31.py /tmp/${SNAP}_mut81.py /tmp/${SNAP}_mut85.py /tmp/${SNAP}_mut82.py /tmp/${SNAP}_anchor.py'; echo HELPER_PRESENCE_RC=\$?"
SYNC_RC=$?
echo SYNC_RC=$SYNC_RC
if [ "$SYNC_RC" != "0" ]; then echo SYNC_FAILED=1 未出网; exit 2; fi

"$SSH" "${OPTS[@]}" "$NAS" "nohup bash /tmp/ma_c95_remote33_unix.sh $SNAP > /tmp/ma_c95_run33.log 2>&1 < /dev/null & echo CONTAINER_BATCH_LAUNCHED=1; sleep 2; wc -l < /tmp/ma_c95_run33.log"
echo LAUNCH_RC=$?
cat > .qoder/tmp-c95-poll33.sh <<'POLL'
#!/usr/bin/env bash
set -uo pipefail
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
"$SSH" -i "$KEY" -o StrictHostKeyChecking=no lidicn@192.168.2.200 \
  "grep -E '_RC=|_DONE|ANCHOR_|SKIPPED|totals|KILLED_LINES|passed|failed' /tmp/ma_c95_run33.log | tail -50"
POLL
echo POLL_SCRIPT=.qoder/tmp-c95-poll33.sh
echo DRIVER_STAGE_DONE=1
