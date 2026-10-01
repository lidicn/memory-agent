"""家庭墙钟口径的时区一致性测试（BUG-TZ1）。

生产事实：events.ts 是 UTC+8 的 naive ISO 字符串，而容器常跑在 UTC。
若用机器本地时区做 epoch<->墙钟换算，所有时间窗会整体平移 (8 - 机器偏移) 小时，
且在本机（+8）跑不出来——所以这里的断言全部用**显式 epoch 值**，与机器时区无关。
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.insights.models import (  # noqa: E402
    HOUSE_TZ_FALLBACK_HOURS, TimeRange, day_key, fmt_ts, hour_of,
    house_dt, house_tz, house_ts, set_house_tz_offset)
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
    assert house_tz().utcoffset(None) == timedelta(hours=HOUSE_TZ_FALLBACK_HOURS)


def test_house_now_tracks_real_epoch():
    """house_now() 的 epoch 必须与真实时间一致（误差 < 5s），不随机器时区漂移。"""
    import time
    from memory_agent.insights.models import house_now
    assert abs(house_ts(house_now()) - time.time()) < 5


@pytest.fixture(autouse=True)
def _pin_house_tz():
    """把注入值钉回 fallback，跑完还原。

    本模块的断言全按 +8 写死；若别处测试调过 get_config()（会按 Config.tz_offset_hours
    注入），不钉住就会把整条时间轴带走，变成跟测试顺序相关的假红。
    """
    before = house_tz()
    set_house_tz_offset(HOUSE_TZ_FALLBACK_HOURS)
    yield
    set_house_tz_offset(before.utcoffset(None).total_seconds() / 3600)


def _hour_offset(tz):
    return tz.utcoffset(None) / timedelta(hours=1)


def test_injected_offset_moves_the_whole_axis():
    """注入 9 小时后，同一 epoch 的家庭墙钟要整体 +1（口径确实可配）。"""
    set_house_tz_offset(9)
    assert _hour_offset(house_tz()) == 9.0
    assert house_dt(HOUSE_EPOCH) == datetime(2016, 4, 1, 8, 0, 0)
    # 反向：naive 墙钟按新偏移解释，epoch 提前一小时
    assert house_ts(NAIVE) == HOUSE_EPOCH - 3600
    assert hour_of(HOUSE_EPOCH) == 8


def test_injection_is_live_for_every_consumer_not_a_snapshot():
    """timeframe / repository 必须读**注入后的**时区。

    这条专门锁"import 时把 HOUSE_TZ 绑成局部名"这类缺陷：那种写法在注入后仍按
    +8 换算，测试在本机（+8）永远绿，生产改了配置却毫无反应。
    """
    set_house_tz_offset(9)
    aware = datetime(2016, 3, 31, 23, 0, 0, tzinfo=timezone.utc)
    assert parse_time(aware) == datetime(2016, 4, 1, 8, 0, 0)
    # naive 输入按注入后的偏移解释（+9 口径下 07:00 比 +8 早一个 epoch 小时）
    assert as_ts("2016-04-01 07:00:00") == HOUSE_EPOCH - 3600
    # 带时区的输入本来就是绝对时刻，偏移怎么改都不该动
    assert as_ts(aware) == HOUSE_EPOCH
    assert _to_iso(HOUSE_EPOCH) == "2016-04-01T08:00:00"


def test_illegal_offset_does_not_move_the_axis():
    """非法值宁可留在 fallback，也不要把整条时间轴打歪。"""
    for bad in ("", "abc", None):
        assert _hour_offset(set_house_tz_offset(bad)) == HOUSE_TZ_FALLBACK_HOURS
    assert _hour_offset(house_tz()) == HOUSE_TZ_FALLBACK_HOURS


def test_out_of_range_offset_does_not_move_the_axis():
    """TZ_OFFSET_HOURS=80 这类笔误换算不报错，却会让所有窗口整体消失——必须拒收。"""
    from memory_agent.insights.models import set_house_tz_offset as set_tz
    for bad in (80, -30, 0.0001 * 10 ** 6, "1e9"):
        assert _hour_offset(set_tz(bad)) == HOUSE_TZ_FALLBACK_HOURS
    # 边界内的真实偏移照常接受（UTC+14 / UTC-12 是地球上存在的时区）
    assert _hour_offset(set_tz(14)) == 14.0
    assert _hour_offset(set_tz(-12)) == -12.0


def test_tz_label_tells_the_truth_about_the_injected_offset():
    """对外回显的口径标签必须跟着注入走，不能永远自称深圳。"""
    from memory_agent.insights.models import house_tz_label
    from memory_agent.insights.utils import resolve_nl_window
    assert house_tz_label() == "Asia/Shanghai"
    assert resolve_nl_window("今天", 7)[2]["timezone"] == "Asia/Shanghai"
    set_house_tz_offset(9)
    assert house_tz_label() == "UTC+9"
    assert resolve_nl_window("最近三天", 3)[2]["timezone"] == "UTC+9"


def test_get_config_injects_tz_offset_hours():
    """唯一部署配置项 = TZ_OFFSET_HOURS → Config.tz_offset_hours → 注入 insights 层。

    这是 DCD 裁定「MA 侧 +8 退化为 fallback」的落点：配置改了就真的生效，
    不需要任何代码里再出现小时数。
    """
    from memory_agent.config import get_config

    saved = {k: os.environ.get(k) for k in ("TZ_OFFSET_HOURS", "JWT_SECRET")}
    try:
        os.environ["TZ_OFFSET_HOURS"] = "9"
        os.environ.setdefault("JWT_SECRET", "ci-test-inject-tz")
        cfg = get_config()
        assert cfg.tz_offset_hours == 9.0
        assert _hour_offset(house_tz()) == 9.0
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # 把注入值交还给真实配置，别把 9 留给后面的测试
        set_house_tz_offset(get_config().tz_offset_hours)


def test_no_machine_local_hour_literals_left_in_src():
    """回归锁：源码里不得再出现写死的小时数字面量。

    - ``now_local(8`` —— 绕过 Config 直接按 +8 取墙钟；
    - ``timedelta(hours=8)`` —— 绕过注入的时区常量。
      构造函数默认值（``tz_offset_hours: float = 8.0``）与 ``getattr(cfg, ..., 8)``
      属 fallback，不在禁止范围。
    """
    root = os.path.join(os.path.dirname(__file__), "..", "src", "memory_agent")
    offenders = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    if "now_local(8" in line or "timedelta(hours=8)" in line:
                        offenders.append(f"{os.path.relpath(path, root)}:{n}")
    assert not offenders, f"仍有写死 +8 的换算点：{offenders}"
