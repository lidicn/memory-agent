"""实体身份与健康层（v0.2）测试。

覆盖 HA 实体动荡三场景的治理：
- A1 重登漂移自愈（entity_id 变了、friendly_name 近似 → 自动改指主实体）
- A2 双集成冗余全自动合并（同 area + 同 domain + 名称高相似 → 合并并选主）
- A3 失效 / 离线清单（长期不可见 → stale，且 HA 不可达时不误判）

同时验证 run_template 的逻辑引用解析与「解析失败不静默」。

不依赖真实 HA / LLM / 网络：HA 与模板管理器均为桩。
"""

import os
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.identity import (  # noqa: E402
    IdentityReconciler,
    IdentityService,
    similarity,
)
from memory_agent.store import Store  # noqa: E402
from memory_agent.templates import (  # noqa: E402
    BehaviorInsight,
    EntityQuery,
    run_template,
)


# ── 桩 ─────────────────────────────────────────────────────────────────────

class _FakeHA:
    """模拟 ha_client.discover_entities() 的返回结构。"""

    def __init__(self, rooms=None, ok=True):
        self.rooms = rooms or {}
        self.ok = ok

    def discover_entities(self):
        if not self.ok:
            return {"ok": False, "error": "HA 不可达"}
        return {
            "ok": True,
            "rooms": self.rooms,
            "total_entities": sum(
                len(r.get("entities", {})) for r in self.rooms.values()
            ),
        }


def _rooms(*entities):
    """_rooms(("media_player.a", "客厅电视", "客厅", "on"), ...) → discover 结构。"""
    out = {}
    for entity_id, name, room, state in entities:
        out.setdefault(room, {"entities": {}})
        out[room]["entities"][entity_id] = {
            "name": name,
            "domain": entity_id.split(".")[0],
            "state": state,
        }
    return out


class _FakeTemplates:
    def __init__(self, templates=None):
        self.templates = list(templates or [])
        self.saved = []

    def list_all(self):
        return self.templates

    def get(self, template_id):
        for t in self.templates:
            if t.id == template_id:
                return t
        return None

    def save(self, tpl):
        self.saved.append(tpl)
        return tpl


class _FakeInsights:
    """run_template 所需的最小算力桩（字段对齐 test_run_template.py）。"""

    def resolve_range(self, days=7, start="", end=""):
        return "2026-09-01T00:00:00", "2026-09-08T00:00:00", {
            "start": "2026-09-01T00:00:00",
            "end": "2026-09-08T00:00:00",
            "days": 7,
            "label": "近7天",
        }

    def name_map(self):
        return {}

    def _fallback_name(self, eid):
        return eid

    def _usage_one(self, entity_id, start_iso, end_iso, on_set, debounce, include_timeline):
        return {
            "entity_id": entity_id,
            "total_seconds": 3600,
            "sessions": 2,
            "total_on_human": "1小时",
            "daily_average_human": "0.5小时",
        }

    def _usage_by_attr(self, entity_id, attribute, value, pattern, start_iso, end_iso,
                       debounce=60, include_timeline=True):
        return {
            "entity_id": entity_id,
            "attribute": attribute,
            "total_seconds": 1800,
            "sessions": 1,
            "total_on_human": "30分钟",
            "daily_average_human": "30分钟",
        }

    def _count_by_filter(self, entity_id, attribute, value, pattern, start_iso, end_iso):
        return {"entity_id": entity_id, "match_count": 5, "by_day_count": {}}


class _FakeRT:
    def __init__(self, tpl, identity=None):
        self.templates = _FakeTemplates([tpl])
        self.insights = _FakeInsights()
        self.identity = identity


# ── fixture ────────────────────────────────────────────────────────────────

@pytest.fixture
def env():
    tmp = tempfile.mkdtemp(prefix="mw_identity_")
    db = os.path.join(tmp, "test.db")
    store = Store(db, tz_offset_hours=0.0)
    store.init_schema()
    service = IdentityService(store, None, 0.0)
    yield store, service
    try:
        os.remove(db)
    except OSError:
        pass


def _reconciler(service, ha, templates=None, **kw):
    return IdentityReconciler(
        service,
        ha_getter=lambda: ha,
        templates_getter=(lambda: templates) if templates is not None else None,
        tz_offset_hours=0.0,
        **kw,
    )


# ── 归一化与相似度 ──────────────────────────────────────────────────────────

def test_similarity_normalizes_name():
    assert similarity("客厅电视", "客厅电视") == 1.0
    assert similarity("客厅电视", "客厅电视 ") == 1.0
    assert similarity("客厅电视", "客厅电视A") >= 0.85  # 重登后带后缀仍视为同一设备
    assert similarity("客厅电视", "卧室空调") < 0.85


# ── 解析与兼容 ──────────────────────────────────────────────────────────────

def test_resolve_passthrough_raw_entity_id(env):
    """裸 entity_id 原样返回，不经身份层（灰度过渡兼容）。"""
    _store, service = env
    eids, reason = service.resolve("media_player.whatever_123")
    assert eids == ["media_player.whatever_123"]
    assert reason is None


def test_resolve_unknown_logical_ref(env):
    _store, service = env
    eids, reason = service.resolve("不存在的设备")
    assert eids == [] and reason == "unresolved"


# ── 对账基础 ────────────────────────────────────────────────────────────────

def test_reconcile_creates_logical_device(env):
    store, service = env
    ha = _FakeHA(_rooms(("media_player.tv_1", "客厅电视", "客厅", "on")))
    res = _reconciler(service, ha).reconcile()

    assert res["ok"] is True and res["devices"] == 1
    dev = store.get_logical_device("media_player__客厅电视")
    assert dev is not None
    assert dev["primary_entity"] == "media_player.tv_1"
    # 逻辑名可解析
    eids, _ = service.resolve("客厅电视")
    assert eids == ["media_player.tv_1"]


def test_reconcile_ha_unreachable_does_not_break(env):
    """HA 不可达：返回 ha_unreachable，且不把任何实体误标失效。"""
    store, service = env
    _reconciler(service, _FakeHA(_rooms(("light.a", "客厅灯", "客厅", "on")))).reconcile()
    before = {r["entity_id"]: r["state"] for r in store.list_device_health()}

    res = _reconciler(service, _FakeHA(ok=False)).reconcile()
    assert res["ok"] is False and res.get("ha_unreachable") is True

    after = {r["entity_id"]: r["state"] for r in store.list_device_health()}
    assert before == after  # 未发生任何误判


# ── A2 双集成冗余全自动合并 ─────────────────────────────────────────────────

def test_auto_merge_two_integrations_same_device(env):
    """xiaomi home + xiaomi miot 同时接入同一台电视 → 合并为一个逻辑设备并选在线者为主。"""
    store, service = env
    ha = _FakeHA(
        _rooms(
            ("media_player.home_123", "客厅电视", "客厅", "on"),
            ("media_player.miot_456", "客厅电视", "客厅", "unavailable"),
        )
    )
    res = _reconciler(service, ha).reconcile()

    assert res["merged"] == 1
    devices = store.list_logical_devices()
    assert len(devices) == 1
    dev = devices[0]
    assert len(dev["candidates"]) == 2
    # 选主：在线优先（miot 那路 unavailable 落选）
    assert dev["primary_entity"] == "media_player.home_123"
    assert dev["provenance"] == "auto-merged"
    # 解析只给主实体，避免双集成重复计数
    eids, _ = service.resolve("客厅电视")
    assert eids[0] == "media_player.home_123"


def test_cross_room_similar_names_not_merged(env):
    """跨 area 的高相似只建独立逻辑设备，不自动合并（降低误并率）。"""
    store, service = env
    ha = _FakeHA(
        _rooms(
            ("media_player.tv_a", "电视", "客厅", "on"),
            ("media_player.tv_b", "电视", "卧室", "on"),
        )
    )
    _reconciler(service, ha).reconcile()

    devices = store.list_logical_devices()
    assert len(devices) == 2  # 两个房间各一台，互不合并
    assert {d["primary_entity"] for d in devices} == {
        "media_player.tv_a",
        "media_player.tv_b",
    }


# ── A1 重登漂移自愈 ─────────────────────────────────────────────────────────

def test_remap_when_entity_id_changes_after_relogin(env):
    """重登后 entity_id 变了但名称基本不变 → 自动改指新实体，模板零断裂。"""
    store, service = env
    rec = _reconciler(service, _FakeHA(_rooms(("media_player.old_123", "客厅电视", "客厅", "on"))))
    rec.reconcile()
    assert service.resolve("客厅电视")[0] == ["media_player.old_123"]

    # 重登：旧实体消失，出现新 entity_id（名称一致）
    rec2 = _reconciler(service, _FakeHA(_rooms(("media_player.new_456", "客厅电视", "客厅", "on"))))
    rec2.reconcile()

    eids, _ = service.resolve("客厅电视")
    assert eids == ["media_player.new_456"]


def test_remap_when_name_slightly_changed(env):
    """重登后连 friendly_name 都微调（加了后缀）→ 仍能按相似度找回并改指。"""
    store, service = env
    _reconciler(
        service, _FakeHA(_rooms(("media_player.old_123", "客厅电视", "客厅", "on")))
    ).reconcile()

    _reconciler(
        service, _FakeHA(_rooms(("media_player.new_456", "客厅电视A", "客厅", "on")))
    ).reconcile()

    # 复用原逻辑设备（不新造重复设备），主实体改指新实体
    devices = store.list_logical_devices()
    assert len(devices) == 1
    assert devices[0]["primary_entity"] == "media_player.new_456"
    # 旧的 display_name 保持不变，模板/查询仍按原名引用
    assert devices[0]["display_name"] == "客厅电视"
    eids, _ = service.resolve("客厅电视")
    assert eids == ["media_player.new_456"]


# ── A3 失效 / 离线清单 ──────────────────────────────────────────────────────

def test_stale_marked_when_entity_disappears(env):
    """实体长期消失 → 标记 stale，且 stale 实体不再参与解析。"""
    store, service = env
    _reconciler(service, _FakeHA(_rooms(("light.a", "客厅灯", "客厅", "on")))).reconcile()
    assert store.get_device_health("light.a")["state"] == "active"

    # stale_days=-1：任何「曾经见过但本轮不在」都立即判失效
    res = _reconciler(service, _FakeHA(_rooms()), stale_days=-1).reconcile()
    assert res["stale"] == 1
    assert store.get_device_health("light.a")["state"] == "stale"

    eids, reason = service.resolve("客厅灯")
    assert eids == [] and reason == "stale"


def test_reconcile_reports_health_changes_only_once(env):
    """对账返回「本轮发生变化」的实体，供 MQTT 推送；无变化时不重复上报。"""
    store, service = env
    _reconciler(service, _FakeHA(_rooms(("light.a", "客厅灯", "客厅", "on")))).reconcile()

    res = _reconciler(service, _FakeHA(_rooms()), stale_days=-1).reconcile()
    assert res["stale"] == 1
    changes = res["health_changes"]
    assert any(c["entity_id"] == "light.a" and c["to"] == "stale" for c in changes)
    assert changes[0]["from"] == "active"

    # 已经是 stale 的不重复上报（避免每轮对账都刷一遍告警）
    again = _reconciler(service, _FakeHA(_rooms()), stale_days=-1).reconcile()
    assert again["health_changes"] == []


def test_referenced_flag_for_template_entities(env):
    """模板引用到的实体会被标记 referenced，供失效清单排序。"""
    store, service = env
    tpl = BehaviorInsight(
        id="t", name="t", description="", category="media",
        entities=[EntityQuery(entity_id="media_player.tv_1", attribute="source",
                              pattern="equals", value="HDMI 3")],
    )
    tm = _FakeTemplates([tpl])
    ha = _FakeHA(_rooms(("media_player.tv_1", "客厅电视", "客厅", "on")))
    _reconciler(service, ha, templates=tm).reconcile()

    assert store.get_device_health("media_player.tv_1")["referenced"] == 1


# ── 模板自愈（把写死的 entity_id 改为逻辑引用）────────────────────────────

def test_bind_stale_template_to_logical_ref(env):
    """模板写死的实体已失效且身份层已找到新实体 → 自动改写为逻辑引用（保留旧值兜底）。"""
    store, service = env
    tpl = BehaviorInsight(
        id="xbox", name="xbox", description="", category="media",
        entities=[EntityQuery(entity_id="media_player.old_123", attribute="source",
                              pattern="equals", value="HDMI 3")],
    )
    tm = _FakeTemplates([tpl])

    _reconciler(
        service,
        _FakeHA(_rooms(("media_player.old_123", "客厅电视", "客厅", "on"))),
        templates=tm,
    ).reconcile()
    assert tpl.entities[0].logical_id == ""  # 实体在线，无需改写

    # 重登漂移后旧实体消失
    _reconciler(
        service,
        _FakeHA(_rooms(("media_player.new_456", "客厅电视A", "客厅", "on"))),
        templates=tm,
    ).reconcile()

    eq = tpl.entities[0]
    assert eq.logical_id == "media_player__客厅电视"
    assert eq.entity_id == "media_player.old_123"  # 旧值保留作兜底
    assert tpl in tm.saved


# ── run_template 与身份层联动 ───────────────────────────────────────────────

def test_run_template_resolves_logical_ref(env):
    """模板用逻辑设备名时，run_template 解析为主实体并用它计算。"""
    store, service = env
    _reconciler(
        service, _FakeHA(_rooms(("media_player.live_9", "客厅电视", "客厅", "on")))
    ).reconcile()

    tpl = BehaviorInsight(
        id="tpl_logic", name="电视时长", description="", category="media",
        entities=[EntityQuery(entity_id="", logical_id="客厅电视", attribute="source",
                              pattern="equals", value="HDMI 3", metric="duration")],
    )
    rt = _FakeRT(tpl, identity=service)
    out = run_template(rt, "tpl_logic")

    assert out["ok"] is True
    e = out["entities"][0]
    assert e["entity_id"] == "media_player.live_9"
    assert e["result"]["entity_id"] == "media_player.live_9"
    assert e["resolved"] == ["media_player.live_9"]


def test_run_template_missing_device_not_silent(env):
    """解析不到设备时不静默返回空结果，而是显式标记 stale。"""
    _store, service = env
    tpl = BehaviorInsight(
        id="tpl_gone", name="已失效", description="", category="media",
        entities=[EntityQuery(entity_id="", logical_id="不存在的设备", attribute="source",
                              pattern="equals", value="HDMI 3", metric="duration")],
    )
    rt = _FakeRT(tpl, identity=service)
    out = run_template(rt, "tpl_gone")

    e = out["entities"][0]
    assert e["stale"] is True
    assert e["resolved"] == []
    assert "失效" in e["result"]["error"]


def test_run_template_compat_without_identity():
    """未装配身份层（如单测桩）时，行为与改造前完全一致。"""
    tpl = BehaviorInsight(
        id="tpl_plain", name="plain", description="", category="media",
        entities=[EntityQuery(entity_id="sensor.test", attribute="state",
                              pattern="equals", value="on", metric="duration")],
    )
    rt = _FakeRT(tpl)  # 无 identity
    out = run_template(rt, "tpl_plain")
    assert out["entities"][0]["entity_id"] == "sensor.test"
    assert out["entities"][0]["result"]["total_seconds"] == 3600


def test_entity_query_logical_id_roundtrip():
    """logical_id 必须能序列化/反序列化，否则模板存盘后会丢。"""
    tpl = BehaviorInsight(
        id="t", name="n", description="", category="media",
        entities=[EntityQuery(entity_id="media_player.x", attribute="source",
                              pattern="equals", value="HDMI 3",
                              metric="duration", logical_id="media_player__客厅电视")],
    )
    back = BehaviorInsight.from_dict(tpl.to_dict())
    assert back.entities[0].logical_id == "media_player__客厅电视"
    assert back.entities[0].metric == "duration"
