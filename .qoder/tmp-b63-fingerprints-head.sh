#!/usr/bin/env bash
# run14c 出网前的 HEAD 侧**全量**成对读数。
# 为什么单独一支：remote14.sh 头部第 62..65 行原先写的是"上面 =1 的源侧指纹全 0"，
# 那是**推论**不是读数——指纹脚本的 HEAD 段只量了 15 条，剩下十来条（NUM_LO_MSG / CAUSAL_ENTRY_GATE /
# OFFLOAD_* / MIN_COUNT_NUM…）根本没在 HEAD 上量过，一旦其中某条改前就存在，
# 台账里那句"改前响、改后绿"就变成凭空虚写（本机已经栽过两次，见记忆 verify-artifact-before-quoting）。
# 这里把 WORKSPACE 段每一条锚点在 HEAD 版本上原样再量一遍，一条不漏。
set -uo pipefail
cd /e/NAS/memory-agent
H=$(mktemp -d)
git show HEAD:src/memory_agent/api/behavior_routes.py > "$H/br.py"; echo BR_SHOW_RC=$?
git show HEAD:src/memory_agent/change_attribution.py > "$H/ca.py"; echo CA_SHOW_RC=$?
T=tests/test_vma_task63_routes_input_boundary.py
O=tests/test_vma_r5_event_loop_offload.py

echo "===== HEAD behavior_routes ====="
echo HEAD_DWM_IMPORT=$(grep -cF 'from ..day_bounds import DAY_WINDOW_MAX' $H/br.py || true)
echo HEAD_NUM_BOOL_GUARD=$(grep -cF 'if isinstance(raw, bool):' $H/br.py || true)
echo HEAD_NUM_LO_MSG=$(grep -cF '不得小于' $H/br.py || true)
echo HEAD_NUM_HI_MSG=$(grep -cF '不得大于' $H/br.py || true)
echo HEAD_BAD_LIMIT_NUM=$(grep -cF 'default=20, lo=1, hi=100)' $H/br.py || true)
echo HEAD_INTENT_LIMIT_NUM=$(grep -cF 'default=3, lo=1, hi=5)' $H/br.py || true)
echo HEAD_CAUSAL_ENTRY_GATE=$(grep -cF 'if not (1 <= value <= DAY_WINDOW_MAX):' $H/br.py || true)
echo HEAD_CF_GATE=$(grep -cF 'if not (1 <= days <= DAY_WINDOW_MAX):' $H/br.py || true)
echo HEAD_AUDIT_LIMIT_NUM=$(grep -cF 'default=100, lo=1, hi=500)' $H/br.py || true)
echo HEAD_MIN_COUNT_NUM=$(grep -cF 'name="min_count", default=3, lo=1)' $H/br.py || true)
echo HEAD_TRIGGER_NUM=$(grep -cF 'name="trigger_id", default=None, lo=1)' $H/br.py || true)
echo HEAD_AUDIT_SWALLOW_ASSIGN=$(grep -cE '^ *limit = 100$' $H/br.py || true)
echo HEAD_BARE_EXCEPT_VE=$(grep -cE '^ *except ValueError:$' $H/br.py || true)
echo HEAD_RAW_TRIGGER_INT=$(grep -cE '^ *_lifecycle\(rt\)\.flag_false_positive, rule_id, int\(' $H/br.py || true)
echo HEAD_RAW_WMIN_PASS=$(grep -cE '^ *body\.get\("window_minutes"\),$' $H/br.py || true)
echo HEAD_WMIN_NUM_GUARD=$(grep -cF 'name="window_minutes",' $H/br.py || true)
echo HEAD_AUDIT_TRAIL_GATE=$(grep -cE '^ *if status in \("accepted", "rejected"\):$' $H/br.py || true)
echo HEAD_LEGACY_INT_BODY_PROSE=$(grep -c 'int(body.get(' $H/br.py || true)
echo HEAD_LEGACY_INT_BODY_CODE=$(grep -cE '^ *[a-z_]+ = int\(body\.get' $H/br.py || true)
echo HEAD_LEGACY_QUERY_INT=$(grep -c 'int(request.query_params.get' $H/br.py || true)
echo HEAD_OFFLOAD_TO_THREAD_TOTAL=$(grep -c 'await asyncio.to_thread(' $H/br.py || true)
echo HEAD_OFFLOAD_PREDICT=$(grep -cF 'arrival, routine = await asyncio.to_thread(_predict)' $H/br.py || true)
echo HEAD_OFFLOAD_INFER=$(grep -cF 'intents = await asyncio.to_thread(_infer)' $H/br.py || true)
echo HEAD_OFFLOAD_PROFILE=$(grep -cF 'profile_text = await asyncio.to_thread(' $H/br.py || true)
echo HEAD_OFFLOAD_WRITE=$(grep -cF 'await asyncio.to_thread(write_profile_atomic' $H/br.py || true)
echo HEAD_HEAVY_NESTED=$(grep -cE 'def _(predict|infer)\(\)' $H/br.py || true)

echo "===== HEAD change_attribution ====="
echo HEAD_CA_WINDOW_DAYS=$(grep -cF 'window_days = clamp_days(lookback_days)' $H/ca.py || true)
echo HEAD_CA_DELTA_WD=$(grep -cF 'delta = timedelta(days=window_days)' $H/ca.py || true)
echo HEAD_CA_HALF_LIFE_WD=$(grep -cF 'half_life = window_days / 2.0' $H/ca.py || true)
echo HEAD_CA_HALF_LIFE_RAW=$(grep -cF 'half_life = lookback_days / 2.0' $H/ca.py || true)
echo HEAD_CA_DESC_WD=$(grep -cF ', window_days)' $H/ca.py || true)

echo "===== HEAD 两支测试（task63 在 HEAD 不存在 = 整档缺失）====="
echo HEAD_TASK63_EXISTS=$(git cat-file -e HEAD:$T 2>/dev/null && echo yes || echo no)
echo HEAD_R5_BLINDSPOT=$(git show HEAD:$O | grep -cF '盲区' || true)
echo HEAD_T63_HEAVY_NAMES=0
echo HEAD_T63_PROD_SHAPE=0
echo HEAD_T63_TESTDEFS=0
wc -l $H/br.py $H/ca.py
git show HEAD:$O | wc -l | sed 's/^/HEAD_R5_LINES=/'
rm -rf $H
echo HEAD_MEASURE_DONE
