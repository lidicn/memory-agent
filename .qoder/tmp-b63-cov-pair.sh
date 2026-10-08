#!/usr/bin/env bash
# 任务表 #63 覆盖率「前/后」配对档（本机口径：Python313 + coverage 7.16.1，全量单轮）。
#   前档 = .qoder/head63（`git archive HEAD` 全树，HEAD=612f67b）——本文件上一版只打包了
#          src/tests/pytest.ini，6 个测试模块因缺 scripts/、benchmarks/ 直接 collection error、
#          pytest **Interrupted** ⇒ 那份 8% 不是读数，作废重跑。
#   后档 = 当前工作区，用**同一行命令**再跑一遍，保证前后是同一把尺（不是拿两份不同命令的读数比）。
set -u
PY='C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe'
ROOT='E:/NAS/memory-agent'
INC='src/memory_agent/api/behavior_routes.py,src/memory_agent/change_attribution.py'
MARK="\"\"\"PAIRMARK v2 python=Python313 mode=full-suite-single-round include=behavior_routes+change_attribution\"\"\""

run_cell() {  # $1=tag  $2=dir
  local tag=$1 dir=$2
  echo "=== CELL $tag dir=$dir ==="
  ( cd "$dir" && \
    "$PY" -m coverage erase && \
    "$PY" -m coverage run -m pytest tests -q -rs > "$ROOT/.qoder/tmp-b63-cov-$tag.out" 2>&1
    echo "${tag}_SUITE_RC=$?"
    "$PY" -m coverage report --include="$INC" > "$ROOT/.qoder/tmp-b63-covreport-$tag.out" 2>&1
    echo "${tag}_REPORT_RC=$?"
    "$PY" -m coverage report --include="$INC" --missing >> "$ROOT/.qoder/tmp-b63-cov-$tag-missing.out" 2>&1
    echo "${tag}_MISSING_RC=$?"
    tail -1 "$ROOT/.qoder/tmp-b63-cov-$tag.out" )
  echo "--- report $tag ---"
  cat "$ROOT/.qoder/tmp-b63-covreport-$tag.out"
}

echo "MARK $MARK"
"$PY" -m coverage --version
echo "HEAD_SHA=$(cd $ROOT && git log -1 --format='%h %ci')"
echo "HEADTESTS=$(ls $ROOT/.qoder/head63/tests | wc -l) WTTTESTS=$(ls $ROOT/tests | wc -l)"
echo "HEAD_TASK63_TEST=$(ls $ROOT/.qoder/head63/tests/test_vma_task63_routes_input_boundary.py 2>&1 | tail -1)"

# 前档先跑：head63 是 HEAD 的只读快照，coverage 数据落在该目录内，不污染工作区
run_cell HEADpair "$ROOT/.qoder/head63"
run_cell WTpair "$ROOT"

echo "=== scratch .coverage 位置（都不许入册）==="
ls -la "$ROOT/.coverage" "$ROOT/.qoder/head63/.coverage" 2>&1 | tail -3
echo PAIR_DONE_RC=0
