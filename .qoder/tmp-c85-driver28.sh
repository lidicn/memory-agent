#!/usr/bin/env bash
# run28 = 任务表 #85（2期 第十一~十九轮 MA-25/26/27/28/30/31/33/35 八条落码）的容器权威门。
#         远端本体 = tmp-c85-remote28.sh。工作树在 HEAD `800c17b` 冻结：门在飞期间只写 .qoder/ 与文档。
#
# 出网前四把尺，任一不过就 exit 2 不发容器：
#   尺1 语法（remote / 三份量具 + 哈希脚本的 ast / 本轮八个落点 + 新测试 + 共享尺子的 ast）；
#   尺2 两层引号 lint；
#   尺3 本机读数档必须在盘上且判定对得上：全量本机档 0 failed、11 腿变异档（Q-0 绿 + killed=10
#       survived=1 invalid=0 WT_UNTOUCHED=True，唯一存活的 Q02 是容器专属锁）、对锚预检 53/53、
#       引用行号现读门 BAD=0、本机哈希档；
#   尺4 快照清单 + 关键文件必在树里 + r20 那格跳过的不交集证据（现读 git diff，不写上轮结论）。
# 期望值全部当场从文件量，不抄上一档的数。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c85snap28
TGZ=.qoder/tmp-c85-snap28.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
BAD=0
guard() { if [ "$2" != "$3" ]; then echo "GUARD_FAIL $1 期望=$2 实际=$3"; BAD=1; else echo "GUARD_OK $1=$3"; fi; }

echo HEAD=$(git rev-parse --short HEAD)
CODE_DIFF=$(git status --porcelain --untracked-files=no -- \
  src tests scripts benchmarks .gates-baseline.txt .gates.toml .gates pytest.ini \
  pyproject.toml gates.sh Dockerfile vendor)
guard CODE_CLEAN 0 "$(printf '%s' "$CODE_DIFF" | grep -c . )"
echo "DOC_DIRTY=$(git status --porcelain --untracked-files=no -- doc | wc -l | tr -d ' ')"
guard HEAD_IS_85 800c17b "$(git rev-parse --short HEAD)"
guard BASE_LINES 174 "$(wc -l < .gates-baseline.txt | tr -d ' ')"
guard BASE_STALE_TEACH 0 "$(grep -cF 'mcp_server.py#fake-ok-const#_build_server.teach_signal' .gates-baseline.txt)"

# ── 尺 1：语法 ──
bash -n .qoder/tmp-c85-remote28.sh; guard REMOTE_SYNTAX_RC 0 $?
for h in tmp-c85-mut tmp-c81-mut tmp-c82-mut tmp-r20-mut tmp-c68-hashes tmp-c85-anchorcheck tmp-c85-linecheck; do
  "$PY" -c "import ast,io,sys;ast.parse(io.open(sys.argv[1],encoding='utf-8').read())" ".qoder/$h.py"; guard AST_$(basename $h) 0 $?
done
for f in src/memory_agent/llm_client.py src/memory_agent/mcp_server.py src/memory_agent/agent_memory.py \
         src/memory_agent/signal_learning.py src/memory_agent/api/nr_routes.py src/memory_agent/runtime.py \
         src/memory_agent/config.py src/memory_agent/store.py \
         tests/test_rounds11_19_ma25_35_fixes.py tests/test_vma_phase2_batch3_shared_ruler.py; do
  "$PY" -c "import ast,io,sys;ast.parse(io.open(sys.argv[1],encoding='utf-8').read())" "$f"; guard AST_$(basename $f) 0 $?
done

# ── 尺 2：两层引号 ──
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c85-remote28.sh; guard LINT_RC 0 $?

# ── 尺 3：本机读数档 ──
{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c85-filelist28.txt
echo FILES28=$(wc -l < .qoder/tmp-c85-filelist28.txt)

guard SUITE_OUT 0 "$([ -s .qoder/tmp-c85-suite-local.out ] && echo 0 || echo 1)"
tail -2 .qoder/tmp-c85-suite-local.out
guard LOCAL_SUITE_RC 0 "$(grep -oE 'LOCAL_SUITE_RC=[0-9]+' .qoder/tmp-c85-suite-local.out | cut -d= -f2)"
guard LOCAL_SUITE_FAILED 0 "$(grep -cE '^FAILED |[0-9]+ failed' .qoder/tmp-c85-suite-local.out)"
guard LOCAL_SUITE_PASSED "2023 passed" "$(grep -oE '[0-9]+ passed' .qoder/tmp-c85-suite-local.out | tail -1)"

guard M85_OUT 0 "$([ -s .qoder/tmp-c85-mut-local2.out ] && echo 0 || echo 1)"
guard M85_KILLED 10 "$(grep -c 'KILLED' .qoder/tmp-c85-mut-local2.out)"
guard M85_SURVIVED 1 "$(grep -c 'SURVIVED' .qoder/tmp-c85-mut-local2.out)"
guard M85_INVALID 0 "$(grep -c 'INVALID' .qoder/tmp-c85-mut-local2.out)"
guard M85_WT True "$(grep -oE 'WT_UNTOUCHED=[A-Za-z]+' .qoder/tmp-c85-mut-local2.out | cut -d= -f2)"
guard M85_TOTALS "totals: killed=10 survived=1 invalid=0 legs=11" "$(grep -E '^totals' .qoder/tmp-c85-mut-local2.out)"
guard M85_LEGS 11 "$(grep -cE '^[[:space:]]+\("Q[0-9]+", ' .qoder/tmp-c85-mut.py)"
guard M81_LEGS 22 "$(grep -cE '^[[:space:]]+\("M[0-9]+", ' .qoder/tmp-c81-mut.py)"
guard M82_LEGS 14 "$(grep -cE '^[[:space:]]+\("N[0-9]+", ' .qoder/tmp-c82-mut.py)"
grep -E '^\[Q-0\]|^\[Q02\]' .qoder/tmp-c85-mut-local2.out
# Q02 存活必须与"本机那条锁被 skip"这一件事对上，而不是含糊带过
guard M85_Q02_TARGET mcp_server.py "$(grep -E '^\[Q02\]' .qoder/tmp-c85-mut-local2.out | grep -oE 'mcp_server\.py' | head -1)"
"$PY" -m pytest tests/test_rounds11_19_ma25_35_fixes.py -q -rs --tb=no -p no:cacheprovider > .qoder/tmp-c85-lockskip.out 2>&1
guard LOCK_SKIP_RC 0 $?
guard LOCK_SKIP_N 1 "$(grep -cE '^SKIPPED \[1\]' .qoder/tmp-c85-lockskip.out)"
grep -E '^SKIPPED|passed|failed' .qoder/tmp-c85-lockskip.out | tail -2

guard ANCHORCHECK 0 "$([ -s .qoder/tmp-c85-anchorcheck.out ] && echo 0 || echo 1)"
guard ANCHOR_BAD 0 "$(grep -oE 'ANCHOR_PRECHECK_BAD=[0-9]+' .qoder/tmp-c85-anchorcheck.out | cut -d= -f2)"
guard ANCHOR_OK_N 53 "$(grep -c '^OK' .qoder/tmp-c85-anchorcheck.out)"
guard LINECHECK_BAD 0 "$(grep -oE 'LINECHECK_BAD=[0-9]+' .qoder/tmp-c85-linecheck.out | cut -d= -f2)"
guard LINECHECK_OK_N 45 "$(grep -c '^OK' .qoder/tmp-c85-linecheck.out)"

"$PY" .qoder/tmp-c68-hashes.py .qoder/tmp-c85-filelist28.txt > .qoder/tmp-c85-hashes-local28.out 2>&1
guard HASHES_LOCAL_RC 0 $?
cat .qoder/tmp-c85-hashes-local28.out
guard HASH_MISSING 0 "$(grep -oE 'HASH_MISSING=[0-9]+' .qoder/tmp-c85-hashes-local28.out | cut -d= -f2)"

# 本机锚点读数（容器 0C 格读同一棵树，两侧数字必须逐字一致）
echo ANCHOR_LOCAL LLM_STALE_LOOP=$(grep -cF 'for p in stale:' src/memory_agent/llm_client.py) \
 LLM_KEEP_STALE=$(grep -cF 'stale = self.providers' src/memory_agent/llm_client.py) \
 MCP_PREV_MAX=$(grep -cF 'prev_version = max(prev_version,' src/memory_agent/mcp_server.py) \
 MCP_FAKE_RETURN=$(grep -cF '参数校验通过' src/memory_agent/mcp_server.py) \
 SIG_ENUM_GATE=$(grep -cF 'if exclusion_type not in EXCLUSION_TYPES:' src/memory_agent/signal_learning.py) \
 AGENT_SCOPE_ECHO=$(grep -cF 'member_scope' src/memory_agent/agent_memory.py) \
 NR_NOTIMPL=$(grep -cF '未实现：本服务不执行动作' src/memory_agent/api/nr_routes.py) \
 RT_RETENTION_SHORT=$(grep -cF 'await asyncio.sleep(min(interval, 600))' src/memory_agent/runtime.py) \
 CFG_RETENTION_KEY=$(grep -cF 'data_retention_interval_seconds: int = 86400' src/memory_agent/config.py)
guard L85_DEFS 25 "$(grep -c 'def test_' tests/test_rounds11_19_ma25_35_fixes.py)"
# 25 个用例函数里有一条 4 元参数化 ⇒ 收集到 28 条；两个口径分开报，别把函数数当用例数
"$PY" -m pytest tests/test_rounds11_19_ma25_35_fixes.py -q --collect-only -p no:cacheprovider \
  > .qoder/tmp-c85-collect.out 2>&1
guard L85_COLLECTED "28 tests collected" "$(grep -oE '[0-9]+ tests collected' .qoder/tmp-c85-collect.out | head -1)"

# CR 一律按字节量；`nr_routes.py` 的工作副本按 core.autocrlf 落成 CRLF ⇒ 红线判据取入库 blob
for f in src/memory_agent/llm_client.py src/memory_agent/mcp_server.py src/memory_agent/agent_memory.py \
         src/memory_agent/signal_learning.py src/memory_agent/runtime.py src/memory_agent/config.py \
         src/memory_agent/store.py tests/test_rounds11_19_ma25_35_fixes.py \
         .gates-baseline.txt .qoder/tmp-c85-mut.py .qoder/tmp-c85-remote28.sh; do
  guard CR_$(basename $f) 0 "$(tr -dc '\r' < $f | wc -c | tr -d ' ')"
done
guard CRBLOB_nr_routes 0 "$(git show HEAD:src/memory_agent/api/nr_routes.py | tr -dc '\r' | wc -c | tr -d ' ')"
guard CRLF_WORKCOPY_nr_routes 218 "$(tr -dc '\r' < src/memory_agent/api/nr_routes.py | wc -c | tr -d ' ')"

# ── 尺 4：快照清单 + r20 跳过那格的不交集证据（现读）──
for f in src/memory_agent/llm_client.py src/memory_agent/mcp_server.py src/memory_agent/agent_memory.py \
         src/memory_agent/signal_learning.py src/memory_agent/api/nr_routes.py src/memory_agent/runtime.py \
         src/memory_agent/config.py src/memory_agent/store.py tests/test_rounds11_19_ma25_35_fixes.py \
         tests/test_vma_phase2_batch3_shared_ruler.py tests/test_quality_gates.py \
         tests/test_signal_learning.py tests/test_agent_memory.py tests/test_acp_round20_owner_isolation.py \
         scripts/pyflakes_gate.sh .gates-baseline.txt; do
  n=$(grep -c "^$f\$" .qoder/tmp-c85-filelist28.txt)
  if [ "$n" != 1 ]; then echo "GUARD_FAIL IN_SNAP_$(basename $f) 期望=1 实际=$n"; BAD=1; fi
done
echo IN_SNAP_checked=16
TOUCHED28=$(git diff --name-only 800c17b~1 800c17b)
guard R20_DISJOINT 0 "$(printf '%s\n' "$TOUCHED28" | grep -c 'acp_server')"
guard MR20_LEGS 6 "$(grep -cE '^[[:space:]]+\("P[0-9]+", ' .qoder/tmp-r20-mut.py)"
printf '%s\n' "$TOUCHED28" | sed 's/^/TOUCHED28=/'

if [ "$BAD" != 0 ]; then echo PREFLIGHT_FAILED=1 未出网; exit 2; fi
echo PREFLIGHT_OK=1

tar -czf "$TGZ" -T .qoder/tmp-c85-filelist28.txt; guard TAR_RC 0 $?
echo TAR_SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; guard SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c85-remote28.sh "$NAS:/tmp/ma_c85_remote28.sh"; guard REMOTE_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c85-mut.py "$NAS:/tmp/ma_c85_mut85.py"; guard M85_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c81-mut.py "$NAS:/tmp/ma_c85_mut81.py"; guard M81_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c82-mut.py "$NAS:/tmp/ma_c85_mut82.py"; guard M82_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-hashes.py "$NAS:/tmp/ma_c85_hashes.py"; guard HASHES_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c85-filelist28.txt "$NAS:/tmp/ma_c85_filelist.txt"; guard FILELIST_SCP_RC 0 $?

# Windows 侧拷过去的脚本先洗行尾再验语法（快照树里的 py 不动，行尾与被测对象一致）
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage; echo STAGE_SNAP_RC=\$?"
"$SSH" "${OPTS[@]}" "$NAS" "tr -d '\r' < /tmp/ma_c85_filelist.txt > /tmp/ma_c85_filelist_unix.tmp && docker cp /tmp/ma_c85_filelist_unix.tmp memory-agent:/tmp/${SNAP}_filelist.txt && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_filelist.txt && rm -f /tmp/ma_c85_filelist_unix.tmp /tmp/ma_c85_filelist.txt; echo STAGE_FILELIST_RC=\$?; for s in mut85 mut81 mut82 hashes; do tr -d '\r' < /tmp/ma_c85_\$s.py > /tmp/ma_c85_\${s}_unix.tmp && docker cp /tmp/ma_c85_\${s}_unix.tmp memory-agent:/tmp/${SNAP}_\${s}.py && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_\${s}.py && rm -f /tmp/ma_c85_\${s}_unix.tmp /tmp/ma_c85_\$s.py; echo NORM_\${s}_RC=\$?; done; tr -d '\r' < /tmp/ma_c85_remote28.sh > /tmp/ma_c85_remote28_unix.sh && bash -n /tmp/ma_c85_remote28_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?; docker exec memory-agent sh -c 'wc -l < /tmp/${SNAP}_filelist.txt'; echo SNAP_FILELIST_LINES_RC=\$?"
SYNC_RC=$?
echo SYNC_RC=$SYNC_RC
if [ "$SYNC_RC" != 0 ]; then echo SYNC_FAILED=1 未出网; exit 2; fi

# 远端整跑要 20 分钟量级（容器全量回归单格就 ≈9 分钟，再加 47 条变异腿），
# 所以这一格用 nohup 脱开 ssh 的生命周期：本机 ssh 断了不会把容器里的门一起带走，读数从 NAS 侧日志取。
"$SSH" "${OPTS[@]}" "$NAS" "nohup bash /tmp/ma_c85_remote28_unix.sh $SNAP > /tmp/ma_c85_run28.log 2>&1 < /dev/null & echo REMOTE_LAUNCHED_PID=\$!; sleep 2; head -3 /tmp/ma_c85_run28.log"

echo CONTAINER_BATCH_LAUNCHED=1
echo POLL_CMD="\"\$SSH\" \"\${OPTS[@]}\" \"\$NAS\" \"tail -40 /tmp/ma_c85_run28.log\""
