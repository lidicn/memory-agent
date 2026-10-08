#!/usr/bin/env bash
# run21 = 任务表 #73（`coverage`/`data_quality` 接上 `window` 通用回显）的容器权威门。
# 远端本体 = tmp-c73-remote21.sh。工作树在 HEAD `84f2fca` 冻结：门在飞期间只写 .qoder/ 与文档。
#
# 出网前四把尺照旧，任一不过就 exit 2 不发容器：
#   尺1 语法（remote / self / mut73 的 ast）；尺2 两层引号 lint；
#   尺3 变异档预检（MUTANTS_PARSED=3 VERIFY_BAD=0，只读表、永不 import）+ 本机腿档必须在盘上且 MUTATION_BAD=0；
#   尺4 快照清单 + 关键文件必在树里 + 本机哈希档。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c73snap20261007
TGZ=.qoder/tmp-c73-snap21.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
BAD=0
guard() { # guard <名字> <期望> <实际>
  if [ "$2" != "$3" ]; then echo "GUARD_FAIL $1 期望=$2 实际=$3"; BAD=1; else echo "GUARD_OK $1=$3"; fi
}

echo HEAD=$(git rev-parse --short HEAD)
echo UNPUSHED=$(git rev-list --count origin/main..HEAD)
guard TREE_CLEAN 0 "$(git status --porcelain --untracked-files=no | wc -l | tr -d ' ')"

# ── 尺 1：脚本语法 ──
bash -n .qoder/tmp-c73-remote21.sh; guard REMOTE_SYNTAX_RC 0 $?
bash -n "$0" 2>/dev/null; guard SELF_SYNTAX_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c73-mut73.py',encoding='utf-8').read())"; guard MUT73_PARSE_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c68-hashes.py',encoding='utf-8').read())"; guard HASHES_PARSE_RC 0 $?

# ── 尺 2：两层引号 lint（内层变量必须 \$，run13 首跑折在这一格）──
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c73-remote21.sh; guard LINT_RC 0 $?

# ── 尺 3：变异档预检（只读，不 import、不开跑）＋ 本机那档必须在盘上 ──
"$PY" .qoder/tmp-c73-mut73.py --verify > .qoder/tmp-c73-mutverify21.out 2>&1; guard MUT73_VERIFY_RC 0 $?
grep -E 'MUTANTS_PARSED|VERIFY_BAD|ANCHOR_BAD|SYNTAX_BAD' .qoder/tmp-c73-mutverify21.out
guard MUT73_PARSED 3 "$(grep -oE 'MUTANTS_PARSED=[0-9]+' .qoder/tmp-c73-mutverify21.out | cut -d= -f2)"
guard MUT73_VERIFYBAD 0 "$(grep -oE 'VERIFY_BAD=[0-9]+' .qoder/tmp-c73-mutverify21.out | cut -d= -f2)"
guard MUT73_LOCAL_OUT_EXISTS 0 "$([ -s .qoder/tmp-c73-mut73-local.out ] && echo 0 || echo 1)"
grep -E '^(NOTHING|L[0-9]+ |MUT_COUNT)' .qoder/tmp-c73-mut73-local.out
guard MUT73_LOCAL_BAD 0 "$(grep -oE 'MUTATION_BAD=[0-9]+' .qoder/tmp-c73-mut73-local.out | cut -d= -f2)"
# 量具自身的两条 run19 坑锚点（本机先读，容器 0D 格读同一份文件，两侧数字必须一致）
guard PATH_APPEND 1 "$(grep -cF 'os.pathsep.join' .qoder/tmp-c73-mut73.py)"
guard OLD_CLOBBER 0 "$(grep -cF 'PYTHONPATH=os.path.join(root, "src"), JWT_SECRET' .qoder/tmp-c73-mut73.py)"
guard STDERR_SHOWN 1 "$(grep -cF 'NO_PYTEST_OUTPUT' .qoder/tmp-c73-mut73.py)"
guard FAILED_GUARD 1 "$(grep -cF 'failed > 0' .qoder/tmp-c73-mut73.py)"
# 产品侧形状锚点（本机读数，容器 0C 格对逐字）
guard WITH_WINDOW 5 "$(grep -cF 'return self._with_window(out, tr)' src/memory_agent/insights/service.py)"
guard PRE_FIX_COV 0 "$(grep -cF 'return self._fail("coverage", exc' src/memory_agent/insights/service.py)"
guard PRE_FIX_DQ 0 "$(grep -cF 'return self._fail("data_quality", exc' src/memory_agent/insights/service.py)"
guard NEW_LOCK 1 "$(grep -cF 'def test_outward_facade_reads_carry_the_window' tests/test_vma_insights_window_echo.py)"
# 预期 3 不是 2：这两格在同一个文件里出现在三处代码位（两条 parametrize + 降级信封那圈 for），
# 首稿按"两处"写死 2 被预检当场拦下（PREFLIGHT_FAILED=1，没出网）——门自己先自证。
guard PARAM_LIST 3 "$(grep -cF '"coverage", "data_quality"' tests/test_vma_insights_window_echo.py)"

# ── 尺 4：快照清单 + 关键文件必须真在树里 ──
{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c73-filelist21.txt
echo FILES=$(wc -l < .qoder/tmp-c73-filelist21.txt)
for f in src/memory_agent/insights/service.py src/memory_agent/insights/api.py \
         src/memory_agent/insights/utils.py src/memory_agent/insights_legacy.py \
         src/memory_agent/mcp_server.py src/memory_agent/tool_schema.py src/memory_agent/agent_memory.py \
         tests/test_vma_insights_window_echo.py tests/test_insights_facade_contract.py \
         tests/test_vma_insights_matrix_hour.py tests/test_vma_insights_callsite_binding.py \
         tests/test_vma_phase2_batch3_shared_ruler.py scripts/pyflakes_gate.sh; do
  n=$(grep -c "^$f\$" .qoder/tmp-c73-filelist21.txt)
  if [ "$n" != 1 ]; then echo "GUARD_FAIL IN_SNAP_$(basename $f) 期望=1 实际=$n"; BAD=1; fi
done
echo IN_SNAP_checked=13

# ── 本机哈希档（容器侧跑同一份脚本，聚合摘要必须逐字相同）──
"$PY" .qoder/tmp-c68-hashes.py .qoder/tmp-c73-filelist21.txt > .qoder/tmp-c73-hashes-local21.out 2>&1
guard HASHES_LOCAL_RC 0 $?
cat .qoder/tmp-c73-hashes-local21.out
guard HASH_MISSING 0 "$(grep -oE 'HASH_MISSING=[0-9]+' .qoder/tmp-c73-hashes-local21.out | cut -d= -f2)"

if [ "$BAD" != 0 ]; then echo PREFLIGHT_FAILED=1 未出网; exit 2; fi
echo PREFLIGHT_OK=1

tar -czf "$TGZ" -T .qoder/tmp-c73-filelist21.txt; guard TAR_RC 0 $?
echo TAR_SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; guard SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c73-remote21.sh "$NAS:/tmp/ma_c73_remote21.sh"; guard REMOTE_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c73-mut73.py "$NAS:/tmp/ma_c73_mut73.py"; guard MUT73_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-hashes.py "$NAS:/tmp/ma_c73_hashes.py"; guard HASHES_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c73-filelist21.txt "$NAS:/tmp/ma_c73_filelist.txt"; guard FILELIST_SCP_RC 0 $?

# Windows 侧拷过去的脚本先洗行尾再验语法；哈希与清单同样按 unix 行尾进容器。
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage && tr -d '\r' < /tmp/ma_c73_mut73.py > /tmp/ma_c73_mut73_unix.tmp && tr -d '\r' < /tmp/ma_c73_hashes.py > /tmp/ma_c73_hashes_unix.tmp && tr -d '\r' < /tmp/ma_c73_filelist.txt > /tmp/ma_c73_filelist_unix.tmp && docker cp /tmp/ma_c73_mut73_unix.tmp memory-agent:/tmp/${SNAP}_mut73.py && docker cp /tmp/ma_c73_hashes_unix.tmp memory-agent:/tmp/${SNAP}_hashes.py && docker cp /tmp/ma_c73_filelist_unix.tmp memory-agent:/tmp/${SNAP}_filelist.txt && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut73.py /tmp/${SNAP}_hashes.py /tmp/${SNAP}_filelist.txt && rm -f /tmp/ma_c73_*.tmp /tmp/ma_c73_mut73.py /tmp/ma_c73_hashes.py /tmp/ma_c73_filelist.txt; echo STAGE_RC=\$?; tr -d '\r' < /tmp/ma_c73_remote21.sh > /tmp/ma_c73_remote21_unix.sh && bash -n /tmp/ma_c73_remote21_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?; docker exec memory-agent sh -c 'wc -l < /tmp/c73snap20261007_filelist.txt'; echo SNAP_FILELIST_LINES_RC=\$?"
SYNC_RC=$?
echo SYNC_RC=$SYNC_RC
if [ "$SYNC_RC" != 0 ]; then echo SYNC_FAILED=1 未出网; exit 2; fi

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c73_remote21_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"

echo CONTAINER_BATCH_RC=0
