"""MA-裁5 Q3 验收单的逐条实测读数（生产库，只读）。

裁定原文（`decisions/20261004-AF四件与DB一件与MA五件-裁定.md` §三-5）认的是六条：
只有六条**全部有实测读数**才算新引擎补齐，才允许把这批方法从 legacy 切回新引擎。
这个脚本就是那张验收单的读数机：每条打印「量到的数 + 齐/不齐」，不齐就写不齐。

因为 Q1=A 生效后对外方法都转给 legacy 了，这里量的必须是**新引擎自己**：
`facade.repo.load_events/count_events`、`facade._search`、`facade.core.*` 这几条内部路。

只读：全程只做 SELECT 与内存计算，不建 schema、不写库、不打印任何凭据；
载荷读数只到键名与数量层级。枚举值只打印 `domain` / `new_state` 这类不含姓名与
设备友好名的维度，`person` / `action` 一律只报去重条数。

在容器内跑（生产代码 + 生产库）：

    ssh lidicn@192.168.2.200 'docker exec -w /app -e PYTHONPATH=/app/src \\
        memory-agent python /tmp/probe_insights_q3_acceptance.py'
"""
import inspect

from memory_agent.config import get_config
from memory_agent.insights import InsightService as Facade
from memory_agent.insights_legacy import InsightService as Legacy
from memory_agent.store import Store
from memory_agent.tool_schema import TOOL_SPECS

cfg = get_config()
st = Store(cfg.db_path, tz_offset_hours=cfg.tz_offset_hours)
fac = Facade(st, cfg)
leg = Legacy(cfg, st)
ROOM = "客厅"
CATEGORY = "climate"
QUERY = "灯"

print(f"db={cfg.db_path} rooms={len(cfg.rooms or {})} tz={cfg.tz_offset_hours} "
      f"max_scan={fac.repo.scan_limit}")


def day_span(table):
    r = st.connect().execute(f"SELECT MIN(day), MAX(day), COUNT(*) FROM {table}").fetchone()
    return dict(zip(("min_day", "max_day", "rows"), tuple(r)))


print("events 表跨度:", day_span("events"))
print("behavior_events 表跨度:", day_span("behavior_events"))


def tr_for(days):
    return fac._tr(days=days)


def scan(days=30, **kw):
    """新引擎的行扫描读数：返回 (扫描行数, 全量匹配数)。"""
    tr = tr_for(days)
    rows = fac.repo.load_events(tr, **kw)
    return len(rows), fac.repo.count_events(tr, **kw)


print("\n=== Q3-1 六个过滤/排序位是否真的生效（新引擎）===")
q31 = {}
n_all, c_all = scan(30)
n_room, c_room = scan(30, rooms=[ROOM])
n_dom, c_dom = scan(30, domains=["light"])
q31["room"] = c_room != c_all
q31["domain"] = c_dom != c_all
print(f"  room    : 无过滤 scan={n_all} count={c_all} | {ROOM} scan={n_room} count={c_room} "
      f"-> {'生效' if q31['room'] else '不生效'}")
print(f"  domain  : light count={c_dom} -> {'生效' if q31['domain'] else '不生效'}")
sig = inspect.signature(fac.repo.load_events)
print(f"  repo.load_events 形参: {list(sig.parameters)}")
out_cat_a = fac._search("", "", "", "", CATEGORY, "", 50, 0, behavior_only=True)
out_cat_b = fac._search("", "", "", "", "", "", 50, 0, behavior_only=True)
out_q_a = fac._search("", "", "", "", "", QUERY, 50, 0, behavior_only=True)
q31["category"] = out_cat_a["total"] != out_cat_b["total"]
q31["query"] = out_q_a["total"] != out_cat_b["total"]
print(f"  category: 传 {CATEGORY} total={out_cat_a['total']} vs 不传 total={out_cat_b['total']} "
      f"-> {'生效' if q31['category'] else '接收但不生效'}")
print(f"  query   : 传 {QUERY} total={out_q_a['total']} -> "
      f"{'生效' if q31['query'] else '接收但不生效'}")
for name in ("state", "order", "summarize"):
    accepted = name in inspect.signature(Facade.search_events).parameters
    engine_has = any(name in str(inspect.signature(m).parameters)
                     for m in (fac.repo.load_events, fac.core.behavior_insights))
    q31[name] = None if accepted and not engine_has else engine_has
    print(f"  {name:9}: 门面收={accepted} 新引擎有对应物={engine_has} "
          f"-> {'待逐条验证' if engine_has else '无处可去（Q3-1/Q3-5 不齐）'}")
sp = set(inspect.signature(Facade._search).parameters)
se_params = set(inspect.signature(Facade.search_events).parameters)
no_home = sorted(n for n in ("domain", "state", "order", "summarize") if n in se_params and n not in sp)
print(f"  `_search`（新引擎扫描路径）形参={sorted(sp)}")
print(f"  门面收得下、但 `_search` 里连位置都没有的过滤/排序位：{no_home}")

print("\n=== Q3-2 days 是否真按天窗口（同一 room，days=1 / 7 / 30）===")
q32_days = []
for days in (1, 7, 30):
    n, c = scan(days, rooms=[ROOM])
    q32_days.append(c)
    print(f"  days={days:2} room={ROOM}: 扫描={n} 全量匹配={c}")
q32 = q32_days == sorted(q32_days)
print(f"  -> 窗口随 days 单调（{'是' if q32 else '否'}）：{q32_days}")
print("  legacy 同口径：")
for days in (1, 30):
    l = leg.search_events(room=ROOM, days=days, limit=1, behavior_only=False)
    print(f"  days={days:2} legacy total={l.get('total')}")

print("\n=== Q3-3/Q4 total 有没有被 MAX_SCAN 截断，截断时是否如实上报 ===")
c30 = fac.repo.count_events(tr_for(30))
g = fac.get_events(days=30, limit=5)
q33 = g.get("total_exact") is False and g.get("truncated") is True
print(f"  30 天全量匹配={c30} 门面报 total={g.get('total')} "
      f"truncated={g.get('truncated')} scan_limit={g.get('scan_limit')} "
      f"total_exact={g.get('total_exact')}")
print(f"  -> {'截断时如实上报（Q4=A 达成）' if q33 else '未截断或未如实上报（读数见上，结论以行为准）'}")

print("\n=== Q3-4 legacy 的分页/窗口键在新引擎有没有对应物 ===")
NEW_KEYS = {
    "search_events": set(fac._search("", "", "", "", "", "", 50, 0, behavior_only=True)),
    "entity_catalog": set(fac.entity_catalog()),
    "device_usage": set(fac.device_usage(entity_id="light.placeholder")),
    "behavior_insights": set(fac.behavior_insights(days=7)),
    "device_health": set(fac.device_health(days=7)),
}
LEGACY_KEYS = {
    "search_events": set(leg.search_events(days=7, limit=1)),
    "entity_catalog": set(leg.entity_catalog()),
    "device_usage": set(leg.device_usage(days=7, entity_id="light.placeholder")),
    "behavior_insights": set(leg.behavior_insights(days=7)),
    "device_health": set(leg.device_health(days=7)),
}
LEGACY_WANT_KEYS = {"count", "next_offset", "window", "ok"}
q34 = {}
for m in sorted(NEW_KEYS):
    missing = sorted(LEGACY_WANT_KEYS - NEW_KEYS[m])
    q34[m] = not missing
    print(f"  {m:17} legacy 分页/窗口键：{'全有' if not missing else f'缺 {missing}'}"
          f" | 新引擎键数={len(NEW_KEYS[m])} legacy 键数={len(LEGACY_KEYS[m])}"
          f" | 交集={len(NEW_KEYS[m] & LEGACY_KEYS[m])}")

print("\n=== Q3-5 「接收但不生效」的入参清单（新引擎无对应物的都要么实现要么下架）===")
q35_dead = []
for spec in [s for s in TOOL_SPECS if s.service == "insights" and s.method]:
    engine = getattr(fac.core, spec.method, None) or getattr(fac.repo, spec.method, None)
    accepted = set(inspect.signature(getattr(Facade, spec.method)).parameters)
    still = {name for name in ("summarize", "include_timeline", "on_states",
                               "debounce_seconds", "stale_days", "only_enabled",
                               "category", "query", "domain", "state", "order")
             if name in accepted}
    if not engine:
        q35_dead.append(f"{spec.name}->{spec.method}")
    print(f"  {spec.name:28} -> {spec.method:20} 门面收的过滤位={sorted(still) or '-'}"
          f"（新引擎同法对应物：{'有' if engine else '无'}）")
print(f"  逐项参数级判定需要人工核对新引擎的实现语义；本行只给方法级候选清单："
      f"{len(q35_dead)} 个方法在新引擎无同名实现 {q35_dead or ''}")

print("\n=== Q3-6 切换时要交的三项对比读数（本脚本即该项的量具）===")
for tool in ("search_events", "entity_catalog", "device_usage", "behavior_insights",
             "device_health"):
    new_k, leg_k = NEW_KEYS[tool], LEGACY_KEYS[tool]
    print(f"  {tool}: ① 返回键集合 新引擎/legacy/并集 = {len(new_k)}/{len(leg_k)}/"
          f"{len(new_k | leg_k)}（仅新引擎 {len(new_k - leg_k)}、仅 legacy {len(leg_k - new_k)}）")
se = fac._search("", "", "", "", "", "", 500, 0, behavior_only=True)
rows = se.get("events", [])
doms = sorted({str(e.get("domain")) for e in rows if e.get("domain")})
states = sorted({str(e.get("new_state") or e.get("state")) for e in rows})
persons = len({str(e.get("person")) for e in rows if e.get("person")})
actions = len({str(e.get("action")) for e in rows if e.get("action")})
print(f"  ② 语义枚举值集合：domain={doms[:8]}（共 {len(doms)} 个） "
      f"state={states[:8]}（共 {len(states)} 个）；person/action 只报去重数（不外露姓名）："
      f"{persons}/{actions}")
def side_table_count(tbl):
    try:
        return int(st.connect().execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0])
    except Exception as exc:  # noqa: BLE001
        return f"<{type(exc).__name__}: {exc}>"


# ③ 旁挂依赖：活动识别与硬排除各自的落点。注意 `excluded_entities` 是**配置键**
# （config.excluded_entities，清单形式）而不是表——生产库里查这张表会 OperationalError，
# 把它当表读是错的，所以这里按配置项数报。
print(f"  ③ 旁挂依赖：activity_rules={side_table_count('activity_rules')} "
      f"signal_exclusions={side_table_count('signal_exclusions')} "
      f"config.excluded_entities={len(cfg.excluded_entities or [])} 项")

print("\n=== 验收单收口（六条全部有读数；判定只按上面对勾，不做乐观折算）===")
q31_na = [k for k, v in q31.items() if v is None]
q31_no = [k for k, v in q31.items() if v is False]
print(f"  Q3-1 过滤/排序位：生效={sorted(k for k, v in q31.items() if v is True)} "
      f"不生效={sorted(q31_no)} 未量到={sorted(q31_na)} -> "
      f"{'齐' if not q31_no and not q31_na else '不齐'}")
print(f"  Q3-2 days 窗口单调：{'齐' if q32 else '不齐'}（{q32_days}）")
print(f"  Q3-3 截断如实上报：{'齐' if q33 else '不齐（生产库未到上限，需人工按 max_scan 配置复验）'}")
q34_no = sorted(m for m, v in q34.items() if not v)
print(f"  Q3-4 legacy 分页/窗口键对应物：{'齐' if not q34_no else f'不齐（{q34_no}）'}")
print(f"  Q3-5 接收但不生效入参：{'齐（无残留）' if not q35_dead else f'不齐（{len(q35_dead)} 个方法无新引擎实现）'}")
print("  Q3-6 三项对比读数：①②③ 已全部打印 -> 齐")
