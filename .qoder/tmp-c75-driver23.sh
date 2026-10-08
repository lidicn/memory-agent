#!/usr/bin/env bash
# run23 = 任务表 #75（对外文案承诺键全仓扫描 + 三条活口 + 气候温度链补锁）的容器权威门。
# 远端本体 = tmp-c75-remote23.sh。工作树在 HEAD `6e2d2e9` 冻结：门在飞期间只写 .qoder/ 与文档。
#
# 出网前四把尺照旧，任一不过就 exit 2 不发容器：
#   尺1 语法（remote / self / mut75 / hashes / 新锁的 ast）；尺2 两层引号 lint；
#   尺3 变异档预检（VERIFY_LEGS=8 VERIFY_BAD=0，只读表、永不 import）+ 本机腿档必须在盘上且 MUTATION_BAD=0；
#   尺4 快照清单 + 关键文件必在树里 + 本机哈希档。
# 所有 guard 的"期望"当场从文件量（本轮实测列在下面），不写上轮的数。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c75snap20261007
TGZ=.qoder/tmp-c75-snap23.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
BAD=0
guard() { # guard <名字> <期望> <实际>
  if [ "$2" != "$3" ]; then echo "GUARD_FAIL $1 期望=$2 实际=$3"; BAD=1; else echo "GUARD_OK $1=$3"; fi
}

echo HEAD=$(git rev-parse --short HEAD)
echo UNPUSHED=$(git rev-list --count origin/main..HEAD)
guard TREE_CLEAN 0 "$(git status --porcelain --untracked-files=no | wc -l | tr -d ' ')"

# ── 尺 1：脚本语法 ──
bash -n .qoder/tmp-c75-remote23.sh; guard REMOTE_SYNTAX_RC 0 $?
bash -n "$0" 2>/dev/null; guard SELF_SYNTAX_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c75-mut75.py',encoding='utf-8').read())"; guard MUT75_PARSE_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c75-docscan.py',encoding='utf-8').read())"; guard DOCSCAN_PARSE_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c68-hashes.py',encoding='utf-8').read())"; guard HASHES_PARSE_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('tests/test_vma_promised_keys_runtime_contract.py',encoding='utf-8').read())"; guard LOCK_PARSE_RC 0 $?

# ── 尺 2：两层引号 lint（内层变量必须 \$）──
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c75-remote23.sh; guard LINT_RC 0 $?

# ── 尺 3：变异档预检（只读，不 import、不开跑）＋ 本机那档必须在盘上 ──
# 快照清单先生成：下面本机哈希档要读它（run22 的教训，顺序别再用"上轮能跑"改回去）。
{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c75-filelist23.txt
echo FILES23=$(wc -l < .qoder/tmp-c75-filelist23.txt)
"$PY" .qoder/tmp-c75-mut75.py --verify > .qoder/tmp-c75-mutverify23.out 2>&1; guard MUT75_VERIFY_RC 0 $?
grep -E 'VERIFY_LEGS|VERIFY_BAD' .qoder/tmp-c75-mutverify23.out
guard MUT75_VERIFY_LEGS 8 "$(grep -oE 'VERIFY_LEGS=[0-9]+' .qoder/tmp-c75-mutverify23.out | cut -d= -f2)"
guard MUT75_VERIFY_BAD 0 "$(grep -oE 'VERIFY_BAD=[0-9]+' .qoder/tmp-c75-mutverify23.out | cut -d= -f2)"
guard MUT75_LOCAL_OUT_EXISTS 0 "$([ -s .qoder/tmp-c75-mut75-local8b.out ] && echo 0 || echo 1)"
grep -E '^(M-0|L[0-9]+ |MUT75_COUNT|SRC_UNCHANGED)' .qoder/tmp-c75-mut75-local8b.out
guard MUT75_LOCAL_BAD 0 "$(grep -oE 'MUTATION_BAD=[0-9]+' .qoder/tmp-c75-mut75-local8b.out | cut -d= -f2)"
guard MUT75_LOCAL_LEGS 8 "$(grep -oE 'MUT75_COUNT=[0-9]+' .qoder/tmp-c75-mut75-local8b.out | cut -d= -f2)"
guard MUT75_LOCAL_UNCHANGED True "$(grep -oE 'SRC_UNCHANGED=[A-Za-z]+' .qoder/tmp-c75-mut75-local8b.out | cut -d= -f2)"
# 扫描量具与夹具的留档也必须在盘上（本轮登记的读数全部出自这两份）
guard DOCSCAN_OUT_EXISTS 0 "$([ -s .qoder/tmp-c75-docscan.out ] && echo 0 || echo 1)"
guard CLIMATE_OUT_EXISTS 0 "$([ -s .qoder/tmp-c75-climate.out ] && echo 0 || echo 1)"
guard CTL_OUT_EXISTS 0 "$([ -s .qoder/tmp-c75-ctl.out ] && echo 0 || echo 1)"
# 本机哈希档（容器侧跑同一份脚本，聚合摘要必须逐字相同 ⇒ "容器里跑的就是这棵树"）
"$PY" .qoder/tmp-c68-hashes.py .qoder/tmp-c75-filelist23.txt > .qoder/tmp-c75-hashes-local23.out 2>&1; guard HASHES_LOCAL_RC 0 $?
cat .qoder/tmp-c75-hashes-local23.out
guard HASH_MISSING 0 "$(grep -oE 'HASH_MISSING=[0-9]+' .qoder/tmp-c75-hashes-local23.out | cut -d= -f2)"

# 量具自身的特征锚点（本机先读，容器 0D 格读同一份文件，两侧数字必须一致）
# 本机 guard 的 grep 模式在**单引号**里：内层的 `"` 直接写，不许加 `\`（§四十八 自捉 1）。
guard PATH_APPEND 1 "$(grep -cF 'os.pathsep' .qoder/tmp-c75-mut75.py)"
guard VERIFY_FLAG 2 "$(grep -cF '"--verify"' .qoder/tmp-c75-mut75.py)"
guard AST_GUARD 1 "$(grep -cF 'ast.parse(patched' .qoder/tmp-c75-mut75.py)"
guard L_LEGS 7 "$(grep -cF '("L' .qoder/tmp-c75-mut75.py)"
guard FAILED_GUARD 1 "$(grep -cF 'failed > 0' .qoder/tmp-c75-mut75.py)"
guard SRC_UNCHANGED_FLAG 1 "$(grep -cF 'SRC_UNCHANGED=' .qoder/tmp-c75-mut75.py)"

# 产品侧形状锚点（本机读数，容器 0C 格对逐字）
guard QC_NEW_HEADLINE 1 "$(grep -cF '聚合数据质量：逐项' src/memory_agent/mcp_server.py)"
# mcp_server.py 全文的 data_quality_issues 预期是 **2** 不是 0：`:519` 死 TOOL_CATALOG 字面量 +
# `:1604` device_health 的文案（该工具整条走 legacy、载荷里真有这一格）。同一口径见 §四十八。
guard MCP_DATA_QUALITY_ISSUES_OTHER 2 "$(grep -cF 'data_quality_issues' src/memory_agent/mcp_server.py)"
guard MCP_SEMANTIC_HINTS 0 "$(grep -cF 'semantic_hints' src/memory_agent/mcp_server.py)"
guard MCP_AGENT_MEMORY_HINTS 0 "$(grep -cF 'agent_memory_hints' src/memory_agent/mcp_server.py)"
guard MCP_RECOMMENDED_TOOL 0 "$(grep -cF 'recommended_tool' src/memory_agent/mcp_server.py)"
guard MCP_HINTS_BACKTICK 1 "$(grep -cF '另给顶层键 ' src/memory_agent/mcp_server.py)"
guard SPEC_SEMANTIC_HINTS 0 "$(grep -cF 'semantic_hints' src/memory_agent/tool_schema.py)"
guard SPEC_RECOMMENDED_TOOL 0 "$(grep -cF 'recommended_tool' src/memory_agent/tool_schema.py)"
guard SPEC_INTENT_LIST 1 "$(grep -cF '取值是意图名（device_usage/behavior' src/memory_agent/tool_schema.py)"
guard SPEC_PARAM_HINTS 1 "$(grep -cF 'True 时保留顶层 hints' src/memory_agent/tool_schema.py)"
guard TEST_CLIMATE_DEFS 2 "$(grep -cF 'def test_climate' tests/test_vma_promised_keys_runtime_contract.py)"
guard TEST_TOTAL 10 "$(grep -cF 'def test_' tests/test_vma_promised_keys_runtime_contract.py)"
guard LEGACY_ATTRS_ANCHOR 1 "$(grep -cF 'cur_temp = _as_float(attrs.get("current_temperature"))' src/memory_agent/insights_legacy.py)"
guard UTILS_ROOMTEMP_ANCHOR 1 "$(grep -cF '"room_temp_c": round(sum(rt) / len(rt), 1) if rt else None,' src/memory_agent/insights/utils.py)"

# ── 尺 4：关键文件必须真在快照清单里（清单已在尺 3 前生成并按它取哈希）──
for f in src/memory_agent/mcp_server.py src/memory_agent/tool_schema.py \
         src/memory_agent/insights/api.py src/memory_agent/insights/service.py \
         src/memory_agent/insights/utils.py src/memory_agent/insights_legacy.py \
         src/memory_agent/insights/nlquery.py src/memory_agent/agent_memory.py \
         src/memory_agent/skills_bundle/insight/SKILL.md src/memory_agent/static/js/pages/user_manual.js \
         tests/test_vma_promised_keys_runtime_contract.py tests/test_vma_coverage_docstring_contract.py \
         tests/test_vma_insights_window_echo.py tests/test_insights_facade_contract.py \
         tests/test_tool_schema.py tests/test_mcp_surface_parity.py tests/test_acp_server.py \
         scripts/pyflakes_gate.sh; do
  n=$(grep -c "^$f\$" .qoder/tmp-c75-filelist23.txt)
  if [ "$n" != 1 ]; then echo "GUARD_FAIL IN_SNAP_$(basename $f) 期望=1 实际=$n"; BAD=1; fi
done
echo IN_SNAP_checked=18

if [ "$BAD" != 0 ]; then echo PREFLIGHT_FAILED=1 未出网; exit 2; fi
echo PREFLIGHT_OK=1

tar -czf "$TGZ" -T .qoder/tmp-c75-filelist23.txt; guard TAR_RC 0 $?
echo TAR_SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; guard SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c75-remote23.sh "$NAS:/tmp/ma_c75_remote23.sh"; guard REMOTE_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c75-mut75.py "$NAS:/tmp/ma_c75_mut75.py"; guard MUT75_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-hashes.py "$NAS:/tmp/ma_c75_hashes.py"; guard HASHES_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c75-filelist23.txt "$NAS:/tmp/ma_c75_filelist.txt"; guard FILELIST_SCP_RC 0 $?

# Windows 侧拷过去的脚本先洗行尾再验语法；哈希与清单同样按 unix 行尾进容器。
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage && tr -d '\r' < /tmp/ma_c75_mut75.py > /tmp/ma_c75_mut75_unix.tmp && tr -d '\r' < /tmp/ma_c75_hashes.py > /tmp/ma_c75_hashes_unix.tmp && tr -d '\r' < /tmp/ma_c75_filelist.txt > /tmp/ma_c75_filelist_unix.tmp && docker cp /tmp/ma_c75_mut75_unix.tmp memory-agent:/tmp/${SNAP}_mut75.py && docker cp /tmp/ma_c75_hashes_unix.tmp memory-agent:/tmp/${SNAP}_hashes.py && docker cp /tmp/ma_c75_filelist_unix.tmp memory-agent:/tmp/${SNAP}_filelist.txt && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut75.py /tmp/${SNAP}_hashes.py /tmp/${SNAP}_filelist.txt && rm -f /tmp/ma_c75_*.tmp /tmp/ma_c75_mut75.py /tmp/ma_c75_hashes.py /tmp/ma_c75_filelist.txt; echo STAGE_RC=\$?; tr -d '\r' < /tmp/ma_c75_remote23.sh > /tmp/ma_c75_remote23_unix.sh && bash -n /tmp/ma_c75_remote23_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?; docker exec memory-agent sh -c 'wc -l < /tmp/${SNAP}_filelist.txt'; echo SNAP_FILELIST_LINES_RC=\$?"
SYNC_RC=$?
echo SYNC_RC=$SYNC_RC
if [ "$SYNC_RC" != 0 ]; then echo SYNC_FAILED=1 未出网; exit 2; fi

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c75_remote23_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"

echo CONTAINER_BATCH_RC=0
