"""Q6-2 按天分批扫描的两侧诊断（本机 +8 / 容器 UTC）。只读临时库，绝不碰生产库。

容器 UTC 下 tests/test_vma_q62_daily_batch_scan.py 两条判红、本机 +8 判绿。
量出三者的真实取值：house_tz 来源、events.day 列、_bounds 日键、逐日 SQL 命中数。
"""
import os
import sys
from datetime import datetime, timedelta

ROOT = os.environ.get("MA_SNAP", ".")
sys.path.insert(0, os.path.join(ROOT, "src"))

from memory_agent.insights import models as M
from memory_agent.insights.models import InsightConfig, TimeRange, house_now
from memory_agent.insights.repository import StoreRepository
from memory_agent.store import Store

print("os.environ TZ =", repr(os.environ.get("TZ", "<unset>")))
print("datetime.now() =", datetime.now().isoformat(timespec="seconds"))
print("house_tz =", M.house_tz(), "label =", M.house_tz_label())
try:
    from memory_agent.house_time import is_active
    print("house_time.is_active =", is_active())
except Exception as exc:
    print("house_time import fail", type(exc).__name__, exc)
print("house_now =", house_now().isoformat(timespec="seconds"))

tmp = os.path.join(os.environ.get("TEMP", "/tmp"), "ma_probe_q62_%d.db" % os.getpid())
if os.path.exists(tmp):
    os.remove(tmp)
st = Store(tmp, 8.0)
st.init_schema()

base0 = house_now()
for day_offset, count in ((0, 10), (1, 1), (2, 1)):
    base = base0 - timedelta(days=day_offset)
    for i in range(count):
        ts = (base.replace(hour=0, minute=0, second=0, microsecond=0)
              + timedelta(minutes=i * 10)).isoformat(timespec="seconds")
        st.insert_events([{
            "entity_id": "light.living",
            "ts": ts,
            "new_state": "on",
            "room": "客厅",
            "domain": "light",
            "attrs": {"friendly_name": "客厅灯"},
        }])

cur = st.connect().cursor()
cur.execute("SELECT day, COUNT(*), MIN(ts), MAX(ts) FROM events GROUP BY day ORDER BY day")
for r in cur.fetchall():
    print("db_row", tuple(r))

repo = StoreRepository(st, InsightConfig(max_scan=30))
end = house_now()
start = end - timedelta(days=3)
tr = TimeRange(start, end)
print("tr.start_iso =", tr.start_iso, "tr.end_iso =", tr.end_iso)
print("bounds =", repo._bounds(tr))
print("where =", repo._event_where(tr))
events = repo.load_events(tr)
print("len =", len(events), "truncated =", repo.last_scan_truncated)

st.close()
os.remove(tmp)
print("PROBE_DONE")
