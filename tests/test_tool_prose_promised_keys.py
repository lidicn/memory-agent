r"""门（DCD 20261007 §三 Q1 建门）：ToolSpec 文案点名的返回键，逐个对**运行时探针读数**校验。

**钉的是什么**：`route_question` 那场事故的形状——文案说返回体有 `recommended_tool`，
载荷里从来没有这个键，模型照着文案调不到东西，而全量测试照绿，因为**没有任何一条断言
把"文案里写的键"和"真跑一遍之后的键"放在一起比**。#75 用一次性扫描量出三条活口，
`20261007-MA五件与AF一件-裁定.md` §三 Q1 裁的是：**把这种核对变成常开的门**，
并且按该裁定「甲不做」——不去建 route→工具名的硬映射表（伪精确），
只保证"给模型的键是真的"。

**三分法**（散文里的每个小写标识符 token 必须有落点，不许静默）：
1. 同一工具**声明过的入参名**（`ToolSpec.params`）⇒ 输入侧，不算承诺；
2. 探针载荷里**真实存在的键**（递归收 dict 键，含行内键）⇒ 承诺成立；
3. 其余进本文件的 `REGISTERED` 表，**逐条写理由**（枚举取值 / 另一条读法的出键 /
   落库表名 / 散文惯用语）。登记是双向的：条目过期（文案里已不出现、或载荷里其实有了）
   同样判红——否则台账会慢慢变成"什么都往裡塞"的橡皮章。

**为什么门只罩 `service="insights"` 那 17 条**：探针必须离线可跑（真库 + 真事件 + 真门面），
其余 service（`static`/`agent_memory`/`history`/…）要 HA、LLM 或网络，离线跑不出读数。
这个盲区**不静默**：`NOT_PROBED` 按 service 逐条登记总数，且断言
`PROBED + NOT_PROBED == 全部 ToolSpec 条数`——新增一条不属于任何已登记 service 的 spec，
或把某条 spec 从 insights 搬走，门当场红。

**为什么用 AST 读 `tool_schema.py`、不 import `mcp_server`**：本机 mcp SDK 版本不匹配时
整个模块导不进来（台账 §四十八），锁会在最该跑它的环境里静默 skip。
"""

import ast
import keyword
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.insights.models import EntityInfo, Intent  # noqa: E402
from memory_agent.store import Store  # noqa: E402

_SPEC_SOURCE = os.path.join(_SRC, "memory_agent", "tool_schema.py")

#: 抽"键形状"的 token：小写开头的标识符，长度 >= 4 或含下划线（`ok`/`id` 这类短名进不了承诺键的形态）
_TOKEN = re.compile(r"(?<![A-Za-z0-9_.-])[a-z][a-z0-9_]{1,}(?![A-Za-z0-9_-])")
#: 散文里必然出现、与键无关的英文虚词（形状过滤挡不住的剩下这些）
_STOP = set("""a an and any are as at by for from in into is it no not of on or that the to use
with via when which yes can may must be been if then else per etc eg i.e one two three first
next other also only same such such case cases very more most than too this those these both
each same kind sort given order true false null none list items data info see set up down
""".split())
#: `true/false/null/none` 是取值不是键，单列出来便于读数（它们从候选里扣掉，见 `_candidates`）
_VALUE_WORDS = {"true", "false", "null", "none", "ok"}

#: 探针入参：只有 `required` 非空的三条需要显式喂，其余走门面默认值
PROBE_ARGS = {
    "route_question": {"question": "书房昨晚电脑用了多久"},
    "ask_memory": {"question": "书房昨晚电脑用了多久"},
    "explain_insight": {"insight_id": "insight-missing-1"},
    "define_activity": {"name": "看书", "room": "书房", "keywords": "看书"},
    # 不传定位参数时 device_usage 走的是"没有定位到任何设备"那条错误路径，
    # 键集合会瘦成骨架——探针必须喂到真有读数的那一路。
    "get_device_usage": {"room": "书房"},
}
#: 探针必须**真的量到东西**：这几条如果返回空页，键集合会瘦成骨架，"承诺成立"就是假绿
PROBE_MUST_HAVE_ROWS = ("search_events", "query_behavior_events", "get_device_usage",
                        "get_behavior_insights", "get_behavior_insights_compare",
                        "get_climate_sessions", "get_entity_catalog")

#: 未罩住的 service ⇒ 登记为什么罩不住（离线跑不出读数），条数由门自己核对
NOT_PROBED = {
    "static": (44, "静态说明类工具，无后端载荷可探"),
    "agent_memory": (13, "要 chroma/向量库与写侧状态，离线探不出稳定读数"),
    "templates": (5, "要模板库与调度态"),
    "history": (3, "要 HA 历史 API"),
    "arena": (3, "要 LLM 生成"),
    "vision": (3, "要 VLM/相机流"),
    "collector": (2, "要 MQTT 采集器在线"),
    "signal_learning": (2, "要学习管道写侧状态"),
    "store": (1, "写侧 store 直通，返回形状由写侧门罩"),
}

#: 第 3 类落点：既不是入参、也不在载荷里，逐条给理由。
#: 理由必须可核——跨面那几条同时被 `CROSS_ATTRIBUTION` 拿去对**被指认那条读法**的真实键集合。
REGISTERED = {
    "ask_memory": {
        "semantic": "`route` 入参的取值（走向量库那条路），不是返回键",
        "auto": "`route` 入参的默认取值",
    },
    "define_activity": {
        "activity_rules": "落库的表名（写侧持久化），不是返回键",
        "agent": "散文：『agent 教系统识别新行为』，指调用方身份",
        "rule_sources": "指认 `infer_activities` 的出键，见 CROSS_ATTRIBUTION",
    },
    "explain_insight": {
        "detected_activity": "`insights` 表里活动行的 kind 取值，不是返回键",
    },
    "get_climate_sessions": {
        "climate": "domain 取值（『仅覆盖 climate 域』），不是返回键",
    },
    "get_device_health": {
        # has_data / last_seen / stale_days 不必登记：本工具载荷里真的有这三键，走的是"真实键"那条路。
        # 文案说它们来自 entity_catalog，这句话由 CROSS_ATTRIBUTION 去 entity_catalog 的载荷里核对。
        "entity_catalog": "上游读法名（`get_entity_catalog`），健康探测以它的表为原料",
    },
    "get_entity_catalog": {
        "get_media_playback_history": "反向承诺：文案明说这个工具**不存在**，不许假设它",
    },
    "get_last_event": {
        "open": "状态取值（传感器 open/closed 那一族）",
        "closed": "状态取值",
    },
    "route_question": {
        # 意图取值清单由 #75 第 8 条对 `Intent` 枚举逐字核过，这里只登记"它们是取值不是键"
        "device_usage": "route/intent 的取值（意图名）",
        "behavior": "route/intent 的取值",
        "anomaly": "route/intent 的取值",
        "rhythm": "route/intent 的取值",
        "persona": "route/intent 的取值",
        "auto": "route 认不出来时的取值",
        "template_id": "指认 `run_analysis_template` 的入参，不是本工具的键",
    },
    "search_events": {
        "sensor": "domain 取值（『会过滤 sensor/number 等纯遥测』）",
        "number": "domain 取值",
        "token": "散文惯用语：上下文 token 预算，不是键",
    },
    "query_behavior_events": {
        # 本轮建门抓到的活口：文案原先写"结果按 server_ts 倒序"，而载荷里的时间键叫
        # `time`（`insights_legacy.py:1128` 把落库列 server_ts 改名成 time 再出境）。
        # 按裁5 Q-B 的方向切文案（已改），这格登记的是**剩下的那句真话**：server_ts 是列名。
        "server_ts": "落库列名/排序依据，对应的出键是 `time`（已在载荷核对通过）",
    },
    "get_data_quality": {
        "agent": "散文：『agent 记忆镜像』那一块的定语；真实出键是 `agent_memory`，由载荷核对",
    },
}

#: 文案把某条键指认给**另一条读法**（"基于 entity_catalog 的 has_data/last_seen"）。
#: 这类登记不许只写理由就放行：去探被指认那条读法的真实载荷，键必须真的在。
CROSS_ATTRIBUTION = {
    "get_device_health": ("get_entity_catalog", ("has_data", "last_seen", "stale_days")),
    "define_activity": ("infer_activities", ("rule_sources",)),
}

DAY_COUNT = 2
ROOMS = {
    "书房": {"enabled": True, "entities": {
        "binary_sensor.study_pc": {"name": "书房电脑", "domain": "binary_sensor"},
        "light.study_lamp": {"name": "书房台灯", "domain": "light"}}},
    "主卧": {"enabled": True, "entities": {
        "climate.master_ac": {"name": "主卧空调", "domain": "climate"}}},
}
CATALOG = [
    EntityInfo(entity_id="binary_sensor.study_pc", friendly_name="书房电脑",
               room="书房", domain="binary_sensor"),
    EntityInfo(entity_id="light.study_lamp", friendly_name="书房台灯", room="书房", domain="light"),
    EntityInfo(entity_id="climate.master_ac", friendly_name="主卧空调", room="主卧", domain="climate"),
]


def _specs():
    """AST 取全部 ToolSpec：name/service/method/params/三段散文。"""
    with open(_SPEC_SOURCE, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=_SPEC_SOURCE)
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ToolSpec"):
            continue
        spec = {"params": [], "prose": ""}
        for kw in node.keywords:
            if kw.arg in ("name", "service", "method"):
                if isinstance(kw.value, ast.Constant):
                    spec[kw.arg] = kw.value.value
            elif kw.arg in ("summary", "description", "pitfall"):
                spec["prose"] += " " + _text(kw.value)
            elif kw.arg == "params" and isinstance(kw.value, (ast.List, ast.Tuple)):
                for el in kw.value.elts:
                    # `_p("name", "type", "desc", …)`：形参名一律在第 0 个位置参上
                    if isinstance(el, ast.Call) and el.args:
                        if isinstance(el.args[0], ast.Constant):
                            spec["params"].append(str(el.args[0].value))
                        elif isinstance(el.args[0], ast.Starred):
                            spec["params"] = ["*STARRED*"]     # 展开形状 ⇒ 门该红，交给下面的锁
        out.append(spec)
    return out


def _text(node):
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.JoinedStr):
        return " ".join(str(v.value) for v in node.values if isinstance(v, ast.Constant))
    return ast.unparse(node)


def _candidates(spec, tool_names):
    """候选 = 散文 token − 虚词 − 本工具入参 − 其他工具名 − Python 关键字 − 取值词。"""
    toks = {t for t in _TOKEN.findall(spec["prose"])
            if t not in _STOP and t not in _VALUE_WORDS and not keyword.iskeyword(t)
            and ("_" in t or len(t) >= 4)}
    return {t for t in toks if t not in set(spec["params"]) and t not in tool_names}


def _payload_keys(obj, depth=0, bag=None):
    bag = set() if bag is None else bag
    if depth > 8 or obj is None or isinstance(obj, (str, int, float, bool)):
        return bag
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                bag.add(k)
            _payload_keys(v, depth + 1, bag)
    elif isinstance(obj, (list, tuple, set)):
        for v in list(obj)[:12]:
            _payload_keys(v, depth + 1, bag)
    return bag


def _rows(now):
    """事件时间戳一律相对**真机 now** 生成：窗口是从 now 往前推的，写死日期会让夹具在某个
    钟点之后整批落到窗口外——那时门量到的是空表，看起来像实现坏了，其实是量具自己先失效。"""
    out = []
    for back in (1, 2):
        day = (now - timedelta(days=back)).strftime("%Y-%m-%d")
        for minute, state in ((0, "on"), (10, "off"), (20, "on"), (30, "off"),
                              (40, "on"), (50, "off")):
            out.append({"entity_id": "binary_sensor.study_pc", "ts": "%sT10:%02d:00" % (day, minute),
                        "room": "书房", "domain": "binary_sensor", "new_state": state,
                        "old_state": "x", "attrs_json": '{"friendly_name": "书房电脑"}'})
        out.append({"entity_id": "light.study_lamp", "ts": "%sT11:00:00" % day, "room": "书房",
                    "domain": "light", "new_state": "on", "old_state": "off",
                    "attrs_json": '{"friendly_name": "书房台灯"}'})
        for minute, state in ((0, "off"), (10, "cool"), (40, "cool"), (70, "off")):
            out.append({"entity_id": "climate.master_ac", "ts": "%sT13:%02d:00" % (day, minute),
                        "room": "主卧", "domain": "climate", "new_state": state, "old_state": "x",
                        "attrs_json": '{"friendly_name": "主卧空调", "hvac_action": "%s", '
                                      '"temperature": 26.0, "current_temperature": 28.0}' % state})
    return out


def _behavior_rows(now):
    """`behavior_events`（VLM 在场记录）：`query_behavior_events` 的数据源是这张表，
    不播种 ⇒ 探针永远 0 行 ⇒ 行内键一个也收不到。"""
    out = []
    for back in (1, 2):
        ts = (now - timedelta(days=back)).strftime("%Y-%m-%dT20:15:00")
        out.append({"server_ts": ts, "day": ts[:10], "room": "书房", "camera_src": "study",
                    "persons_json": '[{"person_name": "家人1"}]', "count": 1,
                    "action": "坐在书桌前看书", "scene": "书房有人看书", "confidence": 0.9,
                    "trigger": "manual", "status": "ok"})
    return out


@pytest.fixture(scope="module")
def probe_facade():
    tmp = tempfile.mkdtemp(prefix="ma_c80_")
    now = datetime.now() - timedelta(hours=8)      # 家庭墙钟按 UTC+8 摆在昨天/前天
    store = Store(os.path.join(tmp, "c80.db"), tz_offset_hours=8.0)
    store.init_schema()
    store.insert_events(_rows(now))
    with store._lock:
        conn = store.connect()
        conn.executemany(
            "INSERT INTO behavior_events (server_ts, day, room, camera_src, persons_json,"
            " count, action, scene, confidence, trigger, status)"
            " VALUES (:server_ts, :day, :room, :camera_src, :persons_json, :count, :action,"
            " :scene, :confidence, :trigger, :status)", _behavior_rows(now))
        conn.commit()
    cfg = Config()
    cfg.rooms = ROOMS
    facade = InsightService(store, cfg)
    facade.resolver.refresh(CATALOG)
    try:
        yield facade
    finally:
        store.close()


@pytest.fixture(scope="module")
def readings(probe_facade):
    """每条 insights 工具真跑一遍，收 (载荷键集合, 条数读数)。"""
    facade = probe_facade
    out = {}
    for spec in _specs():
        if spec.get("service") != "insights":
            continue
        fn = getattr(facade, spec.get("method", ""), None)
        assert fn is not None, "%s 的 method=%r 在门面上不存在" % (spec["name"], spec.get("method"))
        payload = fn(**PROBE_ARGS.get(spec["name"], {}))
        assert isinstance(payload, dict), "%s 返回的不是 dict：%r" % (spec["name"], type(payload))
        out[spec["name"]] = (_payload_keys(payload), payload)
    return out


def test_probe_fixture_actually_measured_something(readings):
    """量具自证：探针必须量到读数。空表也能"键集合覆盖了文案"，那是假绿。"""
    empty = [n for n, (_k, payload) in readings.items() if n in PROBE_MUST_HAVE_ROWS
             and not (payload.get("events") or payload.get("entities")
                      or payload.get("sessions") or payload.get("items")
                      or payload.get("rooms") or payload.get("insights")
                      or payload.get("current") or payload.get("devices")
                      or payload.get("total") or payload.get("count"))]
    assert not empty, "这些探针没有可核对的读数，键集合不可信：%s" % empty


def test_every_spec_still_has_a_landing_site(readings):
    """覆盖核对（静默零的门）：PROBED + NOT_PROBED 必须恰好等于全部 ToolSpec 的 service 分布。

    新增一条 spec 属于没登记过的 service，或把某条从 `insights` 搬走 ⇒ 这里当场红，
    而不是让门悄悄少罩一条。
    """
    specs = _specs()
    from collections import Counter
    dist = Counter(s.get("service") for s in specs)
    assert dist["insights"] == len(readings), \
        "有 spec 的 service 是 insights 却没被探到：探针 %d vs 声明 %d" % (len(readings), dist["insights"])
    registered = sum(n for n, _r in NOT_PROBED.values())
    assert registered + dist["insights"] == len(specs), \
        "覆盖对不上：总 %d 条 = 探针 %d + 登记 %d，差 %d 条无落点" % (
            len(specs), dist["insights"], registered, len(specs) - dist["insights"] - registered)
    for svc, (want, _reason) in NOT_PROBED.items():
        assert dist.get(svc, 0) == want, \
            "service=%s 实际 %d 条，登记的是 %d 条——台账过期" % (svc, dist.get(svc, 0), want)


def test_promised_keys_are_either_real_or_registered(readings):
    """门的主判据：散文点名的每个键形状 token，必须是「入参 / 载荷真实键 / 显式登记」三者之一。"""
    tool_names = {s["name"] for s in _specs()}
    unexplained = []
    for spec in _specs():
        if spec.get("service") != "insights" or spec["name"] not in readings:
            continue
        keys = readings[spec["name"]][0]
        allowed = set(REGISTERED.get(spec["name"], {}))
        for tok in sorted(_candidates(spec, tool_names)):
            if tok not in keys and tok not in allowed:
                unexplained.append("%s: %s" % (spec["name"], tok))
    assert not unexplained, (
        "对外文案点名了载荷里不存在的键，且没有登记落点（判据：入参/真实键/REGISTERED 三选一）：\n  "
        + "\n  ".join(unexplained))


def test_registration_table_is_not_a_dodge(readings):
    """登记不许过期：条目要么仍在那条工具的文案里、且确实不在载荷里，要么就该删掉。

    载荷里已经有的键仍挂着登记 = 台账在骗人；文案里已不再提的 token 还留着 = 橡皮章越积越厚。
    """
    specs = {s["name"]: s for s in _specs() if s.get("service") == "insights"}
    stale_in_payload, stale_in_prose = [], []
    for tool, table in REGISTERED.items():
        assert tool in specs, "REGISTERED 里有不存在的工具名：%s" % tool
        prose = specs[tool]["prose"]
        keys = readings[tool][0] if tool in readings else set()
        for tok in table:
            if not re.search(r"(?<![A-Za-z0-9_.-])%s(?![A-Za-z0-9_-])" % re.escape(tok), prose):
                stale_in_prose.append("%s: %s" % (tool, tok))
            elif tok in keys:
                stale_in_payload.append("%s: %s" % (tool, tok))
    assert not stale_in_prose, "这些登记项在文案里已不出现，请删：%s" % stale_in_prose
    assert not stale_in_payload, "这些键载荷里现在真的有了，请撤销登记：%s" % stale_in_payload


def test_cross_surface_claims_are_checked_against_the_named_reader(readings):
    """指认另一条读法的键（"基于 entity_catalog 的 has_data/last_seen"）不许只是说辞：
    去被指认那条读法的真实载荷里查，键必须在。"""
    missing = []
    for tool, (other, tokens) in CROSS_ATTRIBUTION.items():
        assert other in readings, "CROSS_ATTRIBUTION 指向未探针的读法：%s" % other
        keys = readings[other][0]
        for tok in tokens:
            if tok not in keys:
                missing.append("%s 指认 %s 的 %s 不在其载荷键集合里" % (tool, other, tok))
    assert not missing, "；".join(missing)


def test_intent_value_list_in_prose_matches_the_enum():
    """`route_question` 文案里的意图取值清单必须等于 `Intent` 枚举真值（与 #75 第 8 条同判据）。

    这条是给"改文案顺手改取值"准备的：裁定驳回甲案的理由是 route 是**意图分类**不是工具选择，
    所以词表本身就是契约面——清单少一个意图，模型就少一条路。
    """
    spec = {s["name"]: s for s in _specs()}["route_question"]
    prose = spec["prose"]
    hit = [i for i in _TOKEN.findall(prose) if i in {e.value for e in Intent}]
    assert set(hit) == {e.value for e in Intent} - {Intent.UNKNOWN.value}, (
        "文案点名的意图取值与 Intent 枚举不一致：文案=%s 枚举=%s" % (
            sorted(set(hit)), sorted(e.value for e in Intent)))


# ── DCD 20261007 §三 Q2 甲：`window` 回溯到两个存量例外 ──────────────────────

def test_behavior_insights_compare_echoes_the_whole_span(readings, probe_facade):
    """`get_behavior_insights_compare`（→ `core.compare_insights`）现在带 `window`。

    环比页覆盖的是**两个窗口合并的那一段**（前窗 start → 当前窗 end），不是当前窗口：
    只回显当前窗会让消费方把 14 天的对账读成 7 天——Q-A 裁"统一回显键"要的正是能核对口径。
    """
    keys, payload = readings["get_behavior_insights_compare"]
    assert "window" in keys, sorted(keys)
    win = payload["window"]
    cur, prev = payload["current"], payload["previous"]
    assert win["start"] == prev["start_iso"], (win, prev)
    assert win["end"] == cur["end_iso"], (win, cur)
    assert payload["days"] == 7 and prev["start_iso"] < cur["start_iso"], payload["days"]


def test_route_question_carries_both_window_and_time_range(readings):
    """`plan_question` 补 `window`，但**不**用它替换 `time_range`（§三 Q2 的分工口径）。

    `time_range` 是本工具特有的语义窗口对象（从问题里解析出来的起止，可能是"上周""昨晚"）；
    `window` 是与其余读法同形状的通用回显键。两键并存，读的人不必猜哪个是新的。
    """
    keys, payload = readings["route_question"]
    assert "window" in keys and "time_range" in keys, sorted(keys)
    assert payload["window"], "window 空了：探针没能解析出时间窗，回显等于没做"
    assert payload["window"]["start"] and payload["window"]["end"], payload["window"]


def test_degraded_envelopes_still_echo_window(probe_facade):
    """降级信封也带口径键：`window` 不许在失败路径上消失（与 coverage/#73 同判据）。

    非法 `compare_days` 时窗口本身无从算起，所以那一格是**空 dict**而不是缺键——
    缺键会被读成"窗口无限"，空 dict 才是"没算出窗"。
    """
    bad = probe_facade.core.compare_insights(compare_days=-1)
    assert bad["ok"] is False, bad
    assert bad["window"] == {}, bad["window"]
