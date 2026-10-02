"""第五轮审计（启动与事件环稳定性）回归锁。

锁两件事：
1. CRITICAL-2：async 函数里直接调同步 SQLite —— 全局 RLock 会把整个事件循环钉住。
   检测器沿用审计方的 AST 口径，但补掉它的两个洞：嵌套 def 经 to_thread 跑的
   正确写法（原口径会误报）、collector 这类纯内存对象（原口径会误报）。
2. MEDIUM：SSE 订阅队列无界 → 慢客户端把内存拖大。锁住上限与"满了断开而不是挂住"。
"""

from __future__ import annotations

import ast
import asyncio
import os
import pathlib
import re
import tempfile
import threading

import pytest

import memory_agent
from memory_agent.api import debug_routes as dbg
from memory_agent.store import Store

SRC_ROOT = pathlib.Path(memory_agent.__file__).parent
DB_RECEIVERS = ("store", "ha_db")


def _is_to_thread_call(node: ast.Call) -> bool:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr == "to_thread"
    return isinstance(fn, ast.Name) and fn.id == "to_thread"


def _scan_source(text: str) -> list[tuple[int, str]]:
    """返回 async 函数体内【未被 await / to_thread 保护】的同步 DB 调用。"""
    hits: list[tuple[int, str]] = []
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        guarded: set[int] = set()
        nested_defs: dict[str, ast.FunctionDef] = {}
        for sub in ast.walk(node):
            if isinstance(sub, ast.FunctionDef):
                nested_defs[sub.name] = sub
            # await 覆盖到的调用
            if isinstance(sub, ast.Await):
                for c in ast.walk(sub.value):
                    if isinstance(c, ast.Call):
                        guarded.add(id(c))
            # to_thread(fn, ...) 直接传进来的调用
            if isinstance(sub, ast.Call) and _is_to_thread_call(sub):
                for a in sub.args:
                    if isinstance(a, ast.Call):
                        guarded.add(id(a))
                    # 传函数引用：整个嵌套函数体都算已进线程
                    if isinstance(a, ast.Name) and a.id in nested_defs:
                        for c in ast.walk(nested_defs[a.id]):
                            if isinstance(c, ast.Call):
                                guarded.add(id(c))
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call) or id(sub) in guarded:
                continue
            f = sub.func
            if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute)):
                continue
            base = f.value
            if not isinstance(base.value, ast.Name):
                continue
            if base.attr not in DB_RECEIVERS:
                continue
            hits.append((sub.lineno, f"{base.value.id}.{base.attr}.{f.attr}()"))
    return hits


def _scan_repo() -> list[str]:
    out = []
    for path in sorted(SRC_ROOT.glob("**/*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, code in _scan_source(text):
            out.append(f"{path.relative_to(SRC_ROOT)}:{lineno} {code}")
    return out


# ── 检测器本身要真咬得住 ──────────────────────────────────────────────

def test_detector_flags_raw_sync_db_call():
    src = (
        "async def h(rt):\n"
        "    rows = rt.store.list_behavior_states()\n"
        "    return rows\n"
    )
    hits = _scan_source(src)
    assert len(hits) == 1, hits
    assert hits[0][1] == "rt.store.list_behavior_states()"


def test_detector_accepts_await_to_thread():
    src = (
        "import asyncio\n"
        "async def h(rt):\n"
        "    rows = await asyncio.to_thread(rt.store.list_behavior_states)\n"
        "    return rows\n"
    )
    assert _scan_source(src) == []


def test_detector_accepts_nested_def_run_in_thread():
    """审计方原口径在这里误报：conn = rt.store.connect() 在嵌套 def 里，而该 def 整体进线程。"""
    src = (
        "import asyncio\n"
        "async def h(rt):\n"
        "    def _read():\n"
        "        conn = rt.store.connect()\n"
        "        return conn.execute('SELECT 1').fetchall()\n"
        "    return await asyncio.to_thread(_read)\n"
    )
    assert _scan_source(src) == []


def test_detector_flags_nested_def_never_offloaded():
    """反例：嵌套 def 没有进线程，仍然必须报红——不能被'有嵌套函数'豁免掉。"""
    src = (
        "import asyncio\n"
        "async def h(rt):\n"
        "    def _read():\n"
        "        conn = rt.store.connect()\n"
        "        return conn.execute('SELECT 1').fetchall()\n"
        "    return _read()\n"
    )
    assert len(_scan_source(src)) == 1


# ── 全仓零容忍 ────────────────────────────────────────────────────────

def test_no_blocking_db_calls_inside_async_defs():
    """路线图 3.2 的延伸：事件循环里不许出现同步 DB 调用（0 条，不是基线容忍）。"""
    violations = _scan_repo()
    assert violations == [], "async 函数内有未离线执行的 DB 调用：\n" + "\n".join(violations)


def test_collector_status_reads_stay_db_free():
    """审计方把 5 处 `rt.collector.*` 也算进 CRITICAL-2，实测是假阳性。

    假阳性的依据必须锁住，否则哪天 poller 的状态口真去查库，这 5 处就会被
    "本来就被豁免"吞掉。口径：状态三读只能读内存字段，不许调用 store / ha_db 的方法。
    """
    tree = ast.parse((SRC_ROOT / "poller.py").read_text(encoding="utf-8"))
    status_methods = {"get_progress", "current_source", "next_run_time"}
    found = 0
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef) or fn.name not in status_methods:
                continue
            found += 1
            for sub in ast.walk(fn):
                if not isinstance(sub, ast.Call):
                    continue
                f = sub.func
                if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute)
                        and isinstance(f.value.value, ast.Name)
                        and f.value.value.id == "self"
                        and f.value.attr in ("store", "ha_db")):
                    raise AssertionError(
                        f"{cls.name}.{fn.name} 在事件循环读的口径里调了 DB：self.{f.value.attr}.{f.attr}()"
                    )
    assert found == 3, f"状态口少/多了：{found}"


# ── SSE 订阅队列上限 ─────────────────────────────────────────────────

def _make_run() -> dbg.DebugRun:
    return dbg.DebugRun(
        run_id="r1", instruction="x", model=None, mode="run",
        max_rounds=1, conversation_id=None, temperature=None,
    )


def test_subscriber_queue_has_a_bound():
    assert dbg._SUBSCRIBER_MAXSIZE > 0
    q = dbg.new_subscriber_queue()
    assert q.maxsize == dbg._SUBSCRIBER_MAXSIZE


async def test_slow_subscriber_is_dropped_not_hanging(monkeypatch):
    """队列满 → 摘订阅 + 投哨兵。消费者必须拿到结束信号，不能永久 await。"""
    monkeypatch.setattr(dbg, "_SUBSCRIBER_MAXSIZE", 3)
    run = _make_run()
    q = dbg.new_subscriber_queue()
    run.subscribers.append(q)

    for i in range(3):
        run.emit("tool_call", {"i": i})
    assert len(run.subscribers) == 1
    assert run.dropped_subscribers == 0

    run.emit("tool_call", {"overflow": True})
    assert run.subscribers == []
    assert run.dropped_subscribers == 1

    drained = []
    while True:
        item = await asyncio.wait_for(q.get(), timeout=1)
        if item is dbg._TERMINAL:
            break
        drained.append(item)
    assert len(drained) == 2  # 腾位丢 1 条，其余缓冲仍在
    assert not q.full()


async def test_fast_subscriber_gets_everything(monkeypatch):
    monkeypatch.setattr(dbg, "_SUBSCRIBER_MAXSIZE", 100)
    run = _make_run()
    q = dbg.new_subscriber_queue()
    run.subscribers.append(q)
    for i in range(10):
        run.emit("tool_call", {"i": i})
    run.finish()
    got = []
    while True:
        item = await asyncio.wait_for(q.get(), timeout=1)
        if item is dbg._TERMINAL:
            break
        got.append(item)
    assert len(got) == 10
    assert run.dropped_subscribers == 0
    assert len(run.history) == 10


def test_debug_stream_uses_the_bounded_factory():
    """两处 SSE 订阅都必须走同一个构造口，否则上限会被绕过。"""
    dr = (SRC_ROOT / "api" / "debug_routes.py").read_text(encoding="utf-8", errors="replace")
    acp = (SRC_ROOT / "acp_server.py").read_text(encoding="utf-8", errors="replace")
    assert "own = new_subscriber_queue()" in dr
    assert "own: asyncio.Queue = new_subscriber_queue()" in acp
    assert "asyncio.Queue()" not in dr, "debug_routes 里不该再有裸无界队列"
    assert "asyncio.Queue()" not in acp, "acp_server 里不该再有裸无界队列"


def test_no_bare_create_task_left():
    """审计方报的"无 handle fire-and-forget"在 8a05eb5 之后不成立；这条锁住它不回退。"""
    offenders = []
    for path in sorted(SRC_ROOT.glob("**/*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.name == "task_registry.py":
            continue
        if "asyncio.create_task(" in text or "loop.create_task(" in text:
            offenders.append(str(path.relative_to(SRC_ROOT)))
    assert offenders == [], offenders


# ── TV 人脸事件的后端识别节流 ────────────────────────────────────────

def _stub_vision(monkeypatch):
    from memory_agent import vision_service as vs
    svc = object.__new__(vs.VisionService)
    svc._vlm_inflight = set()
    svc._skip_counts = {}
    svc._last_result = {}
    return svc, vs


async def test_face_event_throttles_while_room_busy(monkeypatch):
    """同一房间上一次识别没落地前，第二条身份变化不再投线程（审计 MEDIUM 2）。"""
    svc, vs = _stub_vision(monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    runs: list[str] = []

    def fake(self, room, **kw):
        runs.append(room)
        entered.set()
        assert release.wait(5), "测试未释放"
        return {"ok": True}

    monkeypatch.setattr(vs.VisionService, "analyze_room", fake)

    r1 = svc.record_face_event("客厅", [{"name": "甲"}], "identity_change", None)
    assert r1["vlm_dispatched"] is True and r1["deduped"] is False
    await asyncio.to_thread(entered.wait, 5)
    assert "客厅" in svc._vlm_inflight

    r2 = svc.record_face_event("客厅", [{"name": "乙"}], "identity_change", None)
    assert r2["deduped"] is True and r2["vlm_dispatched"] is False
    assert svc._skip_counts["客厅"]["vlm_inflight"] == 1
    assert runs == ["客厅"]

    release.set()
    for _ in range(100):
        if not svc._vlm_inflight:
            break
        await asyncio.sleep(0.05)
    assert svc._vlm_inflight == set(), "识别结束后没腾出在跑位"

    entered.clear()
    r3 = svc.record_face_event("客厅", [{"name": "乙"}], "identity_change", None)
    assert r3["vlm_dispatched"] is True
    await asyncio.to_thread(entered.wait, 5)
    release.set()
    assert runs == ["客厅", "客厅"]


async def test_other_room_is_not_blocked(monkeypatch):
    """节流按房间，不跨房间串行。"""
    svc, vs = _stub_vision(monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def fake(self, room, **kw):
        entered.set()
        assert release.wait(5)
        return {"ok": True}

    monkeypatch.setattr(vs.VisionService, "analyze_room", fake)
    assert svc.record_face_event("客厅", [{"name": "甲"}], "t", None)["vlm_dispatched"]
    await asyncio.to_thread(entered.wait, 5)
    r = svc.record_face_event("书房", [{"name": "甲"}], "t", None)
    assert r["vlm_dispatched"] is True and r["deduped"] is False
    release.set()
    for _ in range(100):
        if not svc._vlm_inflight:
            break
        await asyncio.sleep(0.05)
    assert svc._vlm_inflight == set()


# ── 量衡脚本自己撞出来的两条死接口（审计方没报，本轮实测成立） ──────────────

@pytest.fixture
def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
    yield st
    st.close()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def test_bad_case_endpoint_filters_by_status(store):
    """badcase 取料口此前调的是 ``query_events(status=…)``——HA 表没这个参数，
    每次 TypeError 被兜底 except 变成「查询失败」。vMA-2.0 的 0/10 有它一份。"""
    store.insert_behavior_event({"room": "书房", "action": "vlm_failed",
                                 "status": "vlm_failed", "persons": []})
    store.insert_behavior_event({"room": "书房", "action": "在场",
                                 "status": "ok", "persons": []})
    rows = store.list_behavior_events(status="vlm_failed")
    assert [r["status"] for r in rows] == ["vlm_failed"]

    src = (SRC_ROOT / "api" / "behavior_routes.py").read_text(encoding="utf-8")
    assert re.search(r"query_events\([^)]*status=", src) is None, (
        "behavior_routes 又拿 HA 的 query_events 当行为事件口了")
    assert re.search(r'list_behavior_events,\s*[^)]*status="vlm_failed"', src)


def test_diary_event_query_is_executable(store):
    """自我日记的取数口径：旧代码传成位置参数，每夜 TypeError 被 broad except 吞成一行日志；
    就算不炸，它读的 ``state`` 列也不存在（真列名 new_state）。"""
    assert store.query_events(start="2026-10-02T00:00:00",
                              end="2026-10-02T23:59:59", limit=100) == []
    cols = [r["name"] for r in store.connect().execute("PRAGMA table_info(events)").fetchall()]
    assert "new_state" in cols and "state" not in cols, cols

    src = (SRC_ROOT / "runtime.py").read_text(encoding="utf-8")
    assert 'self.store.query_events("", today' not in src
    assert 'e.get("new_state")' in src
