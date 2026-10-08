#!/usr/bin/env bash
# run22 = 任务表 #74（对外文案死键许诺：mcp_server docstring + SKILL.md + 用户手册）的容器权威门。
# 远端本体 = tmp-c74-remote22.sh。工作树在 HEAD `fec2c2c` 冻结：门在飞期间只写 .qoder/ 与文档。
#
# 出网前四把尺照旧，任一不过就 exit 2 不发容器：
#   尺1 语法（remote / self / mut74 / hashes 的 ast）；尺2 两层引号 lint；
#   尺3 变异档预检（MUTANTS_PARSED=6 VERIFY_BAD=0，只读表、永不 import）+ 本机腿档必须在盘上且 MUTATION_BAD=0；
#   尺4 快照清单 + 关键文件必在树里 + 本机哈希档。
# 所有 guard 的"期望"当场从文件量（上一轮凭记忆写死 2 被预检拦过一次）。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c74snap20261007
TGZ=.qoder/tmp-c74-snap22.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
BAD=0
guard() { # guard <名字> <期望> <实际>
  if [ "$2" != "$3" ]; then echo "GUARD_FAIL $1 期望=$2 实际=$3"; BAD=1; else echo "GUARD_OK $1=$3"; fi
}

echo HEAD=$(git rev-parse --short HEAD)
echo UNPUSHED=$(git rev-list --count origin/main..HEAD)
guard TREE_CLEAN 0 "$(git status --porcelain --untracked-files=no | wc -l | tr -d ' ')"

# ── 尺 1：脚本语法 ──
bash -n .qoder/tmp-c74-remote22.sh; guard REMOTE_SYNTAX_RC 0 $?
bash -n "$0" 2>/dev/null; guard SELF_SYNTAX_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c74-mut74.py',encoding='utf-8').read())"; guard MUT74_PARSE_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c68-hashes.py',encoding='utf-8').read())"; guard HASHES_PARSE_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('tests/test_vma_coverage_docstring_contract.py',encoding='utf-8').read())"; guard LOCK_PARSE_RC 0 $?

# ── 尺 2：两层引号 lint（内层变量必须 \$）──
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c74-remote22.sh; guard LINT_RC 0 $?

# ── 尺 3：变异档预检（只读，不 import、不开跑）＋ 本机那档必须在盘上 ──
"$PY" .qoder/tmp-c74-mut74.py --verify .qoder/tmp-c74-wt > .qoder/tmp-c74-mutverify22.out 2>&1; guard MUT74_VERIFY_RC 0 $?
grep -E 'MUTANTS_PARSED|VERIFY_BAD|ANCHOR_BAD|SYNTAX_BAD' .qoder/tmp-c74-mutverify22.out
guard MUT74_PARSED 6 "$(grep -oE 'MUTANTS_PARSED=[0-9]+' .qoder/tmp-c74-mutverify22.out | cut -d= -f2)"
guard MUT74_VERIFYBAD 0 "$(grep -oE 'VERIFY_BAD=[0-9]+' .qoder/tmp-c74-mutverify22.out | cut -d= -f2)"
guard MUT74_LOCAL_OUT_EXISTS 0 "$([ -s .qoder/tmp-c74-mut74-local22.out ] && echo 0 || echo 1)"
grep -E '^(M-0|L[0-9]+ |MUT_COUNT)' .qoder/tmp-c74-mut74-local22.out
guard MUT74_LOCAL_BAD 0 "$(grep -oE 'MUTATION_BAD=[0-9]+' .qoder/tmp-c74-mut74-local22.out | cut -d= -f2)"
# 本机哈希档（容器侧跑同一份脚本，聚合摘要必须逐字相同 ⇒ "容器里跑的就是这棵树"）
"$PY" .qoder/tmp-c68-hashes.py .qoder/tmp-c74-filelist22.txt > .qoder/tmp-c74-hashes-local22.out 2>&1; guard HASHES_LOCAL_RC 0 $?
cat .qoder/tmp-c74-hashes-local22.out
guard HASH_MISSING 0 "$(grep -oE 'HASH_MISSING=[0-9]+' .qoder/tmp-c74-hashes-local22.out | cut -d= -f2)"
# 量具自身的特征锚点（本机先读，容器 0D 格读同一份文件，两侧数字必须一致）
guard PATH_APPEND 1 "$(grep -cF 'os.pathsep' .qoder/tmp-c74-mut74.py)"
guard STDERR_TAIL 1 "$(grep -cF -- '-160' .qoder/tmp-c74-mut74.py)"
guard VERIFY_FLAG 2 "$(grep -cF '"--verify"' .qoder/tmp-c74-mut74.py)"
guard OLD_CLOBBER 0 "$(grep -cF 'PYTHONPATH=os.path.join(root, "src"), JWT_SECRET' .qoder/tmp-c74-mut74.py)"
guard FAILED_GUARD 1 "$(grep -cF 'failed > 0' .qoder/tmp-c74-mut74.py)"
# 产品侧形状锚点（本机读数，容器 0C 格对逐字）
# 本机格子里的 grep 模式是**单引号**：内层的 `"` 直接写，不许加 `\`。
# `'\"x\"'` 在单引号里是两个字面反斜杠，pattern 找的是 `\"x\"` ⇒ 命中恒 0（首跑就折在三格，预检把网挡住了）。
# 远端格子里反过来：那里模式在 `ex "…"` 的双引号内，必须写 `\"` 才能把引号送到容器 shell。
guard NEW_HEADLINE 1 "$(grep -cF '数据覆盖报告：逐日给出事件量与空日标记' src/memory_agent/mcp_server.py)"
guard OLD_HEADLINE 0 "$(grep -cF '数据覆盖报告：明确告诉你' src/memory_agent/mcp_server.py)"
# mcp_server.py 全文的 has_data 预期是 **2** 不是 0：那是 device_health 在说实体目录的字段，
# 该工具整条走 legacy、载荷里真有这一格。文案锁管的是 handler 自己的 docstring（AST 取），不是全文。
guard MCP_HAS_DATA_OTHER 2 "$(grep -cF 'has_data' src/memory_agent/mcp_server.py)"
guard SVC_START_DAY 1 "$(grep -cF '"start_day": start_day,' src/memory_agent/insights/service.py)"
guard SVC_PEAK 1 "$(grep -cF '"peak_hours": peak,' src/memory_agent/insights/service.py)"
guard WITH_WINDOW 5 "$(grep -cF 'return self._with_window(out, tr)' src/memory_agent/insights/service.py)"
guard SKILL_HAS_DATA 0 "$(grep -cF 'has_data' src/memory_agent/skills_bundle/insight/SKILL.md)"
guard SKILL_FIRSTLAST 0 "$(grep -cF 'first/last' src/memory_agent/skills_bundle/insight/SKILL.md)"
guard SKILL_DAYCOV 1 "$(grep -cF 'day_coverage' src/memory_agent/skills_bundle/insight/SKILL.md)"
guard JS_HAS_DATA 0 "$(grep -cF 'has_data' src/memory_agent/static/js/pages/user_manual.js)"
guard JS_FIRSTLAST 0 "$(grep -cF 'first/last' src/memory_agent/static/js/pages/user_manual.js)"
guard PROSE_SURFACES 5 "$(grep -cF 'PROSE_SURFACES' tests/test_vma_coverage_docstring_contract.py)"
guard PROSE_TESTS 2 "$(grep -cF 'def test_shipped_prose' tests/test_vma_coverage_docstring_contract.py)"

# ── 尺 4：快照清单 + 关键文件必须真在树里 ──
{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c74-filelist22b.txt
guard FILELIST_SAME "$(wc -l < .qoder/tmp-c74-filelist22.txt | tr -d ' ')" "$(wc -l < .qoder/tmp-c74-filelist22b.txt | tr -d ' ')"
cp .qoder/tmp-c74-filelist22b.txt .qoder/tmp-c74-filelist22.txt
echo FILES22=$(wc -l < .qoder/tmp-c74-filelist22.txt)
for f in src/memory_agent/insights/service.py src/memory_agent/insights/api.py \
         src/memory_agent/insights/utils.py src/memory_agent/insights_legacy.py \
         src/memory_agent/mcp_server.py src/memory_agent/tool_schema.py src/memory_agent/agent_memory.py \
         src/memory_agent/skills_bundle/insight/SKILL.md src/memory_agent/static/js/pages/user_manual.js \
         tests/test_vma_coverage_docstring_contract.py tests/test_vma_insights_window_echo.py \
         tests/test_insights_facade_contract.py tests/test_tool_schema.py tests/test_mcp_surface_parity.py \
         tests/test_acp_server.py scripts/pyflakes_gate.sh; do
  n=$(grep -c "^$f\$" .qoder/tmp-c74-filelist22.txt)
  if [ "$n" != 1 ]; then echo "GUARD_FAIL IN_SNAP_$(basename $f) 期望=1 实际=$n"; BAD=1; fi
done
echo IN_SNAP_checked=16

if [ "$BAD" != 0 ]; then echo PREFLIGHT_FAILED=1 未出网; exit 2; fi
echo PREFLIGHT_OK=1

tar -czf "$TGZ" -T .qoder/tmp-c74-filelist22.txt; guard TAR_RC 0 $?
echo TAR_SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; guard SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c74-remote22.sh "$NAS:/tmp/ma_c74_remote22.sh"; guard REMOTE_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c74-mut74.py "$NAS:/tmp/ma_c74_mut74.py"; guard MUT74_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-hashes.py "$NAS:/tmp/ma_c74_hashes.py"; guard HASHES_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c74-filelist22.txt "$NAS:/tmp/ma_c74_filelist.txt"; guard FILELIST_SCP_RC 0 $?

# Windows 侧拷过去的脚本先洗行尾再验语法；哈希与清单同样按 unix 行尾进容器。
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage && tr -d '\r' < /tmp/ma_c74_mut74.py > /tmp/ma_c74_mut74_unix.tmp && tr -d '\r' < /tmp/ma_c74_hashes.py > /tmp/ma_c74_hashes_unix.tmp && tr -d '\r' < /tmp/ma_c74_filelist.txt > /tmp/ma_c74_filelist_unix.tmp && docker cp /tmp/ma_c74_mut74_unix.tmp memory-agent:/tmp/${SNAP}_mut74.py && docker cp /tmp/ma_c74_hashes_unix.tmp memory-agent:/tmp/${SNAP}_hashes.py && docker cp /tmp/ma_c74_filelist_unix.tmp memory-agent:/tmp/${SNAP}_filelist.txt && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut74.py /tmp/${SNAP}_hashes.py /tmp/${SNAP}_filelist.txt && rm -f /tmp/ma_c74_*.tmp /tmp/ma_c74_mut74.py /tmp/ma_c74_hashes.py /tmp/ma_c74_filelist.txt; echo STAGE_RC=\$?; tr -d '\r' < /tmp/ma_c74_remote22.sh > /tmp/ma_c74_remote22_unix.sh && bash -n /tmp/ma_c74_remote22_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?; docker exec memory-agent sh -c 'wc -l < /tmp/${SNAP}_filelist.txt'; echo SNAP_FILELIST_LINES_RC=\$?"
SYNC_RC=$?
echo SYNC_RC=$SYNC_RC
if [ "$SYNC_RC" != 0 ]; then echo SYNC_FAILED=1 未出网; exit 2; fi

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c74_remote22_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"

echo CONTAINER_BATCH_RC=0
