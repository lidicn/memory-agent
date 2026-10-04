"""设备事件 feed 的回归锁 —— DCD 裁定 20261001-MA-service_token与设备事件feed §二。

裁定三项都要有对应的锁，否则"落地"只是文档里的字：

* **Q1=B**（独立批量扫描器）：逐条事件必须带**自己的**时间进 ``match_event``——
  回放一小时的历史时若把 ``now_ts`` 交给 ``time.time()``，全部事件都算"同一瞬间"，
  ``min_count`` 门槛形同不存在。``test_count_trigger_does_not_fire_when_…`` 咬这一条。
* **Q2 三项降噪**：domain 白名单（排除 ``sensor``）、只收二元翻转、60 秒/3 次
  作为**裁定值**写进晋升规则的 ``trigger``（此前 ``trigger`` 从不入库）。
* **Q3=(i)**「通道建好、推进等人」：总开关默认关、开启后首轮默认 dry_run，
  保留期 7 天 + 上限 10 万行由 MA 侧裁剪。

另附 ``runtime._periodic_device_feed`` 的两条：开关**每轮重读**（打开通道不等重启）、
旁路通道的异常不得打死常驻任务。
"""

import asyncio
import calendar
import json
import os
import tempfile
import types
from datetime import timedelta

import pytest

from memory_agent import device_feed as df
from memory_agent.device_feed import (
    BINARY_STATES, DEVICE_TRIGGER, FEED_DOMAINS, FEED_TAGS, MIN_COUNT, QUERY_LIMIT,
    TRIGGER_MAX_ROWS, TRIGGER_RETENTION_DAYS, WINDOW_SECONDS,
    DeviceEventFeed, binary_change, friendly_names, get_device_feed, parse_wall,
    wall_to_epoch,
)
from memory_agent.rule_engine import DEVICE_EVENT_KIND, ActiveRuleEngine
from memory_agent.rule_lifecycle import RuleLifecycle, build_condition
from memory_agent.store import CANDIDATE_ACCEPTED, Store, now_local


# ── 夹具 ────────────────────────────────────────────────────────────────────

@pytest.fixture
def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)                      # Store 需要自建文件，否则撞上已存在的空文件
    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
    yield st
    st.close()
    try:
        os.remove(path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _no_feed_singleton():
    """``get_device_feed`` 是进程内单例，测试之间必须清空，否则水位线串台。"""
    df._feed = None
    yield
    df._feed = None


def _cfg(enabled=True, dry_run=True, interval=3600):
    return types.SimpleNamespace(
        device_feed_enabled=enabled, device_feed_dry_run=dry_run,
        device_feed_interval_seconds=interval,
        tz_offset_hours=8.0,
        rooms={"客厅": {"entities": {
            "binary_sensor.front_door": {"friendly_name": "前门磁"},
            "light.study_desk": {"friendly_name": "书房台灯"},
            "sensor.study_temp": {"friendly_name": "书房温度"},
        }}},
    )


def _ev(eid="binary_sensor.front_door", old="off", new="on", ts="2026-09-20T19:05:00",
        room="客厅"):
    return {"id": f"{eid}@{ts}@{old}->{new}", "entity_id": eid, "ts": ts, "room": room,
            "old_state": old, "new_state": new, "action": f"{old}->{new}"}


def _feed(store, engine, cfg=None, names=None):
    return DeviceEventFeed(store, engine, cfg or _cfg(), names=names)


class _NullEngine:
    """只要"有没有命中"，不要规则语义的替身引擎。"""

    def __init__(self):
        self.seen = []

    def match_event(self, event, now_ts=None):
        self.seen.append((event, now_ts))
        return []


class _RecordingEngine:
    """按 tag 命中一条固定规则，并记录每次 ``match_event`` 收到的 now_ts。"""

    def __init__(self, rule, hit_tags=("door",)):
        self.rule = rule
        self.hit_tags = set(hit_tags)
        self.now_ts = []
        self.executed = []

    def match_event(self, event, now_ts=None):
        self.now_ts.append(now_ts)
        if set(event.get("tags") or ()) & self.hit_tags:
            return [self.rule]
        return []

    def execute_action(self, rule, event, dry_run_override=False):
        self.executed.append((rule["rule_id"], event["entity_id"], dry_run_override))
        return {"dry_run": True} if dry_run_override else {"ok": True}


# ── Q2 第 1 项：domain 白名单 ────────────────────────────────────────────────

def test_ruling_whitelist_domains_excludes_sensor():
    assert FEED_DOMAINS == ("binary_sensor", "switch", "light", "cover", "climate",
                            "media_player")
    assert "sensor" not in FEED_DOMAINS


def test_sensor_domain_never_reaches_the_engine(store):
    """``sensor`` 就算报出 on→off 也不进 feed——白名单挡的是域，不是状态形状。"""
    engine = _NullEngine()
    store.insert_events([_ev(eid="sensor.study_door_state", new="off", old="on")])
    res = _feed(store, engine).run_once("2026-09-20T19:00:00", "2026-09-20T19:59:59")
    assert res["scanned"] == 0
    assert engine.seen == []


# ── Q2 第 2 项：只收二元翻转 ─────────────────────────────────────────────────

@pytest.mark.parametrize("old,new,expected", [
    ("off", "on", True),
    ("on", "off", True),
    ("closed", "open", True),
    ("OPEN", "CLOSED", True),          # 大小写归一，HA 各家集成写法不一
    ("on", "on", False),               # 同值重报不是"变化"
    ("19.5", "20.1", False),           # 数值读数是读数，不是翻转
    ("home", "away", False),           # 不在二值态字面量表里
    ("unknown", "on", False),          # 起点未知 → 不能断定发生过翻转
    ("on", "", False),
    ("", "", False),
    (None, "on", False),
])
def test_binary_change_gate_table(old, new, expected):
    assert binary_change({"old_state": old, "new_state": new}) is expected


def test_numeric_events_are_dropped_not_fed(store):
    """domain 在白名单里、状态却是读数的（气候面板温度回读），照样不进 feed。"""
    engine = _NullEngine()
    store.insert_events([
        _ev(eid="climate.study_thermostat", old="19.5", new="20.1",
            ts="2026-09-20T19:01:00"),
        _ev(ts="2026-09-20T19:02:00"),
    ])
    res = _feed(store, engine).run_once("2026-09-20T19:00:00", "2026-09-20T19:59:59")
    assert res["scanned"] == 2 and res["kept"] == 1
    assert res["dropped_non_binary"] == 1
    assert [e["entity_id"] for e, _ in engine.seen] == ["binary_sensor.front_door"]


# ── 事件形状：tags 必须是 list ───────────────────────────────────────────────

def test_to_event_shape_is_engine_visible_and_json_dumpable(store):
    """``_log_trigger`` 把整个 event ``json.dumps`` 落库：tags 是 set 会抛 TypeError
    并只返回 False——触发历史静默写失败，观察期判据就全没了。"""
    feed = _feed(store, _NullEngine(), names=friendly_names(_cfg()))
    event = feed.to_event(_ev(), friendly_names(_cfg()))
    assert event["kind"] == DEVICE_EVENT_KIND and event["device"] is True
    assert event["domain"] == "binary_sensor"
    assert event["tags"] == ["door"] and isinstance(event["tags"], list)
    assert event["state"] == "on" and event["old_state"] == "off"
    assert event["confidence"] == 1.0
    json.dumps(event, ensure_ascii=False)          # 不抛即通过


def test_to_event_domain_falls_back_to_entity_prefix(store):
    row = {"entity_id": "cover.bedroom_curtain", "new_state": "open",
           "old_state": "closed", "ts": "2026-09-20T08:00:00"}
    event = _feed(store, _NullEngine()).to_event(row, {})
    assert event["domain"] == "cover"
    assert event["tags"] == ["cover"]


# ── 时区口径：naive 墙钟不能按机器时区解释 ───────────────────────────────────

def test_wall_to_epoch_uses_house_offset_not_machine_tz():
    wall = parse_wall("2026-09-20T19:05:00")
    assert wall_to_epoch(wall, 8.0) == calendar.timegm(wall.timetuple()) - 8 * 3600
    assert wall_to_epoch(wall, 0.0) == calendar.timegm(wall.timetuple())
    # +8 与 0 的差必须是整 8 小时：口径是"家庭墙钟"，不是本机时区
    assert wall_to_epoch(wall, 8.0) - wall_to_epoch(wall, 0.0) == -8 * 3600


def test_parse_wall_accepts_space_and_tz_and_rejects_junk():
    assert parse_wall("2026-09-20 19:05:00") == parse_wall("2026-09-20T19:05:00")
    assert parse_wall("2026-09-20T19:05:00+08:00") is not None
    assert parse_wall("") is None and parse_wall(None) is None
    assert parse_wall("not-a-time") is None


# ── Q1=B：now_ts 必须是事件自身的时间 ────────────────────────────────────────

def test_feed_passes_each_events_own_epoch_to_the_engine(store):
    engine = _RecordingEngine({"rule_id": "r1", "condition": {}, "action": {"type": "log"}})
    store.insert_events([_ev(ts="2026-09-20T19:05:00"),
                         _ev(ts="2026-09-20T19:06:00", old="on", new="off")])
    _feed(store, engine).run_once("2026-09-20T19:00:00", "2026-09-20T19:59:59")
    assert len(engine.now_ts) == 2
    assert engine.now_ts[1] - engine.now_ts[0] == 60.0, "两条事件相差 60 秒，不是同一瞬间"


def test_count_trigger_fires_after_three_flips_inside_the_window(store):
    """3 次/60 秒（裁定值）：窗口内第 3 次翻转才触发。"""
    rule = {"rule_id": "r_count", "condition": {"kind": DEVICE_EVENT_KIND, "tag": "door"},
            "action": {"type": "log"}, "mode": "live", "enabled": True,
            "trigger": dict(DEVICE_TRIGGER)}
    engine = ActiveRuleEngine(store)
    store.insert_events([
        _ev(ts="2026-09-20T19:05:00", old="off", new="on"),
        _ev(ts="2026-09-20T19:05:20", old="on", new="off"),
        _ev(ts="2026-09-20T19:05:40", old="off", new="on"),
    ])
    with _installed_rule(engine, rule):
        res = _feed(store, engine).run_once(
            "2026-09-20T19:00:00", "2026-09-20T19:59:59", dry_run=True)
    assert res["matched"] == 1, res


def test_count_trigger_does_not_fire_when_flips_are_spread_out(store):
    """10 分钟内散开 3 次 = 不触发。回放历史时若用 ``time.time()`` 当"现在"，
    三次都会被当成同一瞬间 → 这条判红。这是 Q1=B「聚合再评估」的核心语义。"""
    rule = {"rule_id": "r_spread",
            "condition": {"kind": DEVICE_EVENT_KIND, "tag": "door"},
            "action": {"type": "log"}, "mode": "live", "enabled": True,
            "trigger": dict(DEVICE_TRIGGER)}
    engine = ActiveRuleEngine(store)
    store.insert_events([
        _ev(ts="2026-09-20T19:00:00", old="off", new="on"),
        _ev(ts="2026-09-20T19:05:00", old="on", new="off"),
        _ev(ts="2026-09-20T19:10:00", old="off", new="on"),
    ])
    with _installed_rule(engine, rule):
        res = _feed(store, engine).run_once(
            "2026-09-20T19:00:00", "2026-09-20T19:59:59", dry_run=True)
    assert res["matched"] == 0, res


def _installed_rule(engine, rule):
    """把一条规则塞进引擎缓存并保证测后还原（引擎有进程内倒排索引）。"""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        engine._rules_cache[rule["rule_id"]] = rule
        engine._kind_index[DEVICE_EVENT_KIND].append(rule["rule_id"])
        engine._index_dirty = False
        try:
            yield
        finally:
            engine._rules_cache.pop(rule["rule_id"], None)
            if rule["rule_id"] in engine._kind_index[DEVICE_EVENT_KIND]:
                engine._kind_index[DEVICE_EVENT_KIND].remove(rule["rule_id"])
            engine._event_windows.pop(rule["rule_id"], None)
    return _ctx()


# ── Q3=(i)：dry_run 只记录不派发 ────────────────────────────────────────────

def test_dry_run_records_trigger_history_and_dispatches_nothing(store):
    rule = {"rule_id": "r_dry", "condition": {"kind": DEVICE_EVENT_KIND, "tag": "door"},
            "action": {"type": "log"}, "mode": "live", "enabled": True, "trigger": {}}
    engine = ActiveRuleEngine(store)
    store.insert_events([_ev(ts="2026-09-20T19:05:00")])
    with _installed_rule(engine, rule):
        res = _feed(store, engine).run_once(
            "2026-09-20T19:00:00", "2026-09-20T19:59:59", dry_run=True)
    assert res["dry_run"] is True
    assert res["logged_only"] == 1 and res["dispatched"] == 0
    rows = store.list_rule_triggers("r_dry")
    assert len(rows) == 1 and int(rows[0]["dry_run"]) == 1


def test_dry_run_comes_from_config_when_not_forced(store):
    """``run_once()`` 不传 dry_run 时读 ``device_feed_dry_run``——裁定的"开启后首轮
    亦默认 dry_run"就是这个默认值，不是调用方每次自己记得传。"""
    engine = _RecordingEngine({"rule_id": "r_cfg", "condition": {}, "action": {"type": "log"}})
    store.insert_events([_ev(ts="2026-09-20T19:05:00")])
    res = _feed(store, engine, _cfg(dry_run=True)).run_once(
        "2026-09-20T19:00:00", "2026-09-20T19:59:59")
    assert res["dry_run"] is True
    assert engine.executed and engine.executed[0][2] is True

    engine2 = _RecordingEngine({"rule_id": "r_cfg2", "condition": {},
                                "action": {"type": "log"}})
    feed = _feed(store, engine2, _cfg(dry_run=False))
    feed._watermark = ""
    res2 = feed.run_once("2026-09-20T19:00:00", "2026-09-20T19:59:59")
    assert res2["dry_run"] is False
    assert engine2.executed[0][2] is False


def test_default_config_switches_are_off_and_dry_run():
    """裁定 Q3=(i)：反馈面为 0 时通道结构性停在 dry_run，所以总开关默认关。"""
    cfg = types.SimpleNamespace()          # 裸对象：getattr 兜底即裁定默认值
    feed = DeviceEventFeed(None, None, cfg)
    assert feed.enabled() is False
    assert feed.dry_run() is True
    assert feed.interval_seconds() == 3600


def test_config_env_overrides_are_not_silently_ignored(monkeypatch):
    """``device_feed_dry_run`` 默认 True、``interval`` 默认 3600 都是非空值，
    走不到 env_map 的「只在当前值为空时使用」分支——那等于这两个 env 永不过效。
    这条锁住"开关可以双向拨"这件事。"""
    from memory_agent.config import Config, get_config

    for key in ("DEVICE_FEED_ENABLED", "DEVICE_FEED_DRY_RUN", "DEVICE_FEED_INTERVAL_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    base = Config()
    assert (base.device_feed_enabled, base.device_feed_dry_run,
            base.device_feed_interval_seconds) == (False, True, 3600)

    monkeypatch.setenv("DEVICE_FEED_ENABLED", "1")
    monkeypatch.setenv("DEVICE_FEED_DRY_RUN", "false")
    monkeypatch.setenv("DEVICE_FEED_INTERVAL_SECONDS", "900")
    df._feed = None
    cfg = get_config()
    assert cfg.device_feed_enabled is True
    assert cfg.device_feed_dry_run is False
    assert cfg.device_feed_interval_seconds == 900

    monkeypatch.setenv("DEVICE_FEED_INTERVAL_SECONDS", "not-a-number")
    monkeypatch.setenv("DEVICE_FEED_ENABLED", "0")
    df._feed = None
    cfg = get_config()
    assert cfg.device_feed_enabled is False       # 非法值退回默认，不炸也不装通过
    assert cfg.device_feed_interval_seconds == 3600


# ── 水位线：不重复喂、不回捞 ────────────────────────────────────────────────

def test_watermark_advances_so_the_same_events_are_not_fed_twice(store):
    """重复喂同一批会让 count 虚高——同一个事件被数两次就凑满 3 次/60 秒了。

    这条原本只在"事件落在窗口正中"时成立：边界那一秒（`ts == end`）从来没被放过事件，
    于是它一边在 docstring 里写着不重复喂，一边把 `ts BETWEEN` 两端闭区间的重叠
    整个看不见。这里补上边界事件，判据才与它自己的名字对齐。
    """
    engine = _NullEngine()
    store.insert_events([_ev(ts="2026-09-20T19:05:00"),
                         _ev(old="on", new="off", ts="2026-09-20T19:30:00")])
    feed = _feed(store, engine)
    first = feed.run_once("2026-09-20T19:00:00", "2026-09-20T19:30:00")
    assert first["scanned"] == 2
    # 推过终点一秒：下一轮从这里起算，边界那一秒不会二次入账
    assert feed._watermark == "2026-09-20T19:30:01"
    assert first["watermark"] == "2026-09-20T19:30:01"
    assert first["pending_tail"] is False
    second = feed.run_once()                      # 默认窗口 = [水位, now]
    assert second["scanned"] == 0
    assert len(engine.seen) == 2


def test_window_end_plus_one_second_keeps_the_boundary_event_single(store):
    """第二轮用**默认窗口**（起点=上一轮水位）：整秒事件既不丢也不重。

    上一轮停在 `end` 一秒（旧形态）→ 这一轮的起点与它重合，19:30 那条被喂两遍；
    停得太远 → 19:30 或 20:00 会漏。两头都要判，所以既数总账也数单条。
    """
    engine = _NullEngine()
    store.insert_events([
        _ev(ts="2026-09-20T19:00:00"), _ev(old="on", new="off", ts="2026-09-20T19:30:00"),
        _ev(ts="2026-09-20T20:00:00")])
    feed = _feed(store, engine)
    first = feed.run_once("2026-09-20T19:00:00", "2026-09-20T19:30:00")
    second = feed.run_once(end="2026-09-20T20:59:59")     # start 取自水位线
    assert first["scanned"] + second["scanned"] == 3       # 3 条事件、3 次入账
    fed_ts = [event["ts"] for event, _now in engine.seen]
    assert fed_ts.count("2026-09-20T19:30:00") == 1
    assert sorted(fed_ts) == ["2026-09-20T19:00:00", "2026-09-20T19:30:00",
                              "2026-09-20T20:00:00"]


def test_truncated_window_resumes_instead_of_dropping_the_tail(store, monkeypatch):
    """读满上限时**不能**推到窗口终点：尾部整段没读，推过去就是静默丢事件。

    水位推到「最后读到的那条 + 1 秒」，下一轮接着读；本轮 `ok=False`、
    `pending_tail=True`，让运维看得见"这条窗口没读完"。
    """
    monkeypatch.setattr(df, "QUERY_LIMIT", 2)
    engine = _NullEngine()
    store.insert_events([
        _ev(ts="2026-09-20T19:01:00"), _ev(old="on", new="off", ts="2026-09-20T19:02:00"),
        _ev(ts="2026-09-20T19:03:00")])
    feed = _feed(store, engine)
    first = feed.run_once("2026-09-20T19:00:00", "2026-09-20T19:59:59")
    assert first["scanned"] == 2 and first["truncated"] is True
    assert first["ok"] is False and first["pending_tail"] is True
    assert feed._watermark == "2026-09-20T19:02:01"      # 停在读到的最后一条之后
    second = feed.run_once()                              # 默认窗口 = [水位, now]
    assert second["scanned"] == 1 and second["truncated"] is False
    assert sorted(event["ts"] for event, _now in engine.seen) == [
        "2026-09-20T19:01:00", "2026-09-20T19:02:00", "2026-09-20T19:03:00"]


def test_first_window_starts_one_interval_back(store):
    """没有水位就回捞全史 = 首轮把几年的设备事件一次性喂进 count 窗口。"""
    feed = _feed(store, _NullEngine(), _cfg(interval=3600))
    start, end = feed.window()
    assert (parse_wall(end) - parse_wall(start)) == timedelta(seconds=3600)


def test_interval_floor_is_60_seconds(store):
    feed = _feed(store, _NullEngine(), _cfg(interval=1))
    assert feed.interval_seconds() == 60


# ── 失败面如实：errors / truncated ──────────────────────────────────────────

class _OneBadEngine:
    def __init__(self, bad_entity):
        self.bad = bad_entity
        self.seen = []

    def match_event(self, event, now_ts=None):
        self.seen.append(event["entity_id"])
        if event["entity_id"] == self.bad:
            raise RuntimeError("规则条件炸了")
        return []


def test_one_bad_event_does_not_abort_the_round_and_is_not_ok(store):
    engine = _OneBadEngine("light.study_desk")
    store.insert_events([
        _ev(ts="2026-09-20T19:05:00"),
        _ev(eid="light.study_desk", old="off", new="on", ts="2026-09-20T19:06:00"),
        _ev(ts="2026-09-20T19:07:00", old="on", new="off"),
    ])
    res = _feed(store, engine).run_once("2026-09-20T19:00:00", "2026-09-20T19:59:59")
    assert engine.seen == ["binary_sensor.front_door", "light.study_desk",
                           "binary_sensor.front_door"], "吞掉一条不得打断整轮"
    assert len(res["errors"]) == 1 and "light.study_desk" in res["errors"][0]
    assert res["ok"] is False, "本轮吞过异常就不算成功"


def test_truncated_window_is_reported_and_not_ok(store, monkeypatch):
    monkeypatch.setattr(df, "QUERY_LIMIT", 2)
    store.insert_events([
        _ev(ts="2026-09-20T19:05:00"),
        _ev(ts="2026-09-20T19:06:00", old="on", new="off"),
        _ev(ts="2026-09-20T19:07:00"),
    ])
    res = _feed(store, _NullEngine()).run_once("2026-09-20T19:00:00",
                                               "2026-09-20T19:59:59")
    assert res["scanned"] == 2 and res["truncated"] is True
    assert res["ok"] is False, "窗口没读完不能报 ok=True"


def test_clean_round_is_ok(store):
    store.insert_events([_ev(ts="2026-09-20T19:05:00")])
    res = _feed(store, _NullEngine()).run_once("2026-09-20T19:00:00",
                                               "2026-09-20T19:59:59")
    assert res["ok"] is True and res["errors"] == [] and res["truncated"] is False


# ── observed_tags：把「本家有没有这个信号」变成读数 ─────────────────────────

def test_observed_tags_reads_only_whitelisted_binary_traffic(store):
    store.insert_events([
        _ev(ts="2026-09-20T19:05:00"),
        _ev(eid="light.study_desk", old="off", new="on", ts="2026-09-20T19:06:00"),
        _ev(eid="sensor.study_temp", old="19.5", new="20.1", ts="2026-09-20T19:07:00"),
    ])
    read = _feed(store, _NullEngine()).observed_tags("2026-09-20T19:00:00",
                                                     "2026-09-20T19:59:59")
    assert read["tags"] == ["door", "light"]
    assert read["scanned"] == 2 and read["complete"] is True
    assert read["domains"] == list(FEED_DOMAINS)


def test_observed_tags_reports_incompleteness(store, monkeypatch):
    monkeypatch.setattr(df, "QUERY_LIMIT", 1)
    store.insert_events([_ev(ts="2026-09-20T19:05:00"),
                         _ev(eid="light.study_desk", old="off", new="on",
                             ts="2026-09-20T19:06:00")])
    read = _feed(store, _NullEngine()).observed_tags("2026-09-20T19:00:00",
                                                     "2026-09-20T19:59:59")
    assert read["complete"] is False, "读满上限时不能给恒真的 ok 让调用方以为有料"


# ── 保留期裁剪（裁定 §Q3.1）────────────────────────────────────────────────

class _PurgeStore:
    def __init__(self, ret=None):
        self.calls = []
        self.ret = ret if ret is not None else {"deleted_expired": 3,
                                                "deleted_over_cap": 0,
                                                "remaining": 42, "kept_labeled": 1}
    tz_offset_hours = 8.0

    def purge_rule_triggers(self, cutoff, max_rows):
        self.calls.append((cutoff, max_rows))
        return self.ret


def test_purge_applies_both_ruling_caps_and_reports_ok():
    st = _PurgeStore()
    feed = DeviceEventFeed(st, None, _cfg())
    res = feed.purge_trigger_history()
    cutoff, max_rows = st.calls[0]
    assert max_rows == TRIGGER_MAX_ROWS == 100_000
    assert TRIGGER_RETENTION_DAYS == 7
    assert parse_wall(cutoff).date() == (now_local(8.0) - timedelta(days=7)).date()
    assert res["ok"] is True, "store 的返回体没有 ok 字，包装必须补——否则成功被读成失败"
    assert res["retention_days"] == 7 and res["max_rows"] == 100_000


def test_purge_reports_a_broken_store_instead_of_ok():
    st = _PurgeStore(ret=None)
    st.purge_rule_triggers = lambda cutoff, max_rows: "boom"
    res = DeviceEventFeed(st, None, _cfg()).purge_trigger_history()
    assert res["ok"] is False and "boom" in res["error"]


# ── 晋升通道：设备候选不再被 engine_feed_gap 拒绝，且带上裁定的 trigger ─────

def _store_rule():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
    return st, path


def _accepted(store, steps, name="书房工作序列"):
    rid, action = store.upsert_candidate_rule(
        name=name, steps=steps, time_window="", infer="", confidence=0.7,
        evidence=["2026-09-11 一条", "2026-09-12 一条", "2026-09-13 一条"])
    assert rid and action
    store.set_candidate_rule_status(rid, CANDIDATE_ACCEPTED)
    return rid


@pytest.fixture
def lifecycle_store():
    st, path = _store_rule()
    yield st
    st.close()
    try:
        os.remove(path)
    except OSError:
        pass


def test_device_candidate_now_builds_a_device_condition(lifecycle_store):
    """Q1=B 之后，设备序列候选的条件是引擎能匹配的 ``kind="device"`` + tag——
    不再是 engine_feed_gap 的死规则，也不是塞进表里永不命中的占位。"""
    cond, blockers = build_condition({
        "steps": [{"tag": "door", "state": "on"}, {"tag": "light", "state": "on"}],
        "room": "书房", "time_window": "19:00-22:00"})
    assert blockers == []
    assert cond == {"kind": DEVICE_EVENT_KIND, "tag": "door", "state": "on",
                    "room": "书房", "time_range": "19:00-22:00"}


@pytest.mark.parametrize("steps,frag", [
    ([{"tag": "quota", "state": "on"}], "device_feed_gap"),        # 词表外的 tag
    ([{"tag": "climate", "state": "heat"}], "device_feed_gap"),     # 非二元态（Q2 第 2 项）
    ([{"kind": "teleport"}], "engine_feed_gap"),                    # 感知词表外
    ([], "empty_steps"),
])
def test_unfeedable_candidates_are_still_rejected_honestly(steps, frag):
    """放宽的是「引擎有没有这个字」，不是「这个候选能不能命中」。"""
    cond, blockers = build_condition({"steps": steps})
    assert cond == {} and blockers and frag in blockers[0]


def test_promoted_device_rule_carries_the_ruled_count_trigger(lifecycle_store):
    """Q2 第 3 项：60 秒/3 次是**裁定值**，必须随规则入库。此前 ``trigger`` 从不入库，
    ``rule["trigger"]`` 恒为 ``{}``，count 分支是不可达代码。"""
    lc = RuleLifecycle(lifecycle_store, ActiveRuleEngine(lifecycle_store),
                       min_evidence=3, dry_run_days=3)
    rid = _accepted(lifecycle_store, [{"tag": "door", "state": "on"},
                                      {"tag": "light", "state": "on"}])
    res = lc.promote(rid, actor="user")
    assert res["ok"], res
    rule = lc.engine.get_rule(res["rule_id"])
    assert rule["mode"] == "dry_run"                      # 红线 2 不因放宽而失效
    assert rule["origin"] == "candidate_promoted"
    assert rule["condition"]["kind"] == DEVICE_EVENT_KIND
    assert rule["trigger"] == DEVICE_TRIGGER == {"type": "count",
                                                 "window_seconds": WINDOW_SECONDS,
                                                 "min_count": MIN_COUNT}
    assert {"type": "count", "window_seconds": 60, "min_count": 3} == rule["trigger"]
    audit = lifecycle_store.list_rule_lifecycle(rule["rule_id"])
    assert audit[0]["detail"]["trigger"] == DEVICE_TRIGGER, "晋升留痕要能还原裁定值"


def test_promoted_perception_rule_keeps_single_trigger(lifecycle_store):
    """感知链路不受本裁定影响：不塞 count 策略，维持"匹配即触发"。"""
    lc = RuleLifecycle(lifecycle_store, ActiveRuleEngine(lifecycle_store),
                       min_evidence=3, dry_run_days=3)
    rid = _accepted(lifecycle_store, [{"kind": "face_unknown", "room": "客厅"}],
                    name="陌生人入户告警")
    rule = lc.engine.get_rule(lc.promote(rid)["rule_id"])
    assert rule["condition"] == {"kind": "face_unknown", "room": "客厅"}
    assert rule["trigger"] == {}


def test_feed_matches_a_promoted_device_rule_end_to_end(lifecycle_store):
    """通道全链：晋升出的设备规则 + feed 扫库 → 60 秒内凑满 3 次命中记一条 dry_run 触发。

    晋升出的条件带 ``state: "on"``，所以只有"开"的那几次计数——关门不算命中，
    这正是 state 原子（此前是 ``# TODO`` 恒真）该有的行为。
    """
    lc = RuleLifecycle(lifecycle_store, ActiveRuleEngine(lifecycle_store),
                       min_evidence=3, dry_run_days=3)
    rid = _accepted(lifecycle_store, [{"tag": "door", "state": "on"}])
    rule_id = lc.promote(rid)["rule_id"]
    engine = lc.engine
    store = lifecycle_store
    store.insert_events([
        _ev(ts="2026-09-20T19:05:00", old="off", new="on"),
        _ev(ts="2026-09-20T19:05:10", old="on", new="off"),
        _ev(ts="2026-09-20T19:05:20", old="off", new="on"),
        _ev(ts="2026-09-20T19:05:30", old="on", new="off"),
        _ev(ts="2026-09-20T19:05:40", old="off", new="on"),
    ])
    res = _feed(store, engine, _cfg(dry_run=True)).run_once(
        "2026-09-20T19:00:00", "2026-09-20T19:59:59")
    assert res["matched"] == 1 and res["logged_only"] == 1 and res["dispatched"] == 0
    rows = store.list_rule_triggers(rule_id)
    assert len(rows) == 1 and int(rows[0]["dry_run"]) == 1
    assert json.loads(rows[0]["event_json"])["tags"] == ["door"]


# ── 单例与 runtime 常驻任务 ─────────────────────────────────────────────────

def test_get_device_feed_is_a_singleton_per_store(store):
    engine = _NullEngine()
    a = get_device_feed(store, engine, _cfg())
    b = get_device_feed(store, engine, _cfg())
    assert a is b, "重复构造等于每轮都从头回捞同一批事件"
    other, _path = _store_rule()
    try:
        assert get_device_feed(other, engine, _cfg()) is not a
    finally:
        other.close()


def _drive_periodic(stub, rounds, monkeypatch):
    """跑常驻任务：把 ``asyncio.sleep`` 换成即时返回，用 rounds 控制退出。

    替身 ``enabled()`` 在跑满轮数后抛 ``CancelledError`` —— 那是任务自己的退出信号
    （外层只接 CancelledError），比"猜一个超时"确定。
    """
    from memory_agent.runtime import AppRuntime

    real_sleep = asyncio.sleep
    slept = []

    async def _fake_sleep(delay, *a, **k):
        slept.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    class _Feed:
        def __init__(self):
            self.left = rounds
            self.rounds = 0
            self.purges = 0

        def interval_seconds(self):
            return 3600

        def enabled(self):
            if self.left <= 0:
                raise asyncio.CancelledError
            self.left -= 1
            self.rounds += 1
            return True

        def run_once(self):
            return {"window": {"start": "s", "end": "e"}, "scanned": 1, "kept": 1,
                    "matched": 1, "dispatched": 0, "logged_only": 1, "truncated": False,
                    "errors": [], "dry_run": True}

        def purge_trigger_history(self):
            self.purges += 1
            return {"ok": True, "deleted_expired": 0, "deleted_over_cap": 0,
                    "remaining": 0, "kept_labeled": 0}

    feed = _Feed()
    stub._device_feed = feed
    asyncio.run(AppRuntime._periodic_device_feed(stub))
    return feed, slept


class _Stub:
    def __init__(self):
        self.config = types.SimpleNamespace(tz_offset_hours=8.0)
        self._device_feed = None


def test_periodic_device_feed_runs_and_purges_once(monkeypatch, capsys):
    stub = _Stub()
    feed, slept = _drive_periodic(stub, 2, monkeypatch)
    assert feed.rounds == 2
    assert feed.purges == 1, "裁剪每日一次，不是每轮一次"
    assert 60 in slept[:1], "首跑延时避开启动期采集/对账争抢"
    out = capsys.readouterr().out
    assert "[DeviceFeed] s→e" in out


def test_periodic_device_feed_skips_when_switch_off(monkeypatch, capsys):
    """开关每轮重读：SP 打开 ``device_feed_enabled`` 不必等一次重启。"""
    from memory_agent.runtime import AppRuntime

    slept = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay, *a, **k):
        slept.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    class _OffFeed:
        def __init__(self):
            self.runs = 0
            self.left = 3

        def interval_seconds(self):
            return 3600

        def enabled(self):
            if self.left <= 0:
                raise asyncio.CancelledError
            self.left -= 1
            return False

        def run_once(self):                     # 被调即失败：关着就不该扫库
            self.runs += 1
            raise AssertionError("开关关着却扫了库")

    stub = _Stub()
    feed = _OffFeed()
    stub._device_feed = feed
    asyncio.run(AppRuntime._periodic_device_feed(stub))
    assert feed.runs == 0
    assert len(slept) >= 4                      # 首跑延时 + 每轮间隔
    assert capsys.readouterr().out == ""


def test_periodic_device_feed_survives_a_scan_crash(monkeypatch, capsys):
    """旁路通道炸了不能打死常驻任务——下一轮还得照常扫。"""
    from memory_agent.runtime import AppRuntime

    real_sleep = asyncio.sleep

    async def _fake_sleep(delay, *a, **k):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    class _CrashFeed:
        def __init__(self):
            self.left = 2
            self.rounds = 0

        def interval_seconds(self):
            return 3600

        def enabled(self):
            if self.left <= 0:
                raise asyncio.CancelledError
            self.left -= 1
            self.rounds += 1
            return True

        def run_once(self):
            raise RuntimeError("扫库炸了")

    stub = _Stub()
    stub._device_feed = _CrashFeed()
    asyncio.run(AppRuntime._periodic_device_feed(stub))
    assert "[DeviceFeed] 扫描异常: 扫库炸了" in capsys.readouterr().out


def test_startup_registers_the_device_feed_task():
    import inspect

    from memory_agent.runtime import AppRuntime
    src = inspect.getsource(AppRuntime.startup)
    assert 'name="runtime.device_feed"' in src
    assert "_periodic_device_feed()" in src
