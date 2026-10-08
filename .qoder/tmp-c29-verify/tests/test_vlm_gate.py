"""VLM 取帧 Gate 单测（主动感知 v2.0 · Phase 0.3）。

Gate 目的：书房/小黄人等基础款无本地 AI，长期靠 VLM 取帧既贵又慢；
当边缘 AI 已给答案（近期 edge_ai 信号）或房间无运动时，跳过 VLM 取帧。
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from memory_agent.store import Store, now_local
from memory_agent.vision_service import VisionService


class FakeHA:
    def __init__(self, states: dict | None = None):
        self._states = states or {}

    def get_state(self, entity_id):
        return self._states.get(entity_id)


def _cfg(**kw) -> SimpleNamespace:
    base = dict(
        tz_offset_hours=8,
        vlm_gate_enabled=True,
        vlm_gate_window_sec=120,
        room_motion_entities={},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _svc(store, ha, **cfg_kw) -> VisionService:
    return VisionService(_cfg(**cfg_kw), store, ha)


def _memory_store() -> Store:
    st = Store(":memory:")
    st.init_schema()
    return st


def test_gate_disabled_never_skips():
    svc = _svc(_memory_store(), FakeHA(), vlm_gate_enabled=False)
    assert svc._vlm_gate_skip("书房") == (False, "")


def test_gate_skips_on_fresh_edge_signal():
    store = _memory_store()
    ts = now_local(store.tz_offset_hours).isoformat(sep="T")
    store.insert_perception_event({
        "server_ts": ts, "source": "edge_ai", "kind": "human",
        "room": "书房", "entity_id": "event.chuangmi_camera_x_key_area_human_e_11_1",
    })
    svc = _svc(store, FakeHA())
    # 书房近期有边缘信号 → 跳过（盒侧 AI 已给出答案，VLM 冗余）
    assert svc._vlm_gate_skip("书房") == (True, "edge_ai_fresh")
    # 其他房间不受影响
    assert svc._vlm_gate_skip("小黄人") == (False, "")


def test_gate_stale_edge_signal_not_counted():
    store = _memory_store()
    old = (now_local(store.tz_offset_hours) - timedelta(seconds=600)).isoformat(sep="T")
    store.insert_perception_event({
        "server_ts": old, "source": "edge_ai", "kind": "human", "room": "书房",
    })
    svc = _svc(store, FakeHA())
    # 超出 gate 窗口（120s）的信号不算新鲜
    assert svc._vlm_gate_skip("书房") == (False, "")


def test_gate_skips_when_motion_off():
    store = _memory_store()
    ha = FakeHA({"binary_sensor.study_motion": {"state": "off"}})
    svc = _svc(store, ha, room_motion_entities={"书房": "binary_sensor.study_motion"})
    assert svc._vlm_gate_skip("书房") == (True, "no_motion")


def test_gate_passes_when_motion_on():
    store = _memory_store()
    ha = FakeHA({"binary_sensor.study_motion": {"state": "on"}})
    svc = _svc(store, ha, room_motion_entities={"书房": "binary_sensor.study_motion"})
    assert svc._vlm_gate_skip("书房") == (False, "")


def test_gate_fail_open_on_errors():
    """store/ha 异常时 fail-open：不拦截取帧（宁可多花 VLM，不可漏看）。"""

    class BoomStore:
        def has_recent_edge_signal(self, *a, **kw):
            raise RuntimeError("db down")

    class BoomHA:
        def get_state(self, *a, **kw):
            raise RuntimeError("ha down")

    svc = VisionService(
        _cfg(room_motion_entities={"书房": "binary_sensor.study_motion"}),
        BoomStore(), BoomHA(),
    )
    assert svc._vlm_gate_skip("书房") == (False, "")
