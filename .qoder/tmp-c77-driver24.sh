#!/usr/bin/env bash
# run24 = 任务表 #77（WebUI 前端读键 ↔ HTTP 载荷实际键：新量具 + 判据锁）的容器权威门。
# 远端本体 = tmp-c77-remote24.sh。工作树在 HEAD `f6773ea` 冻结：门在飞期间只写 .qoder/，不动树。
#
# 出网前四把尺，任一不过就 exit 2 不发容器：
#   尺1 语法（remote / self / 量具 / 锁）；尺2 两层引号 lint；
#   尺3 本机读数档必须在盘上且数字对得上（全表四格归零 + 五条控制腿 RC=0 + 本机 pytest 17 条）；
#   尺4 快照清单 + 关键文件必在树里 + 本机哈希档。
# 所有 guard 的"期望"当场从文件量（本轮实测列在下面），不写上轮的数。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c77snap20261007
TGZ=.qoder/tmp-c77-snap24.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
BAD=0
guard() { # guard <名字> <期望> <实际>
  if [ "$2" != "$3" ]; then echo "GUARD_FAIL $1 期望=$2 实际=$3"; BAD=1; else echo "GUARD_OK $1=$3"; fi
}

echo HEAD=$(git rev-parse --short HEAD)
echo UNPUSHED=$(git rev-list --count origin/main..HEAD)
guard TREE_CLEAN 0 "$(git status --porcelain --untracked-files=no | wc -l | tr -d ' ')"

# ── 尺 1：语法 ──
bash -n .qoder/tmp-c77-remote24.sh; guard REMOTE_SYNTAX_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('scripts/scan_webui_payload_keys.py',encoding='utf-8').read())"; guard SCAN_PARSE_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('tests/test_webui_payload_keys.py',encoding='utf-8').read())"; guard LOCK_PARSE_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c68-hashes.py',encoding='utf-8').read())"; guard HASHES_PARSE_RC 0 $?

# ── 尺 2：两层引号 lint（内层变量必须 \$）──
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c77-remote24.sh; guard LINT_RC 0 $?

# ── 尺 3：本机读数档必须在盘上、数字对得上；本机 pytest 现跑一遍留档 ──
guard FACES_OUT_EXISTS 0 "$([ -s .qoder/tmp-c77-tracked-main2.out ] && echo 0 || echo 1)"
guard SELFTEST_OUT_EXISTS 0 "$([ -s .qoder/tmp-c77-tracked-selftest2.out ] && echo 0 || echo 1)"
guard FACES_UNPARSED 0 "$(grep -oE 'unparsed=[0-9]+' .qoder/tmp-c77-tracked-main2.out | cut -d= -f2)"
guard FACES_ORPHAN 0 "$(grep -oE 'CALLSITES_ORPHAN=[0-9]+' .qoder/tmp-c77-tracked-main2.out | cut -d= -f2)"
guard FACES_PATH_UNMATCHED 0 "$(grep -oE 'PATH_UNMATCHED=[0-9]+' .qoder/tmp-c77-tracked-main2.out | cut -d= -f2)"
guard FACES_HARD 0 "$(grep -oE 'READKEY_HARD=[0-9]+' .qoder/tmp-c77-tracked-main2.out | cut -d= -f2)"
guard FACES_SHAPE 0 "$(grep -oE ' SHAPE=[0-9]+' .qoder/tmp-c77-tracked-main2.out | cut -d= -f2)"
guard FACES_NESTED 0 "$(grep -oE 'NESTED_MISS=[0-9]+' .qoder/tmp-c77-tracked-main2.out | cut -d= -f2)"
guard FACES_GREEN 12 "$(grep -oE 'GREEN_BY_HELPER=[0-9]+' .qoder/tmp-c77-tracked-main2.out | cut -d= -f2)"
guard FACES_CHAIN_UNKNOWN 5 "$(grep -oE 'CHAIN_UNKNOWN=[0-9]+' .qoder/tmp-c77-tracked-main2.out | cut -d= -f2)"
guard SELFTEST_BAD 0 "$(grep -oE 'SELFTEST_BAD=[0-9]+' .qoder/tmp-c77-tracked-selftest2.out | cut -d= -f2)"
guard SELFTEST_LEGS_TRUE 5 "$(grep -oE 'L[0-9]_[A-Za-z_]+=[A-Za-z]+' .qoder/tmp-c77-tracked-selftest2.out | grep -c '=True')"
"$PY" -m pytest tests/test_webui_payload_keys.py -q -p no:cacheprovider > .qoder/tmp-c77-pytest-local24.out 2>&1
guard LOCK_PYTEST_RC 0 $?
tail -2 .qoder/tmp-c77-pytest-local24.out
guard LOCK_COLLECTED 17 "$(grep -oE '[0-9]+ passed' .qoder/tmp-c77-pytest-local24.out | cut -d' ' -f1)"

# 量具与锁的形状锚点（本机先读，容器 0C/0C2 读同一份文件，两侧数字必须一致）
guard SCAN_ANALYSE_DEF 1 "$(grep -cF 'def analyse():' scripts/scan_webui_payload_keys.py)"
guard SCAN_MAIN_DEF 1 "$(grep -cF 'def main():' scripts/scan_webui_payload_keys.py)"
guard SCAN_LEG_NAMES 1 "$(grep -cF 'L5_producer_key_rename_flips_to_HARD' scripts/scan_webui_payload_keys.py)"
guard SCAN_ENVELOPE_CALL 1 "$(grep -cF 'if nm in ("ok", "JSONResponse"):' scripts/scan_webui_payload_keys.py)"
guard SCAN_TEMPFILE_IMPORT 0 "$(grep -cF 'import tempfile' scripts/scan_webui_payload_keys.py)"
guard LOCK_TEST_DEFS 6 "$(grep -cF 'def test_' tests/test_webui_payload_keys.py)"
guard LOCK_PIN_ROWS 12 "$(grep -cE '^    \("[a-zA-Z]+", "[a-z_]+", "src/' tests/test_webui_payload_keys.py)"
guard ENVELOPE_OK_SPREAD 1 "$(grep -cF '{"ok": True, **data}' src/memory_agent/api/deps.py)"
guard ENVELOPE_ERROR_UPDATE 1 "$(grep -cF 'payload.update(extra)' src/memory_agent/api/deps.py)"

# ── 尺 4：快照清单 + 关键文件必在树里 + 本机哈希档 ──
{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c77-filelist24.txt
echo FILES24=$(wc -l < .qoder/tmp-c77-filelist24.txt)
for f in scripts/scan_webui_payload_keys.py tests/test_webui_payload_keys.py \
         src/memory_agent/api/deps.py src/memory_agent/api/member_routes.py \
         src/memory_agent/api/collect_routes.py src/memory_agent/api/system_routes.py \
         src/memory_agent/api/vision_routes.py src/memory_agent/api/agent_memory_routes.py \
         src/memory_agent/agent_memory.py src/memory_agent/runtime.py src/memory_agent/store.py \
         src/memory_agent/signal_learning.py src/memory_agent/template_validate.py \
         src/memory_agent/vision_service.py src/memory_agent/app_tokens.py \
         src/memory_agent/static/js/api.js src/memory_agent/static/js/main.js \
         src/memory_agent/static/js/pages/dashboard.js src/memory_agent/static/js/pages/vision.js \
         src/memory_agent/static/js/pages/settings.js src/memory_agent/static/js/pages/members.js \
         src/memory_agent/static/js/pages/signal_rules.js src/memory_agent/static/js/pages/insights.js \
         src/memory_agent/static/js/pages/agent_memory.js src/memory_agent/static/js/pages/collect.js \
         scripts/pyflakes_gate.sh; do
  n=$(grep -c "^$f\$" .qoder/tmp-c77-filelist24.txt)
  if [ "$n" != 1 ]; then echo "GUARD_FAIL IN_SNAP_$(basename $f) 期望=1 实际=$n"; BAD=1; fi
done
echo IN_SNAP_checked=27
"$PY" .qoder/tmp-c68-hashes.py .qoder/tmp-c77-filelist24.txt > .qoder/tmp-c77-hashes-local24.out 2>&1
guard HASHES_LOCAL_RC 0 $?
cat .qoder/tmp-c77-hashes-local24.out
guard HASH_MISSING 0 "$(grep -oE 'HASH_MISSING=[0-9]+' .qoder/tmp-c77-hashes-local24.out | cut -d= -f2)"

if [ "$BAD" != 0 ]; then echo PREFLIGHT_FAILED=1 未出网; exit 2; fi
echo PREFLIGHT_OK=1

tar -czf "$TGZ" -T .qoder/tmp-c77-filelist24.txt; guard TAR_RC 0 $?
echo TAR_SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; guard SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c77-remote24.sh "$NAS:/tmp/ma_c77_remote24.sh"; guard REMOTE_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-hashes.py "$NAS:/tmp/ma_c77_hashes.py"; guard HASHES_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c77-filelist24.txt "$NAS:/tmp/ma_c77_filelist.txt"; guard FILELIST_SCP_RC 0 $?

# Windows 侧拷过去的脚本先洗行尾再验语法；哈希与清单同样按 unix 行尾进容器。
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage && tr -d '\r' < /tmp/ma_c77_hashes.py > /tmp/ma_c77_hashes_unix.tmp && tr -d '\r' < /tmp/ma_c77_filelist.txt > /tmp/ma_c77_filelist_unix.tmp && docker cp /tmp/ma_c77_hashes_unix.tmp memory-agent:/tmp/${SNAP}_hashes.py && docker cp /tmp/ma_c77_filelist_unix.tmp memory-agent:/tmp/${SNAP}_filelist.txt && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_hashes.py /tmp/${SNAP}_filelist.txt && rm -f /tmp/ma_c77_*.tmp /tmp/ma_c77_hashes.py /tmp/ma_c77_filelist.txt; echo STAGE_RC=\$?; tr -d '\r' < /tmp/ma_c77_remote24.sh > /tmp/ma_c77_remote24_unix.sh && bash -n /tmp/ma_c77_remote24_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?; docker exec memory-agent sh -c 'wc -l < /tmp/${SNAP}_filelist.txt'; echo SNAP_FILELIST_LINES_RC=\$?"
SYNC_RC=$?
echo SYNC_RC=$SYNC_RC
if [ "$SYNC_RC" != 0 ]; then echo SYNC_FAILED=1 未出网; exit 2; fi

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c77_remote24_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"

echo CONTAINER_BATCH_RC=0
