"""P1.1 过程挖掘单测：纯 Python 过程模型 / 稀有边异常 / 异常落库 / 候选规则产出。

设计要点：过程挖掘核心为**纯 Python**（无 pm4py / 无 AGPL 约束），
故这些用例在任何环境都必须能跑（pm4py 仅作可选增强）。
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import algo_kernel as ak  # noqa: E402
from memory_agent.activity_inference import ActivityInferenceService  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.store import Store  # noqa: E402


# ── 纯 Python 过程模型 ──────────────────────────────────────────────────────

def _cases(specs):
    return [{"case": f"{r}|{d}", "room": r, "day": d, "activities": a}
            for r, d, a in specs]


def _events(room, day, acts, hour=9):
    """把活动名列表（``tag_on``）转成 MA 事件（实体域即标签来源）。"""
    ent = {"door": "binary_sensor.study_door_contact",
           "light": "light.study_ceiling",
           "computer": "switch.study_pc",
           "climate": "climate.study_ac",
           "media": "media_player.living_tv"}
    base = datetime.fromisoformat(f"{day}T{hour:02d}:00:00")
    out = []
    for i, a in enumerate(acts):
        dom = a[:-3] if a.endswith("_on") else a
        out.append({"ts": (base + timedelta(minutes=i)).isoformat(sep="T"),
                    "entity_id": ent.get(dom, f"light.{dom}_x"),
                    "new_state": "on", "room": room})
    return out


def test_pure_engine_detects_rare_edges():
    specs = [("书房", f"2026-09-{d:02d}", ["door_on", "light_on", "computer_on"])
             for d in range(1, 11)]
    specs.append(("书房", "2026-09-20",
                  ["door_on", "light_on", "climate_on", "computer_on"]))
    res = ak.mine_process_model_pure(_cases(specs))

    assert res["ok"] is True and res["engine"] == "pure"
    assert res["cases"] == 11
    assert res["anomaly_count"] == 1
    a = res["anomalies"][0]
    assert a["case"] == "书房|2026-09-20"
    assert a["day"] == "2026-09-20" and a["room"] == "书房"
    assert "rare_edges" in a["reasons"] and "rare_activities" in a["reasons"]
    assert "light_on->climate_on" in a["rare_edges"]
    assert res["fitness_rate"] == round(10 / 11, 3)
    assert res["top_variants"][0]["count"] == 10


def test_pure_engine_uniform_log_has_no_anomaly():
    specs = [("客厅", f"2026-09-{d:02d}", ["door_on", "light_on", "media_on"])
             for d in range(1, 6)]
    res = ak.mine_process_model_pure(_cases(specs))
    assert res["anomaly_count"] == 0
    assert res["fitness_rate"] == 1.0


def test_pure_engine_empty():
    res = ak.mine_process_model_pure([])
    assert res["ok"] is True and res["cases"] == 0 and res["anomaly_count"] == 0


def test_variants_grouped_by_room():
    specs = [
        ("书房", "2026-09-01", ["door_on", "light_on", "computer_on"]),
        ("书房", "2026-09-02", ["door_on", "light_on", "computer_on"]),
        ("客厅", "2026-09-01", ["door_on", "light_on", "media_on"]),
    ]
    res = ak.mine_process_model_pure(_cases(specs))
    assert res["variants_by_room"]["书房"][0]["count"] == 2
    assert res["variants_by_room"]["客厅"][0]["activities"] == \
        ["door_on", "light_on", "media_on"]


# ── 事件流 → case（建日志） ────────────────────────────────────────────────

def test_build_process_cases_compresses_and_filters_short():
    tag_of = lambda eid, name: {eid.split(".")[0]}  # noqa: E731
    base = datetime(2026, 9, 15, 10, 0, 0)
    evs = []
    for i, dom in enumerate(["door", "door", "light", "computer"]):
        evs.append({"ts": (base + timedelta(minutes=i)).isoformat(sep="T"),
                    "entity_id": f"{dom}.x", "new_state": "on", "room": "书房"})
    # 仅 1 步的 case 应被丢弃（< min_case_events）
    evs.append({"ts": (base + timedelta(minutes=30)).isoformat(sep="T"),
                "entity_id": "light.y", "new_state": "on", "room": "厨房"})

    cases = ak.build_process_cases(evs, tag_of)
    assert len(cases) == 1
    assert cases[0]["activities"] == ["door_on", "light_on", "computer_on"]  # 连续重复已压缩
    assert cases[0]["room"] == "书房" and cases[0]["day"] == "2026-09-15"
    assert cases[0]["case"] == "书房|2026-09-15"


def test_mine_process_model_pure_path_without_pm4py():
    """with_pm4py=False：不依赖 pm4py 也能给出异常（核心判定不依赖第三方库）。"""
    tag_of = lambda eid, name: set(  # noqa: E731
        {"door"} if "door" in eid else
        {"light"} if "light" in eid else
        {"computer"} if "pc" in eid else
        {"climate"} if "climate" in eid else set()
    )
    evs = []
    for d in range(1, 11):
        evs += _events("书房", f"2026-09-{d:02d}", ["door_on", "light_on", "computer_on"])
    evs += _events("书房", "2026-09-20",
                   ["door_on", "light_on", "climate_on", "computer_on"])

    res = ak.mine_process_model(evs, tag_of, with_pm4py=False)
    assert res["ok"] is True
    assert res["engine"] == "pure"
    assert res["anomaly_count"] == 1
    assert res["anomalies"][0]["day"] == "2026-09-20"


# ── 行为异常落库（store） ──────────────────────────────────────────────────

@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="ma_proc_")
    s = Store(os.path.join(tmp, "test.db"), tz_offset_hours=0.0)
    s.init_schema()
    yield s


def _ev(store, ts, eid, room, state="on"):
    store.insert_events([{
        "entity_id": eid, "ts": ts, "room": room,
        "domain": eid.split(".")[0], "new_state": state, "old_state": "",
    }])


def test_anomaly_upsert_idempotent_and_keeps_human_status(store):
    aid, action = store.upsert_behavior_anomaly(
        "书房|2026-09-20", day="2026-09-20", room="书房",
        reasons=["rare_edges"], activities=["door_on", "light_on"],
        rare_edges=["light_on->climate_on"], severity=0.5)
    assert action == "added"
    assert store.set_behavior_anomaly_status(aid, "confirmed") is True

    # 重跑挖掘：同一 case 只更新判据，不新增行、不洗掉人工复核结果
    aid2, action2 = store.upsert_behavior_anomaly(
        "书房|2026-09-20", day="2026-09-20", room="书房",
        reasons=["rare_edges", "rare_activities"], severity=0.9)
    assert aid2 == aid and action2 == "updated"

    rows = store.list_behavior_anomalies()
    assert len(rows) == 1
    assert rows[0]["status"] == "confirmed"
    assert rows[0]["severity"] == 0.9
    assert rows[0]["reasons"] == ["rare_edges", "rare_activities"]


def test_anomaly_list_filters_and_purge_keeps_confirmed(store):
    store.upsert_behavior_anomaly("A|2026-09-01", day="2026-09-01", room="A", severity=0.2)
    store.upsert_behavior_anomaly("B|2026-09-10", day="2026-09-10", room="B", severity=0.8)

    assert [r["room"] for r in store.list_behavior_anomalies()] == ["B", "A"]  # 严重度降序
    assert len(store.list_behavior_anomalies(day_from="2026-09-05")) == 1
    assert len(store.list_behavior_anomalies(room="A")) == 1

    b_id = store.list_behavior_anomalies(room="B")[0]["anomaly_id"]
    store.set_behavior_anomaly_status(b_id, "confirmed")
    # 清理旧异常但保留 confirmed（便于复盘）
    assert store.purge_behavior_anomalies("2026-09-30") == 1
    left = store.list_behavior_anomalies()
    assert len(left) == 1 and left[0]["room"] == "B"


# ── 服务层：mine_process ───────────────────────────────────────────────────

class _FakeInsights:
    def _tags_of(self, eid, name):
        return InsightService._tags_of(eid, name)


def _runtime(store):
    cfg = SimpleNamespace(tz_offset_hours=0.0, rooms={})
    return SimpleNamespace(config=cfg, store=store, insights=_FakeInsights())


def _seed_11_days(store):
    base = datetime(2026, 9, 1, 9, 0, 0)
    t = lambda m: (base + timedelta(minutes=m)).isoformat(sep="T")  # noqa: E731
    for d in range(10):
        off = d * 1440
        _ev(store, t(off + 0), "binary_sensor.study_door_contact", "书房")
        _ev(store, t(off + 1), "light.study_ceiling", "书房")
        _ev(store, t(off + 2), "switch.study_pc", "书房")
    # 第 11 天多了一次空调（过程模型里的稀有边）
    off = 10 * 1440
    _ev(store, t(off + 0), "binary_sensor.study_door_contact", "书房")
    _ev(store, t(off + 1), "light.study_ceiling", "书房")
    _ev(store, t(off + 2), "climate.study_ac", "书房")
    _ev(store, t(off + 3), "switch.study_pc", "书房")
    return t(0), t(11 * 1440)


def test_mine_process_service_persists_anomalies_and_emits_rules(store):
    start, end = _seed_11_days(store)
    svc = ActivityInferenceService(_runtime(store))
    res = svc.mine_process(start=start, end=end)

    assert res["ok"] is True
    # 装了 pm4py 的环境会自动叠加增强（engine=pure+pm4py），核心仍是纯 Python
    assert res["engine"].startswith("pure")
    assert res["cases"] == 11
    assert res["anomaly_count"] >= 1
    assert res["persisted"] >= 1

    rows = store.list_behavior_anomalies()
    assert rows and rows[0]["room"] == "书房"
    assert "rare_edges" in rows[0]["reasons"]
    assert rows[0]["status"] == "new"

    # 房间高频变体 → 候选规则（source=process，作为规则缺口提示）
    rules = store.list_candidate_rules()
    assert any(r["source"] == "process" for r in rules)
    proc = [r for r in rules if r["source"] == "process"][0]
    assert proc["name"].startswith("过程变体[书房]")
    assert [s["tag"] for s in proc["steps"]] == ["door", "light", "computer"]


def test_mine_process_service_emit_rules_false(store):
    start, end = _seed_11_days(store)
    svc = ActivityInferenceService(_runtime(store))
    res = svc.mine_process(start=start, end=end, emit_rules=False)
    assert res["ok"] is True and res["candidates"] == 0
    assert store.list_candidate_rules() == []


def test_mine_process_service_persist_false(store):
    start, end = _seed_11_days(store)
    svc = ActivityInferenceService(_runtime(store))
    res = svc.mine_process(start=start, end=end, persist=False)
    assert res["ok"] is True and res["persisted"] == 0
    assert store.list_behavior_anomalies() == []
