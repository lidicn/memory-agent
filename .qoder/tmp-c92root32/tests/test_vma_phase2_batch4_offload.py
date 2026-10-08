"""MA-24 第四批回归锁：路由层「同步方法直待在协程里」清零 + 量具自咬。

二期第十轮（MA-24）口径：全仓 172 个碰 store/runtime 的 async handler，改前卸载 96 / 未卸载 76（56%）。
本批收口的是最后那批点名不到的形状——**路由直调同步服务方法**（不是裸 SQL 动词），
共 32 处：`agent_memory_routes` 10 个 handler、`member_routes` 10 个（含两个"两步取数合进一次跳转"
的嵌套 def）、`llm_routes` 5 处问答缓存读写、`signal_routes` 3 个、`mcp_routes.list_audit` 1 个、
`vision_routes.events_face` 1 个。

为什么这是缺陷而不是风格问题：`Store` 用全局 RLock、`AuthMiddleware` 是纯 ASGI 中间件，
单次 1 ms 的 SQLite 调用按 lesson 118 不足以立项；**风险在高并发叠加**（第十轮实测 60 并发
把 tick 从 95 打到 1）。所以本文件既锁落点（AST），也锁"60 并发时心跳还在走"（真响）。

量具（`scripts/scan_unloaded_async_io.py`）自己的两条教训也必须锁住，否则 HITS=0 可能是尺子坏了：
1. store 面**按身体判、不按变量名判**——`MCPTokenStore` 挂在叫 `store` 的变量上但纯内存
   （`compare_digest` + 节流写），第一轮把它当阻塞面哭狼，我照着读数去卸载 `acp_auth`/`mcp_auth`
   的 token 校验，等于给每跳鉴权加一次线程往返，还推翻了仓内 #51 已登记的「判定不改」。
   那两条腿已回退（备份在 `%TEMP%/ma24_auth_offload.reverted.patch`），本文件第 6 组把它钉死。
2. `HITS=0` 必须能被反例咬红：第 4 组在 **tmp 副本树**里把已卸载的腿改回直调，
   subsys 腿只在 `--transitive` 档响、store 腿在一层档就响，控制腿（什么都不改）必须 0/0。

同批顺带自撞出的两处入参现行（`_num` 口径）也在这里锁：`?limit=abc` 与 `confidence=abc`
改前都是未捕获 ``ValueError`` → Starlette 500。
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
from urllib.parse import quote

import pytest
from starlette.requests import Request

import memory_agent
from memory_agent.api import agent_memory_routes as amr
from memory_agent.api import llm_routes as lr
from memory_agent.api import mcp_routes as mrr
from memory_agent.api import member_routes as mbr
from memory_agent.api import signal_routes as sgr
from memory_agent.api import vision_routes as vr
from memory_agent.store import Store

SRC_ROOT = pathlib.Path(memory_agent.__file__).parent
_SCRIPTS = SRC_ROOT.parents[1] / "scripts"
if not _SCRIPTS.is_dir():                      # 包装位置不同（容器 /tmp 快照）时往上找
    for _up in range(1, 5):
        cand = SRC_ROOT.parents[_up] / "scripts"
        if (cand / "scan_unloaded_async_io.py").is_file():
            _SCRIPTS = cand
            break
sys.path.insert(0, str(_SCRIPTS))
import scan_unloaded_async_io as gauge          # noqa: E402

GAUGE_PATH = _SCRIPTS / "scan_unloaded_async_io.py"


# ── harness ───────────────────────────────────────────────────────────────

def _request(*, method="GET", query="", body=None, rt=None,
             user=None, path_params=None, headers=None):
    app = types.SimpleNamespace(state=types.SimpleNamespace(runtime=rt))
    scope = {"type": "http", "method": method,
             "path": "/api/x", "query_string": query.lstrip("?").lstrip("&").encode("utf-8"),
             "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
             "app": app}
    if user is not None:
        scope["state"] = {"user": user}
    if path_params is not None:
        scope["path_params"] = path_params

    async def receive():
        return {"type": "http.request",
                "body": json.dumps(body or {}).encode("utf-8"), "more_body": False}

    return Request(scope, receive)


_DEFAULTS = {
    "list_members": lambda: [{"id": "m1", "name": "甲"}],
    "list_mcp_audit": lambda: [],
    "list_negative_feedback": lambda: [],
    "retrieve": lambda: [],
    "clear_answer_cache": lambda: 3,
    "list_rules": lambda: {"hard": [], "soft": []},
    "get_answer_cache": lambda: None,
    "recent_presence": lambda: [],
}


class _Recorder:
    """把"谁在哪个线程被调用"记下来；返回值按 `_DEFAULTS` 给形状，其余给 ``{"ok": True}``。"""

    def __init__(self, label, calls, results=None):
        self.label = label
        self.calls = calls
        self.results = results or {}

    def __getattr__(self, name):
        def _call(*a, **k):
            self.calls.append((self.label, name, threading.current_thread().name, a, k))
            if name in self.results:
                v = self.results[name]
                return v(*a, **k) if callable(v) else v
            d = _DEFAULTS.get(name)
            if d is not None:
                return d()
            return {"ok": True}
        return _call


def _rt(**over):
    calls: list[tuple] = []
    ns = {"calls": calls}
    for key in ("store", "agent_memory", "signal_learning", "vision", "insights", "analysis"):
        ns[key] = _Recorder(key, calls)
    ns["config"] = types.SimpleNamespace(vision_device_token="devtok",
                                         process_mining_min_variant_support=3)
    ns.update(over)
    return types.SimpleNamespace(**ns), calls


def _run(coro):
    return asyncio.run(coro)


# ── 1. 量具自证：八份样本，正例必须响、反例必须安静 ──────────────────────

def test_gauge_self_test_passes(capsys):
    assert gauge.self_test() == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert "SELFTEST OK" in out
    # 身体门的两条样本是本批自己踩出来的，少了它们就等于回滚这次修正
    assert "pos_body=1" in out and "neg_body=0" in out


# ── 2. 全仓零命中（一层口径 + 展开口径）──────────────────────────────────

def test_repo_zero_unloaded_direct():
    hits = gauge.scan_paths([str(SRC_ROOT)])
    assert hits == [], "一层口径命中：\n" + "\n".join(hits)


def test_repo_zero_unloaded_transitive():
    hits = gauge.scan_paths([str(SRC_ROOT)], transitive=True)
    assert hits == [], "展开口径命中：\n" + "\n".join(hits)


# ── 3. 落点锁：32 处调用必须在 to_thread 的参数位上 ──────────────────────

def _chain(n):
    if isinstance(n, ast.Attribute):
        return _chain(n.value) + [n.attr]
    if isinstance(n, ast.Name):
        return [n.id]
    if isinstance(n, ast.Call):
        return _chain(n.func) + ["()"]
    return ["?"]


def _is_to_thread(node) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "to_thread")


def _offloaded_ids(fn) -> set:
    """`to_thread` 的第一参数位（以及整体进线程的嵌套 def）里的所有节点。

    这批改动的形状是 `asyncio.to_thread(rt.store.list_members, …)`——方法名是**引用**、
    不是调用，所以按"调用点"找会把每一处都当成不存在（首跑就是 27 条全红）。
    """
    ids: set[int] = set()
    nested = {v.name: v for v in ast.walk(fn) if isinstance(v, ast.FunctionDef)}
    for sub in ast.walk(fn):
        if not _is_to_thread(sub) or not sub.args:
            continue
        target = sub.args[0]
        # 传引用（本批的统一写法）与传调用 `to_thread(store.x(a))` 都算离开循环：整棵子树收进来
        for node in ast.walk(target):
            ids.add(id(node))
        if isinstance(target, ast.Name) and target.id in nested:
            for node in ast.walk(nested[target.id]):
                ids.add(id(node))
    return ids


def _handler(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"找不到 async handler {name}")


LANDING = [
    # (模块, handler, 链尾两段)
    ("agent_memory_routes", "add_memory", ("agent_memory", "add_semantic_memory")),
    ("agent_memory_routes", "list_memories", ("agent_memory", "list_agent_memories")),
    ("agent_memory_routes", "memory_health", ("agent_memory", "health")),
    ("agent_memory_routes", "promote", ("agent_memory", "promote_memory")),
    ("agent_memory_routes", "revoke", ("agent_memory", "revoke_memory")),
    ("agent_memory_routes", "rollback", ("agent_memory", "rollback_agent_memory")),
    ("agent_memory_routes", "feedback", ("agent_memory", "feedback_memory")),
    ("agent_memory_routes", "negative_feedback", ("agent_memory", "list_negative_feedback")),
    ("agent_memory_routes", "sweep", ("agent_memory", "sweep_and_reconcile")),
    ("agent_memory_routes", "retrieve", ("agent_memory", "retrieve")),
    ("member_routes", "member_list", ("store", "list_members")),
    ("member_routes", "member_create", ("store", "create_member")),
    ("member_routes", "member_detail", ("store", "get_member")),
    ("member_routes", "member_delete", ("store", "delete_member")),
    ("member_routes", "member_rooms", ("store", "set_member_rooms")),
    ("member_routes", "member_devices", ("store", "set_member_devices")),
    ("member_routes", "member_tag_add", ("store", "add_member_tag")),
    ("member_routes", "member_tag_delete", ("store", "delete_member_tag")),
    ("member_routes", "member_appearance", ("store", "update_member")),
    ("member_routes", "member_appearance", ("store", "_normalize_appearance")),
    ("member_routes", "member_insight_feedback", ("store", "get_member")),
    ("member_routes", "member_insight_feedback", ("store", "member_insight_feedback")),
    ("llm_routes", "llm_ask", ("store", "get_answer_cache")),
    ("llm_routes", "llm_ask", ("store", "save_answer_cache")),
    ("llm_routes", "llm_cache_clear", ("store", "clear_answer_cache")),
    ("mcp_routes", "list_audit", ("store", "list_mcp_audit")),
    ("signal_routes", "list_rules", ("signal_learning", "list_rules")),
    ("signal_routes", "teach", ("signal_learning", "teach_signal")),
    ("signal_routes", "revoke", ("signal_learning", "revoke_exclusion")),
    ("vision_routes", "events_face", ("vision", "record_face_event")),
]

_MODULES = {"agent_memory_routes": amr, "member_routes": mbr, "llm_routes": lr,
            "mcp_routes": mrr, "signal_routes": sgr, "vision_routes": vr}


def _bare_sites(src: str, handler: str, tail) -> tuple:
    """返回 (该形状的出现次数, 没进线程的行号)。"""
    tree = ast.parse(src)
    fn = _handler(tree, handler)
    offloaded = _offloaded_ids(fn)
    found = [n for n in ast.walk(fn)
             if isinstance(n, (ast.Attribute, ast.Call)) and _chain(n)[-2:] == list(tail)]
    return len(found), [n.lineno for n in found if id(n) not in offloaded]


@pytest.mark.parametrize("mod,handler,tail", LANDING,
                         ids=[f"{m}.{h}.{'.'.join(t)}" for m, h, t in LANDING])
def test_batch4_callsite_is_offloaded(mod, handler, tail):
    src = pathlib.Path(_MODULES[mod].__file__).read_text(encoding="utf-8")
    n_found, bare = _bare_sites(src, handler, tail)
    assert n_found, f"{mod}.{handler} 里已经找不到 {'.'.join(tail)} 这一跳了"
    assert bare == [], f"{mod}.{handler} 的 {'.'.join(tail)} 又回到事件循环里了：行 {bare}"


def test_landing_lock_itself_bites():
    """落点锁的反例档：把一处改成直调，锁必须点名行号——否则"30 条全绿"只是空场。"""
    src = pathlib.Path(amr.__file__).read_text(encoding="utf-8")
    n_before, bare_before = _bare_sites(src, "list_memories",
                                        ("agent_memory", "list_agent_memories"))
    assert (n_before, bare_before) == (1, []), (n_before, bare_before)
    mutated = _swap(src, SUBSYS_PAIRS)
    n_after, bare_after = _bare_sites(mutated, "list_memories",
                                      ("agent_memory", "list_agent_memories"))
    assert n_after == 1, n_after
    assert bare_after, "改成直调之后，落点锁仍然说这一跳在 to_thread 里"


def test_landing_inventory_size_is_locked():
    """点名的落点清单不许悄悄变短。

    30 条 triple 覆盖 32 个调用点：`llm_ask` 的 `get_answer_cache` / `save_answer_cache`
    各有两处（du 档与 llm 档），同一 (handler, 链尾) 只登记一条——上面的用例对该 handler
    要求"这一形状的所有调用都在 to_thread 里"，所以合并不会漏放行。
    """
    assert len(LANDING) == 30, len(LANDING)


# ── 4. 量具自咬：副本树里改回直调必须报红，什么都不改必须 0 ───────────────

def _copy_tree(dest: pathlib.Path) -> pathlib.Path:
    tree = dest / "src" / "memory_agent"
    shutil.copytree(SRC_ROOT, tree, ignore=shutil.ignore_patterns("__pycache__"))
    return tree


def _swap(text: str, pairs) -> str:
    for old, new in pairs:
        assert text.count(old) == 1, f"锚点不唯一：{old!r} → {text.count(old)}"
        text = text.replace(old, new, 1)
    return text


def _unoffload(tree: pathlib.Path, rel: str, pairs) -> None:
    p = tree / rel
    raw = p.read_bytes()
    crlf = raw.count(b"\r\n")
    text = _swap(raw.decode("utf-8").replace("\r\n", "\n"), pairs)
    out = text.replace("\n", "\r\n").encode("utf-8") if crlf else text.encode("utf-8")
    compile(out, str(p), "exec")          # 改坏语法就别往副本里写
    p.write_bytes(out)


SUBSYS_PAIRS = [(
    "    result = await asyncio.to_thread(rt.agent_memory.list_agent_memories, state, source)",
    "    result = rt.agent_memory.list_agent_memories(state, source)",
)]
STORE_PAIRS = [(
    "    members = await asyncio.to_thread(runtime(request).store.list_members)",
    "    members = runtime(request).store.list_members()",
)]


def test_bite_control_leg_is_clean(tmp_path):
    tree = _copy_tree(tmp_path)
    assert gauge.scan_paths([str(tree)]) == []
    assert gauge.scan_paths([str(tree)], transitive=True) == []


def test_bite_subsys_leg_only_rings_in_transitive(tmp_path):
    """把已卸载的 subsys 腿改回直调：一层口径看不见跨函数，展开口径必须报 [subsys]。"""
    tree = _copy_tree(tmp_path)
    _unoffload(tree, "api/agent_memory_routes.py", SUBSYS_PAIRS)
    assert [h for h in gauge.scan_paths([str(tree)]) if "agent_memory_routes" in h] == []
    hits = [h for h in gauge.scan_paths([str(tree)], transitive=True)
            if "agent_memory_routes" in h]
    assert len(hits) == 1, hits
    assert "[subsys]" in hits[0], hits


def test_bite_store_leg_rings_in_direct_pass(tmp_path):
    """真 SQLite 调用（`store.list_members`）在**一层口径**就该响——
    身体门改成看方法体之后，这条不许被"纯内存"豁免吞掉。"""
    tree = _copy_tree(tmp_path)
    _unoffload(tree, "api/member_routes.py", STORE_PAIRS)
    hits = [h for h in gauge.scan_paths([str(tree)]) if "member_routes" in h]
    assert len(hits) == 1, hits
    assert "[store]" in hits[0], hits


def _run_gauge_cli(extra):
    return subprocess.run([sys.executable, str(GAUGE_PATH), *extra],
                          capture_output=True, text=True)


def test_gauge_cli_contract():
    """脚本口径（`python scripts/… --self-test` 退出码）也要一致，门禁走的就是这条。"""
    r = _run_gauge_cli(["--self-test"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SELFTEST OK" in r.stdout, r.stdout
    r2 = _run_gauge_cli([str(SRC_ROOT)])
    assert r2.returncode == 0, r2.stdout + r2.stderr
    r3 = _run_gauge_cli([str(SRC_ROOT), "--transitive"])
    assert r3.returncode == 0, r3.stdout + r3.stderr


# ── 5. 线程证据：这批 handler 的服务调用不落在 MainThread ─────────────────

def _threads(calls):
    return sorted({name for _, _, name, _, _ in calls})


def test_agent_memory_routes_run_off_loop():
    rt, calls = _rt()
    u = {"sub": "u"}
    _run(amr.add_memory(_request(method="POST", body={"text": "客厅灯坏了"}, rt=rt, user=u)))
    _run(amr.list_memories(_request(query="state=active", rt=rt, user=u)))
    _run(amr.memory_health(_request(rt=rt, user=u)))
    _run(amr.promote(_request(method="POST", body={"memory_id": "x"},
                              rt=rt, user={"sub": "u", "is_admin": True})))
    _run(amr.revoke(_request(method="POST", body={"memory_id": "x"}, rt=rt, user=u)))
    _run(amr.rollback(_request(method="POST", body={"session_id": "s"}, rt=rt, user=u)))
    _run(amr.feedback(_request(method="POST", body={"memory_id": "x", "useful": False,
                                                   "question": "今晚谁在家"}, rt=rt, user=u)))
    _run(amr.negative_feedback(_request(query="limit=20", rt=rt, user=u)))
    _run(amr.sweep(_request(rt=rt, user=u)))
    _run(amr.retrieve(_request(method="POST", body={"question": "客厅"}, rt=rt, user=u)))
    names = [name for lbl, _, name, _, _ in calls if lbl == "agent_memory"]
    assert len(names) == 10, names
    assert "MainThread" not in _threads(calls), _threads(calls)


def test_signal_and_vision_routes_run_off_loop():
    rt, calls = _rt()
    u = {"sub": "u"}
    _run(sgr.list_rules(_request(rt=rt, user=u)))
    _run(sgr.teach(_request(method="POST", body={"entity_id": "light.a"}, rt=rt, user=u)))
    _run(sgr.revoke(_request(method="POST", body={"exclusion_id": "e1"}, rt=rt, user=u)))
    _run(vr.events_face(_request(
        method="POST", body={"room": "客厅", "persons": [{"name": "甲"}]},
        rt=rt, headers={"Authorization": "Bearer devtok"})))
    labels = sorted({lbl for lbl, _, _, _, _ in calls})
    assert labels == ["signal_learning", "vision"], labels
    assert "MainThread" not in _threads(calls), _threads(calls)


def test_member_mcp_llm_routes_run_off_loop():
    rt, calls = _rt()
    u = {"sub": "u"}
    _run(mbr.member_list(_request(rt=rt, user=u)))
    _run(mbr.member_detail(_request(rt=rt, user=u, path_params={"member_id": "m1"})))
    _run(mbr.member_tag_delete(_request(rt=rt, user=u,
                                        path_params={"member_id": "m1", "tag": "t"})))
    _run(mbr.member_insight_feedback(_request(rt=rt, user=u, path_params={"member_id": "m1"})))
    _run(mrr.list_audit(_request(query="limit=50", rt=rt, user=u)))
    _run(lr.llm_cache_clear(_request(query="key=k", rt=rt, user=u)))
    names = [name for _, name, _, _, _ in calls]
    for expect in ("list_members", "get_member", "delete_member_tag",
                   "member_insight_feedback", "list_mcp_audit", "clear_answer_cache"):
        assert expect in names, names
    assert "MainThread" not in _threads(calls), _threads(calls)


def test_member_appearance_batches_two_sync_steps_into_one_hop():
    """`_normalize_appearance` 与 `update_member` 是同一次落盘动作，合成一个 worker 调用：
    两次跳转会把"读—改—写"拆到两个线程里，全局 RLock 下反而更容易被插队。"""
    rt, calls = _rt()
    _run(mbr.member_appearance(_request(
        method="POST", body={"appearance": {"gender": "male"}},
        rt=rt, user={"sub": "u"}, path_params={"member_id": "m1"})))
    names = [name for _, name, _, _, _ in calls]
    assert names == ["_normalize_appearance", "update_member"], names
    threads = {thread for _, _, thread, _, _ in calls}
    assert len(threads) == 1, threads
    assert "MainThread" not in threads, threads


# ── 6. 已判不改的腿：内存 token 校验保持不卸载（本批自撞的错误）──────────

def test_token_store_legs_stay_on_loop():
    """`MCPTokenStore.verify/kind/count` 是内存 `compare_digest` + 节流写（`_touch`），
    仓内 #51 已登记「判定不改」。这一跳加线程只换来上下文切换，还让每条鉴权多一次往返。"""
    for rel in ("acp_auth.py", "mcp_auth.py"):
        src = (SRC_ROOT / rel).read_text(encoding="utf-8")
        assert "to_thread" not in src, f"{rel} 里出现了 to_thread：内存 token 校验不该卸载"
    src = (SRC_ROOT / "mcp_tokens.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    verify = [n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "verify"]
    assert verify, "MCPTokenStore.verify 不在了"
    verbs = {"db_query", "db_execute", "execute", "commit", "query_rows", "executescript"}
    attrs = {n.attr for n in ast.walk(verify[0]) if isinstance(n, ast.Attribute)}
    assert not (attrs & verbs), f"verify 开始碰 SQL 了，上面的裁定要重开：{sorted(attrs & verbs)}"


def test_gauge_does_not_ring_on_in_memory_store_face(tmp_path):
    """量具对"叫 store、身体纯内存"的形状必须安静——这正是把我引向错误卸载的那一格。"""
    sample = (
        "class MemStore:\n"
        "    def __init__(self):\n        self.d = {}\n"
        "    def verify(self, token):\n        return self.d.get(token)\n"
        "async def h(store):\n"
        "    name = store.verify('t')\n"
        "    return name\n"
    )
    f = tmp_path / "mem.py"
    f.write_text(sample, encoding="utf-8")
    assert gauge.scan_paths([str(f)], transitive=True) == []


# ── 7. 心跳真响：60 并发下循环不许被钉住（第十轮验收 item 2）──────────────

def _ticks_during(factory, n=60):
    box = {"n": 0}

    async def heartbeat(stop):
        while not stop.is_set():
            box["n"] += 1
            await asyncio.sleep(0.001)

    async def run():
        stop = threading.Event()
        task = asyncio.create_task(heartbeat(stop))
        await asyncio.sleep(0.005)
        before = box["n"]
        await asyncio.gather(*[factory() for _ in range(n)])
        stop.set()
        await task
        return box["n"] - before

    return asyncio.run(run())


def test_instrument_detects_a_blocked_loop():
    """控制腿：同样的 60×5 ms 同步占用如果留在循环里，tick 必须停在个位数——
    否则下面那条"tick 显著 >1"只是空场。

    容 1 格是调度边界，不是放水：快照 `before` 之后、第一跳进入 `time.sleep` 之前，
    心跳协程还可能被排到一次。300 ms 的窗口如果循环是空的会走到 ~300 格，
    所以"≤1"和"≥5"之间没有灰色地带。
    """
    def blocked():
        async def leg():
            time.sleep(0.005)
        return leg()

    assert _ticks_during(blocked) <= 1


def test_sixty_concurrent_member_list_keeps_loop_beating(store, monkeypatch):
    orig = Store.list_members

    def slow(self, *a, **k):
        time.sleep(0.005)              # 单次 5 ms 不立项（lesson 118），缺陷在并发叠加
        return orig(self, *a, **k)

    monkeypatch.setattr(Store, "list_members", slow)

    def leg():
        return mbr.member_list(_request(
            rt=types.SimpleNamespace(store=store), user={"sub": "u"}))

    ticks = _ticks_during(leg)
    # 本机 8 次实测 19~21 格（300 ms 的名义占用被默认 executor 的 worker 线程摊薄），
    # 控制腿同期 8 次全 0；阈值取两端的中位，不靠运气
    assert ticks >= 5, f"60 并发 member_list 期间心跳几乎停摆：tick={ticks}"


# ── 8. 同批自撞的入参边界：`_num` 口径 ───────────────────────────────────

def test_list_audit_limit_uses_shared_ruler():
    """改前 `int(q.get("limit") or 100)`：`?limit=abc` → 未捕获 ValueError → 500，且无上限。"""
    rt, calls = _rt()
    u = {"sub": "u"}
    for bad in ("abc", "-1", "0", "1001", "true", "1e999", "nan"):
        resp = _run(mrr.list_audit(_request(query=f"limit={quote(bad)}", rt=rt, user=u)))
        assert resp.status_code == 400, (bad, resp.status_code, resp.body)
        assert b"limit" in resp.body, bad
    assert [k for _, _, _, _, k in calls] == [], "越界值不该下推到 store"
    resp = _run(mrr.list_audit(_request(query="limit=500", rt=rt, user=u)))
    assert resp.status_code == 200
    assert calls[-1][4]["limit"] == 500, calls[-1]


def test_member_tag_confidence_uses_shared_ruler():
    """改前裸 `float(body.get("confidence", 0.0) or 0.0)`：`"abc"` → 500，
    而 confidence 的契约域是 [0, 1]，`7` 会一路写进库。"""
    rt, calls = _rt()
    u = {"sub": "u"}
    for bad in ("abc", "-0.1", "1.5", "true", "Infinity", "NaN"):
        resp = _run(mbr.member_tag_add(_request(
            method="POST", rt=rt, user=u, path_params={"member_id": "m1"},
            body={"tag": "夜猫子", "confidence": bad})))
        assert resp.status_code == 400, (bad, resp.status_code, resp.body)
    resp = _run(mbr.member_tag_add(_request(
        method="POST", rt=rt, user=u, path_params={"member_id": "m1"},
        body={"tag": "夜猫子", "confidence": 0.8})))
    assert resp.status_code == 200
    assert calls[-1][4]["confidence"] == 0.8, calls[-1]


@pytest.fixture
def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
    st.create_member(name="甲")
    yield st
    st.close()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass
