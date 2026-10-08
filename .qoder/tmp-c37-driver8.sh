#!/usr/bin/env bash
# 20261005 批次 c37（run8，DCD 20261005 §二.1 乙′ 分层扫描 + **夹紧 ts 半开区间**改法）
# 容器权威门的**本地驱动**。形状沿用 run7（两层引号 + tar 快照 + docker cp + chown 10001）。
#
# 为什么 run7 不算收口（读数不能沿用）：
#   run7 的 PROBE 给出「分层有效但慢 25.82×」（743.2ms → 19186.8ms / 30 天窗），
#   根因是小时分支里同时带 `day = ?` 与整窗 `ts BETWEEN`，规划器在两种坏形状之间选：
#   `day=?` ⇒ 每格重扫整天 ×24（408ms/天）；带整窗 ⇒ 走 idx_events_ts 把范围撑回整窗
#   （6109ms/天）。本批改成分支只带**夹紧后的** `ts >= ? AND ts < ?`（18.5ms/天，
#   三者返回同一批行）。这是新的读路径 ⇒ 整套门 + 生产成对读数必须在同一棵树上重跑。
#   生产库那三档耗时是**本机没有**的证据（本机无生产数据），只有这一格能给。
#   另：#59/#60/#61 三个提交在 run7 之后落库，快照口径也换到当前工作树。
# 期望读数由本机同一组 grep 先量出来（SOURCES 对不上=快照带的是旧代码，一切读数作废）：
#   SCAN_DAY=3 HOUR_BUCKET=3 OFFSET_BIND=1 TS_GE=1 SCAN_HOURS=4 WINDOW_HI=5
#   SUBSTR_WHOLE=7 SUBSTR_IN_HOUR_STMT=0 BRANCH_FROM_FILT=1 BRANCH_FROM_WHERE=0
#   HOUR_STMT_LINES=32 TEST_DEFS=13 PROBE_A5=1  wc=919/893/1340/554/216
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c37snap20261005b
TGZ=.qoder/tmp-c37-snap8.tgz

bash -n .qoder/tmp-c37-remote.sh; echo REMOTE_SYNTAX_RC=$?
bash -n .qoder/tmp-c37-driver8.sh; echo SELF_SYNTAX_RC=$?

echo HEAD=$(git rev-parse --short HEAD)

{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c37-filelist8.txt
echo FILES=$(wc -l < .qoder/tmp-c37-filelist8.txt)
echo IN_SNAP_attic=$(grep -c '^attic/' .qoder/tmp-c37-filelist8.txt)
echo IN_SNAP_repo=$(grep -c '^src/memory_agent/insights/repository.py' .qoder/tmp-c37-filelist8.txt)
echo IN_SNAP_q62tests=$(grep -c 'test_vma_q62_daily_batch_scan.py' .qoder/tmp-c37-filelist8.txt)
echo IN_SNAP_probe=$(grep -c 'probe_q62_q2_readings.py' .qoder/tmp-c37-filelist8.txt)
tar -czf "$TGZ" -T .qoder/tmp-c37-filelist8.txt
echo TAR_RC=$? SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; echo SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c37-remote.sh "$NAS:/tmp/ma_c37_remote8.sh"; echo REMOTE_SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c37-mutate.py "$NAS:/tmp/ma_c37_mut8.py"; echo MUTSCP_RC=$?

# 远端脚本从 Windows 侧拷过去可能带 CRLF，先 `tr -d '\r'` 落成 unix 行尾再 bash -n 验一遍
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage && docker cp /tmp/ma_c37_mut8.py memory-agent:/tmp/${SNAP}_mut.py && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut.py && rm -f /tmp/ma_c37_mut8.py; echo STAGE_RC=\$?; tr -d '\r' < /tmp/ma_c37_remote8.sh > /tmp/ma_c37_remote8_unix.sh && bash -n /tmp/ma_c37_remote8_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?"
echo SYNC_RC=$?

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c37_remote8_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"

echo CONTAINER_BATCH_RC=0
