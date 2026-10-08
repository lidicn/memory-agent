#!/usr/bin/env bash
# run24（20261007 档，任务表 #77：WebUI 前端读键 ↔ HTTP 载荷实际键 的一致性尺 + 判据锁）
#
# ── 为什么这一档也要整跑 ──
#   run23 之后落了 `f6773ea`：新在册 `scripts/scan_webui_payload_keys.py` +
#   `tests/test_webui_payload_keys.py`。树变了 ⇒ run23 的聚合摘要作废，快照摘要重取，
#   pyflakes 与全量回归重新走。本批**没动生产码**（只加量具与锁），所以没有变异档：
#   尺自己的五条控制腿就是这一档的"响过"证据，而且这次要在**容器 3.11** 里响。
#
# ── 这一档的格 ──
#  -1 TOOLCHAIN：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ 永远排第一。
#    0 SNAP：本机与容器跑同一份 `_hashes.py`，聚合摘要逐字对账。
#   0C ANCHOR：量具与锁的形状锚点（`def analyse` 1 条、控制腿名 5 条、锁 7 个 def、钉住表 12 行、
#              信封两处展开在位）。锚点用 `-cF` 读**整行代码形状**，不读散文。
#    1/2 门：pyflakes（基线只准减）+ 全量回归（容器 3.11 权威口径）。
#   定向 1：本批主战场——新锁 17 条 + 门禁量具读法约定锁（BOM/AST 那条，新文件自动进它的口径）。
#   定向 2：尺自己在容器里跑五条控制腿（`--selftest`）。副本树开在快照里的 `.qoder/`，
#              跑完必须自删 ⇒ 后面用 `CTL_RUN_LEFT`＝0 与二次哈希摘要复现来证还原。
#   POST：二次哈希（与格 0 逐字同）+ 原样再跑一次新锁 ⇒ 变异/副本腿没碰到被测树。
#
# 教训照旧在册：权威门在飞期间不许动树；副本腿只在快照树里跑；失败名单 grep 用 `^_{3,} `；
# 本机 guard 的 grep 模式在单引号里不许多写反斜杠，远端 `ex "…"` 内层双引号必须 `\"`。
set -uo pipefail
SNAP=${1:-c77snap20261007}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

date -Iseconds
echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

ex "cd $L && PYTHONPATH=/tmp/pylibs python -c 'import pytest, pyflakes; print(\"PYTEST=\" + pytest.__version__); print(\"PYFLAKES=\" + pyflakes.__version__)' && python -V"
echo TOOLCHAIN_RC=$?

# 格 0：SNAP —— 容器树 = 本机树？逐文件按字节 md5，整表聚合摘要与本机档对逐字。
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_snap.log 2>&1
echo SNAP_RC=$?
cat ${L}_snap.log

# 格 0C：ANCHOR —— 量具形状（整行代码，不读散文）
ex "cd $L && echo SCAN_ANALYSE_DEF=\$(grep -cF 'def analyse():' scripts/scan_webui_payload_keys.py); echo SCAN_MAIN_DEF=\$(grep -cF 'def main():' scripts/scan_webui_payload_keys.py); echo SCAN_LEG_NAMES=\$(grep -cF 'L5_producer_key_rename_flips_to_HARD' scripts/scan_webui_payload_keys.py); echo SCAN_ENVELOPE_CALL=\$(grep -cF 'if nm in (\"ok\", \"JSONResponse\"):' scripts/scan_webui_payload_keys.py); echo SCAN_TEMPFILE_IMPORT=\$(grep -cF 'import tempfile' scripts/scan_webui_payload_keys.py)"
echo ANCHOR_SCAN_RC=$?

# 格 0C2：锁形状 + 信封口径的两处展开在位（锁第 5 条的前提）
ex "cd $L && echo LOCK_TEST_DEFS=\$(grep -cF 'def test_' tests/test_webui_payload_keys.py); echo LOCK_PIN_ROWS=\$(grep -cE '^    \(\"[a-zA-Z]+\", \"[a-z_]+\", \"src/' tests/test_webui_payload_keys.py); echo ENVELOPE_OK_SPREAD=\$(grep -cF '{\"ok\": True, **data}' src/memory_agent/api/deps.py); echo ENVELOPE_ERROR_UPDATE=\$(grep -cF 'payload.update(extra)' src/memory_agent/api/deps.py)"
echo ANCHOR_LOCK_RC=$?

# 门 1：pyflakes（口径 src/memory_agent，基线只准减）
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -4 ${L}_gate.log

# 门 2：全量回归（容器 Python 3.11 是本仓权威口径）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs" > ${L}_suite.log 2>&1
echo SUITE_RC=$?
tail -6 ${L}_suite.log
grep -E '^_{3,} |^FAILED ' ${L}_suite.log | head -8; echo SUITE_FAILNAMES_RC=$?

# 定向 1：本批主战场（新锁 17 条 + 门禁量具读法约定 5 条）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_webui_payload_keys.py tests/test_vma_gate_scanners_read_every_py.py -q -rs -p no:cacheprovider" > ${L}_targeted.log 2>&1
echo TARGETED_RC=$?
tail -4 ${L}_targeted.log

# 定向 2：尺在容器里真跑五条控制腿（副本树开在快照 .qoder/ 下，跑完自删）
ex "cd $L && PYTHONPATH=/tmp/pylibs python scripts/scan_webui_payload_keys.py --selftest" > ${L}_selftest.log 2>&1
echo SELFTEST_RC=$?
grep -E 'SELFTEST_' ${L}_selftest.log
ex "ls -d $L/.qoder/tmp-c77-ctl_run 2>/dev/null | wc -l" | sed 's/^/CTL_RUN_LEFT=/'

# 定向 3：全表读数（容器口径的六格数字要与本机逐字对）
ex "cd $L && PYTHONPATH=/tmp/pylibs python scripts/scan_webui_payload_keys.py" > ${L}_faces.log 2>&1
echo FACES_RC=$?
grep -E '^(FACES|FACES2|CALLSITES_ORPHAN|READKEY_HARD|PAIR_CALLSITES)' ${L}_faces.log

# POST：二次哈希摘要复现 + 原样再跑新锁 ⇒ 副本腿没动被测树
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_post_hash.log 2>&1
echo POST_HASH_RC=$?
cat ${L}_post_hash.log
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_webui_payload_keys.py -q -p no:cacheprovider" > ${L}_post.log 2>&1
echo POST_RC=$?
tail -3 ${L}_post.log
date -Iseconds
echo REMOTE_BATCH_RC=0
