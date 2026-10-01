"""家庭墙钟口径的时区一致性测试（BUG-TZ1）。

生产事实：events.ts 是 UTC+8 的 naive ISO 字符串，而容器常跑在 UTC。
若用机器本地时区做 epoch<->墙钟换算，所有时间窗会整体平移 (8 - 机器偏移) 小时，
且在本机（+8）跑不出来——所以这里的断言全部用**显式 epoch 值**，与机器时区无关。
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.insights.models import (  # noqa: E402
    HOUSE_TZ, TimeRange, day_key, fmt_ts, hour_of, house_dt, house_ts)
from memory_agent.insights.parser.timeframe import as_ts, parse_time  # noqa: E402
from memory_agent.insights.repository import _to_iso  # noqa: E402

# 2016-04-01 07:00 深圳墙钟对应的真实 epoch
HOUSE_EPOCH = datetime(2016, 4, 1, 7, 0, 0, tzinfo=timezone(timedelta(hours=8))).timestamp()
NAIVE = datetime(2016, 4, 1, 7, 0, 0)


def test_naive_datetime_is_interpreted_as_house_time():
    assert house_ts(NAIVE) == HOUSE_EPOCH
    tr = TimeRange(NAIVE, NAIVE + timedelta(hours=1))
    assert tr.start_ts == HOUSE_EPOCH
    # repository 的 epoch->ISO 与 house_ts 必须互为逆运算（否则窗口整体平移）
    assert _to_iso(tr.start_ts) == "2016-04-01T07:00:00"
    assert house_dt(HOUSE_EPOCH) == NAIVE


def test_parse_time_and_as_ts_are_tz_independent():
    assert as_ts("2016-04-01 07:00:00") == HOUSE_EPOCH
    assert as_ts("2016-04-01T07:00:00") == HOUSE_EPOCH
    assert parse_time(HOUSE_EPOCH) == NAIVE
    # 带时区的输入折算到家庭墙钟（07:00+08 == 前一日 23:00Z），不是机器本地墙钟
    aware = datetime(2016, 3, 31, 23, 0, 0, tzinfo=timezone.utc)
    assert parse_time(aware) == NAIVE
    assert as_ts(aware) == HOUSE_EPOCH


def test_day_and_hour_helpers_use_house_clock():
    assert hour_of(HOUSE_EPOCH) == 7
    assert day_key(HOUSE_EPOCH) == "2016-04-01"
    assert fmt_ts(HOUSE_EPOCH) == "2016-04-01 07:00:00"
    assert HOUSE_TZ.utcoffset(None) == timedelta(hours=8)


def test_house_now_tracks_real_epoch():
    """house_now() 的 epoch 必须与真实时间一致（误差 < 5s），不随机器时区漂移。"""
    import time
    from memory_agent.insights.models import house_now
    assert abs(house_ts(house_now()) - time.time()) < 5
