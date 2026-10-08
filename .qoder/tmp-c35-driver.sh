#!/usr/bin/env bash
# 20261005 批次 c35（run6，DCD 20261005 §二.2 四件落码）：容器权威门的**本地驱动**。
# 本批形状（决定读数不可沿用 run5）：
#   1) 交付面搬家：8 个 learning_* 从 src/memory_agent 移到 attic/learning（Q2=乙）。
#      attic 必须**进快照**（p32 的存档形状锁、p21 的 zip 搬家守恒锁都靠它才不 skip），
#      但**不进任何门禁口径**（pyflakes 门只扫 src/memory_agent，两个扫描量具参数写死 src）。
#   2) 新增一个测试文件（test_vma_dcd_20261005_rules.py），未跟踪，靠 ls-files --others 收到。
#   3) 路由挂载量具本批由红转绿（unmounted 4→0，SCAN_RC 1→0）：那条锁换了口径，
#      必须在新代码上真跑一次才算数。
#   4) homesdk 门禁的 fake-ok-const 在本批新代码上响过 3 条（manual_add/edit/withdraw 的
#      字面量 ok=True），已按仓内既有做法改成「回读定 ok」（对齐 service_tokens.py:240 的口径）；
#      这一格只有装得起 homesdk 的那侧跑得到，所以 run6 必须跑全量。
# 远端本体拆到 tmp-c35-remote.sh（两层引号，`bash -n` 当场可验）。上一版把引号塞到 5 层，
# 第一次跑就在 SOURCES/GATE 两格报 `syntax error near unexpected token '('`、读数全垃圾
# ——量具自己先踩一次假绿，踩完就拆（DCD 20261005 §四 判例二）。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c35snap20261005c
TGZ=.qoder/tmp-c35-snap.tgz

bash -n .qoder/tmp-c35-remote.sh; echo REMOTE_SYNTAX_RC=$?

echo HEAD=$(git rev-parse --short HEAD)

{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c35-filelist.txt
echo FILES=$(wc -l < .qoder/tmp-c35-filelist.txt)
echo IN_SNAP_attic=$(grep -c '^attic/' .qoder/tmp-c35-filelist.txt)
echo IN_SNAP_newtests=$(grep -c 'test_vma_dcd_20261005_rules.py' .qoder/tmp-c35-filelist.txt)
tar -czf "$TGZ" -T .qoder/tmp-c35-filelist.txt
echo TAR_RC=$? SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; echo SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c35-remote.sh "$NAS:/tmp/ma_c35_remote.sh"; echo REMOTE_SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c34-mut-rulings.py "$NAS:/tmp/ma_c35_mut.py"; echo MUTSCP_RC=$?

# 远端脚本从 Windows 侧拷过去可能带 CRLF，先 `tr -d '\r'` 落成 unix 行尾再 bash -n 验一遍
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage && docker cp /tmp/ma_c35_mut.py memory-agent:/tmp/${SNAP}_mut.py && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut.py && rm -f /tmp/ma_c35_mut.py; echo STAGE_RC=\$?; tr -d '\r' < /tmp/ma_c35_remote.sh > /tmp/ma_c35_remote_unix.sh && bash -n /tmp/ma_c35_remote_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?"
echo SYNC_RC=$?

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c35_remote_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"

echo CONTAINER_BATCH_RC=0
