"""DCD 20261014《print 布尔出口五处修法》裁甲的行为腿（R1–R5）。

**钉的是载荷，⛔ stdout**：裁定 §四 明令「每处一条行为腿（注入 chroma／身份层异常，
断言载荷带降级声明，⛔ 不许只断言 print 出现）」。所以这里没有任何一条断言去看
`capsys`——print 出不出声与这一格有没有被消费方读到，是两件不同的事。

键形制（裁定 §二）：`degraded`（顶层快速判读位，恒在）＋ `degraded_scans`（结构化，
每项 `site`／`code`／`fields`／`reason`）。码取自 `adm_linkage.code()`，⛔ 在本仓另立
第二份字符串词表（裁定 §一 硬前置第 3 条）。

R2 的分数出 `null`＝判例 1（"降级反而更乐观"的值是最坏的形状）；R3 单独落 `ok=False`
＝裁定 §三（那一处本来就有一枚真失败，属"把假回执改真"，⛔ 新键）。
"""

import asyncio
import ast
import os
import re
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

import pytest  # noqa: E402

from memory_agent import adm_linkage  # noqa: E402
from memory_agent.agent_memory import AgentMemoryService  # noqa: E402
from memory_agent.arena import ArenaService  # noqa: E402
from memory_agent.config import Config  # noqa: E402
from memory_agent.insights_legacy import InsightService as LegacyInsight  # noqa: E402
from memory_agent.store import Store  # noqa: E402

DEGRADED_FIELDS = {"degraded", "degraded_scans"}


# ── 假件 ───────────────────────────────────────────────────────────────────
class AmsvcConfig:
    """复刻 agent_memory 里 getattr 默认值的显式配置（与既有单测同口径）。"""

    agent_trust_step = 0.2
    trust_strict_threshold = -0.3
    agent_retrieve_k = 20
    agent_dup_sim = 0.92
    agent_conflict_sim = 0.85
    agent_promote_min_days = 2
    agent_corroborate_min_conf = 0.6
    agent_default_ttl_days = 30
    tz_offset_hours = 0.0


class Col:
    """可控的 chroma 集合桩：query／upsert 各自能"就是抛"，其余走空结果。"""

    def __init__(self, query_raises=False, upsert_raises=False, response=None):
        self.query_raises = query_raises
        self.upsert_raises = upsert_raises
        self.response = response or {"ids": [[]], "distances": [[]],
                                     "documents": [[]], "metadatas": [[]]}
        self.upserts = []

    def query(self, query_texts, where=None, n_results=5, include=None):
        if self.query_raises:
            raise RuntimeError("vector store is down")
        return self.response

    def upsert(self, ids, documents, metadatas):
        if self.upsert_raises:
            raise RuntimeError("flush failed")
        self.upserts.append((list(ids), list(documents), list(metadatas)))


class History:
    def __init__(self, col):
        self._col = col

    @property
    def agent_collection(self):
        return self._col

    @property
    def arena_collection(self):
        return self._col


class ArenaStore:
    def get_arena_snapshot(self, arena_id, version=None):
        return None

    def add_arena_result(self, *a, **k):
        return "arena_x"


def _amsvc(col):
    tmp = __import__("tempfile").mkdtemp(prefix="dcd1014_")
    db = os.path.join(tmp, "t.db")
    store = Store(db, tz_offset_hours=0.0)
    store.init_schema()
    store.insert_events([
        {"id": f"ev-{d}", "entity_id": "light.living", "ts": f"{d}T21:00:00", "day": d}
        for d in ("2026-08-03", "2026-08-05", "2026-08-06")
    ])
    svc = AgentMemoryService(AmsvcConfig(), store, History(col))
    svc._test_db = db
    return svc, svc.store


REFS = ["event:ev-2026-08-03", "event:ev-2026-08-05", "event:ev-2026-08-06"]


def _one_scan(payload, site):
    hits = [s for s in payload["degraded_scans"] if s["site"] == site]
    assert len(hits) == 1, f"期望 {site} 恰有一条声明，实读={payload['degraded_scans']}"
    return hits[0]


# ── R1｜agent_memory.merge_semantic_memory ────────────────────────────────
def test_r1_chroma缺席时载荷声明降级且相似度出null():
    svc, _store = _amsvc(None)
    r = svc.merge_semantic_memory(session_id="s1", text="客厅灯光。", topic_key="T",
                                  source_refs=REFS)
    assert r["ok"] is True and r["action"] == "added"
    assert r["degraded"] is True and r["similarity"] is None
    # 旧键语义不动（判例「旧键语义不动，新键承载新语义」）：note 仍在，值原样。
    assert r["note"] == "chroma 不可用，未合并"
    entry = _one_scan(r, "agent_memory.vector_recall")
    assert entry["code"] == "ADM_ERR_INTERNAL"
    assert "similarity" in entry["fields"] and "action" in entry["fields"]


def test_r1_召回抛异常与chroma缺席同形():
    """R1 的正病：同一种"没做成合并"，两条路以前一条有 note 一条没有。裁甲后同形。"""
    svc, _store = _amsvc(Col(query_raises=True))
    r = svc.merge_semantic_memory(session_id="s1", text="客厅灯光。", topic_key="T",
                                  source_refs=REFS)
    assert r["ok"] is True and r["action"] == "added"
    assert r["degraded"] is True and r["similarity"] is None
    entry = _one_scan(r, "agent_memory.vector_recall")
    assert entry["reason"] == "RuntimeError"  # 只给异常类型名，⛔ 原文进载荷


def test_r1_正常召回时两把键恒在且不谎报降级():
    """负控：⛔ 把"扫描做完了"也报成降级——永远红的声明与没有声明等价。"""
    svc, _store = _amsvc(Col())
    r = svc.merge_semantic_memory(session_id="s1", text="全新的记忆内容。",
                                  topic_key="NEW", source_refs=REFS)
    assert r["ok"] is True and r["action"] == "added"
    assert r["degraded"] is False and r["degraded_scans"] == []
    assert r["similarity"] == 0.0  # 真扫过 ⇒ 数还在（⛔ 借降级把有值改成 null）


# ── R2／R3｜arena.evaluate_creativity ─────────────────────────────────────
def _arena(col):
    return ArenaService(Config(), ArenaStore(), History(col), None, None)


def test_r2_向量查询抛异常时新颖度出null():
    svc = _arena(Col(query_raises=True))
    r = asyncio.run(svc.evaluate_creativity("study", "夜间护眼模式", "灯光联动", ["设备1"]))
    assert r["degraded"] is True
    assert r["creativity_score"] is None and r["novelty_score"] is None
    entry = _one_scan(r, "arena.vector_recall")
    assert entry["code"] == "ADM_ERR_INTERNAL"
    assert {"creativity_score", "novelty_score"} <= set(entry["fields"])
    assert "查重未完成" in r["feedback"]


def test_r2_题目库集合缺席同样声明():
    """chroma 整块缺席时 sim 停在初值 0.0 ⇒ novelty 会算成 1.0，与抛异常同病。"""
    svc = _arena(None)
    r = asyncio.run(svc.evaluate_creativity("study", "夜间护眼模式", "灯光联动", ["设备1"]))
    assert r["degraded"] is True
    assert r["creativity_score"] is None and r["novelty_score"] is None
    assert _one_scan(r, "arena.vector_recall")["reason"] == "题目库集合缺席"


def test_r2_正常查重时分数照常出且不谎报降级():
    svc = _arena(Col())
    r = asyncio.run(svc.evaluate_creativity("study", "夜间护眼模式", "灯光联动", ["设备1"]))
    assert r["degraded"] is False and r["degraded_scans"] == []
    assert r["novelty_score"] == 1.0  # 空题库：真的没有近邻，这个 1.0 是真读数


def test_r3_题目库写入失败回ok_false且不走降级键():
    """裁定 §三：这一处本来就有真失败，属"把假回执改真"，⛔ 新键、⛔ 拖慢 Q1。"""
    svc = _arena(Col(upsert_raises=True))
    r = asyncio.run(svc.evaluate_creativity("study", "夜间护眼模式", "灯光联动", ["设备1"]))
    assert r["ok"] is False
    assert r["code"] == "ADM_ERR_INTERNAL"
    assert "未锁定" in r["error"]
    assert r["degraded"] is False  # 查重做成了；写失败另有 ok=False 承载


# ── R4／R5｜insights_legacy.InsightService.device_health ─────────────────
class _Raise:
    def __init__(self, exc):
        self._exc = exc

    def list_all(self):
        raise self._exc


class _Eq:
    def __init__(self, entity_id="", logical_id=""):
        self.entity_id = entity_id
        self.logical_id = logical_id


class _Tpl:
    def __init__(self, eqs):
        self.entities = eqs


class _Templates:
    def __init__(self, raises=None):
        self._raises = raises
        self.tpls = [_Tpl([_Eq(logical_id="light.bed")])]

    def list_all(self):
        if self._raises:
            raise self._raises
        return self.tpls


class _Identity:
    def __init__(self, raises=False):
        self.raises = raises

    def resolve(self, lid):
        if self.raises:
            raise RuntimeError("identity layer is down")
        return ["dev.1"], {}


class _RT:
    def __init__(self, raises=None, identity_raises=False):
        self.templates = _Templates(raises)
        self.identity = _Identity(identity_raises)


def _health_svc(rt=None, quality_raises=False, identity_raises=False):
    svc = LegacyInsight(Config(), store=object())
    svc.entity_catalog = lambda *a, **k: {
        "total": 2,
        "rooms": {"书房": {"entities": [
            {"entity_id": "dev.1", "has_data": True, "stale_days": 0.0},
            {"entity_id": "dev.2", "has_data": True, "stale_days": 0.0},
        ]}},
    }
    svc._num_stale = lambda e: 0.0
    svc._get_rt = lambda: (rt if rt is not None
                           else _RT(raises=RuntimeError("templates gone"),
                                    identity_raises=identity_raises))
    svc.resolve_range = lambda *a, **k: ("2026-10-01T00:00:00", "2026-10-08T00:00:00", {})

    def _quality(*a, **k):
        if quality_raises:
            raise RuntimeError("scan blew up")
        return []

    svc.data_quality_issues = _quality
    return svc


def test_r4_引用标记整段失败时聚合成null并点名字段():
    svc = _health_svc()
    r = svc.device_health(days=7)
    assert r["ok"] is True  # 裁甲：⛔ 改 ok 的语义（乙/丙档被裁定否掉）
    assert r["degraded"] is True
    assert r["referenced_entities"] is None and r["referenced_entity_ids"] is None
    entry = _one_scan(r, "insights.device_health_reference_marks")
    assert entry["reason"] == "RuntimeError"
    assert {"referenced_entities", "referenced_entity_ids", "referenced"} <= set(entry["fields"])


def test_r4_身份层部分解析失败也声明():
    """内层 except 以前是 pass：一台设备没解析上，聚合数就少报，同样⛔静默。"""
    svc = _health_svc(rt=_RT(raises=None, identity_raises=True))
    r = svc.device_health(days=7)
    assert r["degraded"] is True
    assert r["referenced_entities"] is None
    assert "部分解析失败" in _one_scan(r, "insights.device_health_reference_marks")["reason"]


def test_r5_质量扫描失败时数据面出null并独立声明():
    """R4/R5 同载荷：裁定 §2.2 裁"两件独立失败 ⇒ 各自可声明"，只给顶层＝又分不清哪格没扫。"""
    svc = _health_svc(quality_raises=True)
    r = svc.device_health(days=7)
    assert r["ok"] is True and r["degraded"] is True
    assert r["data_quality_issues"] is None and r["data_quality_count"] is None
    entry = _one_scan(r, "insights.device_health_quality_scan")
    assert entry["code"] == "ADM_ERR_INTERNAL"
    assert {"data_quality_issues", "data_quality_count"} <= set(entry["fields"])
    # 两处失败同时成立时，两条声明各自在位（⛔ 互相顶掉）
    assert len(r["degraded_scans"]) == 2


def test_r4r5_干净路径两把键恒在():
    svc = _health_svc(rt=_RT(raises=None))
    r = svc.device_health(days=7)
    assert r["degraded"] is False and r["degraded_scans"] == []
    # dev.1 经身份层解析出真引用 ⇒ 聚合数是真的（⛔ 借"恒在键"把有值抹成 null）
    assert r["referenced_entities"] == 1 and r["referenced_entity_ids"] == ["dev.1"]
    assert r["data_quality_issues"] == [] and r["data_quality_count"] == 0


# ── 形制锁 ────────────────────────────────────────────────────────────────
def test_降级声明用的码不在本仓另立词表():
    """裁定 §一 硬前置第 3 条：码必须来自 adm.errors 那一组常量语义。"""
    payload = {"ok": True}
    entry = adm_linkage.degrade_scan("x.y", "ADM_ERR_INTERNAL", ("a",), "r")
    assert adm_linkage.is_adm_err(entry["code"]) is True
    # 传错名字⛔ 悄悄造出一个新码：走 code() 的兜底档
    bad = adm_linkage.degrade_scan("x.y", "ADM_ERR_NOT_A_CODE", ("a",), "r")
    assert bad["code"] == "ADM_ERR_INTERNAL"
    out = adm_linkage.with_degradation(payload, [entry])
    assert set(out) >= DEGRADED_FIELDS
    assert out["degraded"] is True and out["degraded_scans"] == [entry]


def test_摘掉声明的载荷会被行为腿判红():
    """负控的负控：把 with_degradation 摘掉 ⇒ 键直接没了，行为腿当场红（⛔ 静默通过）。"""
    out = adm_linkage.with_degradation({"ok": True}, [])
    assert out["degraded"] is False and out["degraded_scans"] == []
    assert "degraded_scans" in out  # 恒在：⛔ 只在出事时才有这一格


# ── 出货文案面的「怎么读」锁 ────────────────────────────────────────────────
# 裁定 §一 硬前置：声明键必须是**必读**键，否则「甲只是把假读数从无标记变成有标记但没人读」。
# 契约表那一栏归 DCD、DB 读侧归 DB（本席⛔ 替对端报已落），但**本仓自己出货的两面文案**是
# 本席唯一可控的阅读面：SKILL.md 会连同工具面一起注入给模型，user_manual.js 是人读的那份。
# 判例（本仓 §五十三 那族的教训）：改一处留两处＝模型照技能包读照样把 null 当 0。
PROSE_SURFACES = {
    "skills_bundle/insight/SKILL.md": os.path.join(
        _SRC, "memory_agent", "skills_bundle", "insight", "SKILL.md"),
    "static/js/pages/user_manual.js": os.path.join(
        _SRC, "memory_agent", "static", "js", "pages", "user_manual.js"),
}
#: 形状六件套：两把键＋声明里的四个字段名（⛔ 只写键名不写读法——那是判例 2 的反面）
SHAPE_TOKENS = ("degraded", "degraded_scans", "site", "code", "fields", "reason")


@pytest.mark.parametrize("surface", sorted(PROSE_SURFACES))
def test_出货文案面点名降级声明的形状并给出null的读法(surface):
    with open(PROSE_SURFACES[surface], encoding="utf-8") as fh:
        text = fh.read()
    missing = [token for token in SHAPE_TOKENS if token not in text]
    assert not missing, "%s 没点名降级声明的形状：%s" % (surface, missing)
    assert "没扫" in text, "%s 说了键名却没说 null 的含义是「没扫」" % surface
    assert ("不得" in text) or ("不等于" in text), (
        "%s 说了「没扫」却没禁止把 null 读成 0／空／干净——那与没给等价（判例 2）" % surface)


# ── 第四、第五枚阅读面：随**每一次调用**出货的工具面 ──────────────────────────
# 两面人读文案之外，模型真正读到的是 handler docstring 与 ToolSpec 的 summary/description/pitfall
# （同时随 MCP schema 与 caps 出货）。本机 mcp SDK 版本不匹配时 `mcp_server` 导不进来 ⇒ 一律
# AST 读源码、⛔ import（同 `test_vma_promised_keys_runtime_contract.py` 的理由）。
_MCP_SOURCE = os.path.join(_SRC, "memory_agent", "mcp_server.py")
_SPEC_SOURCE = os.path.join(_SRC, "memory_agent", "tool_schema.py")


def _read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _handler_doc(func_name):
    tree = ast.parse(_read_text(_MCP_SOURCE), filename=_MCP_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            doc = ast.get_docstring(node)
            assert doc, "%s 没有 docstring，工具描述对客户端是空的" % func_name
            return doc
    raise AssertionError("mcp_server.py 里没有 handler %r" % func_name)


def _spec_field(tool_name, field):
    tree = ast.parse(_read_text(_SPEC_SOURCE), filename=_SPEC_SOURCE)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "ToolSpec"):
            kw = {k.arg: k.value for k in node.keywords}
            if isinstance(kw.get("name"), ast.Constant) and kw["name"].value == tool_name:
                value = kw.get(field)
                return value.value if isinstance(value, ast.Constant) else ""
    raise AssertionError("tool_schema.py 里没有 ToolSpec(name=%r)" % tool_name)


# 载荷里**真的**带这两把键的工具面（`with_degradation()` 现读调用点：merge_semantic_memory
# 六处 return／arena.evaluate_creativity 单 return／insights_legacy.device_health 单 return）。
# `add_semantic_memory` 只在 dry_run=False 走合并管线时才是载体，dry_run 自检路径⛔ 带 ⇒
# 文案必须写明这层条件，⛔ 对它说"恒带"。
TOOL_SURFACES = {
    "mcp_server.py:get_device_health(docstring)":
        lambda: _handler_doc("get_device_health"),
    "tool_schema.py:evaluate_creativity(pitfall)":
        lambda: _spec_field("evaluate_creativity", "pitfall"),
    "tool_schema.py:add_semantic_memory(pitfall)":
        lambda: _spec_field("add_semantic_memory", "pitfall"),
}


@pytest.mark.parametrize("surface", sorted(TOOL_SURFACES))
def test_tool_face_names_the_degradation_shape_and_the_null_reading(surface):
    text = TOOL_SURFACES[surface]()
    missing = [token for token in SHAPE_TOKENS if token not in text]
    assert not missing, "%s 没点名降级声明的形状：%s" % (surface, missing)
    assert "没扫" in text, "%s 说了键名却没说 null 的含义是「没扫」" % surface
    assert ("⛔" in text) or ("不得" in text), (
        "%s 说了「没扫」却没禁止把 null 读成 0／空／干净" % surface)


# ★ 本批真正的病：上一轮写两面文案时把工具名写错了——`merge_agent_memory` 根本⛔ 是注册名，
# 而 `list_device_health` 那面⛔ 带这两把键却被列进"恒带"清单。形状词表全命中、腿照样绿 ⇒
# **形状锁吃不到工具名真假**。下面两条是把这一族钉死的反向锁。
CARRIER_TOOLS = ("get_device_health", "evaluate_creativity", "add_semantic_memory")
# 每面**该**点到哪几枚载体：人读手册里根本没有竞技场条目 ⇒ 那一面⛔ 适用 evaluate_creativity，
# 为它新造一节反而是一条凭空承诺（台账 §五十二 记着这一判断）。
SURFACE_CARRIERS = {
    "skills_bundle/insight/SKILL.md": CARRIER_TOOLS,
    "static/js/pages/user_manual.js": ("get_device_health", "add_semantic_memory"),
}
NOT_CARRIERS = ("list_device_health", "merge_agent_memory")
# 段落里允许出现的非工具名（两把键的形状词、条件词与取值词）
SHAPE_ALLOWLIST = set(SHAPE_TOKENS) | {"dry_run", "null"}


def _degraded_block(path, anchor):
    """从锚句起取这段"降级必读"的连续行（遇新条目／模板串收尾即止）。"""
    lines = _read_text(path).splitlines()
    idx = next(i for i, ln in enumerate(lines) if anchor in ln)
    block = [lines[idx]]
    for ln in lines[idx + 1:idx + 12]:
        if ln.startswith("- ") or "`," in ln or ln.startswith("  }"):
            break
        block.append(ln)
    return "\n".join(block)


def test_prose_only_names_registered_tools():
    """SKILL.md 降级段里每个反引号 snake 词，要么是键名／条件词，要么必须是注册工具名。

    注册口径取 `TOOL_SPECS`（全 95 枚）而⛔ `TOOL_NAMES`（92 枚＝只 MCP 暴露面）：
    `evaluate_creativity` 的 `expose=("arena",)`，拿后者当"注册名"会把**真名**判成幻觉
    ——本腿首跑正是被这个混用打红的，两把口径差 3 枚。
    """
    from memory_agent.tool_schema import TOOL_SPECS

    registered = {spec.name for spec in TOOL_SPECS}
    block = _degraded_block(
        PROSE_SURFACES["skills_bundle/insight/SKILL.md"], "降级声明是必读")
    cited = re.findall(r"`([a-z][a-z0-9_]{2,})`", block)
    assert cited, "降级段一个反引号词都没有，取法失效"
    invented = [t for t in cited if t not in SHAPE_ALLOWLIST and t not in registered]
    assert not invented, "SKILL.md 降级段点名了⛔ 注册的工具名：%s" % (invented,)
    assert len(registered) < len(TOOL_SPECS) + 1, "注册名有重名，两把口径都会失真"


@pytest.mark.parametrize("surface,anchor", [
    ("skills_bundle/insight/SKILL.md", "降级声明是必读"),
    ("static/js/pages/user_manual.js", "降级时怎么读"),
])
def test_prose_does_not_claim_degradation_on_non_carrying_faces(surface, anchor):
    """⛔ 载体面只许出现在否定句里；旧假名 ⛔ 再现；真载体在本面适用的清单里点名齐。"""
    block = _degraded_block(PROSE_SURFACES[surface], anchor)
    assert "merge_agent_memory" not in block, (
        "%s 还在点名一枚⛔ 存在的工具（R1 的注册名是 add_semantic_memory）" % surface)
    for line in block.splitlines():
        if "list_device_health" in line:
            assert "⛔" in line, (
                "%s 把⛔ 载体面 list_device_health 写成了带降级声明的那一面" % surface)
    for tool in SURFACE_CARRIERS[surface]:
        assert tool in block, "%s 的降级段没点名真载体 %r" % (surface, tool)
