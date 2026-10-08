#!/usr/bin/env bash
# 20261006 批次 c63 run14（任务表 #63：behavior_routes 输入边界 + 事件循环收口）的
# 容器权威门**本地驱动**。形状沿用 run13（两层引号 + tar 快照 + docker cp + chown 10001 +
# 远端 tr -d '\r' 后 bash -n）。
#
# 本批为什么必须重跑整套门（不能沿用 run13d 读数）：
#   1) 换了树：#62 出网后又落 2 个源文件改动（api/behavior_routes.py、change_attribution.py）
#      与 2 个测试文件（新增 task63 落点锁 + r5 量具 docstring 登记盲区）。
#      SUITE/TARGETED/TOUCHED/GATE 四格都是整树口径。
#   2) 本批 PROBE 格跑在**临时库**：静态结论证不了"心跳因此停 tick"，这一格出运行时成对读数。
#   3) MUT13（上一批 12 条）一并重跑，作为**自证**：本批刻意没动 insights/api|service|nlquery
#      与三支量具，读数应与 run13d 一字不差（失败条数 3/2/1/1/1/1/1/1/1/3/2/2）。
#      对不上就说明改动渗到了不该渗的地方。
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
SNAP=c63snap20261006a
TGZ=.qoder/tmp-c63-snap14.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe

bash -n .qoder/tmp-c63-remote14.sh; echo REMOTE_SYNTAX_RC=$?
bash -n .qoder/tmp-c63-driver14.sh; echo SELF_SYNTAX_RC=$?
# 出网前的引号 lint：`ex "…"` 里的内层变量必须写成 `\$R`，裸写会在 NAS 侧展开。
# run13 首跑就是折在这一格（`line 64: R: unbound variable` + REMOTE_DRIVER_RC=1，十一格全没跑），
# 而仿真器**验不出这一类**（它在同一个 sh 里前后脚执行，展开得刚刚好）。
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c63-remote14.sh; echo LINT_RC=$?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-b63-mut.py',encoding='utf-8').read())"
echo MUT63_PARSE_RC=$?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c38-probe-br.py',encoding='utf-8').read())"
echo PROBE_PARSE_RC=$?

echo HEAD=$(git rev-parse --short HEAD)

{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c63-filelist14.txt
echo FILES=$(wc -l < .qoder/tmp-c63-filelist14.txt)
# 快照必须真的带上本批四件，否则"门绿"其实是"没扫"
echo IN_SNAP_t63=$(grep -c '^tests/test_vma_task63_routes_input_boundary.py' .qoder/tmp-c63-filelist14.txt)
echo IN_SNAP_r5=$(grep -c '^tests/test_vma_r5_event_loop_offload.py' .qoder/tmp-c63-filelist14.txt)
echo IN_SNAP_br=$(grep -c '^src/memory_agent/api/behavior_routes.py' .qoder/tmp-c63-filelist14.txt)
echo IN_SNAP_ca=$(grep -c '^src/memory_agent/change_attribution.py' .qoder/tmp-c63-filelist14.txt)
echo IN_SNAP_repo=$(grep -c '^src/memory_agent/insights/repository.py' .qoder/tmp-c63-filelist14.txt)
echo IN_SNAP_qbscan=$(grep -c '^scripts/scan_qb_param_landing.py' .qoder/tmp-c63-filelist14.txt)
echo IN_SNAP_attic=$(grep -c '^attic/' .qoder/tmp-c63-filelist14.txt)
tar -czf "$TGZ" -T .qoder/tmp-c63-filelist14.txt
echo TAR_RC=$? SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; echo SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c63-remote14.sh "$NAS:/tmp/ma_c63_remote14.sh"; echo REMOTE_SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-b63-mut.py "$NAS:/tmp/ma_c63_mut63.py"; echo MUT63_SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c37-mutate13.py "$NAS:/tmp/ma_c63_mut13.py"; echo MUT13_SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c38-probe-br.py "$NAS:/tmp/ma_c63_probe.py"; echo PROBE_SCP_RC=$?

# 远端脚本从 Windows 侧拷过去可能带 CRLF，先 `tr -d '\r'` 落成 unix 行尾再 bash -n 验一遍
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage && docker cp /tmp/ma_c63_mut63.py memory-agent:/tmp/${SNAP}_mut63.py && docker cp /tmp/ma_c63_mut13.py memory-agent:/tmp/${SNAP}_mut13.py && docker cp /tmp/ma_c63_probe.py memory-agent:/tmp/${SNAP}_probe.py && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut63.py /tmp/${SNAP}_mut13.py /tmp/${SNAP}_probe.py && rm -f /tmp/ma_c63_mut63.py /tmp/ma_c63_mut13.py /tmp/ma_c63_probe.py; echo STAGE_RC=\$?; tr -d '\r' < /tmp/ma_c63_remote14.sh > /tmp/ma_c63_remote14_unix.sh && bash -n /tmp/ma_c63_remote14_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?"
echo SYNC_RC=$?

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c63_remote14_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"

echo CONTAINER_BATCH_RC=0
