#!/usr/bin/env bash
# 20261006 批次 c37 run13（任务表 #62：Q-B 参数级落点收口）的容器权威门**本地驱动**。
# 形状沿用 run11（两层引号 + tar 快照 + docker cp + chown 10001 + 远端 tr -d '\r' 后 bash -n）。
#
# 本批为什么必须重跑整套门（不能沿用 run11 读数）：
#   1) 换了树：#61 出网后又落本批 6 个文件改动（api/service/nlquery + 两支量具）
#      与 2 个测试文件（qb 落点锁 9 条 + callsite 深链 2 条）。
#      SUITE/TARGETED/TOUCHED/GATE 四格都是整树口径。
#   2) 本批**新增两格**：PROBEANOM（生产库成对读数——静态结论证不了"读数因此变了"）
#      与 QBLAND（量具 --self-test + --strict）。
#   3) MUT11（8 条）/MUT10（10 条）一并重跑，作为**自证**：本批刻意没动 intent/predictor/
#      utils/attic，也没动 insights 的读路径（repository.py 一字未动），读数应与 run11/run10
#      一字不差（M1..M10 = 5/6/5/8/3/2/4/1/1/24）。对不上就说明改动渗到了不该渗的地方。
#
# run8 的教训照旧在册：容器 recreate 会清空 /tmp/pylibs（可写层），届时三条门格会读成
# `No module named`、变异会被当成"咬住了"（MUTATION_BAD=10 戳穿过一次）。所以 TOOLCHAIN 格
# 排第一，量不到就整跑作废、先按交接单口径重建 /tmp/pylibs 再重跑。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c37snap20261006d
TGZ=.qoder/tmp-c37-snap13d.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe

bash -n .qoder/tmp-c37-remote13.sh; echo REMOTE_SYNTAX_RC=$?
bash -n .qoder/tmp-c37-driver13.sh; echo SELF_SYNTAX_RC=$?
# 出网前的引号 lint：`ex "…"` 里的内层变量必须写成 `\$R`，裸写会在 NAS 侧展开。
# run13 首跑就是折在这一格（`line 64: R: unbound variable` + REMOTE_DRIVER_RC=1，
# 十一格全没跑），而仿真器**验不出这一类**（它在同一个 sh 里前后脚执行，展开得刚刚好）。
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c37-remote13.sh; echo LINT_RC=$?
"$PY" .qoder/tmp-c37-emu13.py > .qoder/tmp13emu.log 2>&1; echo EMU_DRIVER_RC=$?; grep -E '^(EMU_RC|LINE_HITS|TAIL_OK|REMAIN_BS_DOLLAR)=' .qoder/tmp13emu.log
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c37-mutate13.py',encoding='utf-8').read())"
echo MUT13_PARSE_RC=$?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c37-probe-anom13.py',encoding='utf-8').read())"
echo PROBE_PARSE_RC=$?

echo HEAD=$(git rev-parse --short HEAD)

{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c37-filelist13.txt
echo FILES=$(wc -l < .qoder/tmp-c37-filelist13.txt)
# 快照必须真的带上本批五件，否则"门绿"其实是"没扫"
echo IN_SNAP_qbtest=$(grep -c '^tests/test_vma_qb_param_landing.py' .qoder/tmp-c37-filelist13.txt)
echo IN_SNAP_qbscan=$(grep -c '^scripts/scan_qb_param_landing.py' .qoder/tmp-c37-filelist13.txt)
echo IN_SNAP_api=$(grep -c '^src/memory_agent/insights/api.py' .qoder/tmp-c37-filelist13.txt)
echo IN_SNAP_svc=$(grep -c '^src/memory_agent/insights/service.py' .qoder/tmp-c37-filelist13.txt)
echo IN_SNAP_nl=$(grep -c '^src/memory_agent/insights/nlquery.py' .qoder/tmp-c37-filelist13.txt)
echo IN_SNAP_repo=$(grep -c '^src/memory_agent/insights/repository.py' .qoder/tmp-c37-filelist13.txt)
echo IN_SNAP_engscan=$(grep -c '^scripts/scan_insights_engine_attrs.py' .qoder/tmp-c37-filelist13.txt)
echo IN_SNAP_callsite=$(grep -c '^tests/test_vma_insights_callsite_binding.py' .qoder/tmp-c37-filelist13.txt)
echo IN_SNAP_attic=$(grep -c '^attic/' .qoder/tmp-c37-filelist13.txt)
tar -czf "$TGZ" -T .qoder/tmp-c37-filelist13.txt
echo TAR_RC=$? SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; echo SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c37-remote13.sh "$NAS:/tmp/ma_c37_remote13.sh"; echo REMOTE_SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c37-mutate.py "$NAS:/tmp/ma_c37_mut10.py"; echo MUT10_SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c37-mutate11.py "$NAS:/tmp/ma_c37_mut11.py"; echo MUT11_SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c37-mutate13.py "$NAS:/tmp/ma_c37_mut13.py"; echo MUT13_SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c37-probe-anom13.py "$NAS:/tmp/ma_c37_probe_anom.py"; echo PROBE_SCP_RC=$?

# 远端脚本从 Windows 侧拷过去可能带 CRLF，先 `tr -d '\r'` 落成 unix 行尾再 bash -n 验一遍
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage && docker cp /tmp/ma_c37_mut10.py memory-agent:/tmp/${SNAP}_mut.py && docker cp /tmp/ma_c37_mut11.py memory-agent:/tmp/${SNAP}_mut11.py && docker cp /tmp/ma_c37_mut13.py memory-agent:/tmp/${SNAP}_mut13.py && docker cp /tmp/ma_c37_probe_anom.py memory-agent:/tmp/${SNAP}_probe_anom.py && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut.py /tmp/${SNAP}_mut11.py /tmp/${SNAP}_mut13.py /tmp/${SNAP}_probe_anom.py && rm -f /tmp/ma_c37_mut10.py /tmp/ma_c37_mut11.py /tmp/ma_c37_mut13.py /tmp/ma_c37_probe_anom.py; echo STAGE_RC=\$?; tr -d '\r' < /tmp/ma_c37_remote13.sh > /tmp/ma_c37_remote13_unix.sh && bash -n /tmp/ma_c37_remote13_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?"
echo SYNC_RC=$?

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c37_remote13_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"

echo CONTAINER_BATCH_RC=0
