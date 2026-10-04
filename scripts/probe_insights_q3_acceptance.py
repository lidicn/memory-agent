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
print(f"  repo 层 room : 无过滤 scan={n_all} count={c_all} | {ROOM} scan={n_room} count={c_room} "
      f"-> {'生效' if q31['room'] else '不生效'}")
print(f"  repo 层 domain: light count={c_dom} -> {'生效' if q31['domain'] else '不生效'}")
sig = inspect.signature(fac.repo.load_events)
print(f"  repo.load_events 形参: {list(sig.parameters)}")


def S(**kw):
    """门面的新引擎扫描路径，一律按关键字传（位置串位本身就是本轮在抓的那族缺陷）。

    成对读数要在改前快照（HEAD）上跑得下去：HEAD 的 `_search` 没有 `domain/state/order/
    summarize` 这四个位，直接抛 TypeError。这里把它收成 `total=-1` 并记下原因，
    让「无处可去」以读数形式出现在改前列，而不是让整支量具红掉。
    """
    a = {"start": "", "end": "", "entity_id": "", "room": "", "category": "", "query": "",
         "limit": 50, "offset": 0, "behavior_only": True}
    a.update(kw)
    try:
        return fac._search(**a)
    except TypeError as exc:
        return {"total": -1, "count": -1, "events": [], "filters": {},
                "_err": "%s: %s" % (type(exc).__name__, exc)}


base = S()
b_total = int(base.get("total") or -1)


def diff_vs_base(payload):
    """「传了这个位、读数与基线不同」才算生效；**拿不到读数判 None（未量到），绝不判 True**。

    改前实测就是这条的反例：HEAD 的 `_search` 没有 `domain`/`state` 位 ⇒ `S()` 把 TypeError
    收成 `total=-1`，而旧判据 `int(total) != b_total` 把 -1 当成「与基线不同」，于是
    「无处可去」被读成「生效」。这是本轮在抓的那一族的镜像形状：**错误哨兵值参与了判据**，
    一次假绿比一次红更难发现，因为它看起来像证据。
    `total=0` 是合法读数（fail-closed 那一格），所以只认 `None` 与 `_err`，不用 `or -1` 兜。
    """
    if payload.get("_err") or payload.get("total") is None:
        return None
    return int(payload["total"]) != b_total


def word(flag):
    return {True: "生效", False: "接收但不生效", None: "未量到（该位无处可去）"}[flag]


def F(payload):
    """`filters` 回声取值：HEAD 的 `_search` **根本不返回 `filters` 键**（改前实测
    `filters=[]`），所以这里一律走 `.get`——直接下标会让量具在改前崩成 KeyError，
    而「改前跑不下去」等于把要交的读数弄丢了。"""
    return (payload or {}).get("filters") or {}
top_state = st.connect().execute(
    "SELECT new_state FROM events WHERE new_state IS NOT NULL AND new_state<>'' "
    "GROUP BY new_state ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
top_state = str((top_state or ["on"])[0])

cat, qry, dom2, sty = S(category=CATEGORY), S(query=QUERY), S(domain="light"), S(state=top_state)
q31["category"] = diff_vs_base(cat)
q31["query"] = diff_vs_base(qry)
q31["state"] = diff_vs_base(sty)
# `domain` 这一格两路都要过：repo 层 `domains=[light]` 与门面 `domain="light"`。
# 门面那一路在改前是「形参无处可去」⇒ 判 None（未量到），不能因为 repo 层生效就整格判绿——
# Q3-1 问的是门面收下的六个位，repo 层单方面生效不等于门面把位传下来了。
dom_facade = diff_vs_base(dom2)
q31["domain"] = (dom_facade and (c_dom != c_all)) if dom_facade is not None else None
print(f"  门面基线      : total={b_total} count={base.get('count')} ok={base.get('ok')} "
      f"filters={sorted(F(base))}")
print(f"  门面 category : 传 {CATEGORY} total={cat['total']} "
      f"domains_resolved={F(cat).get('domains_resolved', '-')} "
      f"err={cat.get('_err', '-')} -> {word(q31['category'])}")
print(f"  门面 query    : 传 {QUERY} total={qry['total']} "
      f"entities_resolved={F(qry).get('entities_resolved', '-')} "
      f"err={qry.get('_err', '-')} -> {word(q31['query'])}")
print(f"  门面 domain   : 传 light total={dom2['total']}（与 repo 层同域互证 {c_dom}） "
      f"err={dom2.get('_err', '-')} -> {word(q31['domain'])}"
      f"（判据=门面与 repo 两路都生效）")
print(f"  门面 state    : 传 {top_state!r} total={sty['total']} err={sty.get('_err', '-')} -> "
      f"{word(q31['state'])}")

asc, desc = S(order="asc", limit=5), S(order="desc", limit=5)
asc_ts = [e.get("ts") for e in asc["events"]]
desc_ts = [e.get("ts") for e in desc["events"]]
q31["order"] = (None if (asc.get("_err") or desc.get("_err")) else
                (bool(asc_ts) and bool(desc_ts) and asc_ts == sorted(asc_ts)
                 and desc_ts == sorted(desc_ts, reverse=True)
                 and asc_ts != desc_ts
                 and F(asc).get("order") == "asc" and F(desc).get("order") == "desc"))
order_word = {True: "生效且回声同源", False: "不生效或回声与取数不同源",
              None: "未量到（该位无处可去）"}[q31["order"]]
print(f"  门面 order    : asc 首末={asc_ts[:1]}/{asc_ts[-1:]} desc 首末={desc_ts[:1]}/{desc_ts[-1:]} "
      f"err=({asc.get('_err', '-')},{desc.get('_err', '-')}) "
      f"回声=({F(asc).get('order', '-')},{F(desc).get('order', '-')}) -> {order_word}"
      f"（改前这一格先问『位有没有落点』：HEAD 的 `order` 无处可去 ⇒ 两侧同序）")

sm, nosm = S(summarize=True), S(summarize=False)
q31["summarize"] = (None if (sm.get("_err") or nosm.get("_err")) else
                    ("summary" in sm and "summary" not in nosm
                     and int(sm.get("count") or 0) == int(nosm.get("count") or 0)
                     and len(sm.get("events") or []) <= 50 <= int(nosm.get("count") or 0)))
sum_word = {True: "生效且 count 未被 50 条样本冒充", False: "不生效或 count 被样本数冒充",
            None: "未量到（该位无处可去）"}[q31["summarize"]]
print(f"  门面 summarize: 有 summary={'summary' in sm} 样本={len(sm.get('events') or [])} "
      f"count={sm.get('count')}（不传时 count={nosm.get('count')} 样本={len(nosm.get('events') or [])}）"
      f" err=({sm.get('_err', '-')},{nosm.get('_err', '-')}) -> {sum_word}"
      f"（注：本条要 limit≥50 且窗口内有足够事件才量得出，读数里的 count 见上）")

unresolved = S(query="绝不相干的设备名甲乙丙")
fail_closed_ok = (int(unresolved["total"]) == 0
                  and "unresolved" in (unresolved.get("filters") or {}))
print(f"  解析不出时    : 传「绝不相干的设备名甲乙丙」total={unresolved['total']} "
      f"filters.unresolved={'unresolved' in (unresolved.get('filters') or {})} "
      f"err={unresolved.get('_err', '-')} -> "
      f"{'fail-closed（不回落成全量）' if fail_closed_ok else 'fail-open（静默放宽成全量）'}")
print(f"  （fail-closed 不在 Q3-1 的六个位里，登记为额外一条：legacy 在这一格是漏的）")

print("\n=== Q3-2 days 是否真按天窗口（同一 room，days=1 / 7 / 30）===")
q32_days = []
for days in (1, 7, 30):
    n, c = scan(days, rooms=[ROOM])
    q32_days.append(c)
    print(f"  days={days:2} room={ROOM}: 扫描={n} 全量匹配={c}")
q32 = q32_days == sorted(q32_days)
print(f"  -> 窗口随 days 单调（{'是' if q32 else '否'}）：{q32_days}")
print("  legacy 同口径（**另一次读**：生产采集在跑，与新引擎那三行差 1–3 行属漂移，"
      "不能当成窗口右界口径差——此前把它解释成口径差是没验证过的归因）：")
for days in (1, 30):
    l = leg.search_events(room=ROOM, days=days, limit=1, behavior_only=False)
    print(f"  days={days:2} legacy total={l.get('total')}")

print("\n=== Q3-3/Q4 total 有没有被 MAX_SCAN 截断，截断时是否如实上报 ===")
# 窗口**冻结**：三次读（前置计数 / 门面 / 后置计数）必须落在同一个绝对时间窗上。
# 此前用 `days=30`，右界随 `house_now()` 滑动 ⇒ 新事件一边被写进窗口一边被数，
# 三次读天然不等（实测 950208/950279/950173 这一量级的漂移），于是「total 等于独立计数」
# 这条判据在生产库上**永远判不齐**——那是量具的错，不是代码的错，也不能反过来当成绿。
ANCHOR_START, ANCHOR_END = fac._days_to_range(30, "", "")
tr30 = fac._tr(ANCHOR_START, ANCHOR_END)
c30_before = fac.repo.count_events(tr30)
g = fac.get_events(start=ANCHOR_START, end=ANCHOR_END, limit=5)
c30_after = fac.repo.count_events(tr30)
stable = (c30_before == c30_after)
total_g = int(g.get("total") or -1)
truncated_g = g.get("truncated")
# 口径更正（裁5 追加 **Q-A** 之后）：旧判据 `total_exact is False and truncated is True`
# 量的是「total 由被截的切片行数冒充、并且如实上报」——Q-A 授权新增不带 LIMIT 的计数查询
# 之后，`total` 已经是匹配总数，`total_exact` 就该是 True；继续按旧判据会把修好的形态读成红。
# 现在三条一起看：① total 等于同一冻结窗口上的独立计数；② total_exact=True；
# ③ 切片被截时 truncated/scan_limit 同时如实给出，且 total 不再等于 count。
q33 = None if not stable else (
    total_g == c30_before and g.get("total_exact") is True
    and (truncated_g is not True or (g.get("scan_limit") == fac.repo.scan_limit
                                     and total_g > int(g.get("count") or 0))))
print(f"  冻结窗口 {ANCHOR_START} ~ {ANCHOR_END}（右界在过去的绝对窗，新事件落不进来）")
print(f"  30 天全量匹配={c30_before}（后置复点={c30_after}，窗口{'稳定' if stable else '在漂移'}） "
      f"门面报 total={total_g} count={g.get('count')} "
      f"truncated={truncated_g} scan_limit={g.get('scan_limit')} "
      f"total_exact={g.get('total_exact')}（repo.scan_limit={fac.repo.scan_limit}）")
if q33 is True:
    q33_verdict = "total 不被切片冒充且截断如实上报（Q-A + Q4=A 达成）"
elif q33 is None:
    q33_verdict = ("不可判：同一冻结窗口两次独立计数都不一致，说明采集把窗口搬动了——"
                   "本条既不折算成绿，也不折算成红")
else:
    q33_verdict = "不齐（读数见上，结论以行为准）"
print(f"  -> {q33_verdict}")

print("\n=== Q3-4 legacy 的分页/窗口键在新引擎有没有对应物 ===")
# 口径（裁6 §二.4「读数必须标口径」）：Q3-4 问的是**新引擎自己**有没有对应物，所以这里量的
# 必须是 `core.*` / `repo.*` 那几条内部路径。门面当前对外返回的键集另列（NEW_KEYS），
# 因为那四条按裁5 Q1=A 仍转 legacy——拿门面键集去判 Q3-4 等于拿 legacy 比 legacy，
# 必然全绿、零信息量。
# 第二条口径：`count`/`next_offset` 实测只有 `search_events` 在 legacy 顶层真的给，
# 另四条顶层没有（`device_usage` 给 `device_count`、`behavior_insights` 给 `total_events`、
# `device_health` 给 `window_days`）。所以判据按「该方法 legacy 实给的分页/窗口键 ∩ 词表」
# 逐条算，不能拿一个统一集合套五条——那会把「legacy 自己就没有」误记成新引擎的欠账。
PAGINATION_VOCAB = {"ok", "count", "next_offset", "offset", "limit", "has_more", "total",
                    "window", "time_range", "truncated", "total_exact", "scan_limit"}
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
NEWENGINE_PATHS = {
    "search_events": ("_search", lambda: fac._search("", "", "", "", "", "", 50, 0,
                                                     behavior_only=True)),
    "device_usage": ("core.usage", lambda: fac.core.usage(fac._tr(days=7))),
    "behavior_insights": ("core.behavior_insights",
                          lambda: fac.core.behavior_insights(fac._tr(days=7))),
    "device_health": ("core.device_health", lambda: fac.core.device_health(fac._tr(days=7))),
    # 新引擎没有目录组装法：只有 `repo.entity_catalog` 的原行列表（非信封），
    # 门面那条是纯转 legacy。这一格按「无信封可对齐」登记，不折算成「缺两个键」。
    "entity_catalog": ("repo.entity_catalog", lambda: fac.repo.entity_catalog()),
}
q34 = {}
for m in sorted(NEW_KEYS):
    path_name, thunk = NEWENGINE_PATHS[m]
    err = ""
    try:
        payload = thunk()
    except Exception as exc:  # noqa: BLE001 - 量具要把失败读成读数，不外抛
        payload, err = None, " %s: %s" % (type(exc).__name__, exc)
    is_env = isinstance(payload, dict)
    new_k = set(payload) if is_env else set()
    # 注意：`want` 要留成**集合**参与差集，`sorted(want)` 只用于打印。
    # 写成 `sorted(...) - new_k` 会在**第一个方法**就 TypeError（`list` 减 `set`），
    # 于是整段 Q3-4 一条读数都出不来——量具自己也要能被自己的形状咬到。
    want = PAGINATION_VOCAB & LEGACY_KEYS[m]
    missing = sorted(want - new_k)
    q34[m] = (not missing) if is_env else None
    print(f"  {m:17} legacy 实给分页/窗口键={sorted(want)} | 新引擎路径={path_name}"
          f"{'' if is_env else '（原行列表，无信封）'} 缺={missing or '无'}{err}")
    print(f"  {'':17} 载荷键差集：仅 legacy={sorted(LEGACY_KEYS[m] - new_k)} "
          f"仅新引擎={sorted(new_k - LEGACY_KEYS[m])}"
          f"（切换时要走 DCD 载荷键名登记：等价物不等于同名）")

print("\n=== Q3-5 「接收但不生效」的入参清单（新引擎无对应物的都要么实现要么下架）===")
# 口径（裁6 §二.5「漏咬与跨平台等价位要能区分」）：新引擎里**异名同职**的实现不是「无对应物」，
# 但也不能当作等价——名字不同就是载荷键名/调用点要另走登记的另一件事。所以分三档报：
# 同名有 / 异名等价物（语义要逐条核）/ 真无。
NEWENGINE_ALIAS = {
    "device_usage": "usage",
    "data_coverage": "coverage",
    "get_data_quality": "data_quality",
    "get_user_persona": "user_persona",
    "get_behavior_insights_compare": "compare_insights",
}
q35_dead = []
q35_alias = []
for spec in [s for s in TOOL_SPECS if s.service == "insights" and s.method]:
    engine = getattr(fac.core, spec.method, None) or getattr(fac.repo, spec.method, None)
    alias_name = NEWENGINE_ALIAS.get(spec.method, "")
    alias = getattr(fac.core, alias_name, None) if alias_name else None
    accepted = set(inspect.signature(getattr(Facade, spec.method)).parameters)
    still = {name for name in ("summarize", "include_timeline", "on_states",
                               "debounce_seconds", "stale_days", "only_enabled",
                               "category", "query", "domain", "state", "order")
             if name in accepted}
    if not engine and alias is not None:
        q35_alias.append(f"{spec.name}->{spec.method}≈core.{alias_name}")
    elif not engine:
        q35_dead.append(f"{spec.name}->{spec.method}")
    print(f"  {spec.name:28} -> {spec.method:20} 门面收的过滤位={sorted(still) or '-'}"
          f"（新引擎对应物：{'同名有' if engine else f'异名 {alias_name}' if alias else '无'}）")
print(f"  同名无实现但存在异名等价物（语义待逐条核，切换时要另走载荷键名登记）："
      f"{len(q35_alias)} 个 {q35_alias or ''}")
print(f"  逐项参数级判定需要人工核对新引擎的实现语义；本行只给方法级候选清单："
      f"{len(q35_dead)} 个方法在新引擎既无同名也无异名等价 {q35_dead or ''}")

print("\n=== Q3-6 切换时要交的三项对比读数（本脚本即该项的量具）===")
for tool in ("search_events", "entity_catalog", "device_usage", "behavior_insights",
             "device_health"):
    new_k, leg_k = NEW_KEYS[tool], LEGACY_KEYS[tool]
    print(f"  {tool}: ① 返回键集合 门面/legacy/并集 = {len(new_k)}/{len(leg_k)}/"
          f"{len(new_k | leg_k)}（仅门面 {len(new_k - leg_k)}、仅 legacy {len(leg_k - new_k)}）"
          f"——口径：这一列量的是**门面当前对外返回**的键（search_events 走 `_search` 新引擎"
          f"扫描路径，另四条按裁5 Q1=A 仍是 legacy 门面体 + 补键），切换后要比的才是"
          f"`core.*` 那一路（见 Q3-4 的新引擎路径列）")
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
q33_word = {True: "齐", False: "不齐（读数见上）", None: "不可判（窗口在漂移，见上方两次独立计数）"}[q33]
print(f"  Q3-3 截断如实上报：{q33_word}")
q34_no = sorted(m for m, v in q34.items() if v is False)
q34_na = sorted(m for m, v in q34.items() if v is None)
print(f"  Q3-4 legacy 分页/窗口键对应物：{'齐' if not (q34_no or q34_na) else '不齐'}"
      f"（有路径但缺键={q34_no or '无'}；新引擎无同名组装法、无信封可对齐={q34_na or '无'}——"
      f"两类分开登记，前者是补齐项、后者属 Q3-5 的未迁清单）")
print(f"  Q3-5 接收但不生效入参：{'齐（无残留）' if not (q35_dead or q35_alias) else '不齐'}"
      f"（既无同名也无异名等价 {len(q35_dead)} 个；有异名等价物、语义待核 {len(q35_alias)} 个）")
print("  Q3-6 三项对比读数：①②③ 已全部打印 -> 齐")
