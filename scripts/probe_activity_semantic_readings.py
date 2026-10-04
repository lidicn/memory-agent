"""MA-裁6 Q4 的三项对比读数：legacy vs 新引擎（生产库，只读）。

裁定原文（`decisions/20261004-AF四件与DB一件与MA五件-裁定.md` §三.6）Q4=认可：
「门面每切一个方法到新实现，必须留 legacy vs 新实现的『返回键集合 + 语义枚举值集合 +
旁挂依赖（规则表/排除表）』三项对比读数，缺一项判红。」

`infer_activities` 这一条这次切到了新引擎（Q1=A 语义回归 + Q2=A 引擎读 `activity_rules`
+ Q3=A 硬排除生效），所以切换前必须先把这三项读数在**生产数据**上量出来：
- ① 返回键集合：legacy 承诺的键在新返回体里是否同名存在；
- ② 语义枚举值集合：两侧各自算出的活动类型集合，以及新引擎的 `source` 档位；
- ③ 旁挂依赖：`activity_rules` 启用/停用条数、`signal_exclusions` 的排除/分类条数，
  与新引擎自己报的 `rule_sources` / `excluded_entities` 是否对得上。

只读：全程只做 SELECT 与内存计算，不建 schema、不写库、不打印凭据。
不外露友好名/人名/设备序列号：规则名、实体 id 一律只报条数；活动标签只报引擎内置的
`bath/study/tv/sleep/cooking` 与时段兜底标签这类固定枚举。

在容器内跑（生产代码 + 生产库）：

    ssh lidicn@192.168.2.200 'docker exec -w /app -e PYTHONPATH=/app/src \\
        memory-agent python /app/scripts/probe_activity_semantic_readings.py'
"""
from collections import Counter

from memory_agent.config import get_config
from memory_agent.insights import InsightService as Facade
from memory_agent.insights.activity import BUILTIN_ACTIVITIES
from memory_agent.insights_legacy import InsightService as Legacy
from memory_agent.store import Store

cfg = get_config()
st = Store(cfg.db_path, tz_offset_hours=cfg.tz_offset_hours)
fac = Facade(st, cfg)
leg = Legacy(cfg, st)
DAYS = 30

print(f"db={cfg.db_path} tz={cfg.tz_offset_hours} days={DAYS} "
      f"max_scan={fac.repo.scan_limit} builtin_rules={len(BUILTIN_ACTIVITIES)}")


def count(tbl, where="1=1", params=()):
    try:
        cur = st.connect().execute(f"SELECT COUNT(*) FROM {tbl} WHERE {where}", params)
        return int(cur.fetchone()[0])
    except Exception as exc:  # noqa: BLE001
        return f"<{type(exc).__name__}: {exc}>"


tr = fac._tr(days=DAYS)
new = fac.infer_activities(days=DAYS)
old = leg.infer_activities(days=DAYS)

print("\n=== ① 返回键集合 ===")
k_new, k_old = set(new or {}), set(old or {})
print(f"  legacy 键（{len(k_old)}）: {sorted(k_old)}")
print(f"  新引擎键（{len(k_new)}）: {sorted(k_new)}")
print(f"  legacy 有而新引擎缺: {sorted(k_old - k_new) or '无'}")
print(f"  新引擎新增: {sorted(k_new - k_old) or '无'}")
readable = {"activities", "total_activities", "activity_types", "window"}
print(f"  消费侧要读的键是否都在: {sorted(readable)} -> "
      f"{'齐' if readable <= k_new else f'不齐（缺 {sorted(readable - k_new)}）'}")

rows = (new or {}).get("activities") or []
old_rows = (old or {}).get("activities") or []
src_set = Counter(str(r.get("source")) for r in rows)
print("\n=== ② 语义枚举值集合 ===")
print(f"  legacy activity_types={sorted({str(a.get('activity')) for a in old_rows})} "
      f"条数={len(old_rows)}")
print(f"  新引擎 activity_types={sorted((new or {}).get('activity_types') or [])} "
      f"条数={len(rows)}")
print(f"  新引擎 source 档位={dict(src_set)}（semantic=语义规则判定，heuristic=无标签设备兜底）")
print(f"  新引擎行级键（取第一行）: {sorted(rows[0]) if rows else '本次窗口无产出'}")
shared = sorted(set(rows[0]) & set(old_rows[0])) if rows and old_rows else []
print(f"  与 legacy 行同名的键（{len(shared)}）: {shared}")

print("\n=== ③ 旁挂依赖（规则表 / 排除表）===")
rules_enabled = count("activity_rules", "enabled=1")
rules_all = count("activity_rules")
excl_all = count("signal_exclusions", "revoked=0")
excl_hard = count("signal_exclusions", "revoked=0 AND exclusion_type='exclude'")
excl_soft = excl_all - excl_hard if isinstance(excl_all, int) and isinstance(excl_hard, int) else "?"
rs = (new or {}).get("rule_sources") or {}
ex = (new or {}).get("excluded_entities") or {}
print(f"  库里：activity_rules 启用/全部 = {rules_enabled}/{rules_all}；"
      f"signal_exclusions 生效中 = {excl_all}（硬排除 {excl_hard}、分类标注 {excl_soft}）")
print(f"  新引擎自报：rule_sources={{builtin: {rs.get('builtin')}, "
      f"activity_rules_table: {rs.get('activity_rules_table')}, custom_applied: {rs.get('custom_applied')}, "
      f"selected: {rs.get('selected')}, events_scanned: {rs.get('events_scanned')}, "
      f"events_total: {rs.get('events_total')}, scan_limit: {rs.get('scan_limit')}, "
      f"scan_truncated: {rs.get('scan_truncated')}, "
      f"excluded_entity_ids: {rs.get('excluded_entity_ids')}, "
      f"error: {rs.get('activity_rules_error') or '-'}}}")
print(f"  新引擎自报：excluded_entities={{count: {ex.get('count')}, sources: {ex.get('sources')}, "
      f"scopes: {ex.get('scopes')}}}")
side_ok = (rs.get("activity_rules_table") == rules_enabled
           and ex.get("count") == excl_hard)
print(f"  -> 自报数与库里真值是否一致：{'一致' if side_ok else '不一致（以库里真值为准，逐条核对）'}")

print("\n=== Q3 硬排除的实际作用面（同一窗口，带/不带排除各读一次）===")
ids = list(ex.get("entity_ids") or [])
n_with = len(fac.repo.load_events(tr, exclude_entity_ids=ids))
n_without = len(fac.repo.load_events(tr))
c_with = fac.repo.count_events(tr, exclude_entity_ids=ids)
c_without = fac.repo.count_events(tr)
print(f"  排除集大小={len(ids)} | load_events 带排除={n_with} 不带={n_without} | "
      f"count_events 带排除={c_with} 不带={c_without}")
verdict = ("排除确实落到了事件读取（差值 %d）" % (c_without - c_with) if c_with != c_without
           else "生产库当前没有生效中的硬排除行（差值 0 属预期，不是失效）")
print("  -> " + verdict)
mat_with = sum(int(r.get("count") or 0) for r in fac.repo.activity_matrix(tr, exclude_entity_ids=ids))
mat_without = sum(int(r.get("count") or 0) for r in fac.repo.activity_matrix(tr))
print(f"  activity_matrix 计数合计：带排除={mat_with} 不带={mat_without} "
      f"（裁6 Q3=A 要求 activity_matrix 一侧也剔除）")

print("\n=== 口径边界（如实登记，不计入「迁完」）===")
print("  已迁：内置 5 条语义规则 + activity_rules 自定义规则的判定，时段启发式降为兜底输出。")
print("  语义一侧的事件来自 load_events 的截断切片（受 max_scan 约束），兜底一侧走 "
      "activity_matrix（SQL 聚合，全窗口）；两个数由 rule_sources 自报，逐信号缺口见 "
      "probe_activity_coverage_gap.py。")
print("  未迁：legacy 的静默间隔睡眠/离家检测器、_noise_entities、detector_report/"
      "signal_inventory、detected_activity 落库——这些仍在 legacy 一侧，"
      "解释侧（explain_insight）因此维持裁5 的 legacy 路由。")
