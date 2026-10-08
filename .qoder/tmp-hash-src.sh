#!/bin/sh
# 只读：把 NAS 生产挂载目录里 src/ 每个文件的 blob 哈希与路径成对打印
cd /vol1/1000/docker/memory-agent || { echo "CD_RC=$?"; exit 2; }
find src -type f -not -path '*__pycache__*' | sort > /tmp/ma_src_list.txt
echo LIST_COUNT=$(wc -l < /tmp/ma_src_list.txt)
while IFS= read -r f; do
  h=$(git hash-object "$f")
  printf '%s %s\n' "$h" "$f"
done < /tmp/ma_src_list.txt
rm -f /tmp/ma_src_list.txt
