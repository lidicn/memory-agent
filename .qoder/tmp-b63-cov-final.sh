#!/usr/bin/env bash
# 任务表 #63 覆盖率「后档」重跑（终树，本机口径：Python313 + coverage 7.16.1，全量单轮）。
#   为什么重跑：05:54→06:04 那格 WTpair 是脏的——它跑在 M8 变异腿未还原的树上（`causal_analyze`
#   的 days 门是 `if False:`），产物已改名 BAD-contaminated-* 隔离，读数一律不许引用。
#   尺子必须与「前档」HEADpair 同一行命令（coverage run -m pytest tests -q -rs + 同一 --include），
#   否则 21% → ?% 不是配对读数。
#   本格只在工作区跑：跑之前 .qoder/tmp-b63-mut-verify.py 必须 VERIFY_RC=0（22 锚点各 1 次、
#   无残留死门），且这期间不得有任何变异腿在同一棵树里并跑。
set -u
PY='C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe'
ROOT='E:/NAS/memory-agent'
INC='src/memory_agent/api/behavior_routes.py,src/memory_agent/change_attribution.py'
TAG=WTfinal

cd "$ROOT" || { echo "CD_RC=$?"; exit 1; }
echo "MARK v3 python=Python313 mode=full-suite-single-round include=behavior_routes+change_attribution tag=$TAG"
echo "TREE_HEAD=$(git log -1 --format='%h')"
echo "TREE_LINES=$(wc -l < src/memory_agent/api/behavior_routes.py)/$(wc -l < tests/test_vma_task63_routes_input_boundary.py)"
echo "GATE_ANCHOR=$(grep -cF 'if not (1 <= value <= DAY_WINDOW_MAX):' src/memory_agent/api/behavior_routes.py)"
echo "DEADGATE=$(grep -cE '^ *if False:$' src/memory_agent/api/behavior_routes.py || true)"

"$PY" -m coverage erase
"$PY" -m coverage run -m pytest tests -q -rs > "$ROOT/.qoder/tmp-b63-cov-$TAG.out" 2>&1
echo "${TAG}_SUITE_RC=$?"
"$PY" -m coverage report --include="$INC" > "$ROOT/.qoder/tmp-b63-covreport-$TAG.out" 2>&1
echo "${TAG}_REPORT_RC=$?"
"$PY" -m coverage report --include="$INC" --missing > "$ROOT/.qoder/tmp-b63-cov-$TAG-missing.out" 2>&1
echo "${TAG}_MISSING_RC=$?"
tail -1 "$ROOT/.qoder/tmp-b63-cov-$TAG.out"
cat "$ROOT/.qoder/tmp-b63-covreport-$TAG.out"
echo "COVERAGE_FINAL_DONE"
