"""ADM 联动执行计划-MA 第 0 步：家庭墙钟的主路径换成 `homesdk.time`。

依据：`ADM联动主题注册表与消息契约.md` §四、`ADM联动执行计划-MA.md` 第 0 步 ②③、
DCD `20261001-AF-homesdk接入四问-裁定.md` 问题 2。

这里锁的是三件事，缺一件都算不上"接上了"：
1. **接缝会换挡**——homesdk 在场且家庭时区是按名字声明的，换算必须走它，
   MA 的 `tz_offset_hours` 只能当 fallback；
2. **换挡有门**——装了库但没按时区名声明时，MA 自己配的偏移必须继续说了算
   （否则"多装一个包"就把非 +8 家庭的时间轴换成了库默认的 Asia/Shanghai）；
3. **热更新接得上**——第七轮 CRITICAL-1 的同一形态：进程内缓存的"不可用"判定
   必须能被 `reset_homesdk_probe()` 解锁。

`test_insights_house_timezone.py` 里那套断言全部按固定偏移写死，它守的是第 2 条
（fallback 分支）；本文件守的是第 1、3 条和两个分支的键名优先级。
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent import house_time  # noqa: E402
from memory_agent.store import now_local, parse_ts  # noqa: E402

TZ_KEYS = ("HOMESDK_TZ", "TZ", "AF_TZ", "HOMESDK_TZ_OFFSET_HOURS", "TZ_OFFSET_HOURS")


@pytest.fixture(autouse=True)
def _clean_mechanism():
    """每个用例都从"未探测 + 无时区环境变量 + 偏移快照已知"起步，跑完原样交还。"""
    saved_env = {k: os.environ.get(k) for k in TZ_KEYS}
    for k in TZ_KEYS:
        os.environ.pop(k, None)
    saved_probe = house_time._probe
    saved_hours = house_time._fallback_hours_cache
    house_time.set_fallback_hours(8.0)
    yield
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    house_time._probe = saved_probe
    house_time._fallback_hours_cache = saved_hours


def _stub(tz=None, source="env:TZ", resolved=True, now=None):
    """一个 homesdk.time 形状的最小替身（不依赖装没装库）。"""
    tz = tz or timezone(timedelta(hours=-5))
    return SimpleNamespace(
        house_tz=lambda: tz,
        house_now=lambda: now or datetime(2026, 10, 2, 1, 30, 0, tzinfo=tz),
        house_tz_status=lambda: {
            "tz_name": getattr(tz, "key", "UTC-05"),
            "source": source,
            "resolved_by_name": resolved,
            "utc_offset": tz.utcoffset(None),
            "tzdata_available": True,
        },
    )


# ── 探测缓存与热更新接缝 ────────────────────────────────────────────────────

def test_probe_caches_the_unavailable_verdict(monkeypatch):
    """homesdk 不在场时判定为不可用，且结果被缓存（不会每条事件都重试导入）。"""
    monkeypatch.setitem(sys.modules, "homesdk", None)
    house_time._probe = None
    assert house_time.homesdk_time() is None
    assert house_time._probe is False, "不可用的判定应当缓存，而不是每次重新导入"
    assert house_time.is_active() is False


def test_reset_homesdk_probe_unlocks_the_negative_cache(monkeypatch):
    """刚 pip install 完必须能解锁：否则进程永远停在"这机器没有 homesdk"。"""
    monkeypatch.setitem(sys.modules, "homesdk", None)
    house_time._probe = None
    assert house_time.homesdk_time() is None
    assert house_time.reset_homesdk_probe() is True, "已探测态应报告"
    assert house_time._probe is None

    monkeypatch.setitem(sys.modules, "homesdk", SimpleNamespace(time=_stub()))
    assert house_time.homesdk_time() is not None
    house_time.set_fallback_hours(7.5)
    house_time.reset_homesdk_probe()
    assert house_time._fallback_hours_cache is None, "reset 同时清掉偏移快照"


# ── 第 1 条：主路径当家时的委托 ─────────────────────────────────────────────

def test_active_only_when_the_zone_is_named_not_guessed():
    house_time._probe = _stub(source="env:TZ")
    assert house_time.is_active() is True
    house_time._probe = _stub(source="default")
    assert house_time.is_active() is False, "库的默认猜测不能当家庭的钟"
    house_time._probe = _stub(source="env:TZ_OFFSET_HOURS", resolved=True)
    assert house_time.is_active() is False, "小时偏移旧键走 MA 自己的换算"
    house_time._probe = _stub(source="env:TZ", resolved=False)
    assert house_time.is_active() is False, "名字解析不动（无 tzdata）就不换主路径"


def test_clock_and_tzinfo_delegate_to_homesdk_when_active():
    """now_local 的偏移参数在当家时不参与换算——契约 §四 的口径，不是笔误。"""
    house_time._probe = _stub()
    stamp = house_time.now_local(8.0)
    assert stamp == datetime(2026, 10, 2, 1, 30, 0), "取的是 homesdk 的墙钟，不是 +8 偏移算出来的"
    assert stamp.tzinfo is None and stamp.microsecond == 0
    assert house_time.house_tz().utcoffset(None) == timedelta(hours=-5)
    assert house_time.utc_offset_hours() == -5.0


def test_store_now_local_and_parse_ts_follow_the_mechanism_path():
    """`store` 是全仓唯一的写入口：它必须跟着主路径走，否则 day/server_ts 落库口径分叉。"""
    house_time._probe = _stub()
    assert now_local(8.0) == datetime(2026, 10, 2, 1, 30, 0)
    aware_utc = datetime(2026, 10, 2, 6, 30, tzinfo=timezone.utc)
    # UTC 06:30 在 UTC-5 的家庭里是 01:30；按 tz_offset_hours=8 硬算会得到 14:30
    assert parse_ts(aware_utc.isoformat(), 8.0) == datetime(2026, 10, 2, 1, 30)


def test_status_reports_the_active_backend():
    house_time._probe = _stub()
    st = house_time.status()
    assert st["backend"] == "homesdk.time"
    assert st["homesdk_available"] is True
    assert st["source"] == "env:TZ"
    assert st["utc_offset_hours"] == -5.0
    assert "reason" not in st


# ── 第 2 条：门——装了库不等于换了钟 ─────────────────────────────────────────

def test_library_present_but_undeclared_keeps_ma_offset(monkeypatch):
    """非 +8 家庭升级 homesdk 后时间轴不许被打歪：没有时区名声明就不换主路径。"""
    house_time._probe = _stub(source="default")
    house_time.set_fallback_hours(-5.0)
    monkeypatch.setattr(house_time, "datetime", _FixedNow(datetime(2026, 10, 2, 6, 30)))
    assert house_time.now_local(-5.0) == datetime(2026, 10, 2, 1, 30)
    st = house_time.status()
    assert st["backend"] == "ma_fallback"
    assert st["homesdk_available"] is True, "排障时要能看出库在场但没接管"
    assert "未按 IANA 名声明" in st["reason"]


def test_insights_house_tz_prefers_mechanism_then_injection():
    """insights 层的退化顺序：homesdk（当家）→ Config 注入 → 模块级 fallback 常量。"""
    from memory_agent.insights import models

    models.set_house_tz_offset(9)
    try:
        house_time._probe = _stub(source="default")
        assert models.house_tz().utcoffset(None) == timedelta(hours=9), "没当家时注入仍然说话"
        house_time._probe = _stub()
        assert models.house_tz().utcoffset(None) == timedelta(hours=-5)
        assert models.house_tz_label() == "UTC-5"
    finally:
        house_time._probe = None
        models.set_house_tz_offset(8.0)


# ── 第 3 条：配置与偏移快照的同步 ───────────────────────────────────────────

class _FixedNow:
    """替身 datetime：只认 `now(tz)`，把"容器时钟"钉死成 UTC 的某个时刻。"""

    def __init__(self, fixed_utc):
        self._fixed = fixed_utc

    def now(self, tz=None):
        if tz is None:
            return self._fixed
        hours = tz.utcoffset(None) / timedelta(hours=1)
        return (self._fixed + timedelta(hours=hours)).replace(tzinfo=tz)

    def __getattr__(self, name):  # datetime.date 等其余属性透传
        return getattr(datetime, name)


def test_fallback_branch_follows_the_configured_offset():
    """未装库的部署照旧：偏移就是换算依据，与 homesdk 的默认 Asia/Shanghai 无关。"""
    house_time._probe = False
    house_time.set_fallback_hours(9.0)
    tz = house_time.house_tz()
    assert tz.utcoffset(None) == timedelta(hours=9)
    assert house_time.utc_offset_hours() is None, "不当家时不向 Config 广播偏移"
    assert house_time.status()["backend"] == "ma_fallback"


def test_device_health_ts_is_the_house_clock_not_the_machine_clock(monkeypatch):
    """跨仓 payload 的 `ts` 以前用 `time.strftime` = 机器时区：UTC 容器里发出去的时刻
    比 MA 库里的 `ts` 早 8 小时，DB/AF 按它排"刚刚发生"就会看到未来的事件。"""
    import json

    from memory_agent.config import Config
    from memory_agent.mqtt_bridge import MqttBridge

    class _Client:
        def __init__(self):
            self.published = []

        def is_connected(self):
            return True

        def publish(self, topic, payload, qos=0, retain=False):
            self.published.append({"topic": topic, "payload": payload})

    cfg = Config()
    cfg.tv_mqtt_host = "192.168.2.200"
    cfg.tv_mqtt_port = 1883
    cfg.ma_mqtt_enabled = True
    cfg.tz_offset_hours = 8.0
    client = _Client()
    bridge = MqttBridge(cfg, client_factory=lambda c: client)

    house_time._probe = False
    monkeypatch.setattr(house_time, "datetime", _FixedNow(datetime(2026, 10, 2, 16, 30)))
    assert bridge.publish_health_change("sensor.x", "ok", "stale") is True
    body = json.loads(client.published[0]["payload"])
    # UTC 16:30 的家庭墙钟是次日 00:30；写成 "2026-10-02T16:30:00" 就是把容器钟当家庭钟
    assert body["ts"] == "2026-10-03T00:30:00"


def test_config_seeds_tz_offset_hours_from_homesdk_when_active(monkeypatch):
    """HOMESDK_TZ 声明的偏移要落进 Config.tz_offset_hours：显示类的 +8 快照才不再错。"""
    from memory_agent.config import get_config

    house_time._probe = _stub()
    monkeypatch.setenv("TZ_OFFSET_HOURS", "8")
    monkeypatch.setenv("JWT_SECRET", "ci-test-step0")
    cfg = get_config()
    assert cfg.tz_offset_hours == -5.0, "主路径当家时以 homesdk 的偏移为准"
    assert house_time._fallback_hours_cache == -5.0, "偏移快照随配置刷新"


def test_config_keeps_env_offset_when_homesdk_undeclared(monkeypatch):
    from memory_agent.config import get_config
    from memory_agent.insights.models import house_tz

    house_time._probe = _stub(source="default")
    monkeypatch.setenv("TZ_OFFSET_HOURS", "9")
    monkeypatch.setenv("JWT_SECRET", "ci-test-step0")
    cfg = get_config()
    assert cfg.tz_offset_hours == 9.0, "库的默认猜测不许盖过显式配置"
    assert house_tz().utcoffset(None) == timedelta(hours=9)


# ── 真库口径（装了 homesdk 才跑，CI 未装时跳过）─────────────────────────────

def test_real_homesdk_key_priority_homesdk_tz_wins():
    """验收标准里的键名优先级：HOMESDK_TZ > TZ_OFFSET_HOURS（过渡别名）。"""
    hs = pytest.importorskip("homesdk.time", reason="homesdk>=0.3.1 才带 time 模块")
    os.environ["HOMESDK_TZ"] = "Asia/Tokyo"
    os.environ["TZ_OFFSET_HOURS"] = "8"
    try:
        house_time._probe = hs
        assert house_time.is_active() is True
        assert hs.house_tz_name() == "Asia/Tokyo"
        assert house_time.utc_offset_hours() == 9.0
        assert house_time.house_tz().utcoffset(datetime(2026, 10, 2)) == timedelta(hours=9)
    finally:
        os.environ.pop("HOMESDK_TZ", None)


def test_real_homesdk_alias_still_understands_the_hour_offset():
    """只给 `TZ_OFFSET_HOURS` 时 homesdk 认这个别名，但主路径仍归 MA（门在第 2 条）。"""
    hs = pytest.importorskip("homesdk.time")
    os.environ["TZ_OFFSET_HOURS"] = "-3"
    try:
        house_time._probe = hs
        assert hs.house_tz_status()["source"] == "env:TZ_OFFSET_HOURS"
        assert house_time.is_active() is False
        assert house_time.utc_offset_hours() is None
        assert house_time.now_local(-3.0).tzinfo is None
    finally:
        os.environ.pop("TZ_OFFSET_HOURS", None)


def test_real_homesdk_named_tz_takes_the_wheel():
    hs = pytest.importorskip("homesdk.time")
    os.environ["HOMESDK_TZ"] = "America/New_York"
    try:
        house_time._probe = hs
        assert house_time.is_active() is True
        # 容器 UTC 与家庭墙钟的差必须来自 IANA 解析（-4/-5 随 DST 变），不是写死的 +8
        offset = house_time.utc_offset_hours()
        assert offset in (-4.0, -5.0)
        assert house_time.status()["tz_name"] == "America/New_York"
    finally:
        os.environ.pop("HOMESDK_TZ", None)
