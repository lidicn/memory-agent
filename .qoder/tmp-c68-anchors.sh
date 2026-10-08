#!/bin/sh
# run17 格 0C 的锚点表。**本机与容器跑同一份脚本**，产出逐字可对账的 key=value。
# 期望值不写在脚本里：本机档 = 对账基线（.qoder/tmp-c68-anchors-local17.out），
# 容器档 = 权威读数。两档 diff 必须为空；不空就是"快照/树不同"，不是"锚点写错了"。
#
# 这批要量的是第四轮 #68 的卸载面：六支路由的 to_thread 总量、几处逐字落点、
# 改前的直调形状必须归零、反向腿（纯内存 token 校验不许被卸载）、量具自身、新锁自身。
# 行为面（"这条腿真的进了线程"）不在这里判，由 tests/test_vma_phase2_batch4_offload.py 判。
p()  { echo "$1=$(grep -cF -- "$2" "$3" 2>/dev/null)"; }
pn() { echo "$1=$(grep -cE -- "$2" "$3" 2>/dev/null)"; }
w()  { echo "$1=$(wc -l < "$2" 2>/dev/null)"; }

A=src/memory_agent/api/agent_memory_routes.py
M=src/memory_agent/api/member_routes.py
S=src/memory_agent/api/signal_routes.py
V=src/memory_agent/api/vision_routes.py
L=src/memory_agent/api/llm_routes.py
C=src/memory_agent/api/mcp_routes.py
AC=src/memory_agent/acp_auth.py
MA=src/memory_agent/mcp_auth.py
B=src/memory_agent/api/behavior_routes.py
G=scripts/scan_unloaded_async_io.py
T=tests/test_vma_phase2_batch4_offload.py

# ── 每支路由的卸载总数（#63/#51 那两批的旧卸载也计在内，作渗染对照）──
p TT_AMR 'await asyncio.to_thread(' $A
p TT_MBR 'await asyncio.to_thread(' $M
p TT_SGR 'await asyncio.to_thread(' $S
p TT_VRS 'await asyncio.to_thread(' $V
p TT_LLR 'await asyncio.to_thread(' $L
p TT_MCR 'await asyncio.to_thread(' $C
p TT_BHR 'await asyncio.to_thread(' $B
# 反向腿：这两支**不许**出现 to_thread（MCPTokenStore 纯内存，卸载它是过度工程）
p TT_ACP 'to_thread' $AC
p TT_MCA 'to_thread' $MA

# ── 逐字落点（各 =1）──
p L_AMR_LIST 'asyncio.to_thread(rt.agent_memory.list_agent_memories, state, source)' $A
p L_MBR_MEMBERS 'asyncio.to_thread(runtime(request).store.list_members)' $M
p L_MBR_APPEAR '_normalize_appearance(appearance))' $M
p L_MBR_FETCH 'asyncio.to_thread(_fetch)' $M
p L_MBR_CONF 'lo=0.0, hi=1.0' $M
p L_MCR_LIMIT 'name="limit", default=100, lo=1, hi=1000' $C
p L_MCR_ROWS 'rows = await asyncio.to_thread(' $C
p L_LLR_CACHE 'asyncio.to_thread(runtime(request).store.clear_answer_cache, key)' $L
p L_ACP_ONLOOP 'name = store.verify(token) if token else None' $AC

# ── 改前的直调形状必须归零（不为 0 = 这一腿没转换成功）──
pn OLD_AMR_DIRECT '^ *result = rt\.agent_memory\.list_agent_memories\(' $A
pn OLD_MBR_DIRECT '^ *members = runtime\(request\)\.store\.list_members\(\)' $M
pn OLD_LLR_DIRECT '^ *deleted = runtime\(request\)\.store\.clear_answer_cache' $L

# ── 量具自身 ──
p G_SELFTEST 'def self_test' $G
p G_OK 'SELFTEST OK' $G
p G_STOREFACE '_is_store_face' $G
p G_BODY 'pos_body' $G
p G_SUBSYS '"subsys"' $G
p G_MEMO '同步但纯内存' $G
p G_TRANSITIVE '--transitive' $G
w G_LINES $G

# ── 新锁自身（形状，不是结果）──
pn T_DEFS '^def test_' $T
p T_LANDING 'LANDING = [' $T
p T_OFFLOADED '_offloaded_ids' $T
p T_SELFT 'test_gauge_self_test_passes' $T
p T_HEART 'keeps_loop_beating' $T
p T_REV 'stay_on_loop' $T
p T_RULER 'uses_shared_ruler' $T
w T_LINES $T
w L_AMR $A
w L_MBR $M
w L_SGR $S
w L_VRS $V
w L_LLR $L
w L_MCR $C
