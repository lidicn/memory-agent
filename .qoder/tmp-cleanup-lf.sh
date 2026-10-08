#!/bin/sh
# 清理本轮探针在宿主机与容器 /tmp 里留下的自产物（只删自己建的文件名）
for f in /tmp/ma_probe.py /tmp/ma_san.py /tmp/ma_san2.py /tmp/ma_hash.sh /tmp/nas_hashes.txt \
         /tmp/host_tarlist.txt /tmp/ma_full.tar /tmp/ma_contract.tar; do
  [ -e "$f" ] && rm -f "$f" && echo "host_removed=$f"
done
docker exec -u root memory-agent sh -c '
for f in /tmp/ma_probe.py /tmp/ma_san.py /tmp/ma_san2.py /tmp/prod_probe.out /tmp/san.out /tmp/san2.out \
         /tmp/tarlist.txt /tmp/sha.txt /tmp/ef.txt /tmp/vs34_suite.log /tmp/vs34_gate.log; do
  [ -e "$f" ] && rm -f "$f" && echo "ctr_removed=$f"
done
[ -d /tmp/vs34 ] && rm -rf /tmp/vs34 && echo "ctr_removed=/tmp/vs34"
'
echo CLEANUP_RC=$?
docker exec memory-agent sh -c 'ls /tmp | head -20; echo LIST_RC=$?'
