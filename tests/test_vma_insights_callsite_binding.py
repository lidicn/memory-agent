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


def test_outward_method_params_cover_what_the_toolspec_declares():
    """ToolSpec 登记的参数（对外可见的入参）必须都能被门面接住。"""
    by_method = {s.method: s for s in TOOL_SPECS if s.service == "insights" and s.method}
    for name in LEGACY_OUTWARD_METHODS:
        spec = by_method.get(name)
        if spec is None:
            continue
        declared = {p.name for p in spec.params}
        accepted = {p.name for p in inspect.signature(getattr(InsightService, name)).parameters.values()}
        assert declared <= accepted, f"{name}: ToolSpec 声明但门面不收 {sorted(declared - accepted)}"


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
