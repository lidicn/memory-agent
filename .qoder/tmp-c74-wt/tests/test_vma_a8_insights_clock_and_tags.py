"""A8 定向扫描核实落下的两条 insights 缺陷锁（2026-10-05，#50）。

A8 §三把 `service.py:68` 判成"未闭合会话兜底取了最早事件"——那条不成立（`service.py:50`
先 `sorted(..., key=e.ts)` 升序，`evs[-1]` 就是最新）。但同一批扫描里**裸时钟**这一格是真的：
`EventRecord.dt` 走 `datetime.fromtimestamp(ts)`（机器本地时区），而同一条记录的 `day` / `hour`
走 `house_dt()`（家庭墙钟）。容器 TZ=UTC、家庭 +8 时，两者差 8 小时——`scripts/probe_activity_coverage_gap.py:39`
打印的事件跨度就是按 `dt` 取的，而它旁边的按天分桶按家庭墙钟算。

锁的口径：**不依赖跑在哪台机器上**。每条都先把家庭时区改成"与本机本地时区不同的那个偏移"，
所以同一条断言在本机（+8）、容器（UTC）都必须为真；改前必红。
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from memory_agent.insights import models as m
from memory_agent.insights.models import EventRecord
from memory_agent.insights.repository import StoreRepository

# 一个固定的历史时刻，避免用运行时刻（跨天会让断言随钟摆）。
TS = 1759823400.0  # 2025-10-07 07:50:00 UTC（+8 侧为同日 15:50 ⇒ 本例只有 hour 档能分家，day 档两边同日）

MACHINE_OFFSET = datetime.now().astimezone().utcoffset()


def _offset_that_differs_from_machine() -> timezone:
    """挑一个**保证**与本机本地偏移不同的固定偏移，锁才不瞎。"""
    for hours in (13, 5, -7, 0):
        if timedelta(hours=hours) != MACHINE_OFFSET:
            return timezone(timedelta(hours=hours))
    raise AssertionError("找不到与本机不同的偏移，这条锁在本机是瞎的")


@pytest.fixture
def foreign_house_tz(monkeypatch):
    alt = _offset_that_differs_from_machine()
    monkeypatch.setattr(m, "house_tz", lambda: alt)
    return alt


def test_the_two_clocks_are_actually_distinguishable_in_this_lock():
    """门自证：本锁制造的两条时钟必须真的能分开，否则后面的断言恒真、等于没锁。"""
    alt = _offset_that_differs_from_machine()
    assert alt.utcoffset(None) != MACHINE_OFFSET
    assert datetime.fromtimestamp(TS, tz=alt).replace(tzinfo=None) != datetime.fromtimestamp(TS)


def test_event_record_dt_follows_house_clock_not_machine_local(foreign_house_tz):
    ev = EventRecord(ts=TS, entity_id="binary_sensor.door")
    assert ev.dt == m.house_dt(TS)
    assert ev.dt != datetime.fromtimestamp(TS)   # 机器本地那条口径不再被 dt 采用


def test_event_record_dt_day_hour_share_one_clock(foreign_house_tz):
    """同一个事件的时间读数不许自相矛盾：dt 的日期必须等于 day、小时必须等于 hour。"""
    ev = EventRecord(ts=TS, entity_id="binary_sensor.door")
    assert ev.dt.strftime("%Y-%m-%d") == ev.day
    assert ev.dt.hour == ev.hour


def test_event_record_dt_still_equals_local_when_house_is_local():
    """对偶档（该不响）：家庭时区恰好等于本机偏移时，dt 与机器本地朴素时间一致——
    修成 house_dt 不会把"本来就对"的那一侧改坏。"""
    monkeypatch = pytest.MonkeyPatch()
    try:
        local = timezone(MACHINE_OFFSET)
        monkeypatch.setattr(m, "house_tz", lambda: local)
        ev = EventRecord(ts=TS, entity_id="binary_sensor.door")
        assert ev.dt.replace(microsecond=0) == datetime.fromtimestamp(TS).replace(microsecond=0)
        assert ev.day == datetime.fromtimestamp(TS).strftime("%Y-%m-%d")
    finally:
        monkeypatch.undo()


class _FakeStore:
    def __init__(self, rows):
        self._rows = rows
        self.sql = []

    def db_query(self, sql, params=()):
        self.sql.append(sql)
        return list(self._rows)


def _rule_row(tags_json):
    return {"rule_id": "r-7", "name": "看书", "room": "客厅", "tags_json": tags_json,
            "start_hour": 19, "end_hour": 22, "min_events": 3, "confidence": 0.8,
            "note": "", "enabled": 1}


def test_malformed_tags_json_is_logged_with_the_rule_id(caplog):
    """坏 tags_json 会让规则的标签条件**静默消失**（规则照常返回，只是不再按标签过滤）。
    行为仍按无标签运行（不把一次解析失败升级成整条查询外抛），但必须留痕到日志。"""
    repo = StoreRepository(_FakeStore([_rule_row("{not json")]), None)
    with caplog.at_level(logging.WARNING, logger="insights.repository"):
        rows = repo.activity_rules()
    assert rows[0]["tags"] == []                      # 行为不变
    assert "r-7" in caplog.text                       # 但哪条规则被降级要说得出
    assert "tags_json" in caplog.text


def test_valid_tags_json_does_not_warn(caplog):
    """对偶档：合法 JSON 不许产生告警，否则上一条锁靠"总是报警"也能绿。"""
    repo = StoreRepository(_FakeStore([_rule_row('["reading", "quiet"]')]), None)
    with caplog.at_level(logging.WARNING, logger="insights.repository"):
        rows = repo.activity_rules()
    assert rows[0]["tags"] == ["reading", "quiet"]
    assert caplog.records == []


def _entity_row(eid):
    return {"entity_id": eid, "room": "客厅", "domain": eid.split(".")[0],
            "attrs_json": '{"friendly_name": "读数"}', "total": 3}


def test_list_entities_logs_the_row_it_dropped(monkeypatch, caplog):
    """`list_entities` 里那行 `except Exception: continue` 会让一个实体**凭空缺掉**：
    返回形状完全合法、条数只少 1，消费方无从知道是哪一行、为什么被丢。
    口径与 tags_json 那格一致——一行坏不许掀掉整张目录，但降级要留痕到具体 entity_id。"""
    class _Explodes:
        def __init__(self, **kw):
            if kw.get("entity_id") == "light.goes_missing":
                raise TypeError("模拟单行构造失败")
            self.__dict__.update(kw)

    monkeypatch.setattr(m, "EntityInfo", _Explodes)
    repo = StoreRepository(_FakeStore([_entity_row("light.goes_missing"),
                                       _entity_row("sensor.temp")]), None)
    with caplog.at_level(logging.WARNING, logger="insights.repository"):
        rows = repo.list_entities()
    assert [r.entity_id for r in rows] == ["sensor.temp"]   # 行为不变：坏行不进目录
    assert "light.goes_missing" in caplog.text              # 但被丢的是哪一行要说得出


def test_list_entities_healthy_rows_do_not_warn(caplog):
    """对偶档：全部行都合法时不许产生告警，否则上一条锁靠"总是报警"也能绿。"""
    repo = StoreRepository(_FakeStore([_entity_row("sensor.temp"),
                                       _entity_row("binary_sensor.door")]), None)
    with caplog.at_level(logging.WARNING, logger="insights.repository"):
        rows = repo.list_entities()
    assert [r.entity_id for r in rows] == ["sensor.temp", "binary_sensor.door"]
    assert caplog.records == []
