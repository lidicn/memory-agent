#!/usr/bin/env bash
# run25 = 任务表 #78（DCD 20261007 §一 裁甲：删 439 行死目录镜像 + 三把形状锁 + 一条读者锁）的容器权威门。
# 远端本体 = tmp-c78-remote25.sh。工作树在 HEAD `97b7062` 冻结：门在飞期间只写 .qoder/ 与文档。
#
# 出网前四把尺照旧，任一不过就 exit 2 不发容器：
#   尺1 语法（remote / self / mut78 / hashes / 锁文件的 ast）；尺2 两层引号 lint；
#   尺3 变异档预检（VERIFY_LEGS=6 VERIFY_BAD=0，只读表、永不 import）+ 本机腿档必须在盘上且
#       MUTATION_BAD=0 + 对照档 ctl 必须在盘上 + 本机哈希档；
#   尺4 快照清单 + 关键文件必在树里。
# 所有 guard 的"期望"当场从文件量（本机实测：MCP_LINES=2939 / MCP_BYTES=137704 / CAT_BIND=1 /
# CAT_TOTAL=4 / LITERAL_START=0 / DERIVED_LINE=0 / NEW_COMMENT=1 / OLD_COMMENT=0 /
# PAR_TEST_DEFS=17 / PAR_LOAD_ONLY=1 / PAR_READINGS_FN=1 / PAR_DESCRIBE_READ=1 /
# mut78 特征 1/2/1/6/1/1），不写上轮的数。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c78snap20261007
TGZ=.qoder/tmp-c78-snap25.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
BAD=0
guard() { # guard <名字> <期望> <实际>
  if [ "$2" != "$3" ]; then echo "GUARD_FAIL $1 期望=$2 实际=$3"; BAD=1; else echo "GUARD_OK $1=$3"; fi
}

echo HEAD=$(git rev-parse --short HEAD)
echo UNPUSHED=$(git rev-list --count origin/main..HEAD)
guard TREE_CLEAN 0 "$(git status --porcelain --untracked-files=no | wc -l | tr -d ' ')"
guard HEAD_IS_78 97b7062 "$(git rev-parse --short HEAD)"

# ── 尺 1：脚本语法 ──
bash -n .qoder/tmp-c78-remote25.sh; guard REMOTE_SYNTAX_RC 0 $?
bash -n "$0" 2>/dev/null; guard SELF_SYNTAX_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c78-mut78.py',encoding='utf-8').read())"; guard MUT78_PARSE_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c68-hashes.py',encoding='utf-8').read())"; guard HASHES_PARSE_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('tests/test_mcp_surface_parity.py',encoding='utf-8').read())"; guard LOCK_PARSE_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('src/memory_agent/mcp_server.py',encoding='utf-8').read())"; guard SRC_PARSE_RC 0 $?

# ── 尺 2：两层引号 lint（内层变量必须 \$）──
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c78-remote25.sh; guard LINT_RC 0 $?

# ── 尺 3：变异档预检（只读，不 import、不开跑）＋ 本机那档必须在盘上 ──
{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c78-filelist25.txt
echo FILES25=$(wc -l < .qoder/tmp-c78-filelist25.txt)
"$PY" .qoder/tmp-c78-mut78.py --verify > .qoder/tmp-c78-mutverify25.out 2>&1; guard MUT78_VERIFY_RC 0 $?
grep -E 'VERIFY_LEGS|VERIFY_BAD' .qoder/tmp-c78-mutverify25.out
guard MUT78_VERIFY_LEGS 6 "$(grep -oE 'VERIFY_LEGS=[0-9]+' .qoder/tmp-c78-mutverify25.out | cut -d= -f2)"
guard MUT78_VERIFY_BAD 0 "$(grep -oE 'VERIFY_BAD=[0-9]+' .qoder/tmp-c78-mutverify25.out | cut -d= -f2)"
guard MUT78_LOCAL_OUT_EXISTS 0 "$([ -s .qoder/tmp-c78-mut78-local.out ] && echo 0 || echo 1)"
grep -E '^(M-0|L[0-9]+|MUT78_COUNT|SRC_UNCHANGED)' .qoder/tmp-c78-mut78-local.out
guard MUT78_LOCAL_BAD 0 "$(grep -oE 'MUTATION_BAD=[0-9]+' .qoder/tmp-c78-mut78-local.out | cut -d= -f2)"
guard MUT78_LOCAL_LEGS 6 "$(grep -oE 'MUT78_COUNT=[0-9]+' .qoder/tmp-c78-mut78-local.out | cut -d= -f2)"
guard MUT78_LOCAL_UNCHANGED True "$(grep -oE 'SRC_UNCHANGED=[A-Za-z]+' .qoder/tmp-c78-mut78-local.out | cut -d= -f2)"
# 对照档（本机 6 档：M-0 绿 + L1..L5 各咬）必须在盘上——本轮"锁会红"的读数出自它
guard CTL_OUT_EXISTS 0 "$([ -s .qoder/tmp-c78-ctl.out ] && echo 0 || echo 1)"
guard CTL_BAD 0 "$(grep -oE 'CTL_BAD=[0-9]+' .qoder/tmp-c78-ctl.out | cut -d= -f2)"
guard CTL_TREE_GREEN True "$(grep -oE 'TREE_GREEN=[A-Za-z]+' .qoder/tmp-c78-ctl.out | cut -d= -f2 | head -1)"
# 本机全量档（容器权威门之前的自扫，必须已跑完且 0 failed）
guard LOCAL_SUITE_EXISTS 0 "$([ -s .qoder/tmp-c78-local-suite.out ] && echo 0 || echo 1)"
tail -3 .qoder/tmp-c78-local-suite.out
guard LOCAL_SUITE_FAILED 0 "$(grep -cE '^FAILED |[0-9]+ failed' .qoder/tmp-c78-local-suite.out)"

# 本机哈希档（容器侧跑同一份脚本，聚合摘要必须逐字相同 ⇒ "容器里跑的就是这棵树"）
"$PY" .qoder/tmp-c68-hashes.py .qoder/tmp-c78-filelist25.txt > .qoder/tmp-c78-hashes-local25.out 2>&1; guard HASHES_LOCAL_RC 0 $?
cat .qoder/tmp-c78-hashes-local25.out
guard HASH_MISSING 0 "$(grep -oE 'HASH_MISSING=[0-9]+' .qoder/tmp-c78-hashes-local25.out | cut -d= -f2)"

# 量具自身的特征锚点（本机先读，容器 0D 格读同一份文件，两侧数字必须一致）
# 本机 guard 的 grep 模式在**单引号**里：内层的 `"` 直接写，不许加 `\`（§四十八 自捉 1）。
guard PATH_APPEND 1 "$(grep -cF 'os.pathsep' .qoder/tmp-c78-mut78.py)"
guard VERIFY_FLAG 2 "$(grep -cF '"--verify"' .qoder/tmp-c78-mut78.py)"
guard AST_GUARD 1 "$(grep -cF 'ast.parse(patched' .qoder/tmp-c78-mut78.py)"
guard L_LEGS 6 "$(grep -cF '("L' .qoder/tmp-c78-mut78.py)"
guard FAILED_GUARD 1 "$(grep -cF 'failed > 0' .qoder/tmp-c78-mut78.py)"
guard SRC_UNCHANGED_FLAG 1 "$(grep -cF 'SRC_UNCHANGED=' .qoder/tmp-c78-mut78.py)"

# 产品侧形状锚点（本机读数，容器 0C 格对逐字）
guard MCP_LINES 2939 "$(wc -l < src/memory_agent/mcp_server.py | tr -d ' ')"
guard MCP_BYTES 137704 "$(wc -c < src/memory_agent/mcp_server.py | tr -d ' ')"
guard CAT_BIND 1 "$(grep -cF 'TOOL_CATALOG = build_catalog()' src/memory_agent/mcp_server.py)"
# 全文 TOOL_CATALOG 计数 = 4：三处函数体读者（help 的两处 + describe 的一处）+ 一处真源绑定。
# 注意 describe() 里同时读 TOOL_NAMES 与 TOOL_CATALOG，所以这 4 条不是"四个读者"。
guard CAT_TOTAL 4 "$(grep -cF 'TOOL_CATALOG' src/memory_agent/mcp_server.py)"
guard LITERAL_START 0 "$(grep -cF 'TOOL_CATALOG: list[dict] = [' src/memory_agent/mcp_server.py)"
guard DERIVED_LINE 0 "$(grep -cF 'TOOL_NAMES = [t["name"] for t in TOOL_CATALOG]' src/memory_agent/mcp_server.py)"
guard NEW_COMMENT 1 "$(grep -cF '目录唯一真源 = ' src/memory_agent/mcp_server.py)"
guard OLD_COMMENT 0 "$(grep -cF '为兼容历史保留' src/memory_agent/mcp_server.py)"
# CR 用 tr 按字节量：`"$()"` 里的 `$'\r'` 在**双引号内**不是 ANSI-C 引用，
# 上一版把整份文件的行数当成了 CR 数（首跑被自己拦下，未出网）。
guard MCP_CR 0 "$(tr -dc '\r' < src/memory_agent/mcp_server.py | wc -c | tr -d ' ')"
guard PAR_TEST_DEFS 17 "$(grep -cF 'def test_' tests/test_mcp_surface_parity.py)"
guard PAR_LOAD_ONLY 1 "$(grep -cF 'isinstance(node.ctx, ast.Load)' tests/test_mcp_surface_parity.py)"
guard PAR_READINGS_FN 1 "$(grep -cF 'def _catalog_source_readings(path):' tests/test_mcp_surface_parity.py)"
guard PAR_DESCRIBE_READ 1 "$(grep -cF '"catalog": TOOL_CATALOG,' src/memory_agent/mcp_server.py)"
guard PAR_DESCRIBE_TEST 1 "$(grep -cF 'def test_describe_page_serves_the_whole_spec_catalog():' tests/test_mcp_surface_parity.py)"
guard LOCK_CR 0 "$(tr -dc '\r' < tests/test_mcp_surface_parity.py | wc -c | tr -d ' ')"

# ── 尺 4：关键文件必须真在快照清单里 ──
for f in src/memory_agent/mcp_server.py src/memory_agent/tool_schema.py \
         src/memory_agent/api/mcp_routes.py src/memory_agent/runtime.py \
         tests/test_mcp_surface_parity.py tests/test_tool_schema.py \
         tests/test_acp_server.py tests/test_vma_step1_adm_presence.py \
         tests/test_vma_promised_keys_runtime_contract.py \
         tests/test_vma_insights_callsite_binding.py tests/test_webui_payload_keys.py \
         scripts/pyflakes_gate.sh; do
  n=$(grep -c "^$f\$" .qoder/tmp-c78-filelist25.txt)
  if [ "$n" != 1 ]; then echo "GUARD_FAIL IN_SNAP_$(basename $f) 期望=1 实际=$n"; BAD=1; fi
done
echo IN_SNAP_checked=12

if [ "$BAD" != 0 ]; then echo PREFLIGHT_FAILED=1 未出网; exit 2; fi
echo PREFLIGHT_OK=1

tar -czf "$TGZ" -T .qoder/tmp-c78-filelist25.txt; guard TAR_RC 0 $?
echo TAR_SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; guard SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c78-remote25.sh "$NAS:/tmp/ma_c78_remote25.sh"; guard REMOTE_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c78-mut78.py "$NAS:/tmp/ma_c78_mut78.py"; guard MUT78_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-hashes.py "$NAS:/tmp/ma_c78_hashes.py"; guard HASHES_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c78-filelist25.txt "$NAS:/tmp/ma_c78_filelist.txt"; guard FILELIST_SCP_RC 0 $?

# Windows 侧拷过去的脚本先洗行尾再验语法；哈希与清单同样按 unix 行尾进容器。
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage && tr -d '\r' < /tmp/ma_c78_mut78.py > /tmp/ma_c78_mut78_unix.tmp && tr -d '\r' < /tmp/ma_c78_hashes.py > /tmp/ma_c78_hashes_unix.tmp && tr -d '\r' < /tmp/ma_c78_filelist.txt > /tmp/ma_c78_filelist_unix.tmp && docker cp /tmp/ma_c78_mut78_unix.tmp memory-agent:/tmp/${SNAP}_mut78.py && docker cp /tmp/ma_c78_hashes_unix.tmp memory-agent:/tmp/${SNAP}_hashes.py && docker cp /tmp/ma_c78_filelist_unix.tmp memory-agent:/tmp/${SNAP}_filelist.txt && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut78.py /tmp/${SNAP}_hashes.py /tmp/${SNAP}_filelist.txt && rm -f /tmp/ma_c78_*.tmp /tmp/ma_c78_mut78.py /tmp/ma_c78_hashes.py /tmp/ma_c78_filelist.txt; echo STAGE_RC=\$?; tr -d '\r' < /tmp/ma_c78_remote25.sh > /tmp/ma_c78_remote25_unix.sh && bash -n /tmp/ma_c78_remote25_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?; docker exec memory-agent sh -c 'wc -l < /tmp/${SNAP}_filelist.txt'; echo SNAP_FILELIST_LINES_RC=\$?"
SYNC_RC=$?
echo SYNC_RC=$SYNC_RC
if [ "$SYNC_RC" != 0 ]; then echo SYNC_FAILED=1 未出网; exit 2; fi

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c78_remote25_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"

echo CONTAINER_BATCH_RC=0
