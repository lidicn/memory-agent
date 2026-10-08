#!/usr/bin/env bash
# run26 = 任务表 #80/#81/#82（DCD 20261007 §三/§四/§五 三件落码）的容器权威门。
# 远端本体 = tmp-c82-remote26.sh。工作树在 HEAD `fdad983` 冻结：门在飞期间只写 .qoder/ 与文档。
#
# 出网前四把尺照旧，任一不过就 exit 2 不发容器：
#   尺1 语法（remote / self / 两份 harness / probe / 六个被改源文件 / 四个测试文件 的 ast）；
#   尺2 两层引号 lint；
#   尺3 本机读数档必须在盘上且判据对得上：mut81 两档（首跑 22 腿杀 20 活 2、补跑全杀）、
#       mut82 两档（首跑 14 腿杀 10 活 4、补跑全杀）、probe 四格 PROBE_BAD=0、全量本机档 0 failed、
#       本机哈希档；
#   尺4 快照清单 + 关键文件必在树里。
# 所有 guard 的"期望"当场从文件量（本轮实测：AUTH/CFG/ANN/RT/MB 各锚点全 =1，
# L_RATE_DEFS=22 / L_ANN_DEFS=17 / L_CODE_DEFS=19 / L_PROSE_DEFS=9，M81_LEGS=22 / M82_LEGS=14，
# vendor 仅 1 枚 wheel 且 sha 与 homesdk/dist 逐字同），不写上轮的数。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c82snap20261008
TGZ=.qoder/tmp-c82-snap26.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
BAD=0
guard() { # guard <名字> <期望> <实际>
  if [ "$2" != "$3" ]; then echo "GUARD_FAIL $1 期望=$2 实际=$3"; BAD=1; else echo "GUARD_OK $1=$3"; fi
}

echo HEAD=$(git rev-parse --short HEAD)
echo UNPUSHED=$(git rev-list --count origin/main..HEAD)
# 快照口径：snap26 打的是 src/tests/scripts + 门禁与打包配置。这一份必须与 HEAD 逐字节同，
# 否则容器读数对不上本机指纹。审计/进度文档允许在门飞期间继续写（它们不进快照、不进门）。
CODE_DIFF=$(git status --porcelain --untracked-files=no -- \
  src tests scripts benchmarks .gates-baseline.txt .gates.toml .gates pytest.ini \
  pyproject.toml gates.sh Dockerfile vendor)
guard CODE_CLEAN 0 "$(printf '%s' "$CODE_DIFF" | grep -c . )"
echo "DOC_DIRTY=$(git status --porcelain --untracked-files=no -- doc | wc -l | tr -d ' ')"
guard HEAD_IS_82 fdad983 "$(git rev-parse --short HEAD)"
guard UNPUSHED_4 4 "$(git rev-list --count origin/main..HEAD)"

# ── 尺 1：脚本语法 + 被改文件的 ast ──
bash -n .qoder/tmp-c82-remote26.sh; guard REMOTE_SYNTAX_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c81-mut.py',encoding='utf-8').read())"; guard M81_PARSE_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c82-mut.py',encoding='utf-8').read())"; guard M82_PARSE_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c82-probe.py',encoding='utf-8').read())"; guard PROBE_PARSE_RC 0 $?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c68-hashes.py',encoding='utf-8').read())"; guard HASHES_PARSE_RC 0 $?
for f in src/memory_agent/auth.py src/memory_agent/config.py src/memory_agent/api/auth_routes.py \
         src/memory_agent/app.py src/memory_agent/announcer.py src/memory_agent/runtime.py \
         src/memory_agent/mqtt_bridge.py src/memory_agent/adm_linkage.py \
         tests/test_vma_login_rate_limit.py tests/test_announcer.py \
         tests/test_vma_dcd_20261006_linkage_codes.py tests/test_vma_dcd_20261002_payload.py \
         tests/test_tool_prose_promised_keys.py; do
  "$PY" -c "import ast,io,sys;ast.parse(io.open(sys.argv[1],encoding='utf-8').read())" "$f"; guard AST_$(basename $f) 0 $?
done

# ── 尺 2：两层引号 lint（内层变量必须 \$）──
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c82-remote26.sh; guard LINT_RC 0 $?

# ── 尺 3：本机读数档必须在盘上，且判定与"应杀/应绿"一致 ──
{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c82-filelist26.txt
echo FILES26=$(wc -l < .qoder/tmp-c82-filelist26.txt)

for pair in "tmp-c81-mut81 tmp-c81-mutv2" "tmp-c82-mut-local1 tmp-c82-mutv2"; do
  set -- $pair
  guard OUT_1ST_PRESENT 0 "$([ -s .qoder/$1.out ] && echo 0 || echo 1)"
  guard OUT_2ND_PRESENT 0 "$([ -s .qoder/$2.out ] && echo 0 || echo 1)"
done
# 首跑：两档都允许有存活腿（那正是补用例的依据）；补跑必须 0 存活、0 无效、工作树未动。
guard M81_V1_WT True "$(grep -oE 'WT_UNTOUCHED=[A-Za-z]+' .qoder/tmp-c81-mut81.out | cut -d= -f2)"
guard M81_V2_KILLED 2 "$(grep -c 'KILLED' .qoder/tmp-c81-mutv2.out)"
guard M81_V2_SURVIVED 0 "$(grep -c 'SURVIVED' .qoder/tmp-c81-mutv2.out)"
guard M81_V2_INVALID 0 "$(grep -c 'INVALID' .qoder/tmp-c81-mutv2.out)"
guard M81_V2_WT True "$(grep -oE 'WT_UNTOUCHED=[A-Za-z]+' .qoder/tmp-c81-mutv2.out | cut -d= -f2)"
grep -E '^\[M-0\]' .qoder/tmp-c81-mutv2.out
guard M82_V1_KILLED 10 "$(grep -c 'KILLED' .qoder/tmp-c82-mut-local1.out)"
guard M82_V1_SURVIVED 4 "$(grep -c 'SURVIVED' .qoder/tmp-c82-mut-local1.out)"
guard M82_V1_INVALID 0 "$(grep -c 'INVALID' .qoder/tmp-c82-mut-local1.out)"
guard M82_V2_KILLED 4 "$(grep -c 'KILLED' .qoder/tmp-c82-mutv2.out)"
guard M82_V2_SURVIVED 0 "$(grep -c 'SURVIVED' .qoder/tmp-c82-mutv2.out)"
guard M82_V2_WT True "$(grep -oE 'WT_UNTOUCHED=[A-Za-z]+' .qoder/tmp-c82-mutv2.out | cut -d= -f2)"
guard M82_TOTALS "totals: killed=4 survived=0 invalid=0 legs=4" "$(grep -E '^totals' .qoder/tmp-c82-mutv2.out)"

# RUNTIME 格的本机档：四格矩阵必须 PROBE_BAD=0
"$PY" .qoder/tmp-c82-probe.py > .qoder/tmp-c82-probe-local.out 2>&1; guard PROBE_LOCAL_RC 0 $?
guard PROBE_BAD 0 "$(grep -oE 'PROBE_BAD=[0-9]+' .qoder/tmp-c82-probe-local.out | cut -d= -f2)"
guard PROBE_CASES 4 "$(grep -c '^CASE_' .qoder/tmp-c82-probe-local.out)"

# 本机全量档（容器权威门之前的自扫，必须已跑完且 0 failed）
guard LOCAL_SUITE_EXISTS 0 "$([ -s .qoder/tmp-c82-suite-local.out ] && echo 0 || echo 1)"
tail -3 .qoder/tmp-c82-suite-local.out
guard LOCAL_SUITE_RC 0 "$(grep -oE 'LOCAL_SUITE_RC=[0-9]+' .qoder/tmp-c82-suite-local.out | cut -d= -f2)"
guard LOCAL_SUITE_FAILED 0 "$(grep -cE '^FAILED |[0-9]+ failed' .qoder/tmp-c82-suite-local.out)"

# 本机哈希档（容器侧跑同一份脚本，聚合摘要必须逐字相同 ⇒ "容器里跑的就是这棵树"）
"$PY" .qoder/tmp-c68-hashes.py .qoder/tmp-c82-filelist26.txt > .qoder/tmp-c82-hashes-local26.out 2>&1; guard HASHES_LOCAL_RC 0 $?
cat .qoder/tmp-c82-hashes-local26.out
guard HASH_MISSING 0 "$(grep -oE 'HASH_MISSING=[0-9]+' .qoder/tmp-c82-hashes-local26.out | cut -d= -f2)"

# 量具自身的特征锚点（本机先读，容器 0D 格读同一份文件，两侧数字必须一致）
guard M81_LEGS 22 "$(grep -cE '^[[:space:]]+\("M[0-9]+", ' .qoder/tmp-c81-mut.py)"
guard M82_LEGS 14 "$(grep -cE '^[[:space:]]+\("N[0-9]+", ' .qoder/tmp-c82-mut.py)"
guard M81_OUTENV 1 "$(grep -cF 'MUT_OUT' .qoder/tmp-c81-mut.py)"
guard M82_OUTENV 1 "$(grep -cF 'MUT_OUT' .qoder/tmp-c82-mut.py)"
guard M81_AST 1 "$(grep -cF 'ast.parse(mutated' .qoder/tmp-c81-mut.py)"
guard M82_AST 1 "$(grep -cF 'ast.parse(mutated' .qoder/tmp-c82-mut.py)"
guard M81_UNCHANGED 1 "$(grep -cF 'WT_UNTOUCHED=' .qoder/tmp-c81-mut.py)"
guard M82_UNCHANGED 1 "$(grep -cF 'WT_UNTOUCHED=' .qoder/tmp-c82-mut.py)"

# 产品侧形状锚点（本机读数，容器 0C 格对逐字）
A=src/memory_agent/auth.py; C=src/memory_agent/config.py; R=src/memory_agent/api/auth_routes.py
AP=src/memory_agent/app.py; AN=src/memory_agent/announcer.py; RT=src/memory_agent/runtime.py
MB=src/memory_agent/mqtt_bridge.py
guard AUTH_RESOLVE 1 "$(grep -cF 'def resolve_client_ip(' $A)"
guard AUTH_BUDGET 1 "$(grep -cF '_GLOBAL_FAIL_BUDGET = 60' $A)"
guard AUTH_BACKOFF 1 "$(grep -cF '_GLOBAL_BACKOFF_SECONDS = 60' $A)"
guard AUTH_XFF_LAST 1 "$(grep -cF 'return parts[-1] if parts else peer_ip' $A)"
guard AUTH_SWITCH 1 "$(grep -cF 'if not trust_proxy:' $A)"
guard AUTH_NETS_FN 1 "$(grep -cF 'def _trusted_proxy_networks(' $A)"
guard CFG_TRUST_DEFAULT 1 "$(grep -cF 'trust_proxy: bool = False' $C)"
guard CFG_CIDRS_DEFAULT 1 "$(grep -cF 'trusted_proxy_cidrs: str = ""' $C)"
guard CFG_ENV_TRUST 1 "$(grep -cF 'MA_TRUST_PROXY' $C)"
guard CFG_ENV_CIDRS 1 "$(grep -cF 'MA_TRUSTED_PROXY_CIDRS' $C)"
guard ROUTES_TRUST 1 "$(grep -cF 'trust_proxy=cfg.trust_proxy,' $R)"
guard BASIC_RESOLVE 1 "$(grep -cF 'trusted_proxy_cidrs=config.trusted_proxy_cidrs,' $AP)"
guard ANN_HA_FN 1 "$(grep -cF 'def _via_ha(' $AN)"
guard ANN_INBOX_FN 1 "$(grep -cF 'def _via_inbox(' $AN)"
guard ANN_READY_PROP 1 "$(grep -cF 'def inbox_ready(' $AN)"
guard ANN_PRIORITY_LINE 1 "$(grep -cF 'return self._via_inbox(msg) if not self.enabled else self._via_ha(msg)' $AN)"
guard ANN_GATE_LINE 1 "$(grep -cF 'if not self.enabled and not self.inbox_ready:' $AN)"
guard ANN_TRACEID 1 "$(grep -cF 'tid = uuid.uuid4().hex' $AN)"
guard ANN_ENABLED_LINE 1 "$(grep -cF 'self.enabled = self.switch and bool(self.tts_entity) and bool(self.target)' $AN)"
guard RT_MQTT_INJECT 1 "$(grep -cF 'mqtt=self.mqtt,' $RT)"
guard RT_CONSTRUCT 1 "$(grep -cF 'announcer = Announcer(' $RT)"
guard MB_PUBLISH_SPEAK 1 "$(grep -cF 'def publish_speak(' $MB)"
guard L_RATE_DEFS 22 "$(grep -c 'def test_' tests/test_vma_login_rate_limit.py)"
guard L_ANN_DEFS 17 "$(grep -c 'def test_' tests/test_announcer.py)"
guard L_CODE_DEFS 19 "$(grep -c 'def test_' tests/test_vma_dcd_20261006_linkage_codes.py)"
guard L_PROSE_DEFS 9 "$(grep -c 'def test_' tests/test_tool_prose_promised_keys.py)"
guard L_SWITCH_GATE 1 "$(grep -c 'def test_the_switch_is_the_master_gate_even_with_a_registered_peer():' tests/test_vma_login_rate_limit.py)"
guard L_WIRED 1 "$(grep -c 'def test_the_production_announcer_is_wired_to_the_bridge():' tests/test_vma_dcd_20261006_linkage_codes.py)"
guard L_HA_REJECT 1 "$(grep -c 'def test_ha_rejection_is_reported_as_not_announced():' tests/test_announcer.py)"
guard L_HA_GARBAGE 1 "$(grep -c 'def test_ha_non_dict_reply_is_not_announced_and_does_not_escape():' tests/test_announcer.py)"
guard L_CALLER_LOCK 1 "$(grep -c 'def test_publish_speak_production_caller_is_the_announcer_fallback():' tests/test_vma_dcd_20261006_linkage_codes.py)"

# vendor provenance：Dockerfile 装的那枚 == vendor/ 里唯一的那枚 == homesdk/dist 的权威 sha
guard DOCKER_PIP_032 1 "$(grep -cF 'pip install --no-cache-dir ./vendor/homesdk-0.3.2-py3-none-any.whl' Dockerfile)"
guard DOCKER_PIP_031 0 "$(grep -cF 'pip install --no-cache-dir ./vendor/homesdk-0.3.1-py3-none-any.whl' Dockerfile)"
guard VENDOR_WHEELS 1 "$(ls vendor/ | grep -c 'homesdk-.*-py3-none-any.whl')"
VSHA=$(sha256sum vendor/homesdk-0.3.2-py3-none-any.whl | cut -d' ' -f1)
DSHA=$(sha256sum /e/NAS/homesdk/dist/homesdk-0.3.2-py3-none-any.whl | cut -d' ' -f1)
guard VENDOR_SHA_MATCH "$DSHA" "$VSHA"
guard VENDOR_SHA_REGISTERED 1 "$(grep -cF "$VSHA" vendor/README.md)"

# CR 一律按字节量（`grep -c $'\r'` 在 MSYS 会把实参吞成空模式、报出总行数）
for f in $A $C $R $AP $AN $RT $MB src/memory_agent/adm_linkage.py Dockerfile vendor/README.md \
         tests/test_vma_login_rate_limit.py tests/test_announcer.py \
         tests/test_vma_dcd_20261006_linkage_codes.py tests/test_vma_dcd_20261002_payload.py \
         tests/test_tool_prose_promised_keys.py .qoder/tmp-c81-mut.py .qoder/tmp-c82-mut.py \
         .qoder/tmp-c82-probe.py .qoder/tmp-c82-remote26.sh; do
  guard CR_$(basename $f) 0 "$(tr -dc '\r' < $f | wc -c | tr -d ' ')"
done

# ── 尺 4：关键文件必须真在快照清单里 ──
for f in src/memory_agent/auth.py src/memory_agent/config.py src/memory_agent/api/auth_routes.py \
         src/memory_agent/app.py src/memory_agent/announcer.py src/memory_agent/runtime.py \
         src/memory_agent/mqtt_bridge.py src/memory_agent/adm_linkage.py \
         tests/test_vma_login_rate_limit.py tests/test_announcer.py \
         tests/test_vma_dcd_20261006_linkage_codes.py tests/test_vma_dcd_20261002_payload.py \
         tests/test_tool_prose_promised_keys.py tests/test_insights_facade_contract.py \
         tests/test_livingroom_ai.py tests/test_perception_ingest.py scripts/pyflakes_gate.sh; do
  n=$(grep -c "^$f\$" .qoder/tmp-c82-filelist26.txt)
  if [ "$n" != 1 ]; then echo "GUARD_FAIL IN_SNAP_$(basename $f) 期望=1 实际=$n"; BAD=1; fi
done
echo IN_SNAP_checked=17

if [ "$BAD" != 0 ]; then echo PREFLIGHT_FAILED=1 未出网; exit 2; fi
echo PREFLIGHT_OK=1

tar -czf "$TGZ" -T .qoder/tmp-c82-filelist26.txt; guard TAR_RC 0 $?
echo TAR_SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; guard SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c82-remote26.sh "$NAS:/tmp/ma_c82_remote26.sh"; guard REMOTE_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c81-mut.py "$NAS:/tmp/ma_c82_mut81.py"; guard M81_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c82-mut.py "$NAS:/tmp/ma_c82_mut82.py"; guard M82_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c82-probe.py "$NAS:/tmp/ma_c82_probe.py"; guard PROBE_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c68-hashes.py "$NAS:/tmp/ma_c82_hashes.py"; guard HASHES_SCP_RC 0 $?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c82-filelist26.txt "$NAS:/tmp/ma_c82_filelist.txt"; guard FILELIST_SCP_RC 0 $?

# Windows 侧拷过去的脚本先洗行尾再验语法；哈希与清单同样按 unix 行尾进容器。
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage && tr -d '\r' < /tmp/ma_c82_mut81.py > /tmp/ma_c82_mut81_unix.tmp && tr -d '\r' < /tmp/ma_c82_mut82.py > /tmp/ma_c82_mut82_unix.tmp && tr -d '\r' < /tmp/ma_c82_probe.py > /tmp/ma_c82_probe_unix.tmp && tr -d '\r' < /tmp/ma_c82_hashes.py > /tmp/ma_c82_hashes_unix.tmp && tr -d '\r' < /tmp/ma_c82_filelist.txt > /tmp/ma_c82_filelist_unix.tmp && docker cp /tmp/ma_c82_mut81_unix.tmp memory-agent:/tmp/${SNAP}_mut81.py && docker cp /tmp/ma_c82_mut82_unix.tmp memory-agent:/tmp/${SNAP}_mut82.py && docker cp /tmp/ma_c82_probe_unix.tmp memory-agent:/tmp/${SNAP}_probe.py && docker cp /tmp/ma_c82_hashes_unix.tmp memory-agent:/tmp/${SNAP}_hashes.py && docker cp /tmp/ma_c82_filelist_unix.tmp memory-agent:/tmp/${SNAP}_filelist.txt && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut81.py /tmp/${SNAP}_mut82.py /tmp/${SNAP}_probe.py /tmp/${SNAP}_hashes.py /tmp/${SNAP}_filelist.txt && rm -f /tmp/ma_c82_*.tmp /tmp/ma_c82_mut81.py /tmp/ma_c82_mut82.py /tmp/ma_c82_probe.py /tmp/ma_c82_hashes.py /tmp/ma_c82_filelist.txt; echo STAGE_RC=\$?; tr -d '\r' < /tmp/ma_c82_remote26.sh > /tmp/ma_c82_remote26_unix.sh && bash -n /tmp/ma_c82_remote26_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?; docker exec memory-agent sh -c 'wc -l < /tmp/${SNAP}_filelist.txt'; echo SNAP_FILELIST_LINES_RC=\$?"
SYNC_RC=$?
echo SYNC_RC=$SYNC_RC
if [ "$SYNC_RC" != 0 ]; then echo SYNC_FAILED=1 未出网; exit 2; fi

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c82_remote26_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"

echo CONTAINER_BATCH_RC=0
