"""判定「行为推断空转」的根因在哪一侧：上游没新数据，还是推断层自己不出状态。"""
import sqlite3
import sys
from datetime import timedelta

sys.path.insert(0, "/app/src")
from memory_agent.config import get_config      # noqa: E402
from memory_agent.store import now_local        # noqa: E402

cfg = get_config()
c = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)


def q(sql):
    try:
        return c.execute(sql).fetchall()
    except Exception as exc:
        return [("ERR", type(exc).__name__, str(exc)[:90])]


now = now_local(cfg.tz_offset_hours)
print("WALLCLOCK =", now.isoformat(timespec="seconds"))
for label, hours in (("15m", 0.25), ("1h", 1.0), ("24h", 24.0), ("168h", 168.0)):
    since = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    ev = q(f"select count(*) from events where ts>='{since}'")[0][0]
    pe = q(f"select count(*) from perception_events where server_ts>='{since}'")[0][0]
    be = q(f"select count(*) from behavior_events where server_ts>='{since}'")[0][0]
    print(f"window={label:5} events={ev:6} perception={pe:6} behavior={be:6}  since={since}")

print("events_max_ts =", q("select max(ts) from events")[0])
print("perception_max_server_ts =", q("select max(server_ts) from perception_events")[0])
print("behavior_max_server_ts =", q("select max(server_ts) from behavior_events")[0])
print("behavior_states =", q("select count(*) from behavior_states")[0][0])
print("presence_tables =", q(
    "select name from sqlite_master where type='table' and name like '%presence%'"))
