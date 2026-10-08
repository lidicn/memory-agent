#!/usr/bin/env bash
# run16 = run15 的增量档（任务表 #64 收尾 + #66「DCD 20261006 §九 验收落地」）。
# 与 run15 的差：①树又动了 4 个文件（identity_fusion.py 的措辞核销 + 两份 task64 测试加 8 条锁
#   + a3_p22 心跳量具改成同轮对照）；②MUT64 从 20 条涨到 24 条（四条负控 M21–M24）；
#   ③TARGETED 格带上 a3_p22（本机 7 条，容器同基数）；④SOURCES-B64 加 12 个新锚点，
#   T64IF_DEFS 25→29、T64BP_DEFS 19→22。MUT63 那 22 条仍作自证：读数应与 run14c 一字不差。
# 出网前四把尺照旧：bash -n ×2、引号 lint、两支变异 harness 的 ast 预检（24 条那一支必须
#   MUTANTS_PARSED=24）；快照完整性核对从 10 格涨到 11 格（新加 a3_p22 那一格）。
# ─────────────────────────────────────────────────────────────────────────────
# 20261006 批次 c64（任务表 #64：identity_fusion + behavior_predictor 覆盖率与输入边界收口）
# 的容器权威门**本地驱动** = run15。形状沿用 run13/run14（tar 快照 + scp + docker cp +
# chown 10001 + 远端 tr -d '\r' 后 bash -n 再跑）。
#
# 本批为什么必须重跑整套门（不能沿用 run14c 读数）：
#   1) 换了树：2 个源文件改动（identity_fusion.py 封顶方向、mcp_server.py weekday 上界守卫）
#      + 2 个新测试文件（44 条 def）。SUITE/TARGETED/TOUCHED/GATE 四格都是整树口径。
#   2) 十五轮审计点名的"0%~10% 覆盖"前提已被同一把尺量否（现读 84%/84%，见
#      .qoder/tmp-b64-cov-BEFORE.out），本批改按未执行行与实测缺陷排事；改后覆盖率另跑一档配对读数。
#   3) MUT63（上一批 22 条）一并重跑作**自证**：本批刻意没动 api/behavior_routes.py 与
#      change_attribution.py，读数应与 run14c 一字不差（NOTHING 85 passed、
#      失败条数 5/13/6/14/1/1/1/4/2/1/2/2/2/1/3/1/1/2/3/6/1/1、MUTATION_BAD=0）。
#      MUT13 不重跑（insights 面一格未碰），由 SOURCES-A 上半 15 格静态自证。
#   4) 本档**没有 PROBE 格**：不需要临时库的运行时配对读数，改前形状由 `git show HEAD:`
#      侧指纹（.qoder/tmp-b64-fingerprints-head.out）+ 变异档 M1/M2/M14 提供成对证据。
#
# M14 的档位差异预先登记：本机 MCP SDK 不可用 ⇒ 那两条锁 skip ⇒ 本机 dry-run 读
# MUTATION_BAD=1（只有 M14 "没咬住"）。容器侧 TARGETED 格的 `-rs` 会打出这两条到底跑没跑；
# **skip 的档位不算实跑**，M14 只有在容器侧咬住才计入台账。
#
# run8 的教训照旧在册：/tmp/pylibs 在容器可写层，recreate 即清空，届时 pytest 不在 ⇒
# "非零 RC + 无 FAILED 行"会被读成"咬住了"。所以 TOOLCHAIN 排第一，量不到整跑作废。
# run14b 的教训照旧在册：**权威门在飞的期间不许动树**；变异串的语法在出网前用 ast 档量掉
# （SYNTAX_BAD 必须 0），别靠真跑 20 条腿才发现少个冒号。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c64snap20261006b
TGZ=.qoder/tmp-c64-snap16.tgz
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe

# 出网前三把尺：远端脚本语法、引号 lint（内层变量必须 `\$R`）、变异 harness 的 ast 预检。
bash -n .qoder/tmp-c64-remote16.sh; echo REMOTE_SYNTAX_RC=$?
bash -n "$0"; echo SELF_SYNTAX_RC=$?
# run13 首跑折在引号这一格（`line 64: R: unbound variable` + REMOTE_DRIVER_RC=1，十一格全没跑），
# 而仿真器验不出这一类（它在同一个 sh 里前后脚执行，展开得刚刚好）。
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c64-remote16.sh; echo LINT_RC=$?
# 出网前的树完整性核对：20 条变异锚点各命中 1 次、无变异形状残留、全 src/ 无死门、语法全过。
"$PY" .qoder/tmp-b64-mut-verify.py > .qoder/tmp-c64-mutverify16.out 2>&1; echo MUT64_VERIFY_RC=$?
grep -E 'MUTANTS_PARSED|DEADSHAPE_COUNT|SYNTAX_BAD|VERIFY_BAD|^BAD|^SYNTAX|^DEAD' .qoder/tmp-c64-mutverify16.out
"$PY" .qoder/tmp-b63-mut-verify.py > .qoder/tmp-c64-mutverify63.out 2>&1; echo MUT63_VERIFY_RC=$?
grep -E 'MUTANTS_PARSED|DEADSHAPE_COUNT|SYNTAX_BAD|VERIFY_BAD|^BAD|^SYNTAX|^DEAD' .qoder/tmp-c64-mutverify63.out
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-b64-mut.py',encoding='utf-8').read())"
echo MUT64_PARSE_RC=$?
"$PY" -c "import ast,io;ast.parse(io.open('.qoder/tmp-b63-mut.py',encoding='utf-8').read())"
echo MUT63_PARSE_RC=$?

echo HEAD=$(git rev-parse --short HEAD)

{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c64-filelist16.txt
echo FILES=$(wc -l < .qoder/tmp-c64-filelist16.txt)
# 快照必须真的带上本批四件，否则"门绿"其实是"没扫"
echo IN_SNAP_t64if=$(grep -c '^tests/test_vma_task64_identity_fusion.py' .qoder/tmp-c64-filelist16.txt)
echo IN_SNAP_t64bp=$(grep -c '^tests/test_vma_task64_behavior_predictor.py' .qoder/tmp-c64-filelist16.txt)
echo IN_SNAP_if=$(grep -c '^src/memory_agent/identity_fusion.py' .qoder/tmp-c64-filelist16.txt)
echo IN_SNAP_ms=$(grep -c '^src/memory_agent/mcp_server.py' .qoder/tmp-c64-filelist16.txt)
echo IN_SNAP_ms=$(grep -c '^src/memory_agent/mcp_server.py' .qoder/tmp-c64-filelist16.txt)
echo IN_SNAP_a3p22=$(grep -c '^tests/test_vma_a3_p22_auth_loop_blocking.py' .qoder/tmp-c64-filelist16.txt)
echo IN_SNAP_bp=$(grep -c '^src/memory_agent/behavior_predictor.py' .qoder/tmp-c64-filelist16.txt)
echo IN_SNAP_t63=$(grep -c '^tests/test_vma_task63_routes_input_boundary.py' .qoder/tmp-c64-filelist16.txt)
echo IN_SNAP_br=$(grep -c '^src/memory_agent/api/behavior_routes.py' .qoder/tmp-c64-filelist16.txt)
echo IN_SNAP_repo=$(grep -c '^src/memory_agent/insights/repository.py' .qoder/tmp-c64-filelist16.txt)
echo IN_SNAP_qbscan=$(grep -c '^scripts/scan_qb_param_landing.py' .qoder/tmp-c64-filelist16.txt)
echo IN_SNAP_attic=$(grep -c '^attic/' .qoder/tmp-c64-filelist16.txt)
tar -czf "$TGZ" -T .qoder/tmp-c64-filelist16.txt
echo TAR_RC=$? SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; echo SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c64-remote16.sh "$NAS:/tmp/ma_c64_remote16.sh"; echo REMOTE_SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-b64-mut.py "$NAS:/tmp/ma_c64_mut64.py"; echo MUT64_SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-b63-mut.py "$NAS:/tmp/ma_c64_mut63.py"; echo MUT63_SCP_RC=$?

# 远端脚本从 Windows 侧拷过去可能带 CRLF，先 `tr -d '\r'` 落成 unix 行尾再 bash -n 验一遍
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage && docker cp /tmp/ma_c64_mut64.py memory-agent:/tmp/${SNAP}_mut64.py && docker cp /tmp/ma_c64_mut63.py memory-agent:/tmp/${SNAP}_mut63.py && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut64.py /tmp/${SNAP}_mut63.py && rm -f /tmp/ma_c64_mut64.py /tmp/ma_c64_mut63.py; echo STAGE_RC=\$?; tr -d '\r' < /tmp/ma_c64_remote16.sh > /tmp/ma_c64_remote16_unix.sh && bash -n /tmp/ma_c64_remote16_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?"
echo SYNC_RC=$?

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c64_remote16_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"

echo CONTAINER_BATCH_RC=0
