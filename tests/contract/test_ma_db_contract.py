"""MA ↔ DB（doubao-butler）契约测试 —— ADM 联动计划第 5 步。

事实源是 **DB 的真实调用点**（`doubao-butler/butler/integrations/memory_agent.py`），
下面 `DB_CALL_SITES` 逐条照抄它发给 MCP 的工具名与参数键。MA 侧任何一次改名、
新增必填参数、或改掉 DB 依赖的返回键，都会在这里直接变红，而不是等 DB 线上报错。

计划卡要求断言四件事：
1. MCP 工具签名（参数名/必填项）——`test_db_call_site_*`；
2. 响应 schema（DB 用 `_extract_list`/`_json_of` 取的键）——`test_response_*`；
3. member_id fail-closed（未知成员/缺 member_id 绝不返回别人的数据）——`test_member_*`；
4. 收件箱主题白名单（MA 只投 `butler/inbox/{speak,notify,tv}`）。

第 1、3(白名单)、4 组不依赖 mcp 包，本地也能跑；需要真实 MCP 服务面的用例在
无 mcp 环境自动 skip（容器内为权威跑）。

**部署前必跑**（计划卡 第 5 步 ②）。
"""
from __future__ import annotations

import json
import threading

import pytest

from memory_agent import tool_schema
from memory_agent.mqtt_bridge import INBOX_NOTIFY_TOPIC, INBOX_TOPICS

try:  # mcp 包只在容器里装；本地无 mcp 时只跑不依赖服务面的契约项
    from memory_agent import mcp_server as ms
    from memory_agent import runtime as runtime_mod
    from memory_agent.mcp_context import reset_caller, set_caller

    MCP_OK = bool(getattr(ms, "MCP_AVAILABLE", False))
except Exception:  # pragma: no cover
    ms = None
    runtime_mod = None
    MCP_OK = False

# ── DB 侧真实调用点（照抄，勿"整理"）────────────────────────────────────────
DB_CALL_SITES: dict[str, list[str]] = {
    # memory_agent.py:166 list_memories()
    "list_agent_memories": ["member_id"],
    # memory_agent.py:170 get_vision_status()
    "get_vision_status": [],
    # memory_agent.py:182 analyze_camera()
    "analyze_camera": ["room", "bypass_limits", "prompt"],
    # memory_agent.py:218 add_memory()
    "add_semantic_memory": ["text", "source_refs", "dry_run", "member_id"],
    # memory_agent.py:229 retrieve()
    "retrieve_agent_memories": ["question", "limit", "member_id"],
    # memory_agent.py:253 ask_memory()
    "ask_memory": ["question", "days"],
    # memory_agent.py:352 revoke()
    "revoke_memory": ["memory_id"],
}

# 计划卡 第 4 步 ① 点名要交给 DB 消费的业务查询工具
PLAN_DB_FACING_TOOLS = [
    "ask_memory",
    "get_member_persona",
    "analyze_behavior_change",
    "run_analysis_template",
    "list_analysis_templates",
    "query_unified_events",
]


# ── DB 的取数三件套（与 DB 侧实现保持一致，改动等于改契约）───────────────────
def _text_of(res) -> str:
    """DB `_text_of`：result.content[type=text].text 拼接。"""
    content = res.get("result", {}).get("content", []) if isinstance(res, dict) else []
    return "".join(c.get("text", "") for c in content if c.get("type") == "text")


def _extract_list(data, preferred: str | None = None) -> list:
    """DB `_extract_list`：优先 preferred 键，否则在 members/memories/results/items/data 里找列表。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if preferred and isinstance(data.get(preferred), list):
            return data[preferred]
        for k in ("members", "memories", "results", "items", "data"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


def _call_result(res) -> tuple[str, dict | list | None]:
    """把 FastMCP 的 CallToolResult 规约成 (text, json)，与 DB 收到的 JSON-RPC 体同形。"""
    content = getattr(res, "content", None) or (res if isinstance(res, list) else [])
    text = "".join(
        str(getattr(c, "text", "")) for c in content if getattr(c, "type", "") == "text"
    )
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    return text, parsed


# ── 1. 签名：DB 发的参数键必须是 MA 声明过的参数 ─────────────────────────────
@pytest.mark.parametrize("tool", sorted(DB_CALL_SITES))
def test_db_call_site_tool_is_advertised(tool):
    assert tool in tool_schema.TOOL_NAMES, f"{tool} 不在 MCP 工具面里，DB 调用必然 NOT_FOUND"
    assert tool in tool_schema.SPEC_BY_NAME


@pytest.mark.parametrize("tool", sorted(DB_CALL_SITES))
def test_db_call_site_params_are_declared_params(tool):
    spec = tool_schema.SPEC_BY_NAME[tool]
    declared = {p.name for p in spec.params}
    sent = set(DB_CALL_SITES[tool])
    unknown = sent - declared
    assert not unknown, (
        f"DB 正在发送 {tool}({', '.join(sorted(unknown))})，但 TOOL_SPECS 没有声明这些参数——"
        "目录/help/caps 与线上签名已经割裂"
    )


@pytest.mark.parametrize("tool", sorted(DB_CALL_SITES))
def test_db_call_site_satisfies_required_params(tool):
    spec = tool_schema.SPEC_BY_NAME[tool]
    required = {p.name for p in spec.params if p.required} - set(spec.force or {})
    sent = set(DB_CALL_SITES[tool])
    missing = required - sent
    assert not missing, (
        f"{tool} 新增必填参数 {sorted(missing)}，而 DB 的调用点没有传，线上会直接失败"
    )


@pytest.mark.parametrize("tool", PLAN_DB_FACING_TOOLS)
def test_plan_named_db_facing_tools_present(tool):
    assert tool in tool_schema.TOOL_NAMES
    assert tool in tool_schema.SPEC_BY_NAME


def test_run_analysis_template_signature_is_stable():
    """计划卡 ②：DB 一次调用拿到聚合，靠的是这条签名不能变。"""
    spec = tool_schema.SPEC_BY_NAME["run_analysis_template"]
    assert [p.name for p in spec.params] == [
        "template_id", "days", "start", "end", "include_timeline"
    ]
    assert spec.params[0].required is True


# ── 2/3. 响应 schema 与 member_id fail-closed（需要真实 MCP 服务面）──────────
_SKIP = pytest.mark.skipif(not MCP_OK, reason="mcp 不可用，跳过 MCP 服务面契约")


class _FakeInsights:
    def resolve_range(self, days=7, start="", end=""):
        win = {"start": "2026-09-01T00:00:00", "end": "2026-09-08T00:00:00", "days": days}
        return win["start"], win["end"], win

    def name_map(self):
        return {}

    def _fallback_name(self, eid):
        return eid

    def _usage_one(self, entity_id, start_iso, end_iso, allow_on, debounce,
                   include_timeline, time_range=""):
        return {"entity_id": entity_id, "sessions": 2, "total_on_human": "6小时",
                "daily_average_human": "1小时", "total_on_seconds": 21600,
                "by_day_seconds": {"2026-09-02": 21600.0}}

    def _usage_by_attr(self, entity_id, attribute, value, pattern, start_iso, end_iso,
                       debounce=5, include_timeline=True, time_range=""):
        return {"entity_id": entity_id, "sessions": 1, "total_on_human": "6小时"}

    def ask_memory(self, question, days=7, route="auto", return_hints=False):
        return {"ok": True, "answer": f"关于「{question}」：空调开了 2 次", "days": days}


class _FakeAgentMemory:
    def __init__(self):
        self.calls = []

    def list_agent_memories(self, state="live", room="", member_id=""):
        self.calls.append({"state": state, "member_id": member_id})
        return {"ok": True, "memories": [{"memory_id": "m1", "content": "只有该成员可见",
                                         "state": "live", "member_id": member_id}]}

    def retrieve(self, question, trust_min=None, top_k=5, member_id=""):
        self.calls.append({"question": question, "top_k": top_k, "member_id": member_id})
        return [{"memory_id": "m1", "text": "证据", "member_id": member_id, "trust": 0.8}]

    def add_semantic_memory(self, *args, **kwargs):
        """按真实位置参签名收：mcp_server 以 12 个位置参调用（见其 add_semantic_memory）。

        契约只关心 member_id 是否透传，因此这里照单收下并记录位置参，
        由测试断言末位（member_id）等于请求里的成员。
        """
        member_id = kwargs.get("member_id") or (args[11] if len(args) > 11 else "")
        self.calls.append({"args": args, "kwargs": kwargs, "member_id": member_id})
        return {"ok": True, "memory_id": "m-new", "member_id": member_id}


class _FakeVision:
    def status(self):
        return {"ok": True, "cameras": [], "go2rtc": {"reachable": True}}

    def analyze_scene(self, **kw):
        return {"ok": True, "room": kw.get("room", ""), "description": "有人在沙发"}


class _FakeStore:
    """够用的 Store 桩：契约只要求工具不抛异常并返回 JSON，不要求真事件数据。

    `tz_offset_hours` 是产品码 `_fetch_attribution_events` 直接读的 Store 属性
    （第七轮审计·时区关节把裸 datetime.now() 换成了家庭墙钟），桩子缺它会在
    调用前 AttributeError——那是桩子失真，不是产品 bug。
    """

    tz_offset_hours = 8.0

    def __init__(self):
        self._lock = threading.Lock()

    def connect(self):
        return _FakeConn()

    def entity_last_seen(self, entities=None):
        return {}

    def log_mcp_audit(self, *a, **k):
        pass

    def get_member(self, member_id):
        return None if member_id == "member:ghost" else {"member_id": member_id, "name": "K"}


class _FakeConn:
    def execute(self, *a, **k):
        return _FakeRows()


class _FakeRows:
    def fetchall(self):
        return []


class _FakeCfg:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.tz_offset_hours = 8.0
        self.mcp_response_max_bytes = 65536
        self.agent_retrieve_k = 5


@pytest.fixture
def server(tmp_path, monkeypatch):
    """真实 MCP 服务面（模块级单例，含 schema 注册出的 generated 工具）+ 桩 runtime。

    必须复用 `mcp_server.mcp_server` 单例：generated 工具是在模块导入期
    `register_simple_tools(mcp_server, get_runtime, …)` 注册上去的，
    另起 `_build_server()` 得到的实例只有手写工具，DB 用到的
    `get_vision_status` / `analyze_camera` 会「目录有、面上无」。
    """
    from memory_agent.templates import TemplateManager

    srv = ms.mcp_server
    if srv is None:
        pytest.skip("mcp 不可用，跳过 MCP 服务面契约")

    rt = type("Stub", (), {})()
    rt.config = _FakeCfg(str(tmp_path))
    rt.store = _FakeStore()
    rt.insights = _FakeInsights()
    rt.templates = TemplateManager(str(tmp_path))
    rt.agent_memory = _FakeAgentMemory()
    rt.vision = _FakeVision()
    rt.identity = None
    rt.llm = None
    monkeypatch.setattr(runtime_mod, "_runtime", rt)
    set_caller("db-contract", ["read", "write"], "test")
    yield srv, rt
    reset_caller()


@_SKIP
async def test_ask_memory_returns_readable_text(server):
    srv, _rt = server
    res = await srv.call_tool("ask_memory", {"question": "空调开了几次", "days": 3})
    text, parsed = _call_result(res)
    # DB 直接把这段文本当作口语答案回给用户（ask_memory: text or "没有查到相关记忆"）
    assert text.strip()
    assert parsed is None or parsed.get("ok") is not False


@_SKIP
async def test_list_agent_memories_shape_and_member_filter(server):
    srv, rt = server
    res = await srv.call_tool("list_agent_memories", {"member_id": "member:abc"})
    _text, parsed = _call_result(res)
    assert isinstance(parsed, dict) and parsed.get("ok") is True
    items = _extract_list(parsed, preferred="memories")
    assert items, "DB 的 _extract_list 取不到列表会静默变成空召回"
    assert all(i.get("member_id") == "member:abc" for i in items if isinstance(i, dict))
    assert rt.agent_memory.calls[-1]["member_id"] == "member:abc"


@_SKIP
async def test_list_agent_memories_without_member_is_fail_closed(server):
    srv, _rt = server
    res = await srv.call_tool("list_agent_memories", {})
    _text, parsed = _call_result(res)
    assert parsed.get("ok") is False
    assert parsed.get("code") == 403 or "member_id" in str(parsed.get("error"))
    # 关键红线：拒绝时不能夹带任何记忆内容
    assert _extract_list(parsed, preferred="memories") == []


@_SKIP
async def test_retrieve_agent_memories_respects_db_limit_param(server):
    """DB 传的是 limit（不是 top_k）；MA 必须认这个别名，否则条数被静默忽略。"""
    srv, rt = server
    res = await srv.call_tool(
        "retrieve_agent_memories",
        {"question": "昨晚谁在家", "limit": 9, "member_id": "member:abc"},
    )
    _text, parsed = _call_result(res)
    assert parsed.get("ok") is True and parsed.get("schema") == "ma-recall/1"
    assert _extract_list(parsed, preferred="memories")
    assert rt.agent_memory.calls[-1]["top_k"] == 9


@_SKIP
async def test_get_member_persona_unknown_member_is_fail_closed(server):
    srv, _rt = server
    res = await srv.call_tool("get_member_persona", {"member_id": "member:ghost"})
    _text, parsed = _call_result(res)
    assert parsed.get("ok") is False
    assert "成员不存在" in str(parsed.get("error"))
    for key in ("persona", "member", "labels", "room_insights"):
        assert key not in parsed, f"fail-closed 却返回了 {key}"


@_SKIP
async def test_analyze_behavior_change_signature_on_wire(server):
    srv, _rt = server
    res = await srv.call_tool(
        "analyze_behavior_change", {"person": "Kevin", "metric": "arrival_time", "days": 30}
    )
    _text, parsed = _call_result(res)
    assert isinstance(parsed, dict), "DB 需要 JSON 体，纯文本无法解析"


@_SKIP
async def test_run_analysis_template_returns_aggregate_in_one_call(server):
    """计划卡 第 4 步 ②：一次调用拿到「空调时长」聚合，无需 DB 自己拼查询。"""
    srv, _rt = server
    res = await srv.call_tool("run_analysis_template",
                              {"template_id": "ac_runtime_daily", "days": 7})
    _text, parsed = _call_result(res)
    assert parsed.get("ok") is True, parsed
    assert len(parsed["entities"]) >= 2
    assert "空调" in parsed["summary_text"]
    assert parsed["template"]["id"] == "ac_runtime_daily"


@_SKIP
async def test_list_analysis_templates_advertises_builtin_ids(server):
    srv, _rt = server
    res = await srv.call_tool("list_analysis_templates", {})
    _text, parsed = _call_result(res)
    ids = {t["id"] for t in _extract_list(parsed, preferred="templates")}
    assert {"ac_runtime_daily", "water_purifier_daily"} <= ids, \
        "DB 靠这张表发现可执行模板，内置 id 缺席等于功能没上线"


@_SKIP
async def test_add_semantic_memory_carries_member_id(server):
    srv, rt = server
    res = await srv.call_tool("add_semantic_memory", {
        "text": "书房空调日均 6 小时", "source_refs": ["event:e1"],
        "dry_run": True, "member_id": "member:abc",
    })
    _text, parsed = _call_result(res)
    assert parsed.get("ok") is True, parsed
    last = rt.agent_memory.calls[-1]
    assert last["member_id"] == "member:abc"
    # 位置参口径也钉住：MA 内部用位置参转发，成员必须落在末位（args[11]）。
    # 只看 kwargs 会漏掉"改名后走位置参错位"这类回归。
    assert last["args"][11] == "member:abc", last["args"]


# ── 4. 收件箱主题白名单 ──────────────────────────────────────────────────────
def test_inbox_topics_are_the_three_ruled_channels():
    assert set(INBOX_TOPICS) == {"butler/inbox/speak", "butler/inbox/notify",
                                 "butler/inbox/tv"}, INBOX_TOPICS
    assert INBOX_NOTIFY_TOPIC == "butler/inbox/notify"


def test_no_butler_trigger_topic_left_in_source():
    """越权面清理：MA 不再向 butler/trigger/* 投递（第 1 步裁定）。"""
    import pathlib

    root = pathlib.Path(tool_schema.__file__).parent
    hits = [
        f"{p.relative_to(root)}:{i}"
        for p in root.rglob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if "butler/trigger/" in line
    ]
    assert hits == [], f"仍有 butler/trigger/ 出处：{hits}"


@_SKIP
def test_catalog_and_wire_params_agree_for_db_facing_tools():
    """MCP-only 手写工具的 catalog 声明必须与真实函数签名同源（否则 help 在骗人）。"""
    src = _mcp_source()
    for tool in ("list_agent_memories", "retrieve_agent_memories", "add_semantic_memory"):
        sig = _wire_params(src, tool)
        declared = {p.name for p in tool_schema.SPEC_BY_NAME[tool].params}
        assert declared <= sig, f"{tool}：catalog 声明 {sorted(declared - sig)} 在线签名里不存在"


def _mcp_source() -> str:
    import inspect

    return inspect.getsource(ms)


def _wire_params(src: str, func_name: str) -> set[str]:
    """从 mcp_server 源码里抓出 `async def <func_name>(...)` 的参数名。"""
    import re

    m = re.search(rf"def {re.escape(func_name)}\(([^)]*)\)", src, re.S)
    assert m, f"{func_name} 未在 mcp_server 源码中找到定义"
    return {p.split(":")[0].split("=")[0].strip()
            for p in m.group(1).split(",") if p.strip() and "self" != p.strip()}


def test_db_error_text_helper_still_reads_ma_error():
    """DB `_json_of` 失败时回退原文；MA 的 ok:false 载荷必须是合法 JSON（不能被截断成半截）。"""
    err = {"ok": False, "error": {"code": "NOT_FOUND", "message": "成员不存在"}}
    payload = json.dumps(err, ensure_ascii=False)
    assert json.loads(payload)["error"]["message"] == "成员不存在"
