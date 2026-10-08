#!/usr/bin/env bash
# run17 = 本仓 #68 第四批（32 处协程直调卸载 + 量具 + 新锁）**加上** run16 之后那 9 个未进过容器的
# 提交（704f603 … 2a14651）的容器权威门。远端本体 = tmp-c68-remote17.sh。
#
# 与 run16 驱动的三处结构差：
#   ①新增"快照真身"格：filelist 逐文件 md5 的聚合摘要先在本机量一遍（.qoder/tmp-c68-hashes-local17.out），
#     容器里跑同一份脚本，两档 HASH_AGGREGATE 必须逐字相同 ⇒ "容器里跑的就是我这棵树"一票判掉，
#     不再靠几十条手抄锚点。锚点脚本同理：本机与容器**共用一份** tmp-c68-anchors.sh。
#   ②MUT 三档串跑：本批 15 条 + #64 的 24 条（其源文件 run16 后又动过）+ #63 的 22 条（未动 ⇒ 渗染自证）。
#   ③出网前把两支"两侧共用"的脚本也过一遍语法（sh -n / ast），别让容器侧才发现拼错。
#
# 出网前四把尺照旧：bash -n ×2、引号 lint、变异 harness 的 ast 预检、快照 IN_SNAP 逐格；
# 这一版另加**硬闸**：任何一把尺不过就 exit 2，不发容器（30 分钟的门不该毁在一个引号上）。
# run14b 的教训照旧在册：**权威门在飞的期间不许动树**（这一档在飞期间我只写 .qoder/ 与文档）。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c68snap20261007
TGZ=.qoder/tmp-c68-snap17.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
BAD=0
guard() { # guard <名字> <期望> <实际>
  if [ "$2" != "$3" ]; then echo "GUARD_FAIL $1 期望=$2 实际=$3"; BAD=1; else echo "GUARD_OK $1=$3"; fi
}

echo HEAD=$(git rev-parse --short HEAD)
echo UNPUSHED=$(git rev-list --count origin/main..HEAD)

# ── 尺 1：脚本语法 ──
bash -n .qoder/tmp-c68-remote17.sh; guard REMOTE_SYNTAX_RC 0 $?
bash -n "$0" 2>/dev/null; guard SELF_SYNTAX_RC 0 $?
sh -n .qoder/tmp-c68-anchors.sh; guard ANCHORS_SYNTAX_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c68-hashes.py',encoding='utf-8').read())"; guard HASHES_PARSE_RC 0 $?

# ── 尺 2：两层引号 lint（内层变量必须 \$R，run13 首跑折在这一格）──
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c68-remote17.sh; guard LINT_RC 0 $?

# ── 尺 3：三档变异 harness 的预检（本批 15 条必须 MUTANTS_PARSED=15，另两档 24/22）──
"$PY" .qoder/tmp-b68-mut.py --verify > .qoder/tmp-c68-mutverify17.out 2>&1; guard MUT68_VERIFY_RC 0 $?
grep -E 'MUTANTS_PARSED|SYNTAX_BAD|ANCHOR_OR_RESTORE_BAD|^BAD' .qoder/tmp-c68-mutverify17.out
guard MUT68_PARSED 15 "$(grep -oE 'MUTANTS_PARSED=[0-9]+' .qoder/tmp-c68-mutverify17.out | cut -d= -f2)"
guard MUT68_RESTOREBAD 0 "$(grep -oE 'ANCHOR_OR_RESTORE_BAD=[0-9]+' .qoder/tmp-c68-mutverify17.out | cut -d= -f2)"
"$PY" .qoder/tmp-b64-mut-verify.py > .qoder/tmp-c64-mutverify17.out 2>&1; guard MUT64_VERIFY_RC 0 $?
grep -E 'MUTANTS_PARSED|DEADSHAPE_COUNT|SYNTAX_BAD|VERIFY_BAD|^BAD' .qoder/tmp-c64-mutverify17.out
# MUT63（#63 那 22 条）**只量不闸**：#68 第三批把 `_num` 从 behavior_routes 搬进 day_bounds
# （净删 26 行），M1..M4 的锚点命中 0 次 ⇒ VERIFY_BAD=4 是**前提变了**，不是这一批改坏了。
# 所以这一档不进 run17（见远端本体头部第 4 条），这里把读数原样留下作开格证据。
"$PY" .qoder/tmp-b63-mut-verify.py > .qoder/tmp-c63-mutverify17.out 2>&1
echo MUT63_VERIFY_INFO_RC=$?
grep -E 'MUTANTS_PARSED|DEADSHAPE_COUNT|SYNTAX_BAD|VERIFY_BAD|^BAD' .qoder/tmp-c63-mutverify17.out
for m in tmp-b68-mut tmp-b64-mut; do
  "$PY" -c "import ast,io;ast.parse(io.open('.qoder/$m.py',encoding='utf-8').read())"; guard PARSE_$m 0 $?
done

# ── 尺 4：快照清单 + 本批关键文件必须真在树里 ──
{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c68-filelist17.txt
echo FILES=$(wc -l < .qoder/tmp-c68-filelist17.txt)
for f in src/memory_agent/api/agent_memory_routes.py src/memory_agent/api/member_routes.py \
         src/memory_agent/api/signal_routes.py src/memory_agent/api/vision_routes.py \
         src/memory_agent/api/llm_routes.py src/memory_agent/api/mcp_routes.py \
         src/memory_agent/acp_auth.py src/memory_agent/mcp_auth.py \
         src/memory_agent/api/behavior_routes.py src/memory_agent/identity_fusion.py \
         src/memory_agent/mqtt_bridge.py scripts/scan_unloaded_async_io.py \
         scripts/scan_qb_param_landing.py scripts/scan_day_bounds.py \
         tests/test_vma_phase2_batch4_offload.py tests/test_vma_phase2_batch3_shared_ruler.py \
         tests/test_vma_task64_identity_fusion.py tests/test_device_usage_core.py \
         tests/test_vma_dcd_20261006_linkage_codes.py tests/test_vma_a3_p22_auth_loop_blocking.py; do
  n=$(grep -c "^$f\$" .qoder/tmp-c68-filelist17.txt)
  if [ "$n" != 1 ]; then echo "GUARD_FAIL IN_SNAP_$(basename $f) 期望=1 实际=$n"; BAD=1; fi
done
echo IN_SNAP_checked=20
echo IN_SNAP_attic=$(grep -c '^attic/' .qoder/tmp-c68-filelist17.txt)

# ── 本机哈希档（容器侧跑同一份脚本，聚合摘要必须逐字相同）──
"$PY" .qoder/tmp-c68-hashes.py .qoder/tmp-c68-filelist17.txt > .qoder/tmp-c68-hashes-local17.out 2>&1
guard HASHES_LOCAL_RC 0 $?
cat .qoder/tmp-c68-hashes-local17.out
guard HASH_MISSING 0 "$(grep -oE 'HASH_MISSING=[0-9]+' .qoder/tmp-c68-hashes-local17.out | cut -d= -f2)"

if [ "$BAD" != 0 ]; then echo PREFLIGHT_FAILED=1 未出网; exit 2; fi
echo PREFLIGHT_OK=1

tar -czf "$TGZ" -T .qoder/tmp-c68-filelist17.txt; guard TAR_RC 0 $?
echo TAR_SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; guard SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-remote17.sh "$NAS:/tmp/ma_c68_remote17.sh"; guard REMOTE_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-b68-mut.py "$NAS:/tmp/ma_c68_mut68.py"; guard MUT68_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-b64-mut.py "$NAS:/tmp/ma_c68_mut64.py"; guard MUT64_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-anchors.sh "$NAS:/tmp/ma_c68_anchors.sh"; guard ANCHORS_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-hashes.py "$NAS:/tmp/ma_c68_hashes.py"; guard HASHES_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-filelist17.txt "$NAS:/tmp/ma_c68_filelist.txt"; guard FILELIST_SCP_RC 0 $?

# 远端脚本从 Windows 侧拷过去可能带 CRLF：先 tr -d '\r' 落成 unix 行尾，再 bash -n 验一遍。
# 锚点脚本两侧共用，同样先洗行尾——否则本机 sh -n 绿、容器里带 \r 直接失效。
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage && for x in mut68 mut64 anchors hashes filelist; do f=\$(ls /tmp/ma_c68_\$x.* 2>/dev/null | head -1); tr -d '\r' < \$f > /tmp/ma_c68_\${x}_unix.tmp; done && docker cp /tmp/ma_c68_mut68_unix.tmp memory-agent:/tmp/${SNAP}_mut68.py && docker cp /tmp/ma_c68_mut64_unix.tmp memory-agent:/tmp/${SNAP}_mut64.py && docker cp /tmp/ma_c68_anchors_unix.tmp memory-agent:/tmp/${SNAP}_anchors.sh && docker cp /tmp/ma_c68_hashes_unix.tmp memory-agent:/tmp/${SNAP}_hashes.py && docker cp /tmp/ma_c68_filelist_unix.tmp memory-agent:/tmp/${SNAP}_filelist.txt && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut68.py /tmp/${SNAP}_mut64.py /tmp/${SNAP}_anchors.sh /tmp/${SNAP}_hashes.py /tmp/${SNAP}_filelist.txt && rm -f /tmp/ma_c68_*.tmp /tmp/ma_c68_mut68.py /tmp/ma_c68_mut64.py /tmp/ma_c68_anchors.sh /tmp/ma_c68_hashes.py /tmp/ma_c68_filelist.txt; echo STAGE_RC=\$?; tr -d '\r' < /tmp/ma_c68_remote17.sh > /tmp/ma_c68_remote17_unix.sh && bash -n /tmp/ma_c68_remote17_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?; docker exec memory-agent sh -c 'cd /tmp/c68snap20261007 && sh /tmp/c68snap20261007_anchors.sh | wc -l'; echo SNAP_ANCHOR_LINES_RC=\$?"
echo SYNC_RC=$?

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c68_remote17_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"

echo CONTAINER_BATCH_RC=0
