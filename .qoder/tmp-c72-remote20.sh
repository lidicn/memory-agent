#!/usr/bin/env bash
# run20（20261007 档，#72 的**补腿**：只重跑容器里的 MUT72 那一格）
#
# ── 为什么要补 ──
#   run19 除 MUT72 外全绿（SUITE 1907 passed、TARGETED 12、FACES 208、POST 12、GATE/SNAP/ANCHOR 全 0），
#   但门 3 的控制腿读成 `NOTHING -> RC=1 |`（**stdout 空**）⇒ `CONTROL_BAD：控制腿就不绿，整档作废`。
#   根因在量具不在产品：`_run_leg` 用 `PYTHONPATH=os.path.join(root,"src")` **覆盖**了外层
#   `$L/src:/tmp/pylibs`，把容器里 pytest 所在的 /tmp/pylibs 摘掉 ⇒ `python -m pytest` 起不来。
#   本机 3.13 的 pytest 是系统装的，同一份腿本机绿 ⇒ 这是"本机绿≠容器绿"的第 N 次实测。
#
# ── 这一档的格 ──
#   -1 TOOLCHAIN：可写层清空即整档作废，永远排第一。
#    0 SNAP：容器树仍 = run19 那棵 400 文件系统（失败腿在控制腿就退出，没来得及改树；
#            这条不靠"应该没改"，靠聚合摘要逐字对账）。
#   0D HARNESS：证据量具**换过**的两条锚点——新版续 PATH 那行命中 1 次、旧版覆盖写法归零。
#    3 MUT72：控制腿必须绿 + 三腿全咬住 + restored=OK 全条。
#   POST：变异跑完原样再跑同一份用例（还原自证）。
set -uo pipefail
SNAP=${1:-c72snap20261007}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

date -Iseconds
echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

ex "cd $L && PYTHONPATH=/tmp/pylibs python -c 'import pytest, pyflakes; print(\"PYTEST=\" + pytest.__version__); print(\"PYFLAKES=\" + pyflakes.__version__)' && python -V"
echo TOOLCHAIN_RC=$?

# 格 0：容器树 = 本机树？（失败腿不许留下任何字节差）
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_snap20.log 2>&1
echo SNAP_RC=$?
cat ${L}_snap20.log

# 格 0D：量具锚点。新形状必须在、旧形状必须归零，否则跑的是没修的那份。
ex "echo PATH_APPEND=\$(grep -cF 'os.pathsep.join' ${L}_mut72.py); echo OLD_CLOBBER=\$(grep -cF 'PYTHONPATH=os.path.join(root, \"src\"), JWT_SECRET' ${L}_mut72.py); echo STDERR_SHOWN=\$(grep -cF 'NO_PYTEST_OUTPUT' ${L}_mut72.py)"
echo HARNESS_RC=$?

# 门 3（自证）：先只读预检，再跑四条腿（控制 + L1/L2/L3），每腿按字节还原
ex "cd $L && MUT_ROOT=$L PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut72.py --verify" > ${L}_mut20_verify.log 2>&1
echo VERIFY_RC=$?
grep -E 'MUTANTS_PARSED|VERIFY_BAD|ANCHOR_BAD|SYNTAX_BAD' ${L}_mut20_verify.log

ex "cd $L && MUT_ROOT=$L PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut72.py" > ${L}_mut20.log 2>&1
echo MUT72_RC=$?
grep -E '^(NOTHING|L[0-9]+ |MUT_COUNT|MUTATION_BAD|CONTROL_BAD|.*锚点异常)' ${L}_mut20.log
# 腿没跑起来时把 stderr 尾巴也报出来（run19 那次就是因为只报 stdout 而无从下手）
grep -E 'NO_PYTEST_OUTPUT' ${L}_mut20.log

# 还原自证
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_insights_facade_contract.py -q -p no:cacheprovider" > ${L}_post20.log 2>&1
echo POST_RC=$?
tail -3 ${L}_post20.log

# 补完还顺手把 run19 那格没量的 legacy 构造顺序钉一个数：门面 (store,config) / legacy (config,store)
# 探针首跑就是被这条坑骗出满屏假丢键的（首跑读数：'Config' object has no attribute 'day_counts'）。
ex "cd $L && echo FACADE_ORDER=\$(grep -cF 'store: Any = None, config: Optional' src/memory_agent/insights/api.py); echo LEGACY_ORDER=\$(grep -cF 'def __init__(self, config, store: Store)' src/memory_agent/insights_legacy.py)"
echo ORDER_RC=$?
date -Iseconds
echo REMOTE_BATCH_RC=0
