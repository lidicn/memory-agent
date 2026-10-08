#!/usr/bin/env bash
# 20261005 批次 c37（run7，DCD 20261005 §二.1 乙′：日配额按天分批改「按天×按小时分层」）
# 容器权威门的**本地驱动**。形状沿用 run6（两层引号 + tar 快照 + docker cp + chown 10001）。
#
# 本批形状（决定读数不能沿用 run6）：
#   1) 只改读路径：repository.py（分层扫描）+ api.py/service.py（判据口径注释）
#      + test_vma_q62_daily_batch_scan.py（8→12 条锁）+ probe_q62_q2_readings.py（加 A5）。
#      没有搬家、没有新增未跟踪测试文件；attic 仍要进快照（p32/p21 的形状锁靠它才不 skip）。
#   2) 变异自咬从 5 条长到 7 条（新增 M6 闭区间上界、M7 补读 OFFSET 恒 0），
#      本机已用 Python313 跑过一遍全咬住；容器 3.11 口径要在 run7 再跑一遍才算权威。
#   3) 多一格 PROBE：约束② 的改前/改后成对读数只能在生产库上量（本机无生产数据），
#      判据①（夜间 20:00–23:00 必须有返回）与判据②（每日可见时段表）都出自这一格。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c37snap20261005a
TGZ=.qoder/tmp-c37-snap.tgz

bash -n .qoder/tmp-c37-remote.sh; echo REMOTE_SYNTAX_RC=$?

echo HEAD=$(git rev-parse --short HEAD)

{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c37-filelist.txt
echo FILES=$(wc -l < .qoder/tmp-c37-filelist.txt)
echo IN_SNAP_attic=$(grep -c '^attic/' .qoder/tmp-c37-filelist.txt)
echo IN_SNAP_repo=$(grep -c '^src/memory_agent/insights/repository.py' .qoder/tmp-c37-filelist.txt)
echo IN_SNAP_q62tests=$(grep -c 'test_vma_q62_daily_batch_scan.py' .qoder/tmp-c37-filelist.txt)
echo IN_SNAP_probe=$(grep -c 'probe_q62_q2_readings.py' .qoder/tmp-c37-filelist.txt)
tar -czf "$TGZ" -T .qoder/tmp-c37-filelist.txt
echo TAR_RC=$? SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; echo SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c37-remote.sh "$NAS:/tmp/ma_c37_remote.sh"; echo REMOTE_SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c37-mutate.py "$NAS:/tmp/ma_c37_mut.py"; echo MUTSCP_RC=$?

# 远端脚本从 Windows 侧拷过去可能带 CRLF，先 `tr -d '\r'` 落成 unix 行尾再 bash -n 验一遍
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage && docker cp /tmp/ma_c37_mut.py memory-agent:/tmp/${SNAP}_mut.py && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut.py && rm -f /tmp/ma_c37_mut.py; echo STAGE_RC=\$?; tr -d '\r' < /tmp/ma_c37_remote.sh > /tmp/ma_c37_remote_unix.sh && bash -n /tmp/ma_c37_remote_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?"
echo SYNC_RC=$?

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c37_remote_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"

echo CONTAINER_BATCH_RC=0
