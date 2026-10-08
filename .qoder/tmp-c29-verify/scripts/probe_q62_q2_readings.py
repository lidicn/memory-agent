"""裁6 Q6-2 约束②（30 天窗耗时分母）+ 裁1/裁4 Q2=甲（64KB 页预算）的成对读数探针。

DCD 20261004 裁6 §二 Q6-2 约束②：「改前/改后的窗口耗时读数成对交（30 天窗各一次），
否则『成本随天数线性』无法验收」——豆包批次把这条记成"生产实测待补"。
DCD 20261004 裁1/裁4 Q2=甲：「整页投影后 ≤64KB 时自动收窄」，同样要给改前/改后字节读数。

这个脚本一次把两组读数都量出来（生产库，**全程 SELECT + 内存计算，不写库、不建 schema**）：

A. Q6-2 形状与耗时
   1. 改前形状：单条 `ORDER BY ts ASC LIMIT scan_limit`（旧实现）——耗时、条数、覆盖的首末事件、
      跨了几个日键；
   2. 改后形状：`load_events`（按天分批）——耗时、条数、覆盖的首末事件、跨了几个日键、
      `last_scan_truncated`；
   3. 分母：`count_events`（不带 LIMIT 的真值）+ 逐日真值（GROUP BY day），
      用来分辨「日配额相对每日真实量」是多少——**分批把"总量被砍"变成了"每天各砍一刀"，
      每天保留的是当日 ts 最小的前 N 条（一天最前面那段）**，这一维只能靠逐日真值看出来。

B. Q2=甲 的 64KB 页预算
   同一批生产行分别按「不设页预算（改前）」与「64KB（改后）」过一遍 `device_health_page`，
   报整页 JSON 字节、实返行数、被页预算挤掉的行数。`fields` 两侧各测 lean/full。

不打印 entity_id / friendly_name / note 正文（含厂商用户号与中文长名），只报数量与字节数。

    ssh lidicn@192.168.2.200 'docker exec -w /app memory-agent sh -c \\
        "PYTHONPATH=/app/src python /app/scripts/probe_q62_q2_readings.py"'
"""
import json
import time

from memory_agent import mcp_server as mcp
from memory_agent.config import get_config
from memory_agent.insights.parser.timeframe import resolve_range
from memory_agent.insights.repository import _to_iso, build_repository
from memory_agent.store import Store

cfg = get_config()
st = Store(cfg.db_path, tz_offset_hours=cfg.tz_offset_hours)
# 只走仓储 + 只读 SQL：不建 InsightService 门面，免得把 legacy 的模板装载/实体目录
# 一起拖进在役库（本探针一条写入语句都没有）。
repo = build_repository(st, cfg)

print("db=%s tz=%s scan_limit=%s" % (cfg.db_path, cfg.tz_offset_hours, repo.scan_limit))

# ── A. Q6-2：30 天窗改前/改后 ────────────────────────────────────────────────
tr = resolve_range(days=30)
start_iso, end_iso, start_day, end_day = repo._bounds(tr)
n_days_keys = 0
con = st.connect()

cur = con.cursor()
cur.execute(
    "SELECT COUNT(*) FROM (SELECT 1 FROM events WHERE day BETWEEN ? AND ? "
    "AND ts BETWEEN ? AND ?)",
    (start_day, end_day, start_iso, end_iso),
)
true_rows = cur.fetchone()[0]
cur.execute(
    "SELECT day, COUNT(*) FROM events WHERE day BETWEEN ? AND ? AND ts BETWEEN ? AND ? "
    "GROUP BY day ORDER BY day",
    (start_day, end_day, start_iso, end_iso),
)
per_day = cur.fetchall()
n_days_keys = len(per_day)
daily_quota = max(1, repo.scan_limit // (len(per_day) or 1))

print("\n=== A1 分母（口径：day BETWEEN 窗口日键 AND ts BETWEEN 窗口边界，COUNT(*)）===")
print("窗口 %s → %s（日键 %s 个）" % (start_day, end_day, n_days_keys))
print("真值条数=%s  scan_limit=%s  按天分摊后的日配额=%s" % (true_rows, repo.scan_limit, daily_quota))
day_counts = [c for _d, c in per_day]
print("逐日真值：min=%s 中位=%s max=%s（日配额/日均真值=%s）" % (
    min(day_counts), sorted(day_counts)[len(day_counts) // 2], max(day_counts),
    round(daily_quota / (true_rows / len(day_counts)), 4)))
print("日配额占当日真值的比例（首/末三日）：%s" % [
    (d, c, round(daily_quota / c, 4)) for d, c in per_day[:3]])

print("\n=== A2 改前形状：单条 LIMIT scan_limit（旧实现）===")
base_sql = ("SELECT id, ts, day, room, entity_id, domain, action, person, "
            "old_state, new_state, attrs_json FROM events WHERE day BETWEEN ? AND ? "
            "AND ts BETWEEN ? AND ? ORDER BY ts ASC, id ASC LIMIT ?")
t0 = time.perf_counter()
cur.execute(base_sql, (start_day, end_day, start_iso, end_iso, repo.scan_limit))
legacy_rows = cur.fetchall()
legacy_ms = round((time.perf_counter() - t0) * 1000, 1)
legacy_days = {r[2] for r in legacy_rows}
print("耗时=%sms 条数=%s 覆盖日键=%s 个（%s → %s）首末 ts=%s → %s" % (
    legacy_ms, len(legacy_rows), len(legacy_days),
    min(legacy_days) if legacy_days else "-", max(legacy_days) if legacy_days else "-",
    legacy_rows[0][1] if legacy_rows else "-", legacy_rows[-1][1] if legacy_rows else "-"))

print("\n=== A3 改后形状：load_events 按天分批 ===")
timings = []
for _ in range(3):
    t1 = time.perf_counter()
    events = repo.load_events(tr)
    timings.append(round((time.perf_counter() - t1) * 1000, 1))
new_days = {}
for e in events:
    new_days.setdefault(_to_iso(e.ts)[:10], 0)
    new_days[_to_iso(e.ts)[:10]] += 1
print("耗时=三次 %sms（取中位 %sms）条数=%s 覆盖日键=%s 个 truncated=%s" % (
    timings, sorted(timings)[len(timings) // 2], len(events), len(new_days),
    repo.last_scan_truncated))
print("每个日键实返条数：min=%s max=%s（日配额=%s ⇒ 有配额的日用满了它）" % (
    min(new_days.values()), max(new_days.values()), daily_quota))
print("耗时比（改后中位/改前）=%s" % round(sorted(timings)[1] / legacy_ms, 2))

# ── A4 可见性宽度：分批把「总量被砍」变成「每天各砍一刀」，砍的是当天最前面那段 ──
print("\n=== A4 每日可见时段（实返覆盖 vs 当日真实跨度，窗口内最后 5 天）===")
cur.execute(
    "SELECT day, MIN(ts), MAX(ts) FROM events WHERE day BETWEEN ? AND ? "
    "AND ts BETWEEN ? AND ? GROUP BY day ORDER BY day DESC LIMIT 5",
    (start_day, end_day, start_iso, end_iso),
)
true_span = {d: (str(a), str(b)) for d, a, b in cur.fetchall()}
kept_span: dict = {}
for e in events:
    s = _to_iso(e.ts)[:19]          # EventRecord.ts 是 epoch float，不是字符串
    k = s[:10]
    lo, hi = kept_span.get(k, (s, s))
    kept_span[k] = (min(lo, s), max(hi, s))


def _span_hours(a: str, b: str):
    from datetime import datetime
    try:
        d0 = datetime.fromisoformat(a[:19])
        d1 = datetime.fromisoformat(b[:19])
    except ValueError:
        return None
    return round((d1 - d0).total_seconds() / 3600.0, 2)


for d in sorted(true_span, reverse=True):
    t_lo, t_hi = true_span[d]
    k_lo, k_hi = kept_span.get(d, ("-", "-"))
    print("  %s 真实 %s→%s（%s h）｜实返 %s→%s（%s h）" % (
        d, t_lo[11:19], t_hi[11:19], _span_hours(t_lo, t_hi),
        k_lo[11:19], k_hi[11:19], _span_hours(k_lo, k_hi) if k_lo != "-" else "-"))
print("  口径：ts 是本地墙钟字符串（_to_iso 产出），跨度按同一字符串直算，不做时区换算")

# ── B. Q2=甲：64KB 页预算改前/改后 ──────────────────────────────────────────
print("\n=== B 设备健康整页字节预算（同一批生产行，只算不写）===")
total = st.count_device_health("")
raw = st.list_device_health("", limit=mcp.DEVICE_HEALTH_PAGE_LIMIT_DEFAULT, offset=0)
print("device_health 全量条数=%s 本页取回行数=%s（limit=%s）" % (
    total, len(raw), mcp.DEVICE_HEALTH_PAGE_LIMIT_DEFAULT))

REAL_CAP = mcp.DEVICE_HEALTH_PAGE_MAX_BYTES
for fields in ("lean", "full"):
    mcp.DEVICE_HEALTH_PAGE_MAX_BYTES = 10 ** 12  # 改前：没有页预算
    before = mcp.device_health_page(raw, total, limit=len(raw), fields=fields)
    mcp.DEVICE_HEALTH_PAGE_MAX_BYTES = REAL_CAP  # 改后：64KB
    after = mcp.device_health_page(raw, total, limit=len(raw), fields=fields)
    print("fields=%s 改前 page_bytes=%s count=%s | 改后 page_bytes=%s count=%s | 省下=%s 字节（%s%%）" % (
        fields, before["page_bytes"], before["count"], after["page_bytes"], after["count"],
        before["page_bytes"] - after["page_bytes"],
        round(100.0 * (before["page_bytes"] - after["page_bytes"]) / max(1, before["page_bytes"]), 1)))
    print("        改后是否仍≤64KB=%s 单行最大字节=%s" % (
        after["page_bytes"] <= REAL_CAP,
        max([len(json.dumps([r], ensure_ascii=False).encode("utf-8")) for r in after["health"]] or [0])))

mcp.DEVICE_HEALTH_PAGE_MAX_BYTES = REAL_CAP
st.close()
print("\nPROBE_DONE")
