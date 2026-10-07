"""DCD 20261006 路线图裁定 §七 的本仓半边：统一错误码出口 + `butler/inbox/speak`。

裁定 §七 B 的硬指标是「**联动失败必须带码，禁止静默丢弃**」，落点三处：status `reasons[]`
／ MCP·HTTP 响应 `{ok:false, code, message}` ／ `inbox_events` 审计。前两处要等 `adm/*/status`
从字面量翻成 JSON（件 2/3，前置=合并窗），本仓现在就做的那一半是：

1. 词表在仓（库在按库的常量，库不在按契约 §七 B 逐字那份**同键同值**）；
2. 每一次投递失败都落到 `MqttBridge.linkage`（`/api/health` 直接读得到），不再是 print 一行就算交代；
3. `butler/inbox/speak` 载荷按 §E 发（件 5 的本仓半边）。

另有一条本批顺手收掉的静默成功：`publish()`/`publish_raw()` 原先只看"有没有抛异常"，
paho 的失败写在返回值 `rc` 上（未连接时 rc=4 `MQTT_ERR_NO_CONN`），于是 MA 长期把"没发出去"
报成"已投递"。契约既然禁止静默丢弃，这一跳也必须带码返回 False。
"""

from __future__ import annotations

import ast
import json
import os
import sys
import types

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from memory_agent import adm_linkage  # noqa: E402
from memory_agent import mqtt_bridge as mb  # noqa: E402

# 契约 §七 B 的六枚码（逐字，顺序即表里的顺序）
CONTRACT_CODES = (
    "ADM_ERR_BROKER_UNREACHABLE",
    "ADM_ERR_PEER_OFFLINE",
    "ADM_ERR_PAYLOAD_INVALID",
    "ADM_ERR_AUTH_REQUIRED",
    "ADM_ERR_UPSTREAM_TIMEOUT",
    "ADM_ERR_INTERNAL",
)


class _Client:
    def __init__(self, publish_rc=0):
        self.published = []
        self.connected = True
        self.publish_rc = publish_rc

    def is_connected(self):
        return self.connected

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})
        return types.SimpleNamespace(rc=self.publish_rc)

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass


def _cfg():
    from memory_agent.config import Config

    c = Config()
    c.tv_mqtt_host = "192.168.2.200"
    c.tv_mqtt_port = 1883
    c.ma_mqtt_enabled = True
    c.tz_offset_hours = 8.0
    return c


def _bridge(client):
    """建桥。收件箱有两条产出路径（库在调库、库不在本仓直发），容器装 vendored 0.3.1、
    本居装 0.3.2 ⇒ autouse fixture 把 presence 探测钉成"库缺席"，两边量的才是同一个载荷；
    库口径由下面两条 `_library_*` 用例单独量。
    """
    return mb.MqttBridge(_cfg(), client_factory=lambda c: client)


@pytest.fixture(autouse=True)
def _no_library(monkeypatch):
    """presence 与 errors 两处探测缓存都钉成"库缺席"，避免本居装了 0.3.2 就换形状。"""
    monkeypatch.setattr(mb, "homesdk_presence", lambda: False)
    monkeypatch.setattr(adm_linkage, "_errors_probe", False)
    yield
    adm_linkage.reset_errors_probe()


# ── 1. 词表：库与仓两份必须同键同值 ─────────────────────────────────────────

def test_contract_vocabulary_is_the_six_codes_verbatim():
    assert adm_linkage._CONTRACT_CODES == CONTRACT_CODES
    for name in CONTRACT_CODES:
        # 契约把码名本身就当值用（homesdk 那份同形），改名等于给对端换词表
        assert adm_linkage.code(name) == name
        assert adm_linkage.is_adm_err(name) is True
    assert adm_linkage.is_adm_err("MQTT_DOWN") is False
    assert adm_linkage.is_adm_err("") is False


def test_unknown_code_name_degrades_to_internal_instead_of_raising():
    """旁路能力里"码名写错"不该把采集链路拖下水：兜成 INTERNAL 才是 fail-open 该有的样子。"""
    assert adm_linkage.code("ADM_ERR_NOT_IN_CONTRACT") == "ADM_ERR_INTERNAL"
    assert adm_linkage.code("") == "ADM_ERR_INTERNAL"


def test_library_and_fallback_agree_on_every_code():
    """库在场时两那份必须逐字相等——"装没装库"不能改变对端在 `inbox_events` 里看到的码。

    这条同时是 vendored 0.3.1 的当前实况锁：运行面没有 `homesdk.adm` ⇒ 库路径探测为假，
    走本仓词表；窗内升到 0.3.2 后同一条件改为逐枚比对库常量。两种环境下都必须绿。
    """
    try:
        from homesdk.adm import errors as lib_errors
    except Exception:
        lib_errors = None
    for name in CONTRACT_CODES:
        assert adm_linkage.code(name) == name
        if lib_errors is not None:
            assert getattr(lib_errors, name) == name, name
    if lib_errors is not None:
        assert set(lib_errors.ADM_ERRORS) == set(CONTRACT_CODES), sorted(lib_errors.ADM_ERRORS)


def test_errors_probe_is_resettable_after_hot_reload():
    """热更新可能才把库装上：探测缓存必须能解，否则 `code()` 一辈子停在仓内那份。"""
    adm_linkage.reset_errors_probe()
    assert adm_linkage._errors_probe is None
    adm_linkage.homesdk_errors()                       # 探一次，结果被缓存
    assert adm_linkage._errors_probe is not None
    adm_linkage.reset_errors_probe()
    assert adm_linkage._errors_probe is None


# ── 2. 留痕：失败必须带码且可被读走 ─────────────────────────────────────────

def test_journal_collects_codes_and_dedupes_reasons():
    j = adm_linkage.LinkageJournal(limit=4)
    j.note("ADM_ERR_BROKER_UNREACHABLE", "第一次", topic="butler/inbox/speak", trace_id="t-1")
    j.note("ADM_ERR_BROKER_UNREACHABLE", "第二次", topic="butler/inbox/speak", trace_id="t-2")
    j.note("ADM_ERR_PAYLOAD_INVALID", "缺 trace_id", topic="butler/inbox/notify")
    assert j.reasons() == ["ADM_ERR_BROKER_UNREACHABLE", "ADM_ERR_PAYLOAD_INVALID"]
    assert len(j) == 3
    assert j.entries()[-1]["code"] == "ADM_ERR_PAYLOAD_INVALID"
    assert j.entries()[0]["trace_id"] == "t-1"


def test_journal_drops_a_non_contract_code_to_internal():
    j = adm_linkage.LinkageJournal()
    assert j.note("SOMETHING_ELSE", "随手写的")["code"] == "ADM_ERR_INTERNAL"


def test_status_exposes_the_reasons_for_the_health_outlet():
    """契约落点之一：`reasons[]`。合并窗里 `encode_status` 读的就是这一枚，不能再靠日志。"""
    client = _Client()
    client.connected = False
    br = _bridge(client)
    assert br.publish_presence([], "2026-10-06T08:00:00") is False
    snap = br.status()
    assert snap["adm_reasons"] == ["ADM_ERR_BROKER_UNREACHABLE"], snap
    assert snap["adm_failures"][-1]["topic"].endswith("/presence")
    assert snap["homesdk_adm_errors"] is False


# ── 3. 静默成功收口：paho 的 rc 不是零就是没发出去 ──────────────────────────

def test_publish_reports_false_when_paho_rejects_the_message():
    """改前这里 `return True`：paho 把失败写在返回值的 rc 上，不抛异常。

    控制档（rc=0）也在同一条用例里：证明"判红"来自 rc，不是来自量具自己造的场景。
    """
    ok = _Client(publish_rc=0)
    assert _bridge(ok).publish_raw("ma/presence", {"trace_id": "t"}) is True
    assert ok.published

    bad = _Client(publish_rc=4)          # MQTT_ERR_NO_CONN
    br = _bridge(bad)
    assert br.publish_raw("ma/presence", {"trace_id": "t"}) is False
    assert br.status()["adm_reasons"] == ["ADM_ERR_BROKER_UNREACHABLE"]


def test_empty_topic_is_refused_with_a_code():
    client = _Client()
    br = _bridge(client)
    assert br.publish_raw("   ", {"trace_id": "t"}) is False
    assert br.status()["adm_reasons"] == ["ADM_ERR_PAYLOAD_INVALID"]
    assert client.published == []


# ── 4. 件 5：butler/inbox/speak 的本仓半边 ─────────────────────────────────

def test_speak_payload_is_the_ruled_shape():
    """§E 逐字：`{trace_id, ts, text, role?, priority?, expires_at?}`，**没有 `source`**。"""
    client = _Client()
    assert _bridge(client).publish_speak("书房有人", trace_id="t-1") is True
    hit = [p for p in client.published if p["topic"] == "butler/inbox/speak"]
    assert len(hit) == 1, [p["topic"] for p in client.published]
    assert hit[0]["retain"] is False, "收件箱三条都不 retained"
    payload = json.loads(hit[0]["payload"])
    assert set(payload) == {"trace_id", "ts", "text"}
    assert payload["trace_id"] == "t-1" and payload["text"] == "书房有人"
    # 收件箱的 ts 是裁定 20261002 Q6 登记的那条例外：epoch int，不是家庭墙钟 ISO
    assert isinstance(payload["ts"], int)


def test_speak_optional_keys_follow_the_library_include_rules():
    client = _Client()
    br = _bridge(client)
    br.publish_speak("话", trace_id="t-2", role="", priority=0, expires_at=None)
    plain = json.loads(_last(client, "butler/inbox/speak")["payload"])
    assert set(plain) == {"trace_id", "ts", "text"}

    br.publish_speak("话", trace_id="t-3", role="butler", priority=7, expires_at=1_800_000_000)
    full = json.loads(_last(client, "butler/inbox/speak")["payload"])
    assert set(full) == {"trace_id", "ts", "text", "role", "priority", "expires_at"}
    assert full["role"] == "butler" and full["priority"] == 7
    assert full["expires_at"] == 1_800_000_000


def test_speak_bounds_text_instead_of_dropping_the_alert():
    client = _Client()
    _bridge(client).publish_speak("长" * 2000, trace_id="t-4")
    payload = json.loads(_last(client, "butler/inbox/speak")["payload"])
    assert len(payload["text"]) == mb.INBOX_MAX_TEXT
    assert payload["text"].endswith("…")


@pytest.mark.parametrize("text,tid", (("", "t-5"), ("   ", "t-6"), ("要说的话", ""), ("要说的话", None)))
def test_speak_refuses_invalid_payload_and_records_the_code(text, tid):
    client = _Client()
    br = _bridge(client)
    assert br.publish_speak(text, trace_id=tid) is False
    assert client.published == [], "拒发就是拒发，不许留下半条消息"
    assert br.status()["adm_reasons"] == ["ADM_ERR_PAYLOAD_INVALID"]


def test_speak_without_a_broker_connection_is_broker_unreachable():
    client = _Client()
    client.connected = False
    br = _bridge(client)
    assert br.publish_speak("书房有人", trace_id="t-7") is False
    assert client.published == []
    assert br.status()["adm_reasons"] == ["ADM_ERR_BROKER_UNREACHABLE"]


def test_speak_via_the_library_uses_the_library_call(monkeypatch):
    """库口径：白名单与 fail-closed 由库执行，本仓只负责把参数原样交出去。"""
    captured = {}

    def speak(client, text, *, trace_id, role="", priority=0, expires_at=None, qos=1):
        captured.update({"text": text, "trace_id": trace_id, "role": role,
                         "priority": priority, "expires_at": expires_at})

    lib = types.SimpleNamespace(speak=speak)
    monkeypatch.setattr(mb, "homesdk_presence", lambda: lib)
    client = _Client()
    br = _bridge(client)
    assert br.publish_speak("书房有人", trace_id="t-8", role="butler") is True
    assert captured == {"text": "书房有人", "trace_id": "t-8", "role": "butler",
                        "priority": 0, "expires_at": None}
    assert client.published == [], "库已经发过一次，本仓不得再自己发第二遍"


def test_library_failure_still_returns_false_with_a_code(monkeypatch):
    def boom(client, text, **kw):
        raise ValueError("白名单外的主题")

    monkeypatch.setattr(mb, "homesdk_presence", lambda: types.SimpleNamespace(speak=boom))
    client = _Client()
    br = _bridge(client)
    assert br.publish_speak("话", trace_id="t-9") is False
    assert br.status()["adm_reasons"] == ["ADM_ERR_INTERNAL"]


def test_notify_failures_are_recorded_with_the_same_vocabulary():
    """`publish_notify` 早已存在，但它的 False 只是一行 print；两条出口必须同一套码。"""
    client = _Client()
    br = _bridge(client)
    assert br.publish_notify("标题", "正文", trace_id="") is False
    client.connected = False
    assert br.publish_notify("标题", "正文", trace_id="t-10") is False
    assert br.status()["adm_reasons"] == ["ADM_ERR_PAYLOAD_INVALID", "ADM_ERR_BROKER_UNREACHABLE"]


# ── 5. 未挂载哨兵：路由不在自主决定范围内，但它必须是一条会红的门 ────────────

_SRC_DIR = os.path.join(_SRC, "memory_agent")


def _callers_of(method_name: str) -> list[str]:
    out = []
    for dirpath, _dirs, files in os.walk(_SRC_DIR):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                tree = ast.parse(open(path, encoding="utf-8").read())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == method_name):
                    out.append(f"{os.path.relpath(path, _SRC)}:{node.lineno}")
    return out


def test_publish_speak_production_caller_is_the_announcer_fallback():
    """件 5 的"谁来投"由 DCD 20261007 §四 Q1 裁 **B**：`announcer.py` 在 HA 直发未就绪时投。

    这条锁原本钉的是"还没有生产调用点"（未经裁定不许擅自接线）。裁定批准接线之后，
    它换成钉"**只有那一处**投"：调用点每多一处，就多一个可能与 HA 同时说话的地方，
    而"两处同时说话"是裁定驳回 A/C 时点名的失败形状。所以数量与落点都要判。
    """
    callers = [c for c in _callers_of("publish_speak") if "mqtt_bridge.py" not in c]
    files = sorted({c.split(":")[0] for c in callers})
    assert files == [os.path.join("memory_agent", "announcer.py")], \
        f"`butler/inbox/speak` 出现了未经裁定的调用点：{callers}"
    assert len(callers) == 1, f"回落调用点应当只有一处，实际 {callers}"


def _last(client, topic):
    hits = [p for p in client.published if p["topic"].endswith(topic)]
    assert hits, f"没有发往 *{topic} 的消息：{[p['topic'] for p in client.published]}"
    return hits[-1]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
