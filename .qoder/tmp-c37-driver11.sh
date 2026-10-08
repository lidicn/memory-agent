#!/usr/bin/env bash
# 20261006 批次 c37 run11（任务表 #61：A2/A7 静态结论核销第三批）的容器权威门**本地驱动**。
# 形状沿用 run10（两层引号 + tar 快照 + docker cp + chown 10001 + 远端 tr -d '\r' 后 bash -n）。
#
# 本批为什么必须重跑整套门（不能沿用 run10 读数）：
#   1) 换了树：#59/#60/#61 三个提交之后又落了本批改动（intent_inference.py 的 P4-3 修复 +
#      三个新测试文件 + 三个在册 .py 去 BOM）。SUITE/TARGETED/TOUCHED/GATE 四格都是整树口径。
#   2) 本批新增五族锁（P4-3/P2-4/P2-5/P2-8/BOM），每条都要容器 Python 3.11 的变异自咬读数。
#   3) 上一批的 M1..M10 一并重跑：本批刻意**没碰** insights 读路径，所以那一格是**自证**——
#      读数应与 run10 一字不差（5/6/5/8/3/2/4/1/1/24），对不上就说明改动渗到了读路径。
#   4) 仍然**没有 PROBE 格**：生产库成对读数与 run10 同一棵读路径，沿用 §6.18.1 正式基准
#      （0.67× 改前单条 LIMIT / 3.28× 前缀量具），本机无生产数据，重跑只多一倍墙钟时间。
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
SNAP=c37snap20261006a
TGZ=.qoder/tmp-c37-snap11.tgz

bash -n .qoder/tmp-c37-remote11.sh; echo REMOTE_SYNTAX_RC=$?
bash -n .qoder/tmp-c37-driver11.sh; echo SELF_SYNTAX_RC=$?
C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe -c "import ast,io;ast.parse(io.open('.qoder/tmp-c37-mutate11.py',encoding='utf-8').read())"
echo MUT11_PARSE_RC=$?

echo HEAD=$(git rev-parse --short HEAD)

{ git ls-files -- src tests scripts benchmarks attic .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- src tests scripts; } | sort -u > .qoder/tmp-c37-filelist11.txt
echo FILES=$(wc -l < .qoder/tmp-c37-filelist11.txt)
echo IN_SNAP_attic=$(grep -c '^attic/' .qoder/tmp-c37-filelist11.txt)
echo IN_SNAP_p25=$(grep -c 'test_vma_p25_keyword_domains_single_definition.py' .qoder/tmp-c37-filelist11.txt)
echo IN_SNAP_p28=$(grep -c 'test_vma_p28_learning_attic_unreachable.py' .qoder/tmp-c37-filelist11.txt)
echo IN_SNAP_bom=$(grep -c 'test_vma_gate_scanners_read_every_py.py' .qoder/tmp-c37-filelist11.txt)
echo IN_SNAP_repo=$(grep -c '^src/memory_agent/insights/repository.py' .qoder/tmp-c37-filelist11.txt)
tar -czf "$TGZ" -T .qoder/tmp-c37-filelist11.txt
echo TAR_RC=$? SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; echo SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c37-remote11.sh "$NAS:/tmp/ma_c37_remote11.sh"; echo REMOTE_SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c37-mutate.py "$NAS:/tmp/ma_c37_mut10.py"; echo MUT10_SCP_RC=$?
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c37-mutate11.py "$NAS:/tmp/ma_c37_mut11.py"; echo MUT11_SCP_RC=$?

# 远端脚本从 Windows 侧拷过去可能带 CRLF，先 `tr -d '\r'` 落成 unix 行尾再 bash -n 验一遍
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage && docker cp /tmp/ma_c37_mut10.py memory-agent:/tmp/${SNAP}_mut.py && docker cp /tmp/ma_c37_mut11.py memory-agent:/tmp/${SNAP}_mut11.py && docker exec -u root memory-agent chown 10001:10001 /tmp/${SNAP}_mut.py /tmp/${SNAP}_mut11.py && rm -f /tmp/ma_c37_mut10.py /tmp/ma_c37_mut11.py; echo STAGE_RC=\$?; tr -d '\r' < /tmp/ma_c37_remote11.sh > /tmp/ma_c37_remote11_unix.sh && bash -n /tmp/ma_c37_remote11_unix.sh; echo REMOTE_NAS_SYNTAX_RC=\$?"
echo SYNC_RC=$?

"$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c37_remote11_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"

echo CONTAINER_BATCH_RC=0
