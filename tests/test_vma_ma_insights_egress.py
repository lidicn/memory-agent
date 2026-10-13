# -*- coding: utf-8 -*-
"""`ma/insights` 那一跳的装载级出声面（判例 1 的延伸：「改过配置」⛔ 等于「真的发过一次」）。

射程：只罩 `VisionService._maybe_publish_alert` 的投递结果出声行与它的出境纪律。
⛔ 覆盖载荷键名与键序（那是 `test_vma_dcd_20261004_insight_id.py` 与 DB/AF 契约的活），
⛔ 覆盖 broker 真连（现网半边等窗口）。
反向必填腿：`MqttBridge.publish_raw` 的序列口径一旦变更，`_body_sha8` 就与 broker 侧
抓包字节对不上 ⇒ 由 `test_egress_sha8_shares_the_bridges_serialization` 单独咬住。
"""

import hashlib
import json
import os
import types

from memory_agent import vision_service as vs

FIXED_TS = "2026-10-14T03:51:00+08:00"
STRANGERS = [{"name": "陌生人", "via": "vlm", "confidence": 0.42}]
ROOM = "客厅"


class _FakeMqtt:
    """只记不发的两面桩：`publish_raw` 的返回值由测试指定（True/False/抛）。"""

    def __init__(self, result=True, raises=None):
        self.raw = []
        self.notifies = []
        self._result = result
        self._raises = raises

    def publish_raw(self, topic, payload, retain=False):
        self.raw.append((topic, payload, retain))
        if self._raises is not None:
            raise self._raises
        return self._result

    def publish_notify(self, title, body, *, trace_id, channel="", priority=0):
        self.notifies.append({"title": title, "body": body, "trace_id": trace_id})
        return True


def _service(mqtt):
    service = vs.VisionService(types.SimpleNamespace(
        vision_alert_mqtt_enabled=True,
        vision_alert_mqtt_topic="ma/insights",
        vision_alert_cooldown_s=300,
        tz_offset_hours=8.0,
    ), store=None, ha=None)
    service.mqtt = mqtt
    return service


def _publish(monkeypatch, mqtt, persons=None, room=ROOM):
    monkeypatch.setattr(vs, "now_local", lambda tz=None: types.SimpleNamespace(
        isoformat=lambda: FIXED_TS))
    service = _service(mqtt)
    service._maybe_publish_alert(room, list(STRANGERS if persons is None else persons),
                                 "http://nas/snap.jpg", "有人在客厅")
    return service, mqtt.raw[-1][1] if mqtt.raw else None


def test_success_line_names_the_anchor_and_only_the_anchor(monkeypatch, capsys):
    """发成功⇒一行带 `ok=True`／`topic`／`insight_id`／`retained=False`／8 位 `body_sha8`。"""
    mqtt = _FakeMqtt(result=True)
    _, payload = _publish(monkeypatch, mqtt)
    out = capsys.readouterr().out
    line = [l for l in out.splitlines() if "ma/insights 投递" in l]
    assert len(line) == 1, f"投递行应恰好一条，实得 {len(line)}：{out!r}"
    assert "ok=True" in line[0]
    assert "topic=ma/insights" in line[0]
    assert f"insight_id={payload['insight_id']}" in line[0]
    assert "retained=False" in line[0]
    assert "body_sha8=" in line[0]
    sha = line[0].split("body_sha8=")[1].split()[0]
    assert len(sha) == 8 and all(c in "0123456789abcdef" for c in sha), f"sha8 形制坏了：{sha!r}"


def test_refused_publish_still_speaks(monkeypatch, capsys):
    """★ 这一枚咬的是「返回值被丢掉」：桥返回 False，出声行必须写 ok=False。"""
    mqtt = _FakeMqtt(result=False)
    _publish(monkeypatch, mqtt)
    out = capsys.readouterr().out
    line = [l for l in out.splitlines() if "ma/insights 投递" in l]
    assert len(line) == 1, f"返回 False 时也要恰有一条：{out!r}"
    assert "ok=False" in line[0]


def test_raising_publish_speaks_once_and_does_not_escape(monkeypatch, capsys):
    """桥抛异常⇒原「已忽略」行还在，另恰补一条 `ok=False`，且异常⛔ 上抛给巡检主流程。"""
    mqtt = _FakeMqtt(raises=RuntimeError("broker down"))
    _publish(monkeypatch, mqtt)
    out = capsys.readouterr().out
    assert "异常 MQTT 推送失败" in out
    line = [l for l in out.splitlines() if "ma/insights 投递" in l]
    assert len(line) == 1 and "ok=False" in line[0], f"异常路径的出声形状变了：{out!r}"


def test_egress_line_distinguishes_sent_from_refused(monkeypatch, capsys):
    """★ 这枚锁的是出声面的**存在理由**：同一个调用点，发成功与没发成必须读得出区别。

    只断言 `ok=False` 的两条腿（上一条与本条的姊妹腿）在"返回值被丢掉"后**依然绿**——
    因为 `ok` 的初值本就是 False，丢弃返回值只会让成功也读成 False。那条变异只有
    这一枚能当场咬住：两行逐字相同⇒这行再也⛔ 能证明"真的发过一次"⇒判例 1 落空。
    """
    def _line(result):
        capsys.readouterr()
        _publish(monkeypatch, _FakeMqtt(result=result))
        lines = [l for l in capsys.readouterr().out.splitlines() if "ma/insights 投递" in l]
        assert len(lines) == 1, f"期望恰一条，实得 {len(lines)}"
        return lines[0]

    sent, refused = _line(True), _line(False)
    assert "ok=True" in sent and "ok=False" in refused
    assert sent != refused, (
        f"发成功与未发成在日志面上长得一模一样⇒`ok=` 是常量不是结果：{sent!r}"
    )


def test_known_member_produces_no_egress_line(monkeypatch, capsys):
    """反空集：非陌生人根本⛔ 进投递路⇒整行⛔ 出现（否则这行会淹没日志并假报"发过"）。

    已知成员用合成名：判定只看 `name` 在⛔ 在 `_STRANGER_NAMES` 里，与真名无关，
    本席位⛔ 往任何入仓面添加真实标识。
    """
    mqtt = _FakeMqtt()
    _publish(monkeypatch, mqtt, persons=[{"name": "alice", "via": "face", "confidence": 0.9}])
    out = capsys.readouterr().out
    assert "ma/insights 投递" not in out
    assert mqtt.raw == []


def test_egress_line_carries_no_room_no_person_no_snapshot(monkeypatch, capsys):
    """出境锁：日志面只允许锚点，房间名／person 名／快照 URL 一个都⛔ 进这一行。"""
    mqtt = _FakeMqtt(result=True)
    _publish(monkeypatch, mqtt)
    out = capsys.readouterr().out
    line = [l for l in out.splitlines() if "ma/insights 投递" in l][0]
    for forbidden in (ROOM, "陌生人", "snap.jpg"):
        assert forbidden not in line, f"出境面漏出 {forbidden!r}：{line!r}"


def test_body_sha8_matches_the_payload_bytes(monkeypatch):
    """锚点算法锁：`_body_sha8` ＝ sha256(载荷按桥的口径序列化) 前 8 位，逐字节可复算。"""
    mqtt = _FakeMqtt(result=True)
    _, payload = _publish(monkeypatch, mqtt)
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()[:8]
    assert vs._body_sha8(payload) == expected


def test_egress_sha8_shares_the_bridges_serialization():
    """两侧序列口径⛔ 许静默分家：桥改了 `json.dumps(...)` 的参数，本腿先红。"""
    here = os.path.dirname(os.path.abspath(__file__))
    src_root = os.path.join(here, os.pardir, "src", "memory_agent")
    with open(os.path.join(src_root, "mqtt_bridge.py"), encoding="utf-8") as f:
        bridge_src = f.read()
    with open(os.path.join(src_root, "vision_service.py"), encoding="utf-8") as f:
        service_src = f.read()
    assert "json.dumps(payload, ensure_ascii=False)" in bridge_src, (
        "桥的发布序列口径变了（找 `publish_raw` 里那行 `json.dumps`）"
        "⇒ `_body_sha8` 算的⛔ 是 broker 收到的字节，两头锚点当场分家"
    )
    assert "json.dumps(payload, ensure_ascii=False)" in service_src, (
        "`_body_sha8` 的序列口径与桥不一致了"
    )
