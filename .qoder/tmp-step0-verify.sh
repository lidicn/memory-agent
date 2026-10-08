set -e
echo "=== 1) 容器全量回归（/tmp/t_s0/tests，homesdk 0.3.1 在 /tmp/pylibs）==="
docker exec -w /tmp -e MA_SRC_DIR=/app/src -e PYTHONPATH=/tmp/pylibs:/app/src \
  -e JWT_SECRET=ci-test -e TMPDIR=/tmp/ma_test_tmp memory-agent \
  python -m pytest /tmp/t_s0/tests -q -p no:cacheprovider 2>&1 | tail -8

echo "=== 2) 门禁快照 /tmp/gate_s0（GATES_REQUIRE=1）==="
rm -rf /tmp/gate_s0 && mkdir -p /tmp/gate_s0
tar xf /tmp/gate_s0.tar -C /tmp/gate_s0
docker rm -f ma_gate_s0 >/dev/null 2>&1 || true
docker cp /tmp/gate_s0 memory-agent:/tmp/gate_s0
docker exec -w /tmp/gate_s0 -e PYTHONPATH=/tmp/pylibs:/tmp/gate_s0/src -e GATES_REQUIRE=1 \
  -e JWT_SECRET=ci-test -e MA_ENV=test -e TMPDIR=/tmp/ma_test_tmp memory-agent \
  python -m pytest tests/test_quality_gates.py -q -p no:cacheprovider 2>&1 | tail -8

echo "=== 3) pyflakes 复扫（容器侧）==="
docker exec -w /tmp/gate_s0 -e PYTHONPATH=/tmp/pylibs memory-agent \
  python -m pyflakes /tmp/gate_s0/src/memory_agent/house_time.py \
  /tmp/gate_s0/src/memory_agent/store.py /tmp/gate_s0/src/memory_agent/config.py \
  /tmp/gate_s0/src/memory_agent/runtime.py /tmp/gate_s0/src/memory_agent/mqtt_bridge.py \
  /tmp/gate_s0/src/memory_agent/insights/models.py \
  /tmp/gate_s0/tests/test_vma_step0_homesdk_time.py 2>&1 | tail -12 || true

echo "=== 4) homesdk 在容器临时面的读数 ==="
docker exec -e PYTHONPATH=/tmp/pylibs memory-agent python -c "
import homesdk
from homesdk import time as ht
print('version:', homesdk.__version__)
print('status:', ht.house_tz_status())
"

echo "=== 5) MA 侧 house_time 的换挡状态（未声明键 / 声明后）==="
docker exec -e PYTHONPATH=/tmp/pylibs:/app/src -e JWT_SECRET=ci-test memory-agent python -c "
from memory_agent import house_time
print('未声明:', house_time.status())
import os
os.environ['HOMESDK_TZ'] = 'Asia/Shanghai'
house_time.reset_homesdk_probe()
print('声明后:', house_time.status())
print('墙钟:', house_time.now_local(8.0).isoformat())
"
echo "ALL_DONE"
