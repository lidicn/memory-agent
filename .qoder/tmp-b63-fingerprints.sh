#!/usr/bin/env bash
# run14 出网前的本机指纹测量：把 remote 脚本 SOURCES 格的每条 grep 先在这里量一遍，
# 期望值写进 remote 脚本头部注释；同时出 HEAD 侧的成对读数（改前响 / 改后绿）。
# 口径：本机 Python 3.13 不参与，这里全是 grep/wc 的文本口径，容器侧同一组命令复核。
set -uo pipefail
cd /e/NAS/memory-agent
B=src/memory_agent/api/behavior_routes.py
C=src/memory_agent/change_attribution.py
T=tests/test_vma_task63_routes_input_boundary.py
O=tests/test_vma_r5_event_loop_offload.py

echo "===== WORKSPACE ====="
echo DWM_IMPORT=$(grep -cF 'from ..day_bounds import DAY_WINDOW_MAX' $B)
echo NUM_BOOL_GUARD=$(grep -cF 'if isinstance(raw, bool):' $B)
echo NUM_LO_MSG=$(grep -cF '不得小于' $B)
echo NUM_HI_MSG=$(grep -cF '不得大于' $B)
echo BAD_LIMIT_NUM=$(grep -cF 'default=20, lo=1, hi=100)' $B)
echo INTENT_LIMIT_NUM=$(grep -cF 'default=3, lo=1, hi=5)' $B)
echo CAUSAL_ENTRY_GATE=$(grep -cF 'if not (1 <= value <= DAY_WINDOW_MAX):' $B)
echo CF_GATE=$(grep -cF 'if not (1 <= days <= DAY_WINDOW_MAX):' $B)
echo AUDIT_LIMIT_NUM=$(grep -cF 'default=100, lo=1, hi=500)' $B)
echo MIN_COUNT_NUM=$(grep -cF 'name="min_count", default=3, lo=1)' $B)
echo TRIGGER_NUM=$(grep -cF 'name="trigger_id", default=None, lo=1)' $B)
# 三条"改前形状"的锚点一律取**整行代码**，不用会被注释/文档命中的子串（按名字 grep 会罩住散文）。
#   AUDIT_SWALLOW_ASSIGN：改前 `except ValueError: limit = 100` 的那句赋值（吞掉照样 200）
#   BARE_EXCEPT_VE：改前 2 → 改后 1；剩下的那一条是 weekday 的**合法**守卫（:1001，它返回 400），
#                  不是吞掉——留这一条读数就是为了说明"归零"说的是形状而不是关键字。
#   RAW_TRIGGER_INT：改前 `flag_false_positive, rule_id, int(trigger_id), ...` 裸转
#   LEGACY_INT_BODY_PROSE vs _CODE：散文（docstring 里的改前记载）留 2，代码归 0
echo AUDIT_SWALLOW_ASSIGN=$(grep -cE '^ *limit = 100$' $B || true)
echo BARE_EXCEPT_VE=$(grep -cE '^ *except ValueError:$' $B || true)
echo RAW_TRIGGER_INT=$(grep -cE '^ *_lifecycle\(rt\)\.flag_false_positive, rule_id, int\(' $B || true)
# 族 5（第五族：从未被测的 handler 里翻出来的同形缺陷）：
#   RAW_WMIN_PASS = 改前 `body.get("window_minutes"),` 原样透传给引擎的那一行（HEAD 1 → 工作区 0）
#   WMIN_NUM_GUARD = 改后走 `_num(..., name="window_minutes", ...)` 的落点行（HEAD 0 → 工作区 1）
#   AUDIT_TRAIL_GATE = 人工审核留痕分支的**存在性**读数（本批改的是测试不是这条代码，两侧都该是 1）
echo RAW_WMIN_PASS=$(grep -cE '^ *body\.get\("window_minutes"\),$' $B || true)
echo WMIN_NUM_GUARD=$(grep -cF 'name="window_minutes",' $B || true)
echo AUDIT_TRAIL_GATE=$(grep -cE '^ *if status in \("accepted", "rejected"\):$' $B || true)
echo LEGACY_INT_BODY_PROSE=$(grep -c 'int(body.get(' $B || true)
echo LEGACY_INT_BODY_CODE=$(grep -cE '^ *[a-z_]+ = int\(body\.get' $B || true)
echo LEGACY_QUERY_INT=$(grep -c 'int(request.query_params.get' $B)
echo OFFLOAD_TO_THREAD_TOTAL=$(grep -c 'await asyncio.to_thread(' $B)
echo OFFLOAD_PREDICT=$(grep -cF 'arrival, routine = await asyncio.to_thread(_predict)' $B)
echo OFFLOAD_INFER=$(grep -cF 'intents = await asyncio.to_thread(_infer)' $B)
echo OFFLOAD_PROFILE=$(grep -cF 'profile_text = await asyncio.to_thread(' $B)
echo OFFLOAD_WRITE=$(grep -cF 'await asyncio.to_thread(write_profile_atomic' $B)
echo HEAVY_NESTED=$(grep -cE 'def _(predict|infer)\(\)' $B)
echo CA_WINDOW_DAYS=$(grep -cF 'window_days = clamp_days(lookback_days)' $C)
echo CA_DELTA_WD=$(grep -cF 'delta = timedelta(days=window_days)' $C)
echo CA_HALF_LIFE_WD=$(grep -cF 'half_life = window_days / 2.0' $C)
echo CA_HALF_LIFE_RAW=$(grep -cF 'half_life = lookback_days / 2.0' $C || true)
echo CA_DESC_WD=$(grep -cF ', window_days)' $C)
echo T63_TESTDEFS=$(grep -c '^def test_' $T)
echo T63_HEAVY_NAMES=$(grep -cF 'HEAVY_SYNC_CALLEES' $T)
echo T63_PROD_SHAPE=$(grep -cF 'def test_production_read_shapes_are_asc_desc' $T)
echo R5_BLINDSPOT=$(grep -cF '盲区' $O)
wc -l $B $C $T $O

echo "===== HEAD（改前成对读数）====="
H=$(mktemp -d)
git show HEAD:$B > $H/br.py
git show HEAD:$C > $H/ca.py
echo HEAD_DWM_IMPORT=$(grep -cF 'from ..day_bounds import DAY_WINDOW_MAX' $H/br.py || true)
echo HEAD_NUM_BOOL_GUARD=$(grep -cF 'if isinstance(raw, bool):' $H/br.py || true)
echo HEAD_BAD_LIMIT_NUM=$(grep -cF 'default=20, lo=1, hi=100)' $H/br.py || true)
echo HEAD_LEGACY_INT_BODY=$(grep -c 'int(body.get(' $H/br.py || true)
echo HEAD_LEGACY_INT_BODY_CODE=$(grep -cE '^ *[a-z_]+ = int\(body\.get' $H/br.py || true)
echo HEAD_AUDIT_SWALLOW_ASSIGN=$(grep -cE '^ *limit = 100$' $H/br.py || true)
echo HEAD_BARE_EXCEPT_VE=$(grep -cE '^ *except ValueError:$' $H/br.py || true)
echo HEAD_RAW_TRIGGER_INT=$(grep -cE '^ *_lifecycle\(rt\)\.flag_false_positive, rule_id, int\(' $H/br.py || true)
echo HEAD_RAW_WMIN_PASS=$(grep -cE '^ *body\.get\("window_minutes"\),$' $H/br.py || true)
echo HEAD_WMIN_NUM_GUARD=$(grep -cF 'name="window_minutes",' $H/br.py || true)
echo HEAD_AUDIT_TRAIL_GATE=$(grep -cE '^ *if status in \("accepted", "rejected"\):$' $H/br.py || true)
echo HEAD_LEGACY_QUERY_INT=$(grep -c 'int(request.query_params.get' $H/br.py || true)
echo HEAD_OFFLOAD_TO_THREAD_TOTAL=$(grep -c 'await asyncio.to_thread(' $H/br.py || true)
echo HEAD_HEAVY_NESTED=$(grep -cE 'def _(predict|infer)\(\)' $H/br.py || true)
echo HEAD_CA_WINDOW_DAYS=$(grep -cF 'window_days = clamp_days(lookback_days)' $H/ca.py || true)
echo HEAD_CA_HALF_LIFE_WD=$(grep -cF 'half_life = window_days / 2.0' $H/ca.py || true)
echo HEAD_CA_HALF_LIFE_RAW=$(grep -cF 'half_life = lookback_days / 2.0' $H/ca.py || true)
echo HEAD_CA_DESC_WD=$(grep -cF ', window_days)' $H/ca.py || true)
wc -l $H/br.py $H/ca.py

echo "===== CR（按字节量，交付纪律 §六.9）====="
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
"$PY" -c 'for p in ("src/memory_agent/api/behavior_routes.py",
                    "src/memory_agent/change_attribution.py",
                    "tests/test_vma_task63_routes_input_boundary.py",
                    "tests/test_vma_r5_event_loop_offload.py"):
    print("CR {} = {}".format(p, open(p, "rb").read().count(b"\r")))
print("CRLF_CHECK_DONE")'
rm -rf $H
echo MEASURE_DONE
