#!/usr/bin/env bash
# run18（20261007 档，任务表 #70：**#63 那 22 条变异档按"锚点跟着函数走"重锚后的容器补跑**）
#
# ── 这一档只跑三格，不重跑 run17 的整门 ──
#   run17（HEAD=2a14651 + 本批工作树）已把 #68 第四批和前面 10 个未推提交的容器门量全（SUITE 1904 passed /
#   10 skipped）。此后**代码一个字没动**（2054dde 就是那棵树），变的只有 .qoder/ 里的 harness 锚点。
#   所以这里不重复 450 秒的 SUITE，只做三件事：
#     格 -1 TOOLCHAIN —— /tmp/pylibs 在容器可写层，recreate 即清空 ⇒ 排第一，量不到整跑作废（run8 教训）。
#     格 0 SNAP —— 容器树逐文件 md5 聚合摘要，与 run17 那档 `cafb96a7…` **逐字比**。相等就一票判掉
#                  "容器里跑的就是 2054dde 那棵树"，比任何锚点都硬。
#     格 1 MUT63 —— 22 腿 + NOTHING 控制腿，容器 Python 3.11 口径。
#     格 2 POST  —— 变异跑完再原样跑一遍目标用例文件（85 条），证"按字节还原"在容器侧真成立。
#
# ── 重锚的那四条为什么动的是文件而不是判据 ──
#   run17 出网前预检量到 `tmp-b63-mut-verify.py` 读 `VERIFY_BAD=4`：M1 丢 bool 判定 / M2 下界门 /
#   M3 上界门 / M4 转换失败退回默认档四条**锚点命中 0 次**（residue=0 ⇒ 脚本自身没坏）。
#   根因：#68 第三批（2a14651）把 `_num` 从 `api/behavior_routes.py` 提进公共依赖 `api/deps.py`
#   （另有一份 `day_bounds.py` 管"天/分/时"三档 clamp，两者别混）——缺陷的落点换了文件，
#   锁本身一条没改。⇒ 锚点跟着函数走：M1..M4 改指 deps.py，M4 的 except 行同步带上后来补的
#   `OverflowError`（第八轮 lesson 86：`int(float('inf'))` 抛 OverflowError，`(TypeError, ValueError)` 接不住）。
#   出网前四把尺照旧，且这版的尺 3 是**硬闸**（VERIFY_BAD 必须 0，run17 那一档因此被摘掉过）。
set -uo pipefail
SNAP=${1:-c70snap20261007}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

date -Iseconds
echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

# 格 -1：TOOLCHAIN
ex "cd $L && PYTHONPATH=/tmp/pylibs python -c 'import pytest, pyflakes; print(\"PYTEST=\" + pytest.__version__); print(\"PYFLAKES=\" + pyflakes.__version__)' && python -V"
echo TOOLCHAIN_RC=$?

# 格 0：SNAP 聚合摘要（期望与 run17 逐字相同）
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_snap.log 2>&1
echo SNAP_RC=$?
cat ${L}_snap.log

# 格 1：MUT63 二十二腿（串行、每腿按字节还原；NOTHING 控制腿在 harness 内部第一档）
ex "cd $L && MUT_ROOT=$L PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut63.py" > ${L}_mut63.log 2>&1
echo MUT63_RC=$?
grep -E '^(NOTHING|M[0-9]+ |MUT_COUNT|MUTATION_BAD|CONTROL_BAD|.*锚点异常)' ${L}_mut63.log

# 格 2：POST 还原自证 —— 变异跑完原样再跑一遍同一份用例，必须仍是 85 passed
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_task63_routes_input_boundary.py -q -rs -p no:cacheprovider" > ${L}_post.log 2>&1
echo POST_RC=$?
tail -3 ${L}_post.log
date -Iseconds
echo REMOTE_BATCH_RC=0
