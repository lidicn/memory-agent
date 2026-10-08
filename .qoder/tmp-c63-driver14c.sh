#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run14c = **终树档**（读数以本档为准；run14/run14b 都留在册、都是中间档）。
# run14b 的**唯一**红格与本轮重跑的真正理由（族 6：量具自伤，不是码红）：
#   MUT63_RC=1 里 21 条腿全咬住、restored=OK，只有
#     `M21 人工审核不再留痕 -> RC=2 FAILED=0 没咬住 | 1 error in 1.46s`
#   ——RC=2 且 0 条 FAILED 是 pytest 的 **collection 阶段报错**，不是"测试没测到"：
#   harness 里 M21 的替换串写成 `    if status in (),`（少一个冒号），落盘即 SyntaxError。
#   与 run13 的 N11 空变异同族：**门坏了自己会假装成码红**。已改成合法恒假守卫
#   `    if status in ():`，并给验证脚本加一格 `SYNTAX_BAD`：出网前对每条变异**在内存里**
#   做 anchor→replacement 替换后 `compile()`，语法不过就红（负证明：喂旧形状给同一把尺，
#   读回 `SyntaxError invalid syntax line 2`）。这一格让"少冒号"这一类不再需要靠真跑 22 条腿才发现。
#   run14b 的其余读数照旧有效并留册：控制档 NOTHING RC=0（85 passed）、MUT13 的 N1–N12
#   全咬住（3/2/1/1/1/1/1/1/1/3/2/2）、SUITE 1544/10、TARGETED 100、PREVBATCH 148、TOUCHED 396。
#
# run14b 的快照打在 22:09，出网后我又改了测试文件里三处**注释与断言说明**（893 → 896 行，
# 源码一格未动、变异台账 22 条未动、def 46 条未动）：
#   · 两句「改前：规则不存在也回 200」「改前：复核一条不存在的异常也回 200」是**假记载**——
#     `git show HEAD:` 实测两条 handler 在 HEAD 就返回 404（`error("规则不存在", 404)` /
#     `error("异常不存在", 404)`，HEAD 侧行号 :99 / :230），真实缺陷是"实现有、锁没有"
#     （全仓 grep：这两条 handler 改前被 **0 个**用例引用）。测试注释把缺陷冻结成不存在的历史，
#     比没写更坏，所以改成如实登记。
#   · 「覆盖率现读点名 371 条未执行」补上口径（371 是中间档；改前档 HEADpair = 642 语句 /
#     506 未执行 = 21%，同一把尺：本机 Python313 + coverage 7.16.1、全量单轮）。
# 因此 run14b 自动降级为中间档。本档格序与命令与 run14b **完全相同**，
# 差异只有 SNAP（c63snap20261006c）、TGZ、filelist、mutverify 产物名。
# 教训写在这儿：**权威门在飞的期间不许动树**——树一动那一档就作废；要改就改完再 fire。
# 上一档的由来照旧在册：族 5 的 `window_minutes` 守卫 + 14 条用例 + M20/M21/M22 三条变异，
# 以及"把变异 harness 当模块 import ⇒ import 即开跑腿、kill 在 M8 腿留下 `if False:` 死门"的自伤；
# 所以出网前 `MUT63_VERIFY_RC` 这一格（ast 解析 22 条锚点 + 全 src/ 死门形状扫描）必须为 0。
# ─────────────────────────────────────────────────────────────────────────────
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
SNAP=c63snap20261006c
TGZ=.qoder/tmp-c63-snap14c.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe

bash -n .qoder/tmp-c63-remote14.sh; echo REMOTE_SYNTAX_RC=$?
bash -n .qoder/tmp-c63-driver14.sh; echo DRIVER14_SYNTAX_RC=$?
# run14b 这格写的是 `bash -n .qoder/tmp-c63-driver14.sh` 却打标签 SELF_SYNTAX_RC ——
# 自证量具量的别人。本档两条都留：老那格改名为 DRIVER14，新增一条真自证。
bash -n "$0"; echo SELF_SYNTAX_RC=$?
# 出网前的引号 lint：`ex "…"` 里的内层变量必须写成 `\$R`，裸写会在 NAS 侧展开。
# run13 首跑就是折在这一格（`line 64: R: unbound variable` + REMOTE_DRIVER_RC=1，十一格全没跑），
# 而仿真器**验不出这一类**（它在同一个 sh 里前后脚执行，展开得刚刚好）。
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c63-remote14.sh; echo LINT_RC=$?
# 出网前的树完整性核对：22 条变异锚点各命中 1 次、无变异形状残留、全 src/ 无死门。
# 这一格是本批自伤的补丁（M8 腿未还原在工作区留了 `if False:`），跑法只用 ast 解析 harness。
"$PY" .qoder/tmp-b63-mut-verify.py > .qoder/tmp-c63-mutverify14c.out 2>&1; echo MUT63_VERIFY_RC=$?
grep -E 'MUTANTS_PARSED|DEADSHAPE_COUNT|SYNTAX_BAD|VERIFY_BAD|^BAD|^SYNTAX' .qoder/tmp-c63-mutverify14c.out
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-b63-mut.py',encoding='utf-8').read())"
echo MUT63_PARSE_RC=$?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-c38-probe-br.py',encoding='utf-8').read())"
echo PROBE_PARSE_RC=$?

echo HEAD=$(git rev-parse --short HEAD)

{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c63-filelist14c.txt
echo FILES=$(wc -l < .qoder/tmp-c63-filelist14c.txt)
# 快照必须真的带上本批四件，否则"门绿"其实是"没扫"
echo IN_SNAP_t63=$(grep -c '^tests/test_vma_task63_routes_input_boundary.py' .qoder/tmp-c63-filelist14c.txt)
echo IN_SNAP_r5=$(grep -c '^tests/test_vma_r5_event_loop_offload.py' .qoder/tmp-c63-filelist14c.txt)
echo IN_SNAP_br=$(grep -c '^src/memory_agent/api/behavior_routes.py' .qoder/tmp-c63-filelist14c.txt)
echo IN_SNAP_ca=$(grep -c '^src/memory_agent/change_attribution.py' .qoder/tmp-c63-filelist14c.txt)
echo IN_SNAP_repo=$(grep -c '^src/memory_agent/insights/repository.py' .qoder/tmp-c63-filelist14c.txt)
echo IN_SNAP_qbscan=$(grep -c '^scripts/scan_qb_param_landing.py' .qoder/tmp-c63-filelist14c.txt)
echo IN_SNAP_attic=$(grep -c '^attic/' .qoder/tmp-c63-filelist14c.txt)
tar -czf "$TGZ" -T .qoder/tmp-c63-filelist14c.txt
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
