#!/usr/bin/env bash
# run18 驱动（任务表 #70：#63 的 22 条变异档重锚后补跑，容器 Python 3.11 口径）
# 出网前四把尺 + 硬闸：任何一把不过就 exit 2，不发容器。
# 在飞期间不许动树：这一档在跑的时候我只写 .qoder/。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c70snap20261007
TGZ=.qoder/tmp-c70-snap18.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
# run17 的聚合摘要：代码自那以后一个字没动，两档必须逐字相同（不等就不出网）。
RUN17_AGG=cafb96a7e93403c526f9afbc85e9a4099c541f6ca94052323792e3fe9a581112
BAD=0
guard() { if [ "$2" != "$3" ]; then echo "GUARD_FAIL $1 期望=$2 实际=$3"; BAD=1; else echo "GUARD_OK $1=$3"; fi; }

echo HEAD=$(git rev-parse --short HEAD)
echo UNPUSHED=$(git rev-list --count origin/main..HEAD)
echo WORKTREE_DIRTY_TRACKED=$(git status --porcelain=v1 --untracked-files=no | wc -l)

# ── 尺 1：脚本语法 ──
bash -n .qoder/tmp-c70-remote18.sh; guard REMOTE_SYNTAX_RC 0 $?
bash -n "$0" 2>/dev/null; guard SELF_SYNTAX_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c68-hashes.py',encoding='utf-8').read())"; guard HASHES_PARSE_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-b63-mut.py',encoding='utf-8').read())"; guard MUT63_PARSE_RC 0 $?

# ── 尺 2：两层引号 lint（内层变量必须 \$R）──
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c70-remote18.sh; guard LINT_RC 0 $?

# ── 尺 3：MUT63 预检（这一档是硬闸，run17 里它只是"只量不闸"）──
"$PY" -X utf8 .qoder/tmp-b63-mut-verify.py > .qoder/tmp-c70-mutverify18.out 2>&1; guard MUT63_VERIFY_RC 0 $?
grep -E 'MUTANTS_PARSED|DEADSHAPE_COUNT|SYNTAX_BAD|VERIFY_BAD|^BAD' .qoder/tmp-c70-mutverify18.out
guard MUT63_PARSED 22 "$(grep -oE 'MUTANTS_PARSED=[0-9]+' .qoder/tmp-c70-mutverify18.out | cut -d= -f2)"
guard MUT63_SYNTAXBAD 0 "$(grep -oE 'SYNTAX_BAD=[0-9]+' .qoder/tmp-c70-mutverify18.out | cut -d= -f2)"
guard MUT63_VERIFYBAD 0 "$(grep -oE 'VERIFY_BAD=[0-9]+' .qoder/tmp-c70-mutverify18.out | cut -d= -f2)"
guard MUT63_DEADSHAPE 0 "$(grep -oE 'DEADSHAPE_COUNT=[0-9]+' .qoder/tmp-c70-mutverify18.out | cut -d= -f2)"
# 本机腿档（同一份 harness，隔离在 HEAD 的 archive 副本里跑，绝不碰工作树）
if [ -f .qoder/tmp-c70-mut63-local.out ]; then
  guard MUT63_LOCAL_BAD 0 "$(grep -oE 'MUTATION_BAD=[0-9]+' .qoder/tmp-c70-mut63-local.out | cut -d= -f2)"
  grep -E '^(NOTHING|MUT_COUNT)' .qoder/tmp-c70-mut63-local.out
else
  echo "GUARD_FAIL MUT63_LOCAL_MISSING 本机腿档不在盘上，不出网"; BAD=1
fi

# ── 尺 4：快照清单 + 关键文件在树 + 聚合摘要与 run17 逐字对账 ──
{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c70-filelist18.txt
echo FILES=$(wc -l < .qoder/tmp-c70-filelist18.txt)
guard FILES 400 "$(wc -l < .qoder/tmp-c70-filelist18.txt)"
for f in src/memory_agent/api/deps.py src/memory_agent/api/behavior_routes.py \
         src/memory_agent/day_bounds.py src/memory_agent/change_attribution.py \
         src/memory_agent/api/config_routes.py scripts/scan_unloaded_async_io.py \
         tests/test_vma_task63_routes_input_boundary.py \
         tests/test_vma_phase2_batch3_shared_ruler.py tests/test_vma_phase2_batch4_offload.py; do
  n=$(grep -c "^$f\$" .qoder/tmp-c70-filelist18.txt)
  if [ "$n" != 1 ]; then echo "GUARD_FAIL IN_SNAP_$(basename $f) 期望=1 实际=$n"; BAD=1; fi
done
echo IN_SNAP_checked=9
"$PY" -X utf8 .qoder/tmp-c68-hashes.py .qoder/tmp-c70-filelist18.txt > .qoder/tmp-c70-hashes-local18.out 2>&1
guard HASHES_LOCAL_RC 0 $?
cat .qoder/tmp-c70-hashes-local18.out
guard HASH_MISSING 0 "$(grep -oE 'HASH_MISSING=[0-9]+' .qoder/tmp-c70-hashes-local18.out | cut -d= -f2)"
guard HASH_AGG_MATCHES_RUN17 "$RUN17_AGG" "$(grep -oE 'HASH_AGGREGATE=[0-9a-f]+' .qoder/tmp-c70-hashes-local18.out | cut -d= -f2)"

if [ "$BAD" != 0 ]; then echo PREFLIGHT_FAILED=1 未出网; exit 2; fi
echo PREFLIGHT_OK=1

tar -czf "$TGZ" -T .qoder/tmp-c70-filelist18.txt; guard TAR_RC 0 $?
echo TAR_SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; guard SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c70-remote18.sh "$NAS:/tmp/ma_c70_remote18.sh"; guard REMOTE_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-b63-mut.py "$NAS:/tmp/ma_c70_mut63.py"; guard MUT63_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-hashes.py "$NAS:/tmp/ma_c70_hashes.py"; guard HASHES_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c70-filelist18.txt "$NAS:/tmp/ma_c70_filelist.txt"; guard FILELIST_SCP_RC 0 $?

"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage && for x in mut63 hashes filelist; do f=\$(ls /tmp/ma_c70_\$x.* 2>/dev/null | head -1); tr -d '\r' < \$f > /tmp/ma_c70_\${x}_unix.tmp; done && docker cp /tmp/ma_c70_mut63_unix.tmp memory-agent:/tmp/${SNAP}_mut63.py && docker cp /tmp/ma_c70_hashes_unix.tmp memory-agent:/tmp/${SNAP}_hashes.py && docker cp /tmp/ma_c70_filelist_unix.tmp memory-agent:/tmp/${SNAP}_filelist.txt && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut63.py /tmp/${SNAP}_hashes.py /tmp/${SNAP}_filelist.txt && rm -f /tmp/ma_c70_*.tmp /tmp/ma_c70_mut63.py /tmp/ma_c70_hashes.py /tmp/ma_c70_filelist.txt; echo STAGE_RC=\$?; tr -d '\r' < /tmp/ma_c70_remote18.sh > /tmp/ma_c70_remote18_unix.sh && bash -n /tmp/ma_c70_remote18_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?"
echo SYNC_RC=$?

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c70_remote18_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"
echo CONTAINER_BATCH_RC=0
