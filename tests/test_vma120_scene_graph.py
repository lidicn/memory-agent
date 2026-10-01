"""vMA-1.2.0 场景图回归测试（补齐实现配套用例）。

覆盖：
1. 迁移幂等：重复 init_schema 不报错、behavior_events.scene_graph_json 列存在；
2. 插入带 scene_graph_json 的行为事件后按 room / person / minutes(日窗口) / limit 过滤；
3. vision_service 场景图解析：合法 JSON 提取 objects/relations；坏 JSON/缺字段
   降级空列表不抛；scene_graph_enabled=False 时不解析；
4. api/vision_routes GET /api/vision/scene_graph 端点（含鉴权 401 与各过滤器）。

注：实现落在 behavior_events.scene_graph_json + list_behavior_events +
/api/vision/scene_graph（无独立 Store.list_scene_graphs 方法），测试按真实路径断言。
"""
import asyncio
import json
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.store import Store  # noqa: E402
from memory_agent import vision_service as vs_mod  # noqa: E402
from memory_agent.vision_service import VisionService  # noqa: E402
from memory_agent.api import vision_routes  # noqa: E402


def make_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    st = Store(path)
    st.init_schema()
    return st


def _sg(persons=None, objects=None, relations=None):
    return {
        "room": "客厅",
        "scene_text": "客厅里有人看书",
        "emotion": "neutral",
        "persons": persons if persons is not None else [{"name": "小明", "action": "看书"}],
        "action": "看书",
        "quality": "clear",
        "trigger": "patrol",
        "objects": objects if objects is not None else ["沙发", "书"],
        "relations": relations if relations is not None else ["人 坐在 沙发"],
    }


# ── 1. 迁移幂等 ────────────────────────────────────────────────────────────

def test_init_schema_idempotent_and_scene_graph_column():
    st = make_store()
    try:
        st.init_schema()  # 第二次不报错
        st.init_schema()  # 第三次仍幂等
        cols = {r[1] for r in st.connect().execute(
            "PRAGMA table_info(behavior_events)").fetchall()}
        assert "scene_graph_json" in cols
    finally:
        st.close()
        os.remove(st.db_path)


# ── 2. 插入 + 过滤 ─────────────────────────────────────────────────────────

def test_insert_and_filter_scene_graph_events():
    st = make_store()
    try:
        now = datetime.now()
        sg_kevin = _sg(persons=[{"name": "Kevin", "action": "看电视"}],
                       objects=["电视"], relations=["人 面对 电视"])
        sg_ming = _sg(persons=[{"name": "小明", "action": "看书"}])
        recent = now.isoformat(timespec="seconds")
        old = (now - timedelta(days=3)).isoformat(timespec="seconds")
        id1 = st.insert_behavior_event({
            "server_ts": recent, "room": "客厅", "action": "看电视",
            "persons": [{"name": "Kevin"}], "count": 1, "status": "ok",
            "scene_graph_json": sg_kevin,
        })
        id2 = st.insert_behavior_event({
            "server_ts": recent, "room": "书房", "action": "看书",
            "persons": [{"name": "小明"}], "count": 1, "status": "ok",
            "scene_graph_json": sg_ming,
        })
        id3 = st.insert_behavior_event({  # 无场景图
            "server_ts": recent, "room": "客厅", "action": "走动",
            "persons": [], "count": 0, "status": "ok",
        })
        assert min(id1, id2, id3) > 0

        rows = st.list_behavior_events()
        with_sg = [r for r in rows if r.get("scene_graph_json")]
        assert len(with_sg) == 2
        parsed = json.loads(with_sg[0]["scene_graph_json"])
        assert "objects" in parsed and "relations" in parsed

        # room 过滤
        assert {r["room"] for r in st.list_behavior_events(room="书房")} == {"书房"}
        # person 过滤
        kevin = st.list_behavior_events(member="Kevin")
        assert len(kevin) == 1 and kevin[0]["room"] == "客厅"
        # limit
        assert len(st.list_behavior_events(limit=1)) == 1
        # minutes → 日窗口过滤（store 层按 day 范围；3 天前的旧事件被排除）
        day_from = (now - timedelta(minutes=60)).strftime("%Y-%m-%d")
        fresh = st.list_behavior_events(day_from=day_from)
        assert all(str(r["day"]) >= day_from for r in fresh)
        old_event = st.insert_behavior_event({
            "server_ts": old, "room": "客厅", "action": "旧事件",
            "persons": [], "count": 0, "status": "ok",
            "scene_graph_json": _sg(),
        })
        assert old_event > 0
        fresh2 = st.list_behavior_events(day_from=day_from)
        assert old_event not in {r["id"] for r in fresh2}
    finally:
        st.close()
        os.remove(st.db_path)


# ── 3. vision_service 场景图解析 ───────────────────────────────────────────

class _CapStore:
    def __init__(self):
        self.inserted = []

    def insert_behavior_event(self, payload):
        self.inserted.append(payload)
        return len(self.inserted)

    def list_members(self):
        return []


def _vs_cfg(**kw):
    base = dict(
        vision_enabled=True,
        vision_cameras=[{"room": "客厅", "stream": "cam_living", "enabled": True}],
        vision_light_gate=False,
        vision_cooldown_s=0,
        vision_max_per_hour=100,
        vlm_gate_enabled=False,
        scene_graph_enabled=True,
        face_min_conf=0.6,
        tz_offset_hours=8,
        go2rtc_base_url="",
        vision_alert_mqtt_enabled=False,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _run_analyze(vlm_text, **cfg_kw):
    cap = _CapStore()
    svc = VisionService(_vs_cfg(**cfg_kw), store=cap, ha=None)
    svc.fetch_frame = lambda stream, *a, **k: (b"fake-frame", 1)
    svc._save_snapshot = lambda room, frame: ""
    calls = {"n": 0}

    def fake_vlm(frame, prompt):
        calls["n"] += 1
        return vlm_text, 5

    svc.vlm_analyze = fake_vlm
    res = svc.analyze_room("客厅", force=True)
    return cap, res, calls["n"]


def test_scene_graph_parse_valid_json():
    text = json.dumps({
        "persons": [{"identity": "小明", "action": "看书"}],
        "scene": "客厅里有人看书",
        "snapshot_quality": "clear",
        "scene_graph": {"objects": ["沙发", "书"], "relations": ["人 坐在 沙发"]},
    }, ensure_ascii=False)
    cap, res, _ = _run_analyze(text)
    assert res["ok"] is True
    sg = cap.inserted[0]["scene_graph_json"]
    assert sg["objects"] == ["沙发", "书"]
    assert sg["relations"] == ["人 坐在 沙发"]
    assert sg["room"] == "客厅"


def test_scene_graph_parse_bad_json_degrades():
    cap, res, n_calls = _run_analyze("这不是合法 JSON {{{")
    assert res["ok"] is True  # 不抛异常，降级入库
    sg = cap.inserted[0]["scene_graph_json"]
    assert sg["objects"] == [] and sg["relations"] == []
    assert cap.inserted[0]["status"] == "vlm_failed"
    assert n_calls >= 2  # 坏 JSON 触发一次重试


def test_scene_graph_missing_fields_degrades():
    text = json.dumps({"scene": "空房间", "snapshot_quality": "clear"})
    cap, res, _ = _run_analyze(text)
    sg = cap.inserted[0]["scene_graph_json"]
    assert sg["objects"] == [] and sg["relations"] == []


def test_scene_graph_disabled_not_parsed():
    text = json.dumps({
        "scene": "客厅",
        "scene_graph": {"objects": ["沙发"], "relations": []},
    }, ensure_ascii=False)
    cap, res, _ = _run_analyze(text, scene_graph_enabled=False)
    assert cap.inserted[0]["scene_graph_json"] is None


# ── 4. GET /api/vision/scene_graph 端点 ────────────────────────────────────

def _request(query_pairs, user=None):
    from starlette.requests import Request
    from urllib.parse import urlencode

    qs = urlencode(query_pairs).encode("utf-8")
    app = types.SimpleNamespace(state=types.SimpleNamespace(runtime=None))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/vision/scene_graph",
        "query_string": qs,
        "headers": [],
        "app": app,
        "state": {"user": user} if user else {},
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    req = Request(scope, receive)
    return req


@pytest.fixture
def api_store():
    st = make_store()
    now = datetime.now().isoformat(timespec="seconds")
    old = (datetime.now() - timedelta(days=5)).isoformat(timespec="seconds")
    st.insert_behavior_event({
        "server_ts": now, "room": "客厅", "action": "看电视",
        "persons": [{"name": "Kevin"}], "count": 1, "status": "ok",
        "scene_graph_json": _sg(persons=[{"name": "Kevin", "action": "看电视"}],
                                 objects=["电视"], relations=[]),
    })
    st.insert_behavior_event({
        "server_ts": now, "room": "书房", "action": "看书",
        "persons": [{"name": "小明"}], "count": 1, "status": "ok",
        "scene_graph_json": _sg(persons=[{"name": "小明", "action": "看书"}]),
    })
    st.insert_behavior_event({  # 旧的带场景图事件（minutes 窗口外）
        "server_ts": old, "room": "客厅", "action": "看电视",
        "persons": [{"name": "Kevin"}], "count": 1, "status": "ok",
        "scene_graph_json": _sg(persons=[{"name": "Kevin"}]),
    })
    st.insert_behavior_event({  # 无场景图
        "server_ts": now, "room": "客厅", "action": "走动",
        "persons": [], "count": 0, "status": "ok",
    })
    yield st
    st.close()
    os.remove(st.db_path)


def _call(api_store, query_pairs, user=None):
    rt = types.SimpleNamespace(
        store=api_store, config=types.SimpleNamespace(tz_offset_hours=8))
    req = _request(query_pairs, user=user or {"id": "u", "is_admin": True})
    # 把 runtime 挂到 request.app.state 上（deps.runtime 的取法）
    req.scope["app"] = types.SimpleNamespace(state=types.SimpleNamespace(runtime=rt))
    resp = asyncio.run(vision_routes.scene_graph_query(req))
    return resp, (json.loads(resp.body) if resp.body else None)


def test_scene_graph_endpoint_requires_login(api_store):
    rt = types.SimpleNamespace(
        store=api_store, config=types.SimpleNamespace(tz_offset_hours=8))
    req = _request([], user=None)
    req.scope["app"] = types.SimpleNamespace(state=types.SimpleNamespace(runtime=rt))
    resp = asyncio.run(vision_routes.scene_graph_query(req))
    assert resp.status_code == 401


def test_scene_graph_endpoint_filters(api_store):
    # 全量：3 条带场景图的事件（含 5 天前旧事件）
    resp, body = _call(api_store, [])
    assert resp.status_code == 200 and body["ok"] is True
    assert body["count"] == 3

    # room 过滤
    resp, body = _call(api_store, [("room", "书房")])
    assert body["count"] == 1
    assert body["scenes"][0]["room"] == "书房"

    # person 过滤
    resp, body = _call(api_store, [("person", "Kevin")])
    assert all(any("Kevin" in p.get("name", "")
                   for p in s["scene_graph"].get("persons", []))
               for s in body["scenes"])
    assert body["count"] >= 1

    # minutes 过滤：5 天前的旧事件被排除（30 分钟窗口只剩 2 条近期）
    resp, body = _call(api_store, [("minutes", "30")])
    assert body["count"] == 2

    # latest（limit）过滤
    resp, body = _call(api_store, [("latest", "1")])
    assert body["count"] == 1
