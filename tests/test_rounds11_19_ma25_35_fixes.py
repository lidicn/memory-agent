"""2期 第十一轮~第十九轮：MA-25 / 26 / 27 / 28 / 30 / 31 / 33 / 35 的回归锁（任务表 #85）。

这一批不是越权也不是崩溃，全都属于同一族：**声明与实现不是一条线**——
docstring/ToolSpec 承诺了自增、校验、真源同步、周期清理、失败自愈，实现里没有。
每条锁都对着报告「回归验证清单」里那一行写，能本机跑的绝不留给容器。

不在本文件的三条（MA-29 / MA-32 / MA-34）是产品口径选择，已随 20261008 回执 §四 呈 DCD，
不自办；它们落码时应另开一档，别把这条文件的判据当现成答案。
"""
import ast
import asyncio
import inspect
import json
import os
import sys
import tempfile
import types

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from starlette.requests import Request  # noqa: E402

from memory_agent import llm_client, runtime as runtime_mod  # noqa: E402
from memory_agent import mcp_server as ms  # noqa: E402
from memory_agent.agent_memory import AgentMemoryService  # noqa: E402
from memory_agent.api import nr_routes  # noqa: E402
from memory_agent.runtime import AppRuntime  # noqa: E402
from memory_agent.signal_learning import EXCLUSION_TYPES, SignalLearningService  # noqa: E402


# ── MA-25：热更新整表重建必须关掉旧 provider ────────────────────────────────

class _RecordingProvider:
    """替身 LLMProvider：只记「有没有被关」。真 provider 会建 httpx 客户端，本机没装依赖。"""

    instances: list = []

    def __init__(self, backend, config=None):
        self.backend = backend
        self.name = backend.get("name", "")
        self.closed = 0
        _RecordingProvider.instances.append(self)

    def close(self):
        self.closed += 1


def _router_with(monkeypatch, names):
    monkeypatch.setattr(llm_client, "LLMProvider", _RecordingProvider)
    cfg = types.SimpleNamespace(llm_backends=[{"name": n, "enabled": True} for n in names])
    return llm_client.LLMRouter(cfg)


def test_reconfigure_closes_the_providers_it_is_replacing(monkeypatch):
    """改一次配置漏一批 httpx.AsyncClient：旧对象没人 close，连接池随引用消失而悬着。"""
    _RecordingProvider.instances = []
    router = _router_with(monkeypatch, ["A", "B"])
    first_two = list(_RecordingProvider.instances)
    assert len(first_two) == 2

    cfg2 = types.SimpleNamespace(
        llm_backends=[{"name": "C", "enabled": True}, {"name": "D", "enabled": True}]
    )
    router.reconfigure(cfg2)

    assert [p.closed for p in first_two] == [1, 1], (
        f"被替换掉的 provider 必须逐个 close，实得 {first_two}"
    )
    assert len(_RecordingProvider.instances) == 4
    # 反例守卫：新表自己不能被顺手关掉（否则换了配置就没人能用）
    assert [p.closed for p in _RecordingProvider.instances[2:]] == [0, 0]


def test_reconfigure_keeps_new_provider_table_usable(monkeypatch):
    """反例锁：关旧表不能把新表也一起关成空池。"""
    _RecordingProvider.instances = []
    router = _router_with(monkeypatch, ["A"])
    router.reconfigure(types.SimpleNamespace(llm_backends=[{"name": "B", "enabled": True}]))
    assert router.primary_backend is _RecordingProvider.instances[1]


# ── MA-26：save_skill 的版本自增必须以盘上那份为基数 ─────────────────────────

def _tools_by_name():
    server = getattr(ms, "mcp_server", None)
    if server is None:
        return None
    manager = getattr(server, "_tool_manager", None)
    tools = getattr(manager, "_tools", None) if manager else None
    return tools if isinstance(tools, dict) else None


def _call(tool, **kw):
    """save_skill 是同步 `def` 注册的（mcp_server.py:2672）；协程版工具则必须 await。
    用 asyncio.run 调同步函数会直接把返回值当协程炸掉，所以按可等待性分流。"""
    res = tool.fn(**kw)
    return asyncio.run(res) if inspect.isawaitable(res) else res


def test_save_skill_version_never_regresses(monkeypatch):
    """报告回归清单 #1/#2：连续两次不带 frontmatter ⇒ 1→2；盘上 v5 + 无版本号入参 ⇒ 6。"""
    tools = _tools_by_name()
    if tools is None:
        pytest.skip("本机 mcp SDK 过旧，取不到已注册工具函数（容器内有，权威门会跑）")
    tool = tools.get("save_skill")
    assert tool is not None, "save_skill 不在已注册工具表里"

    with tempfile.TemporaryDirectory() as tmp:
        rt = types.SimpleNamespace(
            config=types.SimpleNamespace(skills_dir=tmp, tz_offset_hours=8.0)
        )
        monkeypatch.setattr(ms, "get_runtime", lambda: rt)
        path = os.path.join(tmp, "sweep", "SKILL.md")

        r1 = _call(tool, name="sweep", content="# 第一版正文")
        assert r1.get("ok") is True, r1
        assert r1["version"] == 1, f"首次保存应为 v1，实得 {r1}"

        r2 = _call(tool, name="sweep", content="# 第二版正文")
        assert r2["version"] == 2, f"第二次应为 v2，实得 {r2}（版本没自增）"

        # 盘上已到 v5，入参不带 frontmatter ⇒ 不能被覆盖回 v1
        with open(path, "w", encoding="utf-8") as f:
            f.write("---\nname: sweep\ndescription: d\ncategory: insight\n"
                    "version: 5\nupdated_at: 2026-10-01T00:00:00\n---\n\n旧版\n")
        r3 = _call(tool, name="sweep", content="# 新正文，没有 frontmatter")
        assert r3["version"] == 6, (
            f"盘上 v5 时保存应得 v6，实得 {r3} —— 版本倒退会让消费方永远收不到更新"
        )
        with open(path, encoding="utf-8") as f:
            assert "version: 6" in f.read()

        # 入参版本号比盘上大时以入参为基数（两条来源取 max，不是二选一）
        r4 = _call(tool, name="sweep", content="---\nversion: 40\n---\n\n更高\n")
        assert r4["version"] == 41, r4


# ── MA-27：响应不能把自己说成跨成员全量 ────────────────────────────────────

class _MemStore:
    def __init__(self):
        self.calls = []

    def list_agent_memories(self, state="all", source="", limit=500, member_id="",
                            exact_member=False):
        self.calls.append({"state": state, "source": source, "member_id": member_id,
                           "exact_member": exact_member})
        return []


def _svc(store):
    cfg = types.SimpleNamespace(agent_trust_step=0.2)
    return AgentMemoryService(cfg, store, None)


def test_list_agent_memories_public档不谎称_all():
    store = _MemStore()
    res = _svc(store).list_agent_memories()
    assert res["member_id"] == "", f"不点名成员时不能回显 all，实得 {res['member_id']!r}"
    assert res["member_scope"] == "public_only"
    assert store.calls[0]["exact_member"] is True
    assert store.calls[0]["member_id"] == ""


def test_list_agent_memories_named_member_echoes_member():
    store = _MemStore()
    res = _svc(store).list_agent_memories(member_id="member:abc")
    assert res["member_id"] == "member:abc"
    assert res["member_scope"] == "member"
    assert store.calls[0]["member_id"] == "member:abc"


# ── MA-28：dry_run 必须真的校验，且与写入走同一串判据 ───────────────────────

class _SigStore:
    def __init__(self):
        self.writes = []

    def upsert_signal_exclusion(self, **kw):
        self.writes.append(kw)
        return "excl-1"


class _SigMem:
    def __init__(self):
        self.adds = []

    def add_semantic_memory(self, **kw):
        self.adds.append(kw)
        return {"ok": True, "memory_id": "m-1"}


def _sig():
    store, mem = _SigStore(), _SigMem()
    return SignalLearningService(store, mem), store, mem


@pytest.mark.parametrize("bad_type", ["no_such_type", "hard", "isautomation", "exclude_x"])
def test_dry_run_rejects_out_of_enum_exclusion_type(bad_type):
    svc, store, mem = _sig()
    res = svc.teach_signal("light.x", exclusion_type=bad_type, dry_run=True)
    assert res.get("ok") is False, f"越界 exclusion_type 必须判红，实得 {res}"
    assert res.get("code") == 400
    assert not store.writes and not mem.adds


def test_exclusion_type_is_case_and_space_normalized_before_the_enum_check():
    """归一化是有意为之的口径（大小写/首尾空格不算越界），单独立一档免得被误当漏洞。"""
    svc, store, mem = _sig()
    res = svc.teach_signal("light.x", exclusion_type=" Exclude ", dry_run=True)
    assert res.get("ok") is True, res
    assert res["checked"] and not store.writes


def test_dry_run_accepts_a_good_hard_and_writes_nothing():
    svc, store, mem = _sig()
    res = svc.teach_signal("light.x", scope="wake_anchor", reason="自动化",
                           dry_run=True)
    assert res.get("ok") is True and res.get("dry_run") is True, res
    assert set(res["checked"]) == {"entity_id", "kind", "exclusion_type"}, res
    assert "message" in res
    assert not store.writes and not mem.adds, "dry_run 不落任何一行"


def test_dry_run_soft_requires_text():
    svc, store, mem = _sig()
    res = svc.teach_signal("light.x", kind="soft", dry_run=True)
    assert res.get("ok") is False and "text" in str(res.get("error")), res
    assert not mem.adds


def test_real_write_shares_the_same_judgement():
    """反例锁：校验不能只长在 dry_run 那一支——真写入也得挡，否则探边界探不出真实结果。"""
    svc, store, mem = _sig()
    bad = svc.teach_signal("light.x", exclusion_type="no_such_type")
    assert bad.get("ok") is False, bad
    assert not store.writes

    ok = svc.teach_signal("light.x", scope="wake_anchor", exclusion_type="is_automation")
    assert ok.get("ok") is True, ok
    assert store.writes and store.writes[0]["exclusion_type"] == "is_automation"


def test_exclusion_type_enum_matches_the_toolspec_declaration():
    """声明与实现同源：ToolSpec 的 enum 改了、这里没跟着改 ⇒ 当场判红。"""
    from memory_agent import tool_schema

    spec = tool_schema.SPEC_BY_NAME["teach_signal"]
    declared = next(p.enum for p in spec.params if p.name == "exclusion_type")
    assert list(declared) == list(EXCLUSION_TYPES), (declared, EXCLUSION_TYPES)


def _func_node(source, name):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} 不在源码里")


def test_mcp_facade_no_longer_short_circuits_dry_run():
    """门面原来有一句 `if dry_run: return {"ok": True, ... 参数校验通过}` —— 假回执的来源。"""
    with open(os.path.join(_SRC, "memory_agent", "mcp_server.py"), encoding="utf-8") as f:
        src = f.read()
    fn = _func_node(src, "teach_signal")
    literals = [n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not any("参数校验通过" in s for s in literals), literals
    # dry_run 必须一路带到实现本体
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    assert any(
        any(getattr(a, "id", None) == "dry_run" or getattr(a, "arg", None) == "dry_run"
            for a in list(c.args) + list(c.keywords))
        for c in calls
    ), "teach_signal 门面没把 dry_run 传进本体"


# ── MA-30：零实现的端点不再声称"已执行" ─────────────────────────────────────

def _drive_nr(handler, body, monkeypatch):
    async def _json_body(request):
        return body
    monkeypatch.setattr(nr_routes, "json_body", _json_body)
    monkeypatch.setattr(
        nr_routes, "runtime",
        lambda request=None: types.SimpleNamespace(patterns=None, store=None))
    resp = asyncio.run(handler(Request(
        {"type": "http", "method": "POST", "path": "/api/nr/execute-action",
         "query_string": b"", "headers": []})))
    return resp.status_code, json.loads(resp.body)


def test_execute_action_returns_not_implemented(monkeypatch):
    """报告回归清单：不再返回"动作已执行"。留 200 假成功就是让上游以为设备被动过了。"""
    code, payload = _drive_nr(
        nr_routes.nr_execute_action, {"type": "turn_on", "target": "light.x"}, monkeypatch)
    assert code == 501, (code, payload)
    text = json.dumps(payload, ensure_ascii=False)
    assert "动作已执行" not in text, text
    assert "未实际执行" in text, text


def test_execute_action_route_is_still_registered():
    """路径不变：兼容层红线是"路径一律不得改"，这里只改回执真假。"""
    paths = [r.path for r in nr_routes.ROUTES]
    assert "/api/nr/execute-action" in paths
    assert "/api/analyze/water_purifier" in paths


# ── MA-31：日记任务抛了要能重跑，不是接住了就算完 ────────────────────────────

class _SleepStub:
    """按次序放行前 n 次 sleep，第 n+1 次抛 CancelledError 让循环收尾。"""

    def __init__(self, allow):
        self.allow = allow
        self.seen = []

    async def __call__(self, delay, *a, **k):
        self.seen.append(delay)
        if len(self.seen) > self.allow:
            raise asyncio.CancelledError
        return None


def test_diary_wrapper_restarts_after_round_raises(monkeypatch):
    rounds = {"n": 0}

    async def flaky_round(self):
        rounds["n"] += 1
        if rounds["n"] == 1:
            raise RuntimeError("墙钟读取炸了")

    fake = types.SimpleNamespace(_self_diary_round=lambda: flaky_round(None))
    stub = _SleepStub(allow=1)
    monkeypatch.setattr(runtime_mod.asyncio, "sleep", stub)

    asyncio.run(AppRuntime._periodic_self_diary(fake))

    assert rounds["n"] == 2, f"第一轮异常后必须重跑，实际轮次 {rounds['n']}"
    assert stub.seen[:1] == [60], f"重启前要退避，实得 {stub.seen}"


def test_diary_wrapper_exits_cleanly_on_success(monkeypatch):
    rounds = {"n": 0}

    async def good_round(self):
        rounds["n"] += 1

    fake = types.SimpleNamespace(_self_diary_round=lambda: good_round(None))
    monkeypatch.setattr(runtime_mod.asyncio, "sleep", _SleepStub(allow=0))
    asyncio.run(AppRuntime._periodic_self_diary(fake))
    assert rounds["n"] == 1


def test_self_diary_round_reraises_to_the_wrapper():
    """旧结构里外层 except 只 print 就 return ⇒ 停摆；现在必须把异常交给外壳。

    口径只罩**函数顶层 try** 的兜底 handler：内层 per-iteration「生成失败」那一格
    按语义就该吞掉继续等下一天，把 raise 塞进去反而会让一轮失败终止整个循环。
    """
    with open(os.path.join(_SRC, "memory_agent", "runtime.py"), encoding="utf-8") as f:
        fn = _func_node(f.read(), "_self_diary_round")
    top_trials = [n for n in fn.body if isinstance(n, ast.Try)]
    assert top_trials, "_self_diary_round 的函数体第一层应有 try"
    handlers = [h for t in top_trials for h in t.handlers]
    assert handlers, "顶层 try 应有兜底 handler"
    for h in handlers:
        assert any(isinstance(s, ast.Raise) for s in h.body), (
            "顶层 handler 接住之后必须 raise，否则外壳看不见、任务就此结束（MA-31）"
        )


# ── MA-33：内置技能是真源，版本落后就必须同步进来 ────────────────────────────

def _bundle(tmp, version):
    d = os.path.join(tmp, "bundle", "sweep")
    os.makedirs(d)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: sweep\ndescription: 内置\nversion: %s\n---\n\n内置正文\n" % version)
    return os.path.join(tmp, "bundle")


def _seed(monkeypatch, bundle_dir, disk_content):
    skills = os.path.join(bundle_dir, "..", "skills")
    skills = os.path.abspath(skills)
    os.makedirs(os.path.join(skills, "sweep"), exist_ok=True)
    if disk_content is not None:
        with open(os.path.join(skills, "sweep", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(disk_content)
    monkeypatch.setattr(ms, "BUNDLED_SKILLS_DIR", bundle_dir)
    rt = types.SimpleNamespace(config=types.SimpleNamespace(skills_dir=skills))
    n = ms.seed_builtin_skills(rt)
    path = os.path.join(skills, "sweep", "SKILL.md")
    with open(path, encoding="utf-8") as f:
        return n, f.read()


def test_seed_upgrades_when_disk_lags_behind(monkeypatch):
    """报告版本矩阵：磁盘 v1、bundled v5 ⇒ 磁盘变 v5。"""
    with tempfile.TemporaryDirectory() as tmp:
        bundle = _bundle(tmp, 5)
        n, text = _seed(monkeypatch, bundle,
                        "---\nname: sweep\nversion: 1\n---\n\n旧版\n")
        assert n == 1, n
        assert "version: 5" in text and "内置正文" in text, text


def test_seed_keeps_a_higher_agent_iterated_version(monkeypatch):
    """声明①「不覆盖更高版本」：磁盘 v9 > 内置 v5 ⇒ 不写。"""
    with tempfile.TemporaryDirectory() as tmp:
        bundle = _bundle(tmp, 5)
        n, text = _seed(monkeypatch, bundle,
                        "---\nname: sweep\nversion: 9\n---\n\nAgent 迭代过\n")
        assert n == 0, n
        assert "version: 9" in text and "Agent 迭代过" in text, text


def test_seed_repairs_an_unreadable_disk_copy(monkeypatch):
    """盘上是空壳/被截断（解析不出版本）⇒ 按 0 处理，让真源把它修复回来。"""
    with tempfile.TemporaryDirectory() as tmp:
        bundle = _bundle(tmp, 5)
        n, text = _seed(monkeypatch, bundle, "---\nname: sweep\nversion: 不是数字\n")
        assert n == 1, n
        assert "version: 5" in text, text


def test_seed_writes_when_target_absent(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        bundle = _bundle(tmp, 5)
        skills = os.path.abspath(os.path.join(tmp, "skills"))
        monkeypatch.setattr(ms, "BUNDLED_SKILLS_DIR", bundle)
        rt = types.SimpleNamespace(config=types.SimpleNamespace(skills_dir=skills))
        assert ms.seed_builtin_skills(rt) == 1
        with open(os.path.join(skills, "sweep", "SKILL.md"), encoding="utf-8") as f:
            assert "version: 5" in f.read()


def test_seed_is_idempotent_on_second_run(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        bundle = _bundle(tmp, 5)
        n, _ = _seed(monkeypatch, bundle, None)
        assert n == 1, n
        # 第二遍：盘上已是 v5 ⇒ 不再写
        skills = os.path.abspath(os.path.join(tmp, "skills"))
        rt = types.SimpleNamespace(config=types.SimpleNamespace(skills_dir=skills))
        assert ms.seed_builtin_skills(rt) == 0


# ── MA-35：保留期清理是周期任务，不是开机一锤子 ──────────────────────────────

class _PurgeStore:
    def __init__(self, fail_first=False):
        self.calls = []
        self.fail_first = fail_first

    def purge_old(self, days, **kw):
        self.calls.append(("events", days))
        if self.fail_first and len([c for c in self.calls if c[0] == "events"]) == 1:
            raise RuntimeError("disk busy")
        return 10

    def purge_idempotency(self):
        self.calls.append(("idempotency", None))
        return 1

    def purge_mcp_audit(self, keep_days):
        self.calls.append(("mcp_audit", keep_days))
        return 2


def _cleanup_rt(store, interval=3600):
    return types.SimpleNamespace(
        config=types.SimpleNamespace(data_retention_days=90,
                                     data_retention_interval_seconds=interval),
        store=store,
    )


def _events_rounds(store):
    return len([c for c in store.calls if c[0] == "events"])


def test_retention_cleanup_runs_more_than_once(monkeypatch):
    """长跑不重启的容器里，events 跨度必须仍然 ≈ 保留期 —— 一轮就结束等于策略失效。"""
    store = _PurgeStore()
    stub = _SleepStub(allow=1)
    monkeypatch.setattr(runtime_mod.asyncio, "sleep", stub)
    asyncio.run(AppRuntime._run_retention_cleanup(_cleanup_rt(store)))

    assert _events_rounds(store) == 2, store.calls
    assert ("mcp_audit", 30) in store.calls
    assert stub.seen == [3600, 3600], stub.seen


def test_retention_cleanup_retries_after_failure(monkeypatch):
    """清理失败只损失这一轮：退避后下一轮接着删（报告回归清单 #4）。"""
    store = _PurgeStore(fail_first=True)
    stub = _SleepStub(allow=1)
    monkeypatch.setattr(runtime_mod.asyncio, "sleep", stub)
    asyncio.run(AppRuntime._run_retention_cleanup(_cleanup_rt(store, interval=7200)))

    assert _events_rounds(store) == 2, store.calls
    assert stub.seen[0] == 600, f"失败轮应走短退避而不是等满一个周期，实得 {stub.seen}"


def test_retention_task_is_registered_at_startup():
    """调用点形状锁：startup 注册的是常驻任务本体（不是跑一次就返回的协程包装）。"""
    with open(os.path.join(_SRC, "memory_agent", "runtime.py"), encoding="utf-8") as f:
        src = f.read()
    fn = _func_node(src, "_run_retention_cleanup")
    assert any(isinstance(n, ast.While) for n in ast.walk(fn)), (
        "_run_retention_cleanup 必须是 while 常驻循环（MA-35）"
    )
    startup = _func_node(src, "startup")
    assert any(
        isinstance(n, ast.Attribute) and n.attr == "_run_retention_cleanup"
        for n in ast.walk(startup)
    ), "startup 里应能看到这个任务名"
