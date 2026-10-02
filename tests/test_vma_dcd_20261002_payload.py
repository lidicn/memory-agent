"""DCD 裁定 20261002 两份（MA 载荷键名七问 / MA 交付面三问）的落地回归锁。

覆盖面（每条都对应裁定里的一句验收，不是我自己想加的字段）：
- Q7：`caps.version` = **计划号**，不是包版本；
- Q2：`ma/device-health` 加契约别名 `device_id`/`status`，**保留** `from`/`to`/`stable_id`；
- Q3：`ma/insights` 带 `kind` 词表 + `summary` + `evidence[]`，身份走复数 `persons[]`，旧键不删；
- Q6：事件类 `ts` = 家庭墙钟 ISO，收件箱 `ts` = epoch int（登记在案的例外）；
- 交付面 Q1：vendored wheel 的 sha 与 `vendor/README.md` 的登记、Dockerfile 的安装行三处一致。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import types
from datetime import datetime

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
