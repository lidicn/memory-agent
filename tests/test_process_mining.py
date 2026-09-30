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
from memory_agent.insights.utils import tags_of  # noqa: E402
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


def test_min_cases_per_room_skips_low_data_rooms():
    """样本不足的房间不做一致性检验——没有"常态"就谈不上"偏离常态"。

    线上实测：14 天 / 13 个房间 → 逐房间样本多为 1-2 天，按比例判稀有边会把
    37% 的房间-日全判成异常（不可用）。故先按房间样本数过滤。
    """
    specs = [("书房", f"2026-09-{d:02d}", ["door_on", "light_on", "computer_on"])
             for d in range(1, 16)]                      # 15 天，够
    specs.append(("客房", "2026-09-01",
                  ["door_on", "light_on", "cover_on", "light_on"]))  # 仅 1 天

    res = ak.mine_process_model_pure(_cases(specs), min_cases_per_room=4)
    assert res["cases"] == 15 and res["cases_all"] == 16
    assert res["reviewed_rooms"] == ["书房"]
    assert res["skipped_rooms"] == ["客房"]
    assert res["anomaly_count"] == 0

    # 门槛关掉（=1）时客房被纳入评估，但**单天样本不会自证异常**
    # （房间内比对：自己就是 100% 的常态），仍不会产出假异常
    loose = ak.mine_process_model_pure(_cases(specs), min_cases_per_room=1)
    assert loose["cases"] == 16 and loose["skipped_rooms"] == []
    assert loose["anomaly_count"] == 0


def test_anomaly_is_room_scoped():
    """一致性检验在**房间内**比对：别的房间的日常不会被判成异常。"""
    specs = [("书房", f"2026-09-{d:02d}", ["door_on", "light_on", "computer_on"])
             for d in range(1, 16)]
    # 书房第 16 天多插一步（房间内稀边 → 应判异常）
    specs.append(("书房", "2026-09-20",
                  ["door_on", "light_on", "climate_on", "computer_on"]))
    # 另一个房间完全不同的流程，但自身高度一致 → 不该被判异常
    specs += [("客房", f"2026-09-{d:02d}", ["door_on", "cover_on", "light_on"])
              for d in range(1, 16)]

    res = ak.mine_process_model_pure(_cases(specs), min_cases_per_room=4)
    assert res["cases"] == 31               # 书房 16 天 + 客房 15 天
    assert res["reviewed_rooms"] == ["书房", "客房"]
    assert {a["case"] for a in res["anomalies"]} == {"书房|2026-09-20"}
    assert res["anomaly_count"] == 1
    assert res["fitness_rate"] == round(30 / 31, 3)
    # 全局 dfg_edges 仍汇总两房间的边
    assert res["dfg_edges"] >= 4


def test_min_cases_per_room_all_insufficient():
    specs = [("A", "2026-09-01", ["door_on", "light_on", "media_on"]),
             ("B", "2026-09-01", ["door_on", "light_on", "media_on"])]
    res = ak.mine_process_model_pure(_cases(specs), min_cases_per_room=3)
    assert res["ok"] is True and res["cases"] == 0 and res["cases_all"] == 2
    assert sorted(res["skipped_rooms"]) == ["A", "B"]
    assert res["anomaly_count"] == 0


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


def test_build_process_cases_buckets_high_frequency_alternation():
    """真实家庭事件是秒级交替的（电视/在场来回），须聚合成粗粒度轨迹。

    否则一天的轨迹会有上百步且每天都不重样 → 变体支持度恒为 1、稀边遍地，
    异常判定被噪声主导（线上实测踩到）。聚合口径：按 10min 桶，只在标签
    **相对上一桶新出现**时记一步。
    """
    def tag_of(eid, name):
        if "tv" in eid:
            return {"media"}
        if "pir" in eid:
            return {"presence"}
        return {"door"} if "door" in eid else {"light"}

    base = datetime(2026, 9, 15, 20, 0, 0)
    evs = []
    for i in range(180):  # 20:00-20:30，每 10 秒交替一次（3 个桶全是 media+presence）
        dom = "media_player.tv" if i % 2 == 0 else "binary_sensor.pir_occupancy"
        evs.append({"ts": (base + timedelta(seconds=10 * i)).isoformat(sep="T"),
                    "entity_id": dom, "new_state": "on", "room": "客厅"})
    # 仅 media/presence 反复 → 收敛为 2 步，不足以成为一条轨迹
    assert ak.build_process_cases(evs, tag_of) == []

    # 补上真正"依次发生"的事（20:40 门、20:50 灯）→ 才构成一条轨迹
    evs.append({"ts": (base + timedelta(minutes=40)).isoformat(sep="T"),
                "entity_id": "binary_sensor.hall_door_contact", "new_state": "on",
                "room": "客厅"})
    evs.append({"ts": (base + timedelta(minutes=50)).isoformat(sep="T"),
                "entity_id": "light.living_ceiling", "new_state": "on", "room": "客厅"})
    cases = ak.build_process_cases(evs, tag_of)
    assert len(cases) == 1
    acts = cases[0]["activities"]
    # presence 优先于 media（TAG_VOCAB 顺序），且后续桶无新标签 → 共 4 步
    assert acts == ["presence_on", "media_on", "door_on", "light_on"]

    # bucket_sec=0 → 退化为逐事件（仅调试用），会压缩相邻重复
    raw = ak.build_process_cases(evs, tag_of, bucket_sec=0)
    assert len(raw[0]["activities"]) > 100


def test_build_process_cases_skips_junk_rooms():
    """HA 未分区/未知/空房间名不产生 case（避免无意义的过程轨迹）。"""
    tag_of = lambda eid, name: ({"door"} if eid.endswith("d") else {"light"})  # noqa: E731
    base = datetime(2026, 9, 15, 10, 0, 0)
    evs = [{"ts": (base + timedelta(minutes=15 * i)).isoformat(sep="T"),
            "entity_id": f"binary_sensor.s{i}_{'d' if i % 2 == 0 else 'l'}",
            "new_state": "on", "room": "未分区"} for i in range(4)]

    assert ak.build_process_cases(evs, tag_of) == []
    kept = ak.build_process_cases(evs, tag_of, skip_rooms=())
    assert len(kept) == 1 and len(kept[0]["activities"]) == 4


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
        return tags_of(eid, name)


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
