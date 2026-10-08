"""回归锁：裁5 追加 **Q-B**（Q3-1 六个过滤/排序位 + Q3-4 两代分页/窗口键）。

**钉的是什么**：Phase 4 换引擎后，`_search` 收下 `category`/`query`/`domain`/`state`/
`order`/`summarize` 六个位却一个都不落地——生产实测（`scripts/probe_insights_q3_acceptance.py`
Q3-1 改前读数）`category=climate` 与不传的 `total` 都是 **144,143**、`query=灯` 同样是
144,143，`state/order/summarize` 连形参位置都没有。返回体还缺 legacy 承诺的
`ok/count/next_offset/window` 四键（Q3-4 改前：`search_events` 缺 `['next_offset','ok','window']`）。
这一族的表征始终是「键名/位置对不上 ⇒ 静默放宽或静默归零，而形状仍然合法」。

**每条都配了「该不响」的对偶档**（DCD 20261004 裁6 §三.4：判红要先找反例，门自证要含
「什么都不改」那一档）：过滤位为空时读数必须等于全量，非空时必须不等于全量——
只写正向断言的话，一个恒不加条件的实现也能绿。

**还钉一条 legacy 没有的**：语义位传了但什么都解析不出来时，**不许回落成全量**。
legacy 这里是漏的（`entities or None` + `domains or None` 双双为空 ⇒ 查不到的名字返回全屋），
新引擎按 fail-closed 答 0 条并在 `filters.unresolved` 说明原因。这条不是顺手加的严格性，
是「静默放宽」这一族里最容易把错答当对答交出去的那一格。

口径：`total` = 不带 LIMIT 的匹配总数（裁5 追加 Q-A），`count` = 本页条数。
窗口用固定日期，本机与容器（UTC）读数一致。
"""

import os
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.insights.models import EntityInfo, InsightConfig  # noqa: E402
from memory_agent.insights.repository import build_repository  # noqa: E402
from memory_agent.store import Store  # noqa: E402

DAY_1 = "2026-09-21"
DAY_2 = "2026-09-22"
DAY_3 = "2026-09-23"
START = "%sT00:00:00" % DAY_1
END = "%sT23:59:59" % DAY_3

LIGHT_MAIN = "light.living_main"      # 客厅主灯：6 条（on 3 / off 3）
LIGHT_DESK = "light.study_desk"       # 书房台灯：4 条（on 2 / off 2）
CLIMATE_AC = "climate.living_ac"      # 客厅空调：3 条（on 1 / off 2）
SWITCH_FISH = "switch.aqua_pump"      # 鱼缸水泵：2 条（on 1 / off 1）
SENSOR_POWER = "sensor.living_power"  # 客厅功率：5 条（数值态，既不是 on 也不是 off）

TOTAL_EVENTS = 6 + 4 + 3 + 2 + 5      # 20
CATALOG = [
    EntityInfo(entity_id=LIGHT_MAIN, friendly_name="客厅主灯", room="客厅", domain="light"),
    EntityInfo(entity_id=LIGHT_DESK, friendly_name="书房台灯", room="书房", domain="light"),
    EntityInfo(entity_id=CLIMATE_AC, friendly_name="客厅空调", room="客厅", domain="climate"),
    EntityInfo(entity_id=SWITCH_FISH, friendly_name="鱼缸水泵", room="阳台", domain="switch"),
    EntityInfo(entity_id=SENSOR_POWER, friendly_name="客厅功率", room="客厅", domain="sensor"),
]


def _ev(entity_id, day, hour, minute, state, room, domain, name):
    return {"entity_id": entity_id, "ts": "%sT%02d:%02d:00" % (day, hour, minute),
            "room": room, "domain": domain, "new_state": state, "old_state": "x",
            "attrs": {"friendly_name": name}}


def _seq(entity_id, room, domain, name, states, day=DAY_1, hour=10):
    """一个实体一串事件；分钟逐条错开，避开 `make_event_id` 同秒合并。"""
    return [_ev(entity_id, day, hour, i, st, room, domain, name)
            for i, st in enumerate(states)]


def _dataset():
    rows = []
    rows += _seq(LIGHT_MAIN, "客厅", "light", "客厅主灯", ["on", "off", "on", "off", "on", "off"])
    rows += _seq(LIGHT_DESK, "书房", "light", "书房台灯", ["on", "off", "on", "off"], DAY_2)
    rows += _seq(CLIMATE_AC, "客厅", "climate", "客厅空调", ["on", "off", "off"], DAY_2)
    rows += _seq(SWITCH_FISH, "阳台", "switch", "鱼缸水泵", ["on", "off"], DAY_3)
    rows += _seq(SENSOR_POWER, "客厅", "sensor", "客厅功率",
                 ["120", "121", "122", "123", "124"], DAY_3)
    return rows


@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="ma_filters_")
    s = Store(os.path.join(tmp, "filters.db"), tz_offset_hours=0.0)
    s.init_schema()
    s.insert_events(_dataset())
    yield s


@pytest.fixture
def facade(store):
    svc = InsightService(store, Config())
    svc.resolver.refresh(CATALOG)
    return svc


def _search(facade, **kw):
    args = {"start": START, "end": END, "entity_id": "", "room": "", "category": "",
            "query": "", "limit": 100, "offset": 0, "behavior_only": False}
    args.update(kw)
    start, end = args.pop("start"), args.pop("end")
    return facade._search(start, end, **args)


# ── Q3-1 位 1：category ───────────────────────────────────────────────────

def test_category_position_narrows_the_scan(facade):
    """传 `category=lighting` 只拿到 light 域（6+4=10），不传仍是全量 20。"""
    lit = _search(facade, category="lighting")
    assert lit["ok"] is True, lit
    assert lit["total"] == 10, f"category 位没落地：total={lit['total']}"
    assert lit["filters"]["domains_resolved"] == ["light"], lit["filters"]
    assert lit["filters"]["category"] == "lighting"

    everything = _search(facade)          # 该不响档：不传 = 不加条件
    assert everything["total"] == TOTAL_EVENTS, everything["total"]
    assert everything["filters"]["domains_resolved"] == "(全部)"


# ── Q3-1 位 2：domain ────────────────────────────────────────────────────

def test_domain_position_narrows_the_scan(facade):
    """`domain=climate` 命中 3 条；库里没有该域时必须是 0，不是全量。"""
    ac = _search(facade, domain="climate")
    assert ac["total"] == 3, f"domain 位没落地：{ac['total']}"
    assert {e["entity_id"] for e in ac["events"]} == {CLIMATE_AC}
    assert ac["filters"]["domain"] == "climate"

    empty = _search(facade, domain="vacuum")      # 该响的 0：不能放宽
    assert empty["total"] == 0, f"库里没有 vacuum，却拿到 {empty['total']} 条"


# ── Q3-1 位 3：query（名称匹配 + 解析不出的 fail-closed）─────────────────

def test_query_position_matches_by_friendly_name(facade):
    """`query=鱼缸` 既不是关键词也不是 domain，要靠目录里的友好名命中那 2 条。"""
    fish = _search(facade, query="鱼缸")
    assert fish["total"] == 2, f"query 位没落地：{fish['total']}"
    assert {e["entity_id"] for e in fish["events"]} == {SWITCH_FISH}
    assert fish["filters"]["entities_resolved"] == 1, fish["filters"]


def test_unresolvable_query_fails_closed_instead_of_returning_the_house(facade):
    """查不到的名字答 0 条并说明原因——legacy 在这里会静默放宽成全量。"""
    none = _search(facade, query="调光器")
    assert none["total"] == 0, f"解析不出却拿到 {none['total']} 条（静默放宽）"
    assert none["events"] == []
    assert "unresolved" in none["filters"], "得告诉调用方为什么是 0，别让它以为库里真没有"
    assert none["total_exact"] is True and none["truncated"] is False


# ── Q3-1 位 4：state ─────────────────────────────────────────────────────

def test_state_position_pushes_into_sql(facade):
    """`state=on` 只剩 on 的那 7 条；逗号多位取并集；空值不加条件。"""
    on = _search(facade, state="on")
    assert on["total"] == 7, f"state 位没落地：{on['total']}（应为 on 的 7 条）"
    assert {e["state"] for e in on["events"]} == {"on"}

    both = _search(facade, state="on, off")
    assert both["total"] == 15, both["total"]

    everything = _search(facade, state="")        # 该不响档
    assert everything["total"] == TOTAL_EVENTS


# ── Q3-1 位 5：order（回声与实际取数必须同源）──────────────────────────

def test_order_position_changes_which_events_survive_the_quota(store):
    """`max_scan=5` 下 order 决定日内留哪一段：asc 留最早、desc 留最晚，预算不变。

    这一格是六位里唯一「不改过滤条件、只改取样窗口」的，所以必须同时钉：
    排序方向生效、`filters.order` 与之一致、总预算一个字节没动（裁5 Q4=A）。
    """
    repo = build_repository(store, InsightConfig(max_scan=5))
    svc = InsightService(store, Config(), repository=repo)
    svc.resolver.refresh(CATALOG)

    asc = _search(svc, order="asc", limit=5)
    desc = _search(svc, order="desc", limit=5)
    assert svc.repo.scan_limit == 5
    assert asc["total"] == desc["total"] == TOTAL_EVENTS, "order 不许改变匹配总数"
    assert asc["filters"]["order"] == "asc" and desc["filters"]["order"] == "desc"
    assert [e["ts"] for e in asc["events"]] == sorted(e["ts"] for e in asc["events"])
    assert [e["ts"] for e in desc["events"]] == sorted((e["ts"] for e in desc["events"]),
                                                       reverse=True)
    assert max(e["ts"] for e in desc["events"]) > max(e["ts"] for e in asc["events"]), (
        "desc 拿到的应当是窗口尾段，与 asc 完全同一批就说明 order 没落地")


def test_order_alias_normalises_to_the_same_direction(facade):
    """`Descending` / `desc` / 带空格都要归到同一个方向，且回声与取数同源。"""
    for raw in ("desc", "DESC", " Descending "):
        out = _search(facade, order=raw, limit=3)
        assert out["filters"]["order"] == "desc", raw
        ts = [e["ts"] for e in out["events"]]
        assert ts == sorted(ts, reverse=True), (raw, ts)
    out = _search(facade, order="", limit=3)      # 该不响档：默认 asc
    assert out["filters"]["order"] == "asc"


# ── Q3-1 位 6：summarize ────────────────────────────────────────────────

def test_summarize_position_emits_summary_without_faking_count(store):
    """`summarize=true` 出 summary 并把样本缩到 50 条，`count` 仍是本页条数而不是 50。"""
    store.insert_events(_seq(LIGHT_MAIN, "客厅", "light", "客厅主灯",
                             ["on"] * 60, DAY_2, hour=11))
    svc = InsightService(store, Config())
    svc.resolver.refresh(CATALOG)

    out = _search(svc, room="客厅", category="lighting", limit=100, summarize=True)
    assert out["count"] == 66, f"count 被 50 条样本冒充了：{out['count']}"
    assert len(out["events"]) == 50, len(out["events"])
    assert out["summary"]["entities"][0]["changes"] == 66, out["summary"]["entities"][:2]
    assert "summarize=true" in out["note"]

    plain = _search(svc, room="客厅", category="lighting", limit=100)  # 该不响档
    assert "summary" not in plain and "note" not in plain
    assert len(plain["events"]) == 66


def test_ok_is_derived_from_envelope_invariants(facade):
    """`ok` 不是字面量：信封自相矛盾时必须翻 False（门禁 `fake-ok-const` 的正当满足方式）。

    这条是「该不响」档的反向用法——一个恒写 `ok=True` 的实现过不了这里的任何一格。
    """
    ok = InsightService._envelope_ok
    assert ok({"events": [], "count": 0, "window": {"start": "x"}}, 0) is True
    assert ok({"events": [1], "count": 5, "window": {"start": "x"}}, None) is False, (
        "count 与本页样本对不上还报 ok，就是替一份自相矛盾的载荷背书")
    assert ok({"events": [1], "count": 1, "window": None}, None) is False, "没有窗口不该算好"
    assert ok({"events": [1, 2], "count": 2, "window": {"a": 1}}, 1) is False, (
        "总数比本页还小，两个键至少有一个在说谎")
    assert ok({"events": "不是列表", "count": 0, "window": {"a": 1}}, None) is False
    assert _search(facade, limit=5)["ok"] is True


# ── Q3-4：legacy 承诺的分页/窗口键在新引擎要有对应物 ────────────────────

LEGACY_WANT_KEYS = {"ok", "count", "next_offset", "window"}


def test_envelope_carries_legacy_pagination_keys(facade):
    out = _search(facade, limit=5)
    missing = sorted(LEGACY_WANT_KEYS - set(out))
    assert not missing, f"新引擎缺 legacy 分页/窗口键：{missing}"
    for key in ("events", "total", "offset", "limit", "has_more", "time_range", "filters"):
        assert key in out, key
    assert out["window"] == out["time_range"], "两代窗口键必须指同一段时间，不许各说一套"
    assert out["total"] >= out["count"]


def test_next_offset_tracks_has_more_and_stops_at_the_end(facade):
    first = _search(facade, limit=5, offset=0)
    assert first["has_more"] is True and first["next_offset"] == 5, first["next_offset"]
    second = _search(facade, limit=5, offset=5)
    assert second["next_offset"] == 10
    last = _search(facade, limit=100, offset=0)
    assert last["has_more"] is False and last["next_offset"] is None
    assert last["count"] == TOTAL_EVENTS


# ── Q3-3（Q-A 之后口径）：切片被截与总数精确可以同时成立 ────────────────

def test_truncated_slice_and_exact_total_coexist(store):
    repo = build_repository(store, InsightConfig(max_scan=5))
    svc = InsightService(store, Config(), repository=repo)
    svc.resolver.refresh(CATALOG)

    out = _search(svc, limit=100)
    assert out["truncated"] is True, "命中日配额必须自报"
    assert out["scan_limit"] == 5
    assert out["total_exact"] is True, "total 来自不带 LIMIT 的计数，与切片是否被截无关"
    assert out["total"] == TOTAL_EVENTS, out["total"]
    assert out["count"] < TOTAL_EVENTS
    assert out["has_more"] is True and out["next_offset"] == out["count"]


# ── 形参位不许再被悄悄下架（Q-B 的前提是「不切、不下架」）───────────────

def test_search_signature_keeps_all_six_positions():
    import inspect

    params = set(inspect.signature(InsightService._search).parameters)
    want = {"category", "query", "domain", "state", "order", "summarize"}
    assert want <= params, f"六个过滤/排序位里这些又没了：{sorted(want - params)}"
