"""P1 红→绿锁：`NLQueryEngine` 的六条路由必须按**新引擎**的签名与键名取数。

Phase 4 把 `self.nl` 的 `service` 换成 `BehaviorService`（insights/api.py:168）后，
`nlquery.py` 仍然按 legacy 的 `usage/anomaly_report` 形状调用，四条后果各不相同，
但没有一条会响：

| 路由 | 现读调用 | 新引擎签名 | 后果 |
|---|---|---|---|
| device_usage | `usage(tr, room=, query=, category=, group_by=)` | `usage(tr, room, category, entity_id)` | TypeError |
| behavior | 同上（`group_by="room"`） | 同上 | TypeError |
| anomaly | `anomaly_report(tr, room=, query=)`，读 `data["total"]`、`a["title"]` | `anomaly_report(tr, room, category)`，键是 `summary.count` / `a.message` | TypeError |
| rhythm | 读 `sleep`/`wake`/`samples` | 引擎只给 `hourly`/`weekday`/`peaks`/`days` | 恒答「未知…0 天样本」 |
| persona | 读 `data["persona"]["summary"]` | 引擎给 `traits`/`top_rooms`/`rhythm` | 恒答「暂无画像。」 |

前三条被门面的 `_degrade` 统一降级成 `answer="查询失败"`（api.py:662），
HTTP/MCP 都返回 200 —— 从切引擎那天起这三条路由**从未答对过**，而全量测试仍然绿。

口径约束：新引擎的 `usage` 只有**事件数**维度（`entity_stats`），没有任何时长概念，
所以话术改成事件数口径并显式说明，**不许**沿用 legacy 的「活跃 N 分钟」把读数编出来。
`rhythm` 的 `sleep`/`wake` 由夜窗最长静默段推出（小时级），口径随读数一起回显。
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.insights.models import house_now, house_ts  # noqa: E402
from memory_agent.insights.parser.timeframe import resolve_range  # noqa: E402
from memory_agent.store import Store  # noqa: E402

DAY_1 = "2026-09-21"
DAY_2 = "2026-09-22"
DAY_3 = "2026-09-23"
DOOR = "binary_sensor.study_door"
LAMP = "light.living_room_main"
# 作息样态：22 点是夜窗里最后一次活动，23:00–06:00 全静默，07:00 起有活动。
NIGHTLY_HOURS = (7, 8, 13, 19, 20, 22)


def _tr():
    return resolve_range(start=f"{DAY_1}T00:00:00", end=f"{DAY_3}T23:59:59")


def _ev(day, hour, entity=DOOR, room="书房", friendly="书房门磁", minute=None):
    ts = "%sT%02d:%02d:00" % (day, hour, (minute if minute is not None else (hour % 60)))
    return {"entity_id": entity, "ts": ts, "room": room,
            "domain": entity.split(".")[0], "new_state": "on", "old_state": "off",
            "attrs": {"friendly_name": friendly}}


def _nightly(days=(DAY_1, DAY_2, DAY_3)):
    """每天 6 个时刻的门磁事件；分钟按天错开，避开 `make_event_id` 同秒去重。"""
    rows = []
    for i, day in enumerate(days):
        for hour in NIGHTLY_HOURS:
            rows.append(_ev(day, hour, minute=(hour + i) % 60))
    return rows


def _svc(store, rows):
    """先写数据再建门面：`EntityResolver` 的实体表在构造时读一次。"""
    store.insert_events(rows)
    return InsightService(store, Config())


def _execute(nl, question, days=7):
    """走真实规划，再把窗口钉死——断言不该依赖"今天"。"""
    plan = nl.plan(question, days=days)
    plan.time_range = _tr()
    return nl._execute(plan)


@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="ma_nlq_")
    s = Store(os.path.join(tmp, "nlq.db"), tz_offset_hours=0.0)
    s.init_schema()
    yield s


# ── 1) 三条被 `_degrade` 吞掉的路由：TypeError 必须消失、读数必须是真键 ──

def test_usage_route_reads_the_new_engines_own_keys(store):
    """device_usage：实体数 / 事件数 / 活跃天数都来自 `usage()` 的真实键。"""
    svc = _svc(store, _nightly())
    answer, data = _execute(svc.nl, "书房门用了多久")
    assert data.get("ok"), f"usage 降级了：{data.get('error')}"
    top = data["items"][0]
    assert top["count"] == 18, f"3 天 ×6 条应为 18，实得 {top['count']}"
    assert top["active_days"] == 3
    assert str(top["count"]) in answer and "书房门磁" in answer, answer
    # 新引擎没有时长口径，话术不许凭空造出"分钟"
    assert "分钟" not in answer, f"话术里出现了引擎算不出的时长：{answer}"


def test_behavior_route_groups_by_room(store):
    """behavior：`group_by="room"` 在新引擎里没有这个参数，改读 `by_room`。"""
    rows = _nightly() + [_ev(DAY_1, 20, LAMP, "客厅", "客厅主灯", minute=31),
                         _ev(DAY_2, 20, LAMP, "客厅", "客厅主灯", minute=32)]
    svc = _svc(store, rows)
    answer, data = _execute(svc.nl, "书房待了多久")
    assert data.get("ok"), f"usage 降级了：{data.get('error')}"
    by_room = {r["room"]: r["count"] for r in data["by_room"]}
    assert by_room.get("书房") == 18, by_room
    assert "书房" in answer and "18" in answer, answer
    assert "分钟" not in answer, f"话术里出现了引擎算不出的时长：{answer}"


def test_anomaly_route_reads_summary_count_and_messages(store):
    """anomaly：`total` 与 `a["title"]` 在新引擎里都不存在（键是 `summary.count`/`message`）。"""
    # 只有首尾两天有事件 ⇒ 中间一天数据缺口 ⇒ 异常数 ≥1，打印的不是 0
    svc = _svc(store, _nightly(days=(DAY_1, DAY_3)))
    answer, data = _execute(svc.nl, "书房有什么异常")
    assert data.get("ok"), f"anomaly_report 降级了：{data.get('error')}"
    count = int(data["summary"]["count"])
    assert count >= 1, f"应至少报出一条（09-22 全天空缺），实得 {count}"
    assert str(count) in answer, f"话术里的条数与 summary.count 不符：{answer}"
    first = data["anomalies"][0]["message"]
    assert first[:8] in answer, f"话术没带上真实异常描述：{answer} / {first}"


# ── 2) 两条"永不判红、只会说空话"的路由 ────────────────────────────────

def test_rhythm_route_answers_with_sleep_and_wake(store):
    """rhythm：`sleep`/`wake`/`samples` 必须由引擎给出来，不能再是「未知…0 天样本」。"""
    svc = _svc(store, _nightly())
    answer, data = _execute(svc.nl, "我一般几点睡觉")
    assert data.get("ok"), f"rhythm 降级了：{data.get('error')}"
    assert data["sleep"] == "22:00", f"夜窗里最后活动是 22 点，实得 {data['sleep']}"
    assert data["wake"] == "07:00", f"静默段后首个活动是 7 点，实得 {data['wake']}"
    assert data["samples"] == 3, f"三天样本应报 3，实得 {data['samples']}"
    assert "22:00" in answer and "07:00" in answer and "3 天样本" in answer, answer


def test_persona_route_returns_a_real_summary(store):
    """persona：引擎的画像没有 `persona.summary`，要么给真总结、要么照实说没有。"""
    svc = _svc(store, _nightly())
    svc.core._now = lambda: house_ts(datetime(2026, 9, 25, 0, 0, 0))
    answer, data = _execute(svc.nl, "总结一下我的习惯")
    assert data.get("ok"), f"user_persona 降级了：{data.get('error')}"
    assert answer != "暂无画像。", "画像路由恒回空话术（读的是不存在的 persona.summary）"
    assert "画像" in answer or "作息" in answer, answer
    assert data["traits"], "traits 必须非空才能支撑这句总结"


# ── 3) 对照：本来就接对的那条（修复前后都应绿）─────────────────────────

def test_activity_route_was_already_bound_correctly(store):
    """activity 用的是 `total_activities` + `name`，与新引擎一致——不许被本次改动碰坏。"""
    svc = _svc(store, _nightly())
    answer, data = _execute(svc.nl, "最近有哪些活动")
    assert data.get("ok"), f"infer_activities 降级了：{data.get('error')}"
    assert str(data["total_activities"]) in answer, answer


# ── 4) 对外契约：门面向 `ask_memory` 不许再交"查询失败"信封 ────────────

def test_rhythm_route_stays_silent_without_a_quiet_run(store):
    """对偶档：全天每个小时都有事件 ⇒ 夜窗里没有静默段 ⇒ 不许编出入睡/起床时刻。

    只留上一条（"该有时钟"）的话，一个恒返回 `22:00/07:00` 的实现同样能全绿；
    这一条钉住"证据不足就照实说未知"。
    """
    rows = []
    for day in (DAY_1, DAY_2, DAY_3):
        rows.extend(_ev(day, h, minute=h) for h in range(24))
    svc = _svc(store, rows)
    answer, data = _execute(svc.nl, "我一般几点睡觉")
    assert data.get("ok"), f"rhythm 降级了：{data.get('error')}"
    assert data["sleep"] == "", f"没有静默段却有入睡时刻：{data['sleep']!r}"
    assert data["wake"] == "", f"没有静默段却有起床时刻：{data['wake']!r}"
    assert data["samples"] == 0, f"零天可用样本被报成 {data['samples']}"
    assert "未知" in answer, f"应照实说未知：{answer}"
    assert int(data["sleep_wake"]["days_in_window"]) == 3, "窗口内确实有 3 天数据"

def test_ask_memory_outward_answers_are_not_degraded(store):
    """五条路由各问一次：HTTP/MCP 拿到的必须是真答案，不是 `_degrade` 的信封。

    这份数据用"今天"作锚点（对外契约测的就是带时区的真实链路），窗口放到 20 天，
    跨零点、跨月都不会让断言失效。
    """
    now = house_now()
    rows = []
    for i in range(1, 8):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        rows.extend(_ev(day, h, minute=(h + i) % 60) for h in NIGHTLY_HOURS)
        rows.extend((_ev(day, 20, LAMP, "客厅", "客厅主灯", minute=41),
                     _ev(day, 8, LAMP, "客厅", "客厅主灯", minute=42)))
    svc = _svc(store, rows)
    questions = ("书房门用了多久", "书房待了多久", "最近有什么异常",
                 "我一般几点睡觉", "总结一下我的习惯", "最近有哪些活动")
    bad = []
    for q in questions:
        out = svc.ask_memory(q, days=20)
        if out.get("answer") in ("", "查询失败") or "error" in out:
            bad.append("%s -> %r / %r" % (q, out.get("answer"), out.get("error")))
    assert not bad, "对外仍是被降级的一次问答：\n  " + "\n  ".join(bad)


# ── 6) DCD 20261006 §四.2 Q1 甲：点了名却没解析出来 ⇒ NL 面也 fail-closed ──────

def test_named_but_unresolved_question_does_not_answer_with_the_whole_house(store):
    """用户点名一台在册但没有的设备时，话术必须答"没找到"，不许把空 entity_ids 当全屋。

    改前的现场：`_answer_usage`/`_answer_behavior`/anomaly 三条都传
    `entity_id=",".join(plan.entity_ids)`，解析失败 ⇒ 空串 ⇒ 引擎按"没限制"取全屋，
    于是「鱼缸水泵用了多久」会答成别的数据而不说找不到。判据用现成的
    `plan.params["has_query"]`，不新增出境键；失败态回显复用 `unresolved`（同裁定 Q2）。
    """
    svc = _svc(store, _nightly())
    plan = svc.nl.plan("鱼缸水泵用了多久")
    assert plan.params["has_query"] is True, plan.params
    assert plan.entity_ids == [], plan.entity_ids
    answer, data = _execute(svc.nl, "鱼缸水泵用了多久")
    assert "没找到" in answer, answer
    assert data.get("unresolved") == plan.query, data
    assert not data.get("items"), f"收紧失败，仍交出了全屋条目：{data.get('items')}"


def test_free_question_without_a_named_device_still_answers(store):
    """对偶档：没点名的自由问句仍按房间/类别答——收紧门不许把「有什么异常」判成找不到设备。

    这一条是门自己的反例（"什么都不改"档）：意图触发词与虚词必须留在 `_QUERY_NOISE` 里，
    否则 `has_query` 会把提问类型词当点名材料，本条就会红。
    """
    svc = _svc(store, _nightly(days=(DAY_1, DAY_3)))
    for question in ("最近有什么异常", "书房有什么异常", "书房待了多久"):
        plan = svc.nl.plan(question)
        assert plan.params["has_query"] is False, (question, plan.query, plan.params)
    answer, data = _execute(svc.nl, "书房有什么异常")
    assert "没找到" not in answer, answer
    assert not data.get("unresolved"), data
    assert data.get("ok"), f"anomaly 降级了：{data.get('error')}"
