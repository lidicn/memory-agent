"""行为推断的「窗口可达性」回归锁。

起因为一次生产实测：``behavior_states`` 自 09-03 起 0 行，而周期任务日志一切正常。
根因不在匹配算法，在**扫描窗口比规则自己声明的序列跨度还短**：
``activity_window_minutes`` 默认 15 分钟，而 ``就寝`` 规则末步 ``within_min=25``——
序列的每一步都相对首步计时，于是这条规则在周期任务里结构性不可达，
且因为「0 产出」不打印任何日志，空转无人知晓。

既有 ``tests/test_activity_inference.py`` 全部走 ``run(start=..., end=...)`` 显式窗口，
且夹具把 ``activity_window_minutes`` 设成 120 —— 两条路都绕开了默认窗口，
所以这个缺陷在 500+ 条测试下存活。本文件专门钉默认窗口那条路。
"""

import os
import sys
import tempfile
from datetime import timedelta
from types import SimpleNamespace

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.activity_inference import (  # noqa: E402
    DEFAULT_SEQUENCE_RULES,
    ActivityInferenceService,
)
from memory_agent.insights.utils import tags_of  # noqa: E402
from memory_agent.store import Store, now_local  # noqa: E402

# 生产实测到的真实默认值（不是夹具里那个宽松的 120）
PROD_WINDOW_MINUTES = 15
PROD_INTERVAL_SECONDS = 300


class _FakeInsights:
    def _tags_of(self, eid, name):
        return tags_of(eid, name)


def make_runtime(store, window_minutes=PROD_WINDOW_MINUTES):
    cfg = SimpleNamespace(
        tz_offset_hours=0.0,
        rooms={},
        activity_window_minutes=window_minutes,
        pir_debounce_sec=30,
        activity_conf_threshold=0.6,
    )
    return SimpleNamespace(config=cfg, store=store, insights=_FakeInsights())


@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="ma_win_")
    s = Store(os.path.join(tmp, "t.db"), tz_offset_hours=0.0)
    s.init_schema()
    return s


def _ev(store, ts, eid, room, state):
    store.insert_events([{
        "entity_id": eid, "ts": ts, "room": room,
        "domain": eid.split(".")[0], "new_state": state, "old_state": "",
    }])


def _rule_without_night_gate():
    """就寝 规则去掉 21:00-02:00 时段门，其余步长原样保留。

    时段门只影响「什么时候允许命中」，与窗口可达性无关；保留它会让本用例
    依赖执行时刻的墙钟。跨度（末步 within_min=25）是这条规则的真实形状。
    """
    sleep_rule = next(r for r in DEFAULT_SEQUENCE_RULES if r["infer"] == "user_asleep")
    assert max(s["within_min"] for s in sleep_rule["steps"]) == 25, \
        "就寝 规则步长已变，请同步本用例的跨度假设"
    return {**sleep_rule, "time_window": ""}


# ── L1：结构性判据——有效窗口必须容得下最长的已发布规则 ──────────────────────
def test_default_shipped_rules_need_more_than_the_default_window():
    """先自证前提：默认规则里确实存在比 15 分钟更长的序列。

    这条不成立时，后面两条锁就失去意义，所以单独钉住。
    """
    horizon = max(s["within_min"] for r in DEFAULT_SEQUENCE_RULES for s in r["steps"])
    assert horizon > PROD_WINDOW_MINUTES, (
        f"最长规则跨度 {horizon} 分钟已不大于生产窗口 {PROD_WINDOW_MINUTES} 分钟")


def test_run_widens_window_to_cover_longest_rule(store):
    svc = ActivityInferenceService(make_runtime(store))
    res = svc.run()
    assert res["ok"] is True
    assert res["rule_horizon_minutes"] == 25
    assert res["window_minutes_used"] >= res["rule_horizon_minutes"], (
        "扫描窗口比最长规则跨度还短 → 该规则在周期任务里永远不可能命中")


# ── L2：端到端真响——24 分钟跨度的序列必须能落库 ────────────────────────────
def test_25_minute_sleep_sequence_persists_a_state(store):
    svc = ActivityInferenceService(make_runtime(store))
    svc.rules = [_rule_without_night_gate()]

    now = now_local(0.0).replace(microsecond=0)
    t = lambda m: (now + timedelta(minutes=m)).replace(  # noqa: E731
        second=0, microsecond=0).isoformat(sep="T")
    # 相对首步：0 → +3（门磁闭合，after_prev_min=2 已满足）→ +12 → +24
    _ev(store, t(-24), "binary_sensor.bedroom_door", "主卧室", "off")
    _ev(store, t(-21), "light.bedroom_nightlamp", "主卧室", "on")
    _ev(store, t(-15), "climate.bedroom_ac", "主卧室", "heat")
    _ev(store, t(-1), "light.bedroom_nightlamp", "主卧室", "off")

    res = svc.run()
    assert res["persisted"] == 1, (
        f"24 分钟跨度的就寝序列未落库：{res}（窗口只取了 {res.get('window_minutes_used')} 分钟，"
        f"首步被切在窗口之外）")
    rows = store.list_behavior_states()
    assert [r["activity"] for r in rows] == ["user_asleep"]


# ── L3：空转必须留状态字，而不是「没日志=没事」 ─────────────────────────────
def test_status_reports_last_run_counters(store):
    svc = ActivityInferenceService(make_runtime(store))
    # 只有一串遥测噪声，没有任何规则标签
    now = now_local(0.0).replace(microsecond=0, second=0)
    for i in range(6):
        _ev(store, (now - timedelta(minutes=i)).isoformat(sep="T"),
            "sensor.livingroom_power", "客厅", "123.4")
    res = svc.run()
    assert res["persisted"] == 0 and res["candidates"] == 0

    st = svc.status()
    assert st["ran"] is True
    assert st["scanned"] > 0, "扫到了事件却没记录 scanned 计数"
    assert st["persisted"] == 0
    # 关键：0 产出要以字段形式可见，而不是靠"日志里没有报错"
    assert st["idle"] is True
    assert st["tagged"] == 0, "无标签事件占比应可从状态里读出来"


def test_truncation_is_visible_in_the_result(store):
    """query_events 是 limit+升序：超限时拿到的是**最旧**一批，最近的事件反而被丢掉。

    这条锁不要求修语义，只要求把「本次被截断」如实说出去。
    """
    svc = ActivityInferenceService(make_runtime(store))
    now = now_local(0.0).replace(microsecond=0, second=0)
    for i in range(30):
        _ev(store, (now - timedelta(minutes=i)).isoformat(sep="T"),
            "sensor.hall_temperature", "客厅", str(20 + i / 10))
    res = svc.run(window_minutes=1, limit=5)
    assert res["truncated"] is True, "事件数超过 limit 时必须报告截断"


# ── L4：重叠窗口不得把同一段序列写成多条状态 ────────────────────────────────
def test_overlapping_runs_converge_to_one_row(store):
    """周期任务 5 分钟一跑、窗口 25 分钟，同一段序列会被连续看见数次。

    主键随机时每次看见都是一行（一条 ``就寝`` 落 3-5 份）；这里要求收敛成一条。
    """
    svc = ActivityInferenceService(make_runtime(store))
    svc.rules = [_rule_without_night_gate()]
    now = now_local(0.0).replace(microsecond=0, second=0)
    t = lambda m: (now + timedelta(minutes=m)).isoformat(sep="T")  # noqa: E731
    _ev(store, t(-24), "binary_sensor.bedroom_door", "主卧室", "off")
    _ev(store, t(-21), "light.bedroom_nightlamp", "主卧室", "on")
    _ev(store, t(-15), "climate.bedroom_ac", "主卧室", "heat")
    _ev(store, t(-1), "light.bedroom_nightlamp", "主卧室", "off")

    for _ in range(3):          # 模拟连续三次重叠扫描
        assert svc.run()["persisted"] == 1
    rows = store.list_behavior_states()
    assert len(rows) == 1, f"同一段序列被写成 {len(rows)} 条：{rows}"
    assert rows[0]["activity"] == "user_asleep"


# ── L5：接线不许退回「只在非零时说话」 ──────────────────────────────────────
def test_health_carries_the_activity_block():
    import inspect

    from memory_agent import runtime

    assert '"activity": self.activity.status(),' in inspect.getsource(
        runtime.AppRuntime.health)


def test_periodic_activity_task_warns_on_idle_runs():
    import inspect

    from memory_agent import runtime

    src = inspect.getsource(runtime.AppRuntime._periodic_activity_inference)
    assert "空转告警" in src, "0 产出必须自己开口，不许退回「没日志=没事」"
    assert "last_idle_day" in src, "空转告警要按天节流，不能每 5 分钟刷一条"
