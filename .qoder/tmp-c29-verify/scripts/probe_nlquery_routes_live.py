"""只读量具：#48「自然语言六条路由 × 生产真值」的改前/改后成对读数。

DCD 20261004 裁6 §三.4 的交付纪律（读数必须带口径）+ 裁6 Q6-2 约束②（改前/改后成对）
在这里的落法：同一份探针分别在**改前 src**（HEAD `4ae51c9`）与**改后 src**（工作区快照）
各跑一次，比较六条路由的"是否被 `_degrade` 吞掉"与真实读数键。

口径：
- 探针按 `InsightService.__init__` 的第 165-168 行**逐字复刻**门面装配
  （`build_repository` → `EntityResolver(repo.list_entities())` → `BehaviorService` →
  `NLQueryEngine`），因此走的是生产同一条取数路径；
- **不构造 legacy 门面**（那会往在役库拖模板装载/实体目录写入），全程 SELECT；
- 出境面：不打印 entity_id / friendly_name / room / answer 正文，只报路由、
  字符长度、计数与小时数（异常正文只报首 8 字符的**日期前缀形状**，生产数据里那是 ISO 日）。

跑法（容器内，`PYTHONPATH` 指向被测代码树的 src）：
`PYTHONPATH=/tmp/<snap>/src:/tmp/pylibs python -u /tmp/<snap>/scripts/probe_nlquery_routes_live.py`
"""
import time

from memory_agent.config import get_config
from memory_agent.insights.api import InsightService  # 只用其 _normalize_config
from memory_agent.insights.nlquery import NLQueryEngine
from memory_agent.insights.parser.entity import EntityResolver
from memory_agent.insights.repository import build_repository
from memory_agent.insights.service import BehaviorService
from memory_agent.store import Store

cfg = get_config()
st = Store(cfg.db_path, tz_offset_hours=cfg.tz_offset_hours)
config = InsightService._normalize_config(cfg)
repo = build_repository(st, config)
resolver = EntityResolver(repo.list_entities())
core = BehaviorService(repo, resolver, config)
nl = NLQueryEngine(core, resolver, config)

QUESTIONS = (
    ("书房门用了多久", "device_usage"),
    ("书房待了多久", "behavior"),
    ("最近有什么异常", "anomaly"),
    ("我一般几点睡觉", "rhythm"),
    ("总结一下我的习惯", "persona"),
    ("最近有哪些活动", "activity"),
)

#: `EntityResolver` 的公开面只有 `empty()`/`resolve_ids()`，没有计数接口；
#: 这里只取目录条数做"装配是否真拿到实体表"的读数，直读私有字段是最省的写法。
_catalog_size = len(getattr(resolver, "_items", []) or [])
print("db=%s tz=%s entities=%d" % (cfg.db_path, cfg.tz_offset_hours, _catalog_size))
print()
print("=== 六条路由 × 生产窗（问题正文即探针入参，不含真实实体名）===")
for days in (7, 30):
    print("-- days=%d --" % days)
    degraded = 0
    for question, expect_route in QUESTIONS:
        t0 = time.monotonic()
        try:
            out = nl.ask(question, days=days)
            err = ""
        except Exception as exc:  # 改前的 TypeError 就是从这里冒出来的
            out, err = {}, "%s: %s" % (type(exc).__name__, exc)
        ms = int(round((time.monotonic() - t0) * 1000))
        answer = str(out.get("answer") or "")
        route = str(out.get("route") or "")
        is_degraded = (answer in ("", "查询失败")) or bool(err)
        degraded += 1 if is_degraded else 0
        data = out.get("data") or {}
        extra = ""
        if route == "rhythm":
            extra = " sleep=%r wake=%r samples=%s" % (
                data.get("sleep"), data.get("wake"), data.get("samples"))
        elif route == "anomaly":
            extra = " anomalies=%s" % (data.get("summary") or {}).get("count")
        elif route in ("device_usage", "behavior"):
            extra = " entities=%s events=%s by_room=%d" % (
                data.get("total_entities"), data.get("total_events"),
                len(data.get("by_room") or []))
        elif route == "persona":
            extra = " traits=%d events=%s" % (len(data.get("traits") or []),
                                              data.get("total_events"))
        elif route == "activity":
            extra = " activities=%s" % data.get("total_activities")
        print("  expect=%-13s route=%-13s answer_chars=%-4d degraded=%s %dms match=%s%s%s" % (
            expect_route, route, len(answer), "Y" if is_degraded else "N", ms,
            "Y" if route == expect_route else "N", extra,
            (" " + err) if err else ""))
    print("  小计：降级 %d/%d" % (degraded, len(QUESTIONS)))
    print()

print("PROBE_DONE")
