"""run13 现读（任务表 #62）：`anomaly_report` 的入参落点在**生产库**上的成对读数，只读。

改前三跳的形状：
  第一跳（门面）：`days` 进死赋值（窗口恒 `default_days`）、`query` 全函数从未引用；
  第二跳（core）：`_anomaly_report` 的 `_filters(room, category)` 不带实体集；
  第三跳（NL 路由）：规划阶段解析出 `plan.entity_ids` 却没交给 core；
  第四跳（报告文本面）：`self.core.reports` 这个成员从来不存在——四条 `*_report` 文本面
    调用即 AttributeError，而那一支"成员存在性"扫描器只认一跳形状，连这四条都没计上。
三跳都不抛、不报错、返回形状完好——静态结论只能证明"没读这个变量"，证明不了
"读数因此变了"。这格量的就是后者：同一扇窗口，带实体与不带实体的异常数**必须不同**，
`days=14` 与 `days=2` 的窗口天数**必须不同**，点名的设备解析不出时**必须答 0 条**。

出境纪律：只打印数量、布尔与计数级读数；实体 id / 友好名 / 人名一律不外露，
异常正文（`message` 里带实体）不打印。全程 SELECT 与内存计算，不建 schema、不写库。

    docker exec -w /tmp/<snap> -e PYTHONPATH=/tmp/<snap>/src:/tmp/pylibs \
        memory-agent python /tmp/<snap>_probe_anom.py
"""
import json

from memory_agent.config import get_config
from memory_agent.insights import InsightService as Facade
from memory_agent.store import Store

cfg = get_config()
st = Store(cfg.db_path, tz_offset_hours=cfg.tz_offset_hours)
fac = Facade(st, cfg)
QUERY = "灯"

print("db=%s max_scan=%s default_days=%s" % (cfg.db_path, fac.repo.scan_limit,
                                             getattr(cfg, "default_days", "-")))


def n(d, *path):
    cur = d
    for p in path:
        cur = (cur or {}).get(p)
    return cur


print("\n=== 第一跳：days 是否真的挪窗口（同一 room，改前两值恒等）===")
for days in (2, 7, 14, 30):
    out = fac.anomaly_report(days=days)
    print("  days=%2d -> summary.days=%s 异常数=%s ok=%s truncated=%s" % (
        days, n(out, "summary", "days"), n(out, "summary", "count"),
        out.get("ok"), out.get("truncated")))

print("\n=== 第二跳：实体集有没有进扫描（带实体 vs 不带实体的成对读数）===")
ids = fac.resolver.resolve_ids(query=QUERY)
tr = fac._tr(days=14)
a = fac.core.anomaly_report(tr, room="", category="", entity_id="")
b = fac.core.anomaly_report(tr, room="", category="", entity_id=",".join(ids))
print("  解析「%s」得到实体数=%d（不外露 id）" % (QUERY, len(ids)))
print("  不带实体  : 异常数=%s 事件总数=%s echo.entity_ids=%d ok=%s" % (
    n(a, "summary", "count"), n(a, "summary", "total_events"),
    len((a.get("filters") or {}).get("entity_ids") or []), a.get("ok")))
print("  带 %d 实体: 异常数=%s 事件总数=%s echo.entity_ids=%d ok=%s" % (
    len(ids), n(b, "summary", "count"), n(b, "summary", "total_events"),
    len((b.get("filters") or {}).get("entity_ids") or []), b.get("ok")))
ae, be = n(a, "summary", "total_events"), n(b, "summary", "total_events")
print("  -> 事件总数 %s（带实体的读数%s全量；改前这一格两值恒等 = 实体集没进扫描）" % (
    "变小" if (be or 0) < (ae or 0) else "没变小",
    "少于" if (be or 0) < (ae or 0) else "不小于"))
print("  -> 回显键与下推一致：%s" % (
    len((b.get("filters") or {}).get("entity_ids") or []) == len(ids)))

print("\n=== fail-closed：点名的设备解析不出时答 0 条，不回落全屋 ===")
bogus = "绝不相干的设备名甲乙丙"
out = fac.anomaly_report(days=14, query=bogus)
print("  query=%s -> 异常数=%s ok=%s unresolved=%s 全屋对照异常数=%s" % (
    bogus, n(out, "summary", "count"), out.get("ok"),
    "unresolved" in (out.get("filters") or {}), n(a, "summary", "count")))

print("\n=== 第三跳：NL 路由（异常意图）有没有把规划好的实体集交出去 ===")
try:
    payload = fac.nl.ask("最近%s有什么异常" % QUERY, days=14)
    data = payload.get("data") or {}
    print("  route=%s intent=%s plan.entity_ids=%d data.filters.entity_ids=%d 异常数=%s" % (
        payload.get("route"), payload.get("intent"),
        len(((payload.get("plan") or {}).get("entity_ids") or [])),
        len((data.get("filters") or {}).get("entity_ids") or []),
        n(data, "summary", "count")))
    print("  话术长度=%d 字符（正文含实体名，不外露）" % len(payload.get("answer") or ""))
except Exception as exc:
    print("  NL 路由异常：%s" % type(exc).__name__)

print("\n=== 对照：门面 query 与 NL 路由两条路应当收敛到同一实体集 ===")
m = fac.anomaly_report(days=14, query=QUERY)
print("  门面 query -> 异常数=%s echo.entity_ids=%d（与 NL 的 %d 比较）" % (
    n(m, "summary", "count"), len((m.get("filters") or {}).get("entity_ids") or []),
    len(ids)))
print("\n=== 第四跳：报告文本面（改前 `self.core.reports` 从未挂载，调用即 AttributeError）===")
try:
    j14 = json.loads(fac.anomaly_report_text(fmt="json", days=14))
    j2 = json.loads(fac.anomaly_report_text(fmt="json", days=2))
    md = fac.anomaly_report_text(fmt="markdown", days=14)
    print("  core.reports 挂载=%s 报告内天数 days=14->%s days=2->%s（改前两者恒等）" % (
        hasattr(fac.core, "reports"), n(j14, "summary", "days"),
        n(j2, "summary", "days")))
    print("  markdown 长度=%d 字符 首行=%s 异常行数=%d" % (
        len(md), md.splitlines()[0], len(j14.get("anomalies") or [])))
    for name in ("insight_report", "data_quality_report", "persona_report"):
        txt = getattr(fac, name)(fmt="json")
        print("  %s -> 文本 %d 字符，含 error 键=%s" % (name, len(txt), '"error"' in txt))
except Exception as exc:
    print("  报告面异常：%s（改前这一格就是 AttributeError，被 _degrade 之外直接外抛）"
          % type(exc).__name__)
print("PROBE_ANOM_DONE")
