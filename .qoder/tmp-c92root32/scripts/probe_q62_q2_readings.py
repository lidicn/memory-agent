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

print("\n=== A3 改后形状：load_events 按天×小时分层（乙′）===")
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

# ── A5 本件（乙′）自己的成对读数：#44 的按天前缀 vs 分层，同窗口同预算 ──────────
# A2/A3 那对比的是"一条 LIMIT 打完 30 天"（#44 之前）；本件的改前是 #44 的按天前缀，
# 裁定 :39 的现象（每天只剩凌晨 1 小时可见）只有这一对才量得出来，所以必须现算一遍旧形状。
print("\n=== A5 改前(#44 按天前缀)/改后(乙′ 按小时分层) 成对读数 + 每日可见时段表 ===")
prefix_sql = ("SELECT day, substr(ts, 12, 2) AS hh FROM events WHERE day = ? "
              "ORDER BY ts ASC, id ASC LIMIT ?")
prefix_timings = []
prefix_hours: dict = {}
for _ in range(3):
    t2 = time.perf_counter()
    hours: dict = {}
    for d, _c in per_day:
        for row in cur.execute(prefix_sql, (d, daily_quota)).fetchall():
            hours.setdefault(d, set()).add(str(row[1]))
    prefix_hours = hours
    prefix_timings.append(round((time.perf_counter() - t2) * 1000, 1))
prefix_ms = sorted(prefix_timings)[len(prefix_timings) // 2]

strat_hours: dict = {}
for e in events:
    s = _to_iso(e.ts)[:19]
    strat_hours.setdefault(s[:10], set()).add(s[11:13])

NIGHT = ("20", "21", "22", "23")


def _stats(hours):
    widths = [len(v) for v in hours.values()] or [0]
    widths = sorted(widths)
    night = sum(1 for v in hours.values() if set(v) & set(NIGHT))
    return widths[0], widths[len(widths) // 2], widths[-1], night


p_w = _stats(prefix_hours)
s_w = _stats(strat_hours)
print("按天前缀：耗时=三次 %sms（中位 %sms）｜有货日键=%s 每日可见小时数 首/中/末=%s/%s/%s "
      "夜间出场的天数=%s" % (prefix_timings, prefix_ms, len(prefix_hours), p_w[0], p_w[1], p_w[2], p_w[3]))
print("按小时分层：耗时=三次 %sms（中位 %sms）｜有货日键=%s 每日可见小时数 首/中/末=%s/%s/%s "
      "夜间出场的天数=%s" % (timings, sorted(timings)[len(timings) // 2],
                             len(strat_hours), s_w[0], s_w[1], s_w[2], s_w[3]))
print("耗时比（分层中位/前缀中位）=%s  日配额=%s  小时公平份额=%s" % (
    round(sorted(timings)[len(timings) // 2] / prefix_ms, 2), daily_quota,
    max(1, daily_quota // 24)))

print("每日可见时段（窗口内最后 5 天，只报小时个数与夜间出场数，不报条数以外的明细）：")
for d in sorted(true_span, reverse=True):
    ph = sorted(prefix_hours.get(d, set()))
    sh = sorted(strat_hours.get(d, set()))
    print("  %s 前缀可见 %s 格 %s｜分层可见 %s 格 %s｜夜间(20-23) 前缀=%s 分层=%s" % (
        d, len(ph), ",".join(ph), len(sh), ",".join(sh),
        len(set(ph) & set(NIGHT)), len(set(sh) & set(NIGHT))))

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
