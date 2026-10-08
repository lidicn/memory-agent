"""DCD 20261004「AF ma-insights 载荷三问」的 MA 半边：`insight_id` 稳定身份。

裁定原文（`decisions/20261004-AF安全审计与MA回执与遗留两批-裁定.md` §五）：
- Q1 `conf`：**可选**，报了就用并封顶 0.59，没报记"未上报"；
- Q2 `trace_id` 语义：**稳定身份另给**——契约行加 `insight_id`（**MA 应发**），
  `trace_id` 保持事件级追踪号（每次现场生成）；AF 的去重与回灌键用 `insight_id`；
- Q3 `intent`：**可选**登记，MA 愿意发结构化意图时发。

MA 侧口径：发 `insight_id`（复用告警单飞已在用的 `(session, type)` + 日键），
**不发** `conf`（陌生人告警的"洞察置信度"没有标定过，人脸匹配失败恰恰是无置信度可言的判定，
硬编一个数给 AF 封顶只会污染它的排序）；**不发** `intent`（当前没有结构化意图可给，
可选字段不得变成消费方的硬依赖）。

最后一条锁是这条链路的自警：载荷里出现**既不在契约行、也不在 20261002 "只加不减"白名单**
的键即判红。跨仓载荷加键必须先在注册表登记（`20261002-AF锁文件源与MA载荷键名-裁定` 立的规则，
`page_bytes` 那次就是没走这一步）。
"""
import os
import sys
import types
from datetime import datetime

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import vision_service as vs  # noqa: E402

# 钉死的墙钟：不钉的话，两条 publish 跨过午夜 ⇒ 日键变了 ⇒ 稳定身份"自然"不同，
# 那是运行时刻依赖，不是判据（同 tests/test_vma_q62_daily_batch_scan.py 的教训）。
FIXED_DT = datetime(2026, 10, 4, 21, 30, 0)

CONTRACT_KEYS = {          # homesdk 契约表 `ma/insights` 那一行
    "trace_id", "ts", "insight_id", "kind", "persons", "room",
    "summary", "evidence", "snapshot_url", "conf", "intent",
}
LEGACY_KEYS_20261002 = {   # 20261002 Q3 裁定"只加不减"保留到 DB/AF 迁完的旧键
    "type", "source", "alert_type", "message",
}

STRANGERS = [{"name": "陌生人", "via": "vlm", "confidence": 0.42}]


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


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(vs, "now_local", lambda tz=None: FIXED_DT)
    service = vs.VisionService(types.SimpleNamespace(
        vision_alert_mqtt_enabled=True, vision_alert_mqtt_topic="ma/insights",
        vision_alert_cooldown_s=300, tz_offset_hours=8.0,
    ), store=None, ha=None)
    service.mqtt = _AlertMqtt()
    return service


def _publish(service, room="客厅"):
    service._maybe_publish_alert(room, list(STRANGERS), "http://nas/snap.jpg", "陌生人在客厅")
    assert service.mqtt.raw, "没有投递 ma/insights"
    return service.mqtt.raw[-1][1]


def test_ma_insights_carries_insight_id_before_trace_id(svc):
    """契约行要求 `insight_id`（MA 应发），DCD 追认的键序是 insight_id → trace_id。"""
    p = _publish(svc)
    assert p["insight_id"]
    assert isinstance(p["insight_id"], str)
    assert list(p).index("insight_id") < list(p).index("trace_id"), (
        "键序倒了：AF 侧按 insight_id → hypothesis_id → trace_id 读，登记在案的口径"
    )


def test_insight_id_is_stable_while_trace_id_is_per_event(svc):
    """同房间同类洞察当天重复投递 ⇒ 同一个 insight_id，而 trace_id 必须每次都不同。

    这两件事必须同时成立：把 insight_id 做成 uuid4 的等价物，AF 的去重与回灌就空转；
    把 trace_id 做成稳定号，DB 按 trace_id 拉一屏日志会从 1:1 变 1:N。
    """
    first = _publish(svc)
    second = _publish(svc)
    assert first["insight_id"] == second["insight_id"]
    assert first["trace_id"] != second["trace_id"]
    assert first["insight_id"] != first["trace_id"]


def test_insight_id_is_scoped_to_the_room(svc):
    """客厅与书房的同一条"陌生人"是两条洞察，不能共享去重键。"""
    living = _publish(svc, "客厅")
    study = _publish(svc, "书房")
    assert living["insight_id"] != study["insight_id"]


def test_insight_id_changes_on_a_new_day_and_is_a_pure_function():
    """日键换天 ⇒ 新洞察；同一组输入 ⇒ 同一枚 id（纯函数，不看时钟、不看随机数）。"""
    day1 = vs._insight_id("vision:客厅", "stranger", "2026-10-04")
    assert day1 == vs._insight_id("vision:客厅", "stranger", "2026-10-04")
    assert day1 != vs._insight_id("vision:客厅", "stranger", "2026-10-05")
    assert day1.startswith("ma-ins-") and len(day1) == 7 + 16


def test_conf_and_intent_stay_absent_until_they_are_calibrated(svc):
    """两个可选键：没标定就不发——发了就成了 AF 排序里的假信号。"""
    p = _publish(svc)
    assert "conf" not in p
    assert "intent" not in p


def test_no_unregistered_key_leaves_through_ma_insights(svc):
    """载荷键集是封闭的：契约行 ∪ 20261002 白名单之外一律不许出现。

    这条锁的就是 `page_bytes` 那一类流程偏差——为了自己对账方便往跨仓载荷里加键。
    要加先登记（homesdk 契约表 + DCD 裁定），再把键名加进 `CONTRACT_KEYS`。
    """
    p = _publish(svc)
    extra = set(p) - (CONTRACT_KEYS | LEGACY_KEYS_20261002)
    assert not extra, f"未登记的出境键 {sorted(extra)}：先去契约表登记再来改锁"
