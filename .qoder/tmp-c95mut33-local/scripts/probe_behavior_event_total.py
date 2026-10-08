"""裁5 追加 Q-A 的现网读数探针（生产库，只读）：`member` 后过滤到底少报了多少。

裁定把「不带 LIMIT 的计数查询」授权给 MA，理由是 `total` 要等于匹配总数。但这条授权
真正的分量在**读取形状**上：旧实现先按 `server_ts DESC` 取一页（默认 100，门面 50），
**再**在 Python 里按姓名筛人。于是「最近一页里没有这个人」= 报 0 条，而库里可能有一千多条。

这个脚本只回答一件事：旧口径 vs SQL 下推口径，在每个真实姓名上差多少行。
按 1/7/30 天三个窗口各量一次，并给出门面实际使用的 `limit=50` 页宽。

只读：全程 SELECT，`sqlite3.connect("file:…?mode=ro")`；不写库、不建表。
**不打印任何姓名**（PII），只报姓名条数、行数与缺口分布；页宽与窗口是通用参数。

    ssh lidicn@192.168.2.200 'docker exec -w /app memory-agent sh -c \\
        "PYTHONPATH=/app/src python /app/scripts/probe_behavior_event_total.py"'
"""
import json
import sqlite3
from datetime import datetime, timedelta

from memory_agent.config import get_config

PAGE_LIMITS = (50, 100)          # 门面 clamp 后是 50；Store 默认 100
DAYS = (1, 7, 30)

# 与 Store._behavior_event_where 逐字同形（部署态 /app/src 还没有这个方法，所以这里内联）。
# 注意字符串元素的 type 是 `text` 而不是 `string`——写成 `string` 会漏掉旧格式那 1 行。
MEMBER_CLAUSE = (
    "EXISTS (SELECT 1 FROM json_each("
    "CASE WHEN json_valid(b.persons_json)=1 AND json_type(b.persons_json)='array' "
    "THEN b.persons_json ELSE '[]' END) j "
    "WHERE (j.type='object' AND json_extract(j.value,'$.name')=?)"
    " OR (j.type='text' AND j.value=?))"
)


def names_of(raw):
    out = []
    try:
        arr = json.loads(raw)
    except Exception:
        return out
    if not isinstance(arr, list):
        return out
    for p in arr:
        if isinstance(p, dict) and p.get("name"):
            out.append(str(p["name"]))
        elif isinstance(p, str) and p:
            out.append(p)
    return out


cfg = get_config()
con = sqlite3.connect("file:%s?mode=ro" % cfg.db_path, uri=True)
con.row_factory = sqlite3.Row
today = datetime.now()
print("db=%s behavior_events 总行数=%s" % (
    cfg.db_path, con.execute("SELECT COUNT(*) FROM behavior_events").fetchone()[0]))

for days in DAYS:
    day_to = today.strftime("%Y-%m-%d")
    day_from = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = con.execute("SELECT persons_json FROM behavior_events WHERE day>=? AND day<=?",
                       (day_from, day_to)).fetchall()
    names = sorted({n for r in rows for n in names_of(r[0])})
    print("\n=== 窗口 %d 天（%s → %s）行数=%s 姓名数=%s ===" % (days, day_from, day_to,
                                                    len(rows), len(names)))
    for plimit in PAGE_LIMITS:
        zero = []
        gaps = []
        for nm in names:
            truth = con.execute(
                "SELECT COUNT(*) FROM behavior_events b WHERE b.day>=? AND b.day<=? AND "
                + MEMBER_CLAUSE, (day_from, day_to, nm, nm)).fetchone()[0]
            page = con.execute(
                "SELECT persons_json FROM behavior_events WHERE day>=? AND day<=?"
                " ORDER BY server_ts DESC LIMIT ?", (day_from, day_to, plimit)).fetchall()
            got = sum(1 for r in page if nm in names_of(r[0]))
            if truth and not got:
                zero.append(nm)
            elif truth > got:
                gaps.append(truth - got)
        print("  页宽 limit=%d：旧口径报 0 条而真值非 0 的姓名=%d 个；有少报的姓名=%d 个，"
              "缺口最大=%d 行" % (plimit, len(zero), len(gaps), max(gaps) if gaps else 0))

# 边界自证：SQL 口径与 Python 逐行扫描口径必须逐姓名相等（否则总数不可信）
allrows = con.execute("SELECT persons_json FROM behavior_events").fetchall()
every = sorted({n for r in allrows for n in names_of(r[0])})
mismatch = 0
for nm in every:
    truth = con.execute("SELECT COUNT(*) FROM behavior_events b WHERE " + MEMBER_CLAUSE,
                        (nm, nm)).fetchone()[0]
    scanned = sum(1 for r in allrows if nm in names_of(r[0]))
    mismatch += (truth != scanned)
print("\n=== 口径一致性 ===\n  姓名数=%d 与全表 Python 扫描不一致=%d" % (len(every), mismatch))
con.close()
