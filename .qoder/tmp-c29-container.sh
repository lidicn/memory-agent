#!/usr/bin/env bash
# 20261005 批次 c29：#52（A2/A7 P2-1 zip 配处分诊与修复）的容器权威门与全量回归。
# 与 c28 的差别：本批改了 6 个交付面文件（algo_kernel/entity_resolution/history/
# rule_engine/semantic_dedup/store），所以除了新锁还跑了「被改模块的既有测试」定向档；
# 并在容器（Python 3.11）里跑量具自证与全仓扫描，读数不假定与本机 3.13 等价。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c29snap20261005a
TGZ=.qoder/tmp-c29-snap.tgz

echo HEAD=$(git rev-parse --short HEAD)

{ git ls-files -- src tests scripts benchmarks .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- tests scripts; } | sort -u > .qoder/tmp-c29-filelist.txt
echo FILES=$(wc -l < .qoder/tmp-c29-filelist.txt)
tar -czf "$TGZ" -T .qoder/tmp-c29-filelist.txt
echo TAR_RC=$? SIZE=$(stat -c %s "$TGZ")

"$SCP" "${OPTS[@]}" -q "$TGZ" "$NAS:/tmp/ma_$SNAP.tgz"; echo SCP_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${SNAP}_stage && mkdir -p /tmp/${SNAP}_stage && tar -xzf /tmp/ma_$SNAP.tgz -C /tmp/${SNAP}_stage && rm -f /tmp/ma_$SNAP.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$SNAP' && docker cp /tmp/${SNAP}_stage memory-agent:/tmp/$SNAP && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$SNAP && rm -rf /tmp/${SNAP}_stage; echo STAGE_RC=\$?"
echo SYNC_RC=$?

# 门 1：pyflakes（含新增 scripts/scan_zip_pairing.py）
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh' > /tmp/${SNAP}_gate.log 2>&1; echo GATE_RC=\$?; tail -3 /tmp/${SNAP}_gate.log"

# 门 2：全量回归
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP:/tmp/$SNAP/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs' > /tmp/${SNAP}_suite.log 2>&1; echo SUITE_RC=\$?; tail -4 /tmp/${SNAP}_suite.log"

# 定向 1：本批新锁
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_p21_zip_pairing.py -q -p no:cacheprovider' > /tmp/${SNAP}_targeted.log 2>&1; echo TARGETED_RC=\$?; tail -3 /tmp/${SNAP}_targeted.log"

# 定向 2：被改了交付面的模块，既有测试必须照旧绿（strict 不许把正常路径打崩）
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && PYTHONPATH=/tmp/$SNAP/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_algo_kernel.py tests/test_entity_resolution.py tests/test_semantic_dedup.py tests/test_store_fts_selfheal.py tests/test_vma_activity_semantic.py tests/test_vma_a3_p22_auth_loop_blocking.py -q -p no:cacheprovider' > /tmp/${SNAP}_touched.log 2>&1; echo TOUCHED_RC=\$?; tail -3 /tmp/${SNAP}_touched.log"

# 量具在容器（Python 3.11）里的自证与全仓扫描
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$SNAP && python -V && PYTHONPATH=/tmp/pylibs python scripts/scan_zip_pairing.py --root src/memory_agent' > /tmp/${SNAP}_scan.log 2>&1; echo SCAN_RC=\$?; cat /tmp/${SNAP}_scan.log"

# 运行时读数探针（改后态）：容器侧独立取一份，不拿本机 3.13 的读数冒充
"$SCP" "${OPTS[@]}" -q .qoder/tmp-c29-zip-probe.py "$NAS:/tmp/ma_zipprobe_c29.py"; echo PROBESCRIPT_SCP_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "docker cp /tmp/ma_zipprobe_c29.py memory-agent:/tmp/zipprobe_c29.py && rm -f /tmp/ma_zipprobe_c29.py; echo PROBE_DOCKER_CP_RC=\$?"
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'sha256sum /tmp/zipprobe_c29.py | cut -c1-16'; echo PROBE_SHA_RC=\$?"
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'MA_SRC=/tmp/$SNAP/src python /tmp/zipprobe_c29.py' > /tmp/${SNAP}_probe.log 2>&1; echo PROBE_RC=\$?; cat /tmp/${SNAP}_probe.log"

echo CONTAINER_BATCH_RC=0
