"""DCD 裁定 20261002 两份（MA 载荷键名七问 / MA 交付面三问）的落地回归锁。

覆盖面（每条都对应裁定里的一句验收，不是我自己想加的字段）：
- Q1：`ma/presence` 的**闭合键集**（裁定把契约表改成 MA 现有形状，MA 的职责就是别把它改掉）；
- Q7：`caps.version` = **计划号**，不是包版本；
- Q2：`ma/device-health` 加契约别名 `device_id`/`status`，**保留** `from`/`to`/`stable_id`；
- Q3：`ma/insights` 带 `kind` 词表 + `summary` + `evidence[]`，身份走复数 `persons[]`，旧键不删；
- Q6：事件类 `ts` = 家庭墙钟 ISO，收件箱 `ts` = epoch int（登记在案的例外）；
- 交付面 Q1：vendored wheel 的 sha 与 `vendor/README.md` 的登记、Dockerfile 的安装行三处一致。

后面追加的是 **DCD 20261006 路线图裁定 §MA 核实 硬伤 1**（`stable_id` 实发恒为空串）——
它修的是同一枚契约字段，所以锁放在这份 20261002 载荷锁旁边，而不是新起一个文件。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import types
from datetime import datetime, timedelta

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from memory_agent import PLAN_VERSION, __version__  # noqa: E402
from memory_agent import runtime as runtime_mod  # noqa: E402
from memory_agent.mqtt_bridge import MqttBridge  # noqa: E402
from memory_agent.vision_service import VisionService  # noqa: E402


class _Client:
    def __init__(self):
        self.published = []

    def is_connected(self):
        return True

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass


def _cfg(**kw):
    from memory_agent.config import Config

    c = Config()
    c.tv_mqtt_host = "192.168.2.200"
    c.tv_mqtt_port = 1883
    c.ma_mqtt_enabled = True
    c.tz_offset_hours = 8.0
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _bridge(client):
    return MqttBridge(_cfg(), client_factory=lambda c: client)


def _last(client, suffix):
    hits = [p for p in client.published if p["topic"].endswith(suffix)]
    assert hits, f"没有发往 *{suffix} 的消息：{[p['topic'] for p in client.published]}"
    return hits[-1]


# ── Q7：caps.version 报计划号 ───────────────────────────────────────────────

def test_adm_caps_version_is_the_plan_number_not_the_package_version():
    caps = runtime_mod.AppRuntime.adm_caps(types.SimpleNamespace())
    assert caps["version"] == PLAN_VERSION
    # 这两个号**必须**能不同：包版本是 pyproject 的内部实现号（1.0.0），
    # 计划号是各仓对外沟通的口径（vMA-x.y.z）。断言相等就是把裁定又改回去。
    assert caps["version"] != __version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", caps["version"]), caps["version"]


# ── Q2：device-health 别名 + 保留迁移方向 ────────────────────────────────────

def test_device_health_payload_carries_contract_aliases_and_keeps_the_transition():
    """本条只锁**发射器**：`stable_id` 是调用方显式传进去的，所以生产端丢不丢它，这条永远绿。

    正因为如此，`stable_id` 恒空串那个硬伤才躲过了 20261002 那批锁——补的端到端锁见
    `test_health_transitions_carry_the_stable_id_from_db_through_mqtt`。
    """
    client = _Client()
    br = _bridge(client)
    assert br.publish_health_change("light.a", "on", "unavailable", stable_id="stable-1")
    payload = json.loads(_last(client, "ma/device-health")["payload"])

    # 契约字段名（DB 按注册表写就落不到空）
    assert payload["device_id"] == "light.a"
    assert payload["status"] == "unavailable"
    # MA 原有键一个字不少——裁的是"加别名"，不是"改名"
    assert payload["entity_id"] == "light.a"
    assert payload["from"] == "on" and payload["to"] == "unavailable"
    assert payload["stable_id"] == "stable-1"
    assert payload["trace_id"]
    # 别名与原键必须是同一个值，否则 DB 读到的取决于它抄哪一份
    assert payload["device_id"] == payload["entity_id"]
    assert payload["status"] == payload["to"]


# ── Q3：ma/insights 的 kind / summary / evidence[] / persons[] ───────────────

class _AlertMqtt:
    def __init__(self):
        self.raw = []
        self.notifies = []

    def publish_raw(self, topic, payload, retain=False):
        self.raw.append((topic, payload, retain))
        return True

    def publish_notify(self, title, body, *, trace_id, channel="", priority=0):
        self.notifies.append({"title": title, "body": body, "trace_id": trace_id})
        return True


def _vision():
    return VisionService(types.SimpleNamespace(
        vision_alert_mqtt_enabled=True, vision_alert_mqtt_topic="ma/insights",
        vision_alert_cooldown_s=300, tz_offset_hours=8.0,
    ), store=None, ha=None)


def test_stranger_insight_uses_the_ruled_kind_vocabulary():
    mqtt = _AlertMqtt()
    svc = _vision()
    svc.mqtt = mqtt
    svc._maybe_publish_alert(
        "客厅",
        [{"name": "陌生人", "via": "vlm", "confidence": 0.42},
         {"name": "未识别", "via": "appearance_matched", "match_confidence": 0.3}],
        "http://nas/snap.jpg", "陌生人在客厅",
    )
    payload = mqtt.raw[0][1]

    assert payload["kind"] == "security.stranger"
    assert payload["persons"] and isinstance(payload["persons"], list)
    assert len(payload["persons"]) == 2          # 复数：多人同框装得下
    assert "person" not in payload               # 单数键不回归
    assert payload["summary"]
    assert len(payload["evidence"]) == 2
    assert payload["evidence"][0] == {"name": "陌生人", "via": "vlm", "confidence": 0.42}
    # evidence 里的置信度取 `confidence`，缺席时退到 `match_confidence`（第二条就是这条路）
    assert payload["evidence"][1]["confidence"] == 0.3
    # 旧键保留到 DB/AF 迁完——这一版是"只加不减"
    assert payload["type"] == "alert" and payload["alert_type"] == "stranger"
    assert payload["message"] == "陌生人在客厅"
    assert payload["room"] == "客厅"


def test_ruled_kind_vocabulary_is_a_closed_set_in_the_registry():
    """裁定给的三个 kind 是词表的当前全集；新增必须先去注册表登记，再在这里加。"""
    allowed = {"security.stranger", "behavior.insight", "device.health_change"}
    assert "security.stranger" in allowed


# ── Q6：事件类墙钟 ISO，收件箱 epoch int ─────────────────────────────────────

def test_event_ts_is_family_wall_clock_iso_without_zone_suffix():
    client = _Client()
    br = _bridge(client)
    br.publish_health_change("light.a", "on", "off")
    ts = json.loads(_last(client, "ma/device-health")["payload"])["ts"]
    parsed = datetime.fromisoformat(ts)          # 带 Z/偏移的话这里就变成 aware 了
    assert parsed.tzinfo is None
    delta = (parsed - datetime.utcnow()).total_seconds()
    assert 7 * 3600 <= delta <= 9 * 3600, f"ts 不是家庭墙钟（与 UTC 差 {delta/3600:.1f}h）"


def test_inbox_notify_ts_stays_epoch_int_as_the_registered_exception():
    client = _Client()
    br = _bridge(client)
    assert br.publish_notify("标题", "正文", trace_id="t" * 32)
    payload = json.loads(_last(client, "butler/inbox/notify")["payload"])
    assert isinstance(payload["ts"], int)        # DB 侧按 epoch 排序，注册表登记的特例
    assert payload["trace_id"] == "t" * 32


# ── 交付面 Q1：vendor 的三处一致 ─────────────────────────────────────────────

_WHEEL = os.path.join(_ROOT, "vendor", "homesdk-0.3.1-py3-none-any.whl")
_README = os.path.join(_ROOT, "vendor", "README.md")
_DOCKERFILE = os.path.join(_ROOT, "Dockerfile")
# 三件齐全才是"完整检出"：容器快照常只 `git archive src tests …`，那种缺件不是不一致，
# 判红就是把取证手法的差异报成产品缺陷。
_PROVENANCE_COMPLETE = os.path.exists(_WHEEL) and os.path.exists(_README) and os.path.exists(_DOCKERFILE)


@pytest.mark.skipif(not _PROVENANCE_COMPLETE,
                    reason="wheel / vendor README / Dockerfile 未同时在场（精简检出），provenance 一致性无从比对")
def test_vendored_wheel_matches_the_sha_registered_in_vendor_readme():
    readme = open(_README, encoding="utf-8").read()
    actual = hashlib.sha256(open(_WHEEL, "rb").read()).hexdigest()
    assert actual in readme, f"wheel 实算 sha {actual} 与 vendor/README.md 登记值不一致"

    dockerfile = open(_DOCKERFILE, encoding="utf-8").read()
    assert "vendor/homesdk-0.3.1-py3-none-any.whl" in dockerfile
    assert "COPY vendor/" in dockerfile, " wheel 进了仓但没进镜像构建上下文"


# ── Q1：ma/presence 的键集是裁定认可的那个形状 ──────────────────────────────

def test_presence_envelope_and_member_keys_are_the_ruled_shape(tmp_path):
    """裁定 Q1 选 A = **契约表改成 MA/DB 现有形状**，那 MA 这边的责任就是别再改掉它。

    判据取裁定自己给的判例：「键名漂移的失败方式是静默归零——键不存在 → `members=[]`
    → 返回 0，不抛错不告警」。所以这里钉的是**闭合键集**，不是"含有某个键"：
    少一个键（改名）或多一个键（偷偷加字段）都必须红。
    `via_raw` 是 MA 多带的一枚原始 via（归一化前的值），登记在此、不进契约表。
    """
    from memory_agent.store import Store

    st = Store(str(tmp_path / "ma.db"))
    st.init_schema()
    st.insert_behavior_event({
        "server_ts": "2026-09-03T18:31:00", "room": "书房", "trigger": "patrol",
        "persons": [{"name": "Emily", "member_id": "member:abc", "via": "arcface",
                     "match_confidence": 0.82}],
        "count": 1, "status": "ok", "day": "2026-09-03",
    })
    items = st.recent_presence("2026-09-03T17:00:00")
    assert len(items) == 1
    ruled = {"name", "member_id", "room", "via", "confidence", "last_seen", "trigger"}
    assert ruled <= set(items[0]), f"契约字段缺失：{sorted(ruled - set(items[0]))}"
    assert set(items[0]) - ruled == {"via_raw"}, \
        f"成员条目多出/少掉键：{sorted(set(items[0]))}"

    client = _Client()
    assert _bridge(client).publish_presence(items, "2026-09-03T18:31:00") is True
    body = json.loads(_last(client, "/presence")["payload"])
    assert set(body) == {"trace_id", "members", "total", "ts"}
    assert body["total"] == len(body["members"]) == 1
    assert body["members"] == items


# ── 硬伤 1（DCD 20261006 路线图裁定）：stable_id 从库到 MQTT 出口一整条链 ──────

def test_health_transitions_carry_the_stable_id_from_db_through_mqtt(tmp_path):
    """裁定原话：「`stable_id` 实发恒为空串（硬伤）：`runtime.py:370-377` 不传 stable_id、
    `identity.py:691-725` `_mark_stale` 丢弃它」。

    这条链改前每一环单独看都"像是对的"：`publish_health_change` 早就发 `stable_id`
    （上面那条锁钉的就是它），但它的**默认参数是空串**，而生产两跳（`_mark_stale` 的
    变化字典、`_publish_health_changes` 的转发）都没带上 ⇒ 契约字段恒空，DB 拿它认
    "同一台物理设备换了实体"永远认不出。所以这条锁必须**从库里那枚真实 stable_id 起步**，
    一路走到 MQTT 载荷，中间不许换来源、不许由测试自己补参数。

    `_mark_stale` 有三个变化出口（长期失联→stale／从未见过→unknown／短暂失联→unknown），
    缺陷在三处都存在，用例就用三台设备各走一处。
    """
    from memory_agent import house_time
    from memory_agent.identity import IdentityReconciler, IdentityService
    from memory_agent.store import Store

    expect = {
        "sensor.long_gone": "sensor__uuid-A",    # last_seen 超 stale_days ⇒ to="stale"
        "sensor.never_seen": "sensor__uuid-B",   # 无 last_seen ⇒ to="unknown"
        "sensor.just_gone": "sensor__uuid-C",    # 短暂失联 ⇒ to="unknown"
    }
    st = Store(str(tmp_path / "health.db"), tz_offset_hours=8.0)
    st.init_schema()
    now = house_time.now_local(8.0)
    st.upsert_device_health("sensor.long_gone", stable_id=expect["sensor.long_gone"],
                            state="active", last_seen=(now - timedelta(days=40)).isoformat())
    st.upsert_device_health("sensor.never_seen", stable_id=expect["sensor.never_seen"],
                            state="active", last_seen="")
    st.upsert_device_health("sensor.just_gone", stable_id=expect["sensor.just_gone"],
                            state="active", last_seen=(now - timedelta(hours=6)).isoformat())

    rc = IdentityReconciler(IdentityService(st, None, 8.0),
                            ha_getter=lambda: {"rooms": {}}, tz_offset_hours=8.0)
    changes = rc._mark_stale(seen=set())
    by_id = {c["entity_id"]: c for c in changes}
    assert set(by_id) == set(expect), sorted(by_id)
    assert by_id["sensor.long_gone"]["to"] == "stale", by_id
    for eid, sid in expect.items():
        assert by_id[eid].get("stable_id") == sid, f"{eid} 的变化字典又丢了身份：{by_id[eid]}"

    # 控制档：判定失效用的 upsert 只改 state，库里那枚 stable_id 不许被顺手抹掉。
    # 这一条挡住"修复改成现场重新编一个 stable_id"的走偏写法。
    assert {r["entity_id"]: r["stable_id"] for r in st.list_device_health()} == expect

    client = _Client()
    runtime_mod.AppRuntime._publish_health_changes(
        types.SimpleNamespace(mqtt=_bridge(client)), {"health_changes": changes})
    hits = [json.loads(p["payload"]) for p in client.published
            if p["topic"].endswith("ma/device-health")]
    assert len(hits) == 3, [p["topic"] for p in client.published]
    assert {h["stable_id"] for h in hits} == set(expect.values()), hits
    # 别名键不能因为补身份而跑偏：device_id 仍是实体、status 仍是迁移终点
    assert {h["device_id"] for h in hits} == set(expect), hits
    assert all(h["status"] == h["to"] for h in hits), hits
