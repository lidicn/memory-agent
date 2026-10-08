"""门面切换后「调用点没重接」的结构性回归锁（审计 §十六，P0）。

Phase 4 把 `runtime.insights` 从 `insights_legacy` 换成 `insights/` 包的门面，
方法名一个没变、参数形状却重排了（`(room, category, domain, query, only_enabled, days)`
变成 `(days, room, category, query, limit, only_enabled)`）。MCP handler 与 HTTP 路由
仍按老形状调用，于是：

- 位置传参的点对上新签名 -> 值串到别的槽位（`room=客厅` 变成 `days=客厅`），
  生产读数：`get_entity_catalog` 传房间拿到全屋 501 条；
- 参数比门面多的点 -> TypeError，被 `_degrade` 收成空页，工具从此只回空数据
  （`get_device_usage` / `search_events` / `get_device_health`）；
- 关键字传参传了门面没收的名字 -> 同样 TypeError（`arena.py` / `researcher.py`）。

三条互补断言：
1. **可绑定**：src 里每一个 insights 调用点都能静态 bind（个数 + 关键字名）。
2. **形状一致**：`LEGACY_OUTWARD_METHODS` 的门面签名必须与 legacy 同名方法逐字一致，
   并与 ToolSpec 登记的参数集合一致——对外形状由 ToolSpec/handler 决定，不由引擎决定。
3. **handler 槽位**：MCP handler 位置传参时，变量名必须落进同名形参槽（防止再次串位）。

裁5 落地时补另外三条（同一场事故的另一半与它的运行时后果）：
4. **引擎指向**：门面体里 `self.core/nl/repo/legacy.X(...)` 指向的成员必须存在——
   `_degrade` 对 `AttributeError` 和对 `TypeError` 一样静默，判据得各查一遍。
5. **dispatch 形状 + 真库**：按 `dispatch` 的真实补参规则 bind 得上，且在真 Store 上
   调用后不许以「形状错误」收场（业务上答不出是允许的，炸在半路不允许）。
6. **登记表规模**：登记 11 条 = 实测「仍跑在 legacy 上的对外 insights 工具」全集，
   防止用删条目维持绿。
"""

import ast
import dataclasses
import inspect
import os
import sys
import tempfile

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.insights.api import LEGACY_OUTWARD_METHODS  # noqa: E402
from memory_agent.insights_legacy import InsightService as Legacy  # noqa: E402
from memory_agent.tool_schema import TOOL_SPECS  # noqa: E402

ROOT = os.path.join(_SRC, "memory_agent")

# 门面公开方法的当前签名（含 self 之外的形参）
SIGS = {}
for _name, _member in inspect.getmembers(InsightService, predicate=callable):
    if _name.startswith("__"):
        continue
    try:
        SIGS[_name] = inspect.signature(_member)
    except (TypeError, ValueError):
        pass


def _chain(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return list(reversed(parts))


def _is_insights_target(parts):
    return bool(parts) and ("insights" in parts or parts[-1] == "ins")


def _call_sites(path):
    """产出 (lineno, method, [位置参 AST], [关键字名])。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        fname = ".".join(_chain(f)) if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if fname.endswith(("to_thread", "run_in_executor")) and node.args:
            head = node.args[0]
            if (isinstance(head, ast.Attribute) and _is_insights_target(_chain(head))
                    and head.attr in SIGS):
                yield node.lineno, head.attr, list(node.args[1:]), [k.arg for k in node.keywords]
        elif isinstance(f, ast.Attribute) and _is_insights_target(_chain(f)) and f.attr in SIGS:
            yield node.lineno, f.attr, list(node.args), [k.arg for k in node.keywords]


def _all_sites():
    out = []
    for dirpath, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            if os.path.basename(path) == "insights_legacy.py":
                continue  # legacy 自己不是门面的调用方
            for lineno, method, args, kwnames in _call_sites(path):
                out.append((os.path.relpath(path, _SRC), lineno, method, args, kwnames))
    return out


def _violate_binding(sites):
    bad = []
    for rel, lineno, method, args, kwnames in sites:
        sig = SIGS[method]
        params = [p for p in sig.parameters.values() if p.name != "self"]
        positional = [p.name for p in params
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        has_varpos = any(p.kind == p.VAR_POSITIONAL for p in params)
        has_kwargs = any(p.kind == p.VAR_KEYWORD for p in params)
        if not has_varpos and len(args) > len(positional):
            bad.append(f"{rel}:{lineno} {method} 位置参 {len(args)} 个 > 门面 {len(positional)} 个")
        if not has_kwargs:
            accepted = {p.name for p in params}
            for kw in kwnames:
                if kw and kw not in accepted:
                    bad.append(f"{rel}:{lineno} {method} 传了门面不收的关键字 {kw}")
    return bad


def test_every_insights_call_site_binds_against_the_facade():
    """调用点的参数个数与关键字名必须能被门面接住——这是 TypeError 那一半。"""
    bad = _violate_binding(_all_sites())
    assert not bad, "以下 insights 调用点接不上门面签名：\n" + "\n".join(bad)


def test_outward_methods_keep_the_legacy_parameter_shape():
    """对外 6 条工具线：门面形参名必须与 legacy 同名方法逐字一致。

    形状由调用方（MCP handler / ToolSpec / HTTP 路由）决定，不由引擎内部偏好决定；
    这条断言禁止再出现「换引擎顺手重排参数」的迁移事故。
    """
    for name in LEGACY_OUTWARD_METHODS:
        assert hasattr(Legacy, name), f"legacy 无 {name}，转发无源可指"
        fac = [p.name for p in inspect.signature(getattr(InsightService, name)).parameters.values()
               if p.name != "self"]
        leg = [p.name for p in inspect.signature(getattr(Legacy, name)).parameters.values()
               if p.name != "self"]
        assert fac == leg, f"{name}: 门面 {fac} != legacy {leg}"


def _insights_specs():
    return [s for s in TOOL_SPECS if s.service == "insights" and s.method]


def _dispatch_kwargs(spec):
    """复刻 `tool_schema.dispatch` 的补参规则（1828-1866）。

    dispatch 会把「可选且默认值不是 None」的入参一律补上——这正是
    `query_behavior_events` 的死因：门面签名里没有 `days`，而 ToolSpec 有默认值 7，
    于是每次调用都送出一个门面不收的关键字。锁判据必须按 dispatch 的真实规则来，
    不能只比 ToolSpec 的名字集合。
    """
    kwargs = {}
    for p in spec.params:
        if p.required:
            kwargs[p.name] = "书房" if p.name in ("room", "query") else "x"
        elif p.default is not None:
            kwargs[p.name] = p.default
    kwargs.update(spec.force or {})
    return kwargs


def test_every_insights_toolspec_param_lands_in_a_facade_slot():
    """全部 insights 工具（不只登记表里那几条）：ToolSpec 声明的入参必须都能被门面接住。

    原先这条只遍历 `LEGACY_OUTWARD_METHODS`，等于「只查已经申报过的」——而 Phase 4
    的事故恰恰是没申报。改成遍历 ToolSpec 全集：实测 17 个工具，登记表 11 条。
    """
    for spec in _insights_specs():
        member = getattr(InsightService, spec.method, None)
        assert member is not None, f"{spec.name}: 门面没有 {spec.method}"
        declared = {p.name for p in spec.params}
        accepted = {p.name for p in inspect.signature(member).parameters.values()}
        assert declared <= accepted, \
            f"{spec.name}（{spec.method}）: ToolSpec 声明但门面不收 {sorted(declared - accepted)}"


def test_dispatch_would_bind_every_insights_tool():
    """按 dispatch 的真实补参形状静态 bind：接不住的工具就是「每次调用必炸」的那一类。"""
    bad = []
    for spec in _insights_specs():
        kwargs = _dispatch_kwargs(spec)
        try:
            # bind_partial：只判「送出去的键接不接得住」。门面是未绑定方法，
            # self 由运行时提供，不该由这条断言补位。
            inspect.signature(getattr(InsightService, spec.method)).bind_partial(**kwargs)
        except TypeError as exc:
            bad.append(f"{spec.name}（{spec.method}）补参 {sorted(kwargs)} -> {exc}")
    assert not bad, "dispatch 形状接不上门面：\n" + "\n".join(bad)


def test_mcp_handlers_do_not_land_in_the_wrong_slot():
    """MCP handler 位置传参时，第 i 个实参的变量名必须就是门面第 i 个形参。

    只查 mcp_server.py 的对外工具 handler：那里的实参变量名就是工具自己的形参名，
    所以这条规则没有别名误报（llm_routes 里 `eid` -> `entity_id` 那种不算）。
    """
    path = os.path.join(ROOT, "mcp_server.py")
    bad = []
    for lineno, method, args, _kw in _call_sites(path):
        if method not in LEGACY_OUTWARD_METHODS:
            continue
        positional = [p.name for p in SIGS[method].parameters.values() if p.name != "self"]
        for i, a in enumerate(args):
            if i >= len(positional) or not isinstance(a, ast.Name):
                continue
            if a.id != positional[i]:
                bad.append(f"mcp_server.py:{lineno} {method} 第{i}参 {a.id} 落进 {positional[i]}")
    assert not bad, "handler 实参串位：\n" + "\n".join(bad)


def test_legacy_outward_ledger_is_complete_in_both_directions():
    """登记表必须 = 「对外工具里计算仍跑在 legacy 上」的完整清单，漏登记 / 空登记都算红。

    正向：登记的项必须真的转发回 legacy（防止有人把转发改回未验证的新引擎）。
    反向：ToolSpec 里任何 `service="insights"` 的工具，只要门面体仍然 `self.legacy.X(`，
    就必须出现在登记表里——Phase 4 的教训是「换引擎不申报」，这条把申报变成硬约束。
    """
    import memory_agent.insights.api as API

    declared = set(LEGACY_OUTWARD_METHODS)
    for name in declared:
        src = inspect.getsource(getattr(API.InsightService, name))
        assert f"self.legacy.{name}(" in src, f"{name} 未走 legacy 转发"

    undeclared = []
    for spec in TOOL_SPECS:
        if spec.service != "insights" or not spec.method or spec.method in declared:
            continue
        member = getattr(API.InsightService, spec.method, None)
        if member is None:
            continue
        if f"self.legacy.{spec.method}(" in inspect.getsource(member):
            undeclared.append((spec.name, spec.method))
    assert not undeclared, f"仍跑在 legacy 上却没登记进 LEGACY_OUTWARD_METHODS：{undeclared}"


# ── 过滤器真的生效（生产读数：门面把 room/category/query 全丢了）────────────

from memory_agent.config import Config  # noqa: E402
from memory_agent.store import Store  # noqa: E402

ROOMS = {
    "书房": {"enabled": True, "entities": {"light.shufang_desk": {"name": "书房台灯", "domain": "light"}}},
    "客厅": {"enabled": True, "entities": {"media_player.living_tv": {"name": "客厅电视", "domain": "media_player"}}},
}
W0, W1 = "2026-01-01T00:00:00", "2026-01-01T23:59:59"


def _live():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
    rows = []
    for eid, room in (("light.shufang_desk", "书房"), ("media_player.living_tv", "客厅")):
        for h in (9, 21):
            for s, m in (("on", 0), ("off", 30)):
                rows.append({"entity_id": eid, "ts": f"2026-01-01T{h:02d}:{m:02d}:00",
                             "new_state": s, "room": room, "day": "2026-01-01",
                             "attrs_json": '{"friendly_name": "%s"}' % eid})
    st.insert_events(rows)
    return st, InsightService(st, dataclasses.replace(Config(), rooms=ROOMS))


def test_entity_catalog_room_filter_is_honored_and_flat_list_present():
    st, svc = _live()
    try:
        one = svc.entity_catalog(room="书房")
        assert one.get("error") is None, one
        ids = [e["entity_id"] for e in one["entities"]]
        assert ids == ["light.shufang_desk"], ids
        assert {e["room"] for e in one["entities"]} == {"书房"}, one["entities"]
        full = svc.entity_catalog()
        assert sorted(e["entity_id"] for e in full["entities"]) == sorted(ROOMS["书房"]["entities"]) + sorted(ROOMS["客厅"]["entities"])
        # 两代载荷键并存：legacy 的分组 rooms 与门面的扁平 entities
        assert isinstance(one.get("rooms"), dict) and "entities" in one
    finally:
        st.close()
        os.remove(st.db_path)


def test_search_events_filters_are_honored():
    st, svc = _live()
    try:
        allrows = svc.search_events(start=W0, end=W1, behavior_only=False)
        assert allrows.get("error") is None, allrows
        assert allrows["total"] == 8, allrows["total"]
        study = svc.search_events(room="书房", start=W0, end=W1, behavior_only=False)
        assert study["total"] == 4, study["total"]
        assert {e["entity_id"] for e in study["events"]} == {"light.shufang_desk"}
        tv = svc.search_events(category="media", start=W0, end=W1, behavior_only=False)
        assert {e["entity_id"] for e in tv["events"]} == {"media_player.living_tv"}, tv["total"]
        byq = svc.search_events(query="台灯", start=W0, end=W1, behavior_only=False)
        assert {e["entity_id"] for e in byq["events"]} == {"light.shufang_desk"}, byq["total"]
        # 分页与摘要键仍然在（ToolSpec 承诺 next_offset / count）
        page = svc.search_events(start=W0, end=W1, limit=3, behavior_only=False)
        assert page["limit"] == 3 and page["has_more"] is True and page["next_offset"] == 3, page
        summ = svc.search_events(start=W0, end=W1, summarize=True, behavior_only=False)
        assert "summary" in summ, sorted(summ)
    finally:
        st.close()
        os.remove(st.db_path)


def test_device_usage_reports_items_and_honors_room():
    st, svc = _live()
    try:
        out = svc.device_usage(entity_id="light.shufang_desk,media_player.living_tv",
                               start=W0, end=W1)
        assert out.get("error") is None, out
        assert out["total"] == out["device_count"] == 2, out
        assert [d["entity_id"] for d in out["items"]] == [d["entity_id"] for d in out["devices"]]
        study = svc.device_usage(room="书房", start=W0, end=W1)
        assert [d["entity_id"] for d in study["items"]] == ["light.shufang_desk"], study
        assert study["items"][0]["total_on_seconds"] > 0, study["items"][0]
        assert study["total_on_seconds"] > 0 and "total_on_human" in study, sorted(study)
        # legacy 的既有约定：无定位方式不算"全屋"，算用错参数
        none = svc.device_usage(start=W0, end=W1)
        assert none["ok"] is False and "定位" in str(none.get("error")), none
    finally:
        st.close()
        os.remove(st.db_path)


def test_behavior_insights_returns_the_documented_report_fields():
    st, svc = _live()
    try:
        out = svc.behavior_insights(days=1, rooms="书房")
        assert out.get("error") is None, out
        for field in ("daily_rhythm", "room_transitions", "anomalies", "daily_totals"):
            assert field in out, (field, sorted(out))
        assert out["behavior_only"] is True
    finally:
        st.close()
        os.remove(st.db_path)


# ── 门面向外调出去的那一半：引擎成员必须真实存在（裁5 的姊妹判据）──────────────
#
# `scan_insights_callsites.py` 判的是「外面调进来」的参数形状；门面体里
# `self.core.X(...)` 指向一个不存在的成员是同一场事故的另外一半，而且更隐蔽：
# 参数全绑得上，`_degrade` 照样把 AttributeError 收成空页。对 HEAD 跑扫描器的读数是
# 「35 个指向 / 6 处空」，其中 `get_climate_sessions`、`explain_insight` 是对外 MCP 工具，
# `water_purifier_usage` 是 `templates.py` 净水器日报的消费点。

_ENGINE_SCAN = os.path.join(os.path.dirname(__file__), "..", "scripts",
                            "scan_insights_engine_attrs.py")


def _engine_scanner():
    import importlib.util

    spec = importlib.util.spec_from_file_location("scan_insights_engine_attrs", _ENGINE_SCAN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_facade_engine_pointers_all_resolve():
    """门面体里每一个 `self.<引擎>.<成员>(...)` 都必须指向真实存在的成员。"""
    mod = _engine_scanner()
    targets = mod.load_targets()
    hits = mod.collect(os.path.join(ROOT, "insights", "api.py"), targets)
    missing = [(m, ln, f"self.{o}.{a}") for m, ln, o, a in hits
               if not hasattr(targets[o], a)]
    assert not missing, "门面指向不存在的引擎成员（会被 _degrade 静默收成空页）：\n" + \
        "\n".join(f"{m} (api.py:{ln}) -> {t}" for m, ln, t in missing)
    assert len(hits) >= 30, f"扫描器没有真的读到位（只找到 {len(hits)} 个指向）"


def test_ledger_size_matches_the_measured_outward_surface():
    """登记表现在有 11 条：ToolSpec 全集 17 个工具里，仍跑在 legacy 上的都必须在表内。

    这条把「登记表有多大」也钉住——裁5 落地时它是 6 条，Q1=A 的判据（过滤器语义是
    用户可见的正确性）扫出另外 5 条同病工具后必须是 11 条，不能靠删条目维持绿的假象。
    """
    assert len(LEGACY_OUTWARD_METHODS) == 11, LEGACY_OUTWARD_METHODS
    with_toolspec = [n for n in LEGACY_OUTWARD_METHODS
                     if any(s.method == n for s in _insights_specs())]
    assert len(with_toolspec) == 10, with_toolspec  # 只有 water_purifier_usage 不是 MCP 工具


# ── 工具在目录里、返回永远为空——用真库把「空」和「炸」区分开 ──────────────────

BINDING_ERRORS = ("unexpected keyword argument", "positional arguments",
                  "has no attribute", "takes from", "required positional")

# 这两条会走 embedding/LLM 链路（dispatch 里也一样），容器里判不了绑定形状以外的东西，
# 它们的绑定由上面两条静态用例覆盖。
_NL_TOOLS = ("ask_memory", "route_question")


def test_no_insights_tool_dies_on_a_binding_shape():
    """把 17 个对外工具按 dispatch 形状打在真库上：允许业务上答不出，不许死于形状。"""
    st, svc = _live()
    try:
        bad = []
        for spec in _insights_specs():
            if spec.name in _NL_TOOLS:
                continue
            try:
                out = getattr(svc, spec.method)(**_dispatch_kwargs(spec))
            except (TypeError, AttributeError) as exc:
                bad.append(f"{spec.name} 直接外抛 {type(exc).__name__}: {exc}")
                continue
            err = str(out.get("error")) if isinstance(out, dict) else ""
            if any(m in err for m in BINDING_ERRORS):
                bad.append(f"{spec.name} 被降级成形状错误：{err[:90]}")
        assert not bad, "以下工具仍接不上门面/引擎：\n" + "\n".join(bad)
    finally:
        st.close()
        os.remove(st.db_path)


def test_query_behavior_events_reads_the_vision_table_and_honors_member():
    """`query_behavior_events` 读的是 `behavior_events`（VLM 记录），不是 HA 的 `events`。

    生产实测：`events` 989,240 行里 `person` 非空 **0 行**，`behavior_events` 4,023 行里
    3,595 行带人员——门面原先的实现（读 events）永远答不出「有谁在书房」。
    """
    st, svc = _live()
    try:
        st.insert_behavior_event({"server_ts": "2026-01-01T15:10:00", "day": "2026-01-01",
                                  "room": "书房", "persons": [{"name": "成员甲"}],
                                  "count": 1, "action": "坐在桌前使用电脑",
                                  "scene": "书房有人使用电脑"})
        # 显式窗口：`days=7` 会按「今天往前 7 天」解析，种子里的 2026-01-01 落在窗外。
        out = svc.query_behavior_events(room="书房", start=W0, end=W1)
        assert out.get("error") is None, out
        assert out["ok"] is True and out["count"] == 1, out
        assert out["events"][0]["persons"] == ["成员甲"], out["events"]
        assert svc.query_behavior_events(room="书房", member="成员甲", start=W0, end=W1)["count"] == 1
        assert svc.query_behavior_events(room="书房", member="查无此人", start=W0, end=W1)["count"] == 0
        # 两代键并存（裁5 Q2=A）：legacy 键 + 门面代分页键
        for key in ("ok", "window", "room", "member_filter", "count", "events"):
            assert key in out, (key, sorted(out))
        for key in ("offset", "limit", "has_more", "time_range"):
            assert key in out, (key, sorted(out))
        # 裁5 **追加 Q-A** 之后的契约：`total` = 匹配总数（不带 LIMIT 的 COUNT），
        # `count` = 本页条数，两键分家、不许互相冒充。原先这条锁钉的是「没有 total」
        # （那时 Store 里没有可信的全量口径，改名冒充就是谎报）。
        assert out["total"] == 1 and out["count"] == 1, out
        assert out["total_exact"] is True, out
        empty = svc.query_behavior_events(room="书房", member="查无此人", start=W0, end=W1)
        assert empty["total"] == 0 and empty["count"] == 0, empty
        assert svc.query_behavior_events(room="不存在的房间")["ok"] is False
    finally:
        st.close()
        os.remove(st.db_path)


def test_get_last_event_returns_both_key_generations():
    """`get_last_event` 不再是恒空：legacy 键给全，门面代分页键并存。"""
    st, svc = _live()
    try:
        out = svc.get_last_event("light.shufang_desk", None, None, "off", 3650)
        assert out.get("error") is None, out
        assert out["ok"] is True, out
        assert out["entity_id"] == "light.shufang_desk" and out["new_state"] == "off", out
        assert out["event"] and out["event"]["entity_id"] == "light.shufang_desk", out
        assert (out["total"], out["offset"], out["limit"], out["has_more"]) == (1, 0, 1, False), out
        miss = svc.get_last_event(entity_id="light.shufang_desk", transition="on", days=3650)
        assert miss["ok"] is False and miss["event"] is None and miss["total"] == 0, miss
    finally:
        st.close()
        os.remove(st.db_path)


def test_forwarded_tools_keep_both_key_generations():
    """裁5 Q2=A（两代键并存为正式口径）：新转 legacy 的四条必须同时给出两代键。

    只查键的存在，不查数据——这些用例跑在种子库里，业务上多半答不出内容；
    但「下游按哪一代键取值都能拿到」是裁定里的正式口径，必须有实调用做证。
    """
    st, svc = _live()
    try:
        probes = {
            "query_behavior_events": (dict(room="书房", start=W0, end=W1),
                                      ("ok", "window", "count", "events"),
                                      ("offset", "limit", "has_more", "time_range")),
            "get_last_event":        (dict(entity_id="light.shufang_desk",
                                           transition="off", days=3650),
                                      ("ok", "entity_id", "friendly_name", "ts",
                                       "old_state", "new_state", "transition"),
                                      ("event", "total", "offset", "limit", "has_more")),
            "climate_sessions":      (dict(room="书房", start=W0, end=W1),
                                      ("ok", "window", "sessions"),
                                      ("total", "offset", "limit", "has_more", "time_range")),
            "explain_insight":       (dict(insight_id="不存在"),
                                      ("ok", "error"),
                                      ("insight_id", "found", "total", "offset", "has_more")),
        }
        for method, (kwargs, legacy_keys, compat_keys) in probes.items():
            out = getattr(svc, method)(**kwargs)
            assert isinstance(out, dict), (method, type(out))
            missing = [k for k in legacy_keys if k not in out]
            assert not missing, f"{method} 少了 legacy 承诺键 {missing}：{sorted(out)}"
            missing = [k for k in compat_keys if k not in out]
            assert not missing, f"{method} 少了门面代兼容键 {missing}：{sorted(out)}"
            assert not any(m in str(out.get("error")) for m in BINDING_ERRORS), out
    finally:
        st.close()
        os.remove(st.db_path)


def test_scan_annotation_tells_whether_total_is_a_scan_cap():
    """Q4=A：命中 `max_scan` 的返回体必须自己说「这一页只覆盖了窗口前段」。

    裁5 **追加 Q-A** 之后 `total` 改由不带 LIMIT 的 COUNT 给出，所以命中上限时它不再等于
    扫描行数——期望值从 `total == 4 / total_exact False` 改成 `total == 8 / total_exact True`，
    `truncated` 仍然如实为 True（它说的是切片，与总数精确不冲突）。
    """
    from memory_agent.insights.models import InsightConfig

    st, svc = _live()
    try:
        full = svc.get_events(start=W0, end=W1, limit=2)
        assert full["total"] == 8 and full["truncated"] is False, full
        assert full["total_exact"] is True and full["scan_limit"] == 30000, full
        # 分页翻页时 total 仍是全量（旧实现切片后才数，total 恒等于本页条数）
        page2 = svc.get_events(start=W0, end=W1, limit=2, offset=2)
        assert page2["total"] == 8 and page2["offset"] == 2 and page2["has_more"] is True, page2
        assert isinstance(page2["events"][0], dict), "条目必须是可序列化 dict"

        capped = InsightService(st, InsightConfig(max_scan=4))
        out = capped.get_events(start=W0, end=W1, limit=2)
        assert out["total"] == 8 and out["truncated"] is True, out
        assert out["total_exact"] is True and out["scan_limit"] == 4, out
        # `count` 是本页条数，与 `total` 分家（Q-A：两键不许互相冒充）
        assert out["count"] == 2 and len(out["events"]) == 2, out
    finally:
        st.close()
        os.remove(st.db_path)
