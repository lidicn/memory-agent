"""任务表 #40 ⑤ / #58 Q-D：门面「接收但不生效」的入参必须能判红（DCD 20261005 §三 Q2）。

裁定口径是**每个入参必须有「新实现落点」或「显式不支持」的登记，不允许静默忽略**。
这句话要成立，得有一条能判红的对账，而不是一份人写的表——量具是
`scripts/scan_qb_param_landing.py`，它抓的是既有两支扫描器都不覆盖的第三半：
`scan_insights_callsites.py` 管外面调进来的形状，`scan_insights_engine_attrs.py` 管门面调出去
的成员存在性，本件管**体内**：形参收下了，究竟有没有进到一个会影响结果的调用里。

现场缺陷（改前 HEAD 实测，`FINDINGS=2`）：`api.py:707 anomaly_report(days=0, …, query=")`
的体是 `tr = self._tr(start, end)` 紧跟 `start, end = self._days_to_range(days, start, end)`
——第二条的返回值没人读，`days` 进了死赋值，窗口悄悄退回 `default_days`；
`query` 全函数从未引用。两类都不抛、不报错、返回形状完好，`_degrade` 也管不着。

九条用例：量具在盘（1）、门现在绿（2）、门不是空转（3）、被修的两位落到 core 那一跳（4、5）、
实体集再落到 repo 扫描（6）、NL 路由也交出实体集（7）、fail-closed 的 ok 位是算出来的（8）、
`days` 一路走到报告文本（9）。第（6）条是 run13 的 N6 变异戳出来的缺口：
只量到 `core` 边界的锁允许"收下了但没往下传"在下一层原样重演——顺着这条口径逐跳查下去，
第（7）条抓到的是第三跳（`nlquery` 的 anomaly 路由）的现行：实体集规划好了却没交出去。
第（8）条是同一批 run13 里仓内门禁 `fake-ok-const` 判红本批新分支之后补的：字面量 `ok=True`
换成回读不变式之后，必须真的能翻 False，否则那条门禁只是被绕开而不是被满足。
第（9）条是同一把尺子量到第四跳（报告文本面）时抓到的另一条现行：`self.core.reports`
从来没挂载过，四条 `*_report` 文本面调用即 AttributeError——而负责"成员存在性"的那支
扫描器当时只认一跳形状，这四条连统计都没进（补齐见 `scan_insights_engine_attrs.py`）。
"""

import importlib.util
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.insights.models import InsightConfig  # noqa: E402

_SCANNER = os.path.join(_ROOT, "scripts", "scan_qb_param_landing.py")


def _load_scanner():
    spec = importlib.util.spec_from_file_location("scan_qb_param_landing", _SCANNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_param_landing_scanner_is_present_and_defaults_cover_the_facade():
    """量具自己先在盘上、默认目标含门面——否则后面的绿是"没扫"而不是"扫了没问题"。"""
    assert os.path.exists(_SCANNER)
    scanner = _load_scanner()
    assert "insights/api.py" in " ".join(scanner.DEFAULT_TARGETS)
    for rel in scanner.DEFAULT_TARGETS:
        assert os.path.exists(os.path.join(_ROOT, rel)), rel


def test_no_silent_ignore_in_insights_layers():
    """HEAD 现读：门面 + 新引擎四层的公开形参，一个都不许"收下但不生效"。"""
    scanner = _load_scanner()
    findings = []
    for rel in scanner.DEFAULT_TARGETS:
        rows, found = scanner.scan_file(os.path.join(_ROOT, rel))
        assert rows, "%s 一个形参都没扫到 = 量具失效，不是通过" % rel
        findings += found
    assert findings == [], findings


def test_scanner_bites_both_defect_classes():
    """门自证：把改前那两种形状喂给它，必须各报一类——否则上一格的绿是空转。"""
    src = (
        "class InsightService:\n"
        "    def _tr(self, start, end, days=0):\n"
        "        return (start, end, days)\n"
        "    def _days_to_range(self, days, start, end):\n"
        "        return start, end\n"
        "    def broken_days(self, days=0, start='', end=''):\n"
        "        tr = self._tr(start, end)\n"
        "        start, end = self._days_to_range(days, start, end)\n"
        "        return tr\n"
        "    def broken_query(self, room='', query=''):\n"
        "        return self._tr('', '', days=1)\n"
        "    def fine(self, room=''):\n"
        "        return self._tr('', '', days=0) + room\n"
    )
    import ast
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "synthetic.py")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(src)
        tree = ast.parse(src)
        assert tree.body, "合成源必须能解析"
        scanner = _load_scanner()
        rows, findings = scanner.scan_file(path)
    kinds = {(r[0], r[1], r[2]): r[3] for r in rows if r[3] != "LANDS"}
    assert kinds == {("InsightService", "broken_days", "days"): "DEAD-RESULT",
                     ("InsightService", "broken_query", "room"): "DROPPED",
                     ("InsightService", "broken_query", "query"): "DROPPED"}, rows
    assert len(findings) == 3, findings
    # 正常形参与"结果被读走的赋值"不许被牵连进来（判据窄，才有人愿意修）
    assert ("InsightService", "broken_days", "start") not in kinds
    assert ("InsightService", "fine", "room") not in kinds


class _SpyCore:
    """只记调用参数的假 core：量「入参有没有落到那次真正算数的调用上」。"""

    def __init__(self):
        self.calls = []

    def anomaly_report(self, tr, room="", category="", entity_id=""):
        self.calls.append({"tr": tr, "room": room, "category": category,
                           "entity_id": entity_id})
        return {"anomalies": [], "ok": True, "summary": {"count": 0}, "filters": {}}


class _Resolver:
    def __init__(self, ids):
        self._ids = ids
        self.seen = []

    def resolve_ids(self, room="", category="", query="", domain=""):
        self.seen.append({"room": room, "category": category, "query": query})
        return list(self._ids)


def _facade_with_spy():
    svc = InsightService(None, InsightConfig())
    spy = _SpyCore()
    svc.core = spy
    return svc, spy


def test_anomaly_report_days_lands_in_the_window():
    """`days` 必须真的挪窗口——改前它进了死赋值，14 天与 2 天拿到同一个 span。"""
    svc, spy = _facade_with_spy()
    svc.anomaly_report(days=14)
    svc.anomaly_report(days=2)
    assert len(spy.calls) == 2, spy.calls
    spans = []
    for c in spy.calls:
        tr = c["tr"]
        spans.append(round((tr.end - tr.start).total_seconds() / 86400.0, 1))
    assert spans == [14.0, 2.0], spans
    # 显式 start/end 仍然优先（resolve_range 的既有优先级不许被这次改动带偏）
    svc.anomaly_report(days=14, start="2026-01-01T00:00:00", end="2026-01-03T00:00:00")
    tr = spy.calls[-1]["tr"]
    assert round((tr.end - tr.start).total_seconds() / 86400.0, 1) == 2.0


def test_anomaly_report_query_lands_on_entities_or_fails_closed():
    """`query` 两条出路都要有：解析得出就下推实体集，解析不出就 0 条并说明。"""
    svc, spy = _facade_with_spy()
    svc.resolver = _Resolver(["light.a", "switch.b"])
    svc.anomaly_report(days=7, room="客厅", query="灯")
    assert svc.resolver.seen == [{"room": "客厅", "category": "", "query": "灯"}]
    assert spy.calls[-1]["entity_id"] == "light.a,switch.b", spy.calls

    svc2, spy2 = _facade_with_spy()
    svc2.resolver = _Resolver([])
    out = svc2.anomaly_report(days=7, query="绝不相干的设备名")
    assert out["anomalies"] == [] and out["ok"] is True, out
    assert "unresolved" in out["filters"], out["filters"]
    assert spy2.calls == [], "解析不出时不许回落成全屋异常（legacy 在这一格是漏的）"


class _SpyRepo:
    """记 kwargs 的假 repo：量第二跳——core 收了 entity_id，究竟有没有进到会算数的扫描。

    N6 变异（`_anomaly_report` 里 `_filters(room, category, entity_id)` 少了第三参）
    在第一跳的 spy core 上是**看不见的**：门面确实把实体集交了下去。这一格把判据
    推到 repo 调用面，缺了它，"query 落到结果上"只是一句到 core 为止的半成品。
    """

    def __init__(self):
        self.calls = []

    def _rec(self, name, **kw):
        self.calls.append({"fn": name, **kw})
        return []

    def day_counts(self, tr, **kw):
        self.calls.append({"fn": "day_counts", **kw})
        return {}

    def activity_matrix(self, tr, **kw):
        return self._rec("activity_matrix", **kw)

    def entity_stats(self, tr, **kw):
        return self._rec("entity_stats", **kw)

    def quality_counts(self, tr, **kw):
        self.calls.append({"fn": "quality_counts", **kw})
        return {}

    def entity_action_counts(self, tr, **kw):
        return self._rec("entity_action_counts", **kw)


def _core_with_spy_repo():
    from memory_agent.insights.service import BehaviorService
    repo = _SpyRepo()
    return BehaviorService(repo, _Resolver([]), InsightConfig()), repo


def test_anomaly_report_entity_id_lands_on_repo_scans():
    """第二跳：实体集要进每一条**按实体切片**的扫描，且回显键与之一致。

    `quality_counts` 量的是窗口内的字段缺失，与"用户点名哪台设备"无关，所以它不在
    必须带 entity_ids 的清单里——这一格判的是「该带的都带」，不是「全都带」。
    """
    tr = type("W", (), {"start_iso": "2026-09-20T00:00:00",
                        "end_iso": "2026-09-26T00:00:00"})()
    core, repo = _core_with_spy_repo()
    out = core.anomaly_report(tr, room="", category="", entity_id="light.a,switch.b")
    scoped = [c for c in repo.calls if c["fn"] != "quality_counts"]
    assert len(scoped) == 4, repo.calls
    assert all(c.get("entity_ids") == ["light.a", "switch.b"] for c in scoped), repo.calls
    assert out["filters"]["entity_ids"] == ["light.a", "switch.b"], out["filters"]
    assert out["filters"]["entity_id"] == "light.a,switch.b", out["filters"]

    core2, repo2 = _core_with_spy_repo()
    core2.anomaly_report(tr, room="", category="", entity_id="")
    assert all(c.get("entity_ids") == [] for c in repo2.calls if c["fn"] != "quality_counts"
               ), repo2.calls


def test_nlquery_anomaly_route_forwards_plan_entities():
    """第三跳：NL 路由把规划好的实体集交给 core——改前它只交 room/category。

    规划阶段已经用 `resolve_ids` 解析出实体（`plan.entity_ids`），`_answer_usage` /
    `_answer_behavior` 都把它转成 `entity_id=` 下推，唯独 anomaly 路由漏了：
    问「鱼缸水泵有什么异常」拿到的是**全屋**异常，而话术照样通顺、一条不响。
    这一条不在量具的形参口径里（`plan` 是被读的，漏的是属性转发），只能由行为锁负责。
    """
    from memory_agent.insights.models import QuestionPlan
    from memory_agent.insights.nlquery import NLQueryEngine
    from memory_agent.insights.parser.timeframe import resolve_range
    tr = resolve_range(start="2026-09-20T00:00:00", end="2026-09-26T00:00:00")
    spy = _SpyCore()
    plan = QuestionPlan(question="鱼缸水泵有什么异常", intent="anomaly", route="anomaly",
                        room="阳台", query="鱼缸水泵", entity_ids=["switch.pump"],
                        time_range=tr)
    NLQueryEngine(spy, None)._execute(plan)
    assert spy.calls[-1]["entity_id"] == "switch.pump", spy.calls
    assert spy.calls[-1]["room"] == "阳台", spy.calls


def test_fail_closed_ok_is_derived_not_literal():
    """`ok` 位不许是字面量——仓内门禁 `fake-ok-const` 对本批新分支判过一次红，改法是回读不变式。

    与 `test_vma_insights_search_filters.py:226` 同一口径：正当满足方式不是往
    `.gates-baseline.txt` 里塞一条豁免（那份台账只准减），而是让 `ok` 真的从载荷算出来。
    所以这里必须把三种自相矛盾各喂一次，让 `ok` 当场翻 False——否则"算出来的 ok"
    只是一句注释，字面量 `True` 换成 `_closed_ok(…)` 后行为完全一样，门就白过了一遍。
    """
    good = {"entity_ids": [], "unresolved": "query 传了但解析不出实体"}
    assert InsightService._closed_ok([], {"count": 0}, good) is True
    assert InsightService._closed_ok([], {"count": 3}, good) is False, "count 与列表不符还答 ok"
    assert InsightService._closed_ok(
        [], {"count": 0}, {"entity_ids": ["light.a"], "unresolved": "x"}) is False, "边下推实体边答 0 条"
    assert InsightService._closed_ok([], {"count": 0}, {"entity_ids": []}) is False, "静默的 0 条也答 ok"
    assert InsightService._closed_ok(None, {"count": 0}, good) is False

    svc, _ = _facade_with_spy()
    svc.resolver = _Resolver([])
    out = svc.anomaly_report(days=7, query="绝不相干的设备名")
    assert out["ok"] is True and out["filters"]["unresolved"], out


def test_anomaly_report_text_lands_on_the_report_surface():
    """第四跳：`days` 要一路走到用户可见的报告文本，并且 `core.reports` 得真的挂着。

    审计里这一族的现场症状写的是「`anomaly_report_text(days=14)` 出 7 天的报告」；
    改前其实比那更糟——门面体 `self.core.reports.anomaly_report(…)` 指向 `BehaviorService`
    上从未挂载的成员（实测 `hasattr(BehaviorService, 'reports') == False`），四条文本
    报告面调用即 `AttributeError`。而 `scan_insights_engine_attrs.py` 当时只认
    `self.<引擎>.<成员>(…)` 一跳形状，这四条**连"指向"都没被计上**（读数「35 指向 /
    0 空指向」全绿）。量具那一半由 `test_vma_insights_callsite_binding.py` 的深链锁负责，
    本条量的是读数：报告的窗口必须等于问的窗口。
    """
    import json

    svc = InsightService(None, InsightConfig())
    core, _repo = _core_with_spy_repo()
    svc.core = core
    assert hasattr(core, "reports"), "core.reports 没挂载：四条报告面调用即 AttributeError"
    days = [json.loads(svc.anomaly_report_text(fmt="json", days=d))["summary"]["days"]
            for d in (14, 2)]
    assert days == [15, 3], days
    md = svc.anomaly_report_text(fmt="markdown", days=14)
    assert isinstance(md, str) and md.startswith("# 异常报告"), md[:80]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
