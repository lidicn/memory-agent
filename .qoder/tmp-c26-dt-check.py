"""A8 落下的 `Event.dt` 口径修复：成对生效读数用的最小检查（不读库、不写任何东西）。

`EventRecord` 的 `day` / `hour` 走 `house_dt()`（家庭墙钟），而修前的 `dt` 走
`datetime.fromtimestamp(ts)`（机器本地时区）。容器里机器时区是 UTC、家庭是 +8，
所以同一条事件的三个读数在修前会自相矛盾——这里把矛盾本身量出来。
"""
import time
from datetime import datetime

from memory_agent.insights.models import EventRecord, house_tz

TS = 1759823400.0  # 固定 epoch：2025-10-07 07:50 UTC，不用运行时刻，免得读数随钟摆

ev = EventRecord(ts=TS, entity_id="sensor.probe")
machine_off = datetime.now().astimezone().utcoffset()
house_off = house_tz().utcoffset(None)
print("MACHINE_TZ=%s machine_offset_h=%s house_offset_h=%s" % (
    time.tzname, machine_off.total_seconds() / 3600.0, house_off.total_seconds() / 3600.0))
print("dt=%s day=%s hour=%s" % (
    ev.dt.strftime("%Y-%m-%d %H:%M"), ev.day, ev.hour))
print("MATCH_DT_VS_DAY=%s MATCH_DT_VS_HOUR=%s" % (
    ev.dt.strftime("%Y-%m-%d") == ev.day, ev.dt.hour == ev.hour))
