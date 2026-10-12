"""稳定性/功能性审计 20261012（round-003）三条确证缺陷的回归锁。

引用口径「报告名 + 行号」，报告在 `doc/审计报告/审计报告_memory-agent_20261012.md`：

  D1 报告:28  ⇒ 模块导入期裸 `int(os.getenv(...))`：一个手滑的 env（`MA_VOICE_CACHE_MAX=2000条`、
                空串、误留引号）在 **import 那一刻** 抛 ValueError → 容器启动失败循环。
                传 0/负值更阴：不报错，护栏语义整条反转。**现读 7 个站点**（报告写 6 处，漏了
                `mcp_server.py` 的 MCP_SLOW_MS/MCP_HEAVY_MS 那一族里的一个，实数以此处为准）。
                修复 = `src/memory_agent/env_cfg.py` 收口 + `scripts/scan_import_time_env_cast.py` 门。
  D2 报告:46  ⇒ 更新标记的临时名是固定的 `MARKER_PATH + ".tmp"` + `os.replace`：并发提交时两次
                `json.dump` 写同一个文件，marker 的 ref / requested_by 可能来自不同请求，
                宿主 sidecar 按错误的 ref 执行 git pull + 重启。修复 = 随机后缀 + fsync + 失败清场。
  D3 报告:54  ⇒ `/health` 的七路探测共用一个没有超时的 `asyncio.gather`：最慢的一路
                （VACUUM 期间的 `store.stats`、chroma 客户端默认 60s）就能把 /health 拖住几十秒 →
                外部存活探针超时杀容器 → 崩溃-重建 churn。修复 = 每路 `wait_for` 限时，慢的那一路
                降级为 `{"timeout": true}` 而不是拖垮整面；另给业务锁加「等锁超过阈值记 warning」。

D3 的「**VACUUM 移出业务锁**」这一半**没有采纳**，机制理由写在 `store.py:4790` 的注释里：本仓是
单连接 + 全局锁，把 VACUUM 挪到独立连接只是把「health 挂住」换成「每一次业务写撞 SQLITE_BUSY」。
这条不采纳同样要有锁，否则下一个人会把它当成没修完的活儿顺手挪走 ——
见 `test_vacuum_still_runs_under_the_business_lock`。
"""

import ast
import asyncio
import inspect
import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
import textwrap
import types

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_ROOT, "src")
_SCRIPTS = os.path.join(_ROOT, "scripts")
_MODULES = os.path.join(_SRC, "memory_agent")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from memory_agent import env_cfg  # noqa: E402
from memory_agent.api import system_routes  # noqa: E402
from memory_agent.runtime import AppRuntime  # noqa: E402
from memory_agent import store as store_mod  # noqa: E402

GATE = os.path.join(_SCRIPTS, "scan_import_time_env_cast.py")

#: D1 的坏配置样本：非数字 / 空串 / 负值 / 零 / 带单位，五种形状都要在 import 期活下来。
POISON = {
    "MA_VOICE_CACHE_MAX": "2000条",
    "MA_FEEDBACK_TEXT_MAX": "",
    "MA_DB_LOCK_WARN_AFTER_SECONDS": "-1",
    "MA_RULE_MIN_EVIDENCE": "-1",
    "MA_RULE_DRY_RUN_DAYS": "三天",
    "MCP_SLOW_MS": "slow",
    "MCP_HEAVY_MS": "0",
    "MCP_MAX_STATS_ENTRIES": "0",
    "MA_HEALTH_PROBE_TIMEOUT_SECONDS": "5s",
}
#: 越界与畸形一律回默认值；这些默认值就是修复前那些裸转换的字面量（改了默认值等于改了语义）。
FALLBACK = {
    "MA_VOICE_CACHE_MAX": 2000,
    "MA_FEEDBACK_TEXT_MAX": 500,
    "MA_DB_LOCK_WARN_AFTER_SECONDS": 3.0,
    "MA_RULE_MIN_EVIDENCE": 3,
    "MA_RULE_DRY_RUN_DAYS": 3.0,
    "MCP_SLOW_MS": 5000.0,
    "MCP_HEAVY_MS": 30000.0,
    "MCP_MAX_STATS_ENTRIES": 500,
    "MA_HEALTH_PROBE_TIMEOUT_SECONDS": 5.0,
}


def _load_gate():
    import importlib.util
    spec = importlib.util.spec_from_file_location("scan_import_time_env_cast", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fn_tree(fn):
    """按 AST 读一个函数的代码面（注释与 docstring 不进判定，见 test_marker_tmp_is_not_a_fixed_name）。"""
    return ast.parse(textwrap.dedent(inspect.getsource(fn)))


def _call_lines(tree, attr):
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr]


# ────────────────────────────── D1：导入期 env 收口 ──────────────────────────────

def test_env_helpers_fall_back_instead_of_raising(monkeypatch):
    """坏配置的五种形状都不许抛，且越界（含 0 / 负值）回落默认值而不是把护栏关掉。"""
    for raw in ("2000条", "", "   ", "abc", "1e9999", None):
        if raw is None:
            monkeypatch.delenv("X_CASE", raising=False)
        else:
            monkeypatch.setenv("X_CASE", raw)
        assert env_cfg.env_int("X_CASE", 42, lo=1) == 42, raw

    # 0 与负值：修复前 `int()` 照收，护栏语义整条反转（上限 0 = 这条护栏不存在）。
    for raw in ("0", "-1", "-0.5"):
        monkeypatch.setenv("X_CASE", raw)
        assert env_cfg.env_int("X_CASE", 42, lo=1) == 42, raw
        assert env_cfg.env_float("X_CASE", 4.5, lo=0.5) == 4.5, raw

    # 上界同样收口：防止一个 typo 把缓存上限写成 10 亿。
    monkeypatch.setenv("X_CASE", "999999999999")
    assert env_cfg.env_int("X_CASE", 42, lo=1, hi=10000) == 42


def test_valid_env_values_are_honored(monkeypatch):
    """回落不等于忽略：合法值必须照用，否则这条收口就成了硬编码。"""
    monkeypatch.setenv("X_INT", "50")
    monkeypatch.setenv("X_FLOAT", "1.5")
    monkeypatch.setenv("X_BLANK_OK", " 7 ")
    assert env_cfg.env_int("X_INT", 42, lo=1) == 50
    assert env_cfg.env_float("X_FLOAT", 4.5, lo=0.5) == 1.5
    assert env_cfg.env_int("X_BLANK_OK", 42, lo=1) == 7
    monkeypatch.delenv("X_INT", raising=False)
    assert env_cfg.env_int("X_INT", 42, lo=1) == 42


def test_poisoned_env_does_not_kill_import():
    """报告的崩溃链主体：坏 env 之下 `import memory_agent.store` 必须成功，且值全回落默认。

    必须在子进程里跑 —— 这四个模块的常量是**导入期**算好的，本进程的 import 早就发生了。
    """
    child = (
        "import json, sys;"
        "sys.path.insert(0, sys.argv[1]);"
        "import memory_agent.store as S, memory_agent.rule_lifecycle as RL,"
        " memory_agent.mcp_server as M, memory_agent.runtime as RT;"
        "print('RESULT=' + json.dumps({"
        "'MA_VOICE_CACHE_MAX': S._VOICE_CACHE_MAX,"
        "'MA_FEEDBACK_TEXT_MAX': S._FEEDBACK_TEXT_MAX,"
        "'MA_DB_LOCK_WARN_AFTER_SECONDS': S.DB_LOCK_WARN_AFTER_SECONDS,"
        "'MA_RULE_MIN_EVIDENCE': RL.MIN_EVIDENCE,"
        "'MA_RULE_DRY_RUN_DAYS': RL.DRY_RUN_DAYS,"
        "'MCP_SLOW_MS': M.MCP_SLOW_MS,"
        "'MCP_HEAVY_MS': M.MCP_HEAVY_MS,"
        "'MCP_MAX_STATS_ENTRIES': M.MCP_MAX_STATS_ENTRIES,"
        "'MA_HEALTH_PROBE_TIMEOUT_SECONDS': RT.HEALTH_PROBE_TIMEOUT_SECONDS}))"
    )
    env = dict(os.environ)
    env.update(POISON)
    env["PYTHONPATH"] = _SRC
    proc = subprocess.run([sys.executable, "-c", child, _SRC],
                          capture_output=True, text=True, cwd=_ROOT, env=env, timeout=180)
    assert proc.returncode == 0, "坏 env 把导入链炸了（D1 的原始失效路径）：\n" + proc.stdout + proc.stderr
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT=")), None)
    assert line, "子进程没交出读数：\n" + proc.stdout + proc.stderr
    got = json.loads(line[len("RESULT="):])
    assert got == FALLBACK, "坏配置没有全部回落默认值：%s" % got


def test_import_time_cast_gate_is_green_and_not_idle():
    """G6 门：现扫零站点 + 自检里合成脏样本必须被抓到（零读数可能是门瞎了）。"""
    gate = _load_gate()
    rows = gate.scan_tree(_MODULES)
    assert rows == [], "又出现模块级裸 int/float(os.getenv)：%s" % [
        "%s:%s %s" % (r["file"], r["line"], r["name"]) for r in rows]
    assert gate._self_test() == 0
    proc = subprocess.run([sys.executable, GATE, "--self-test"],
                          capture_output=True, text=True, cwd=_ROOT, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SELFTEST_HIT" in proc.stdout and "SELFTEST_CLEAN" in proc.stdout
    assert "SELFTEST_MISS" not in proc.stdout and "SELFTEST_FALSE" not in proc.stdout


# ────────────────────────────── D2：更新标记的临时名 ──────────────────────────────

class _Request:
    """只带 apply_update 用到的两件事：require_admin 已被替换，剩下的就是 body。"""

    def __init__(self, payload=None, on_call=None):
        self._payload = payload if payload is not None else {"confirm_version": system_routes.__app_version__}
        self._on_call = on_call

    async def json(self):
        if self._on_call is not None:
            self._on_call()
        return self._payload


def _adm(request):
    return {"username": "admin"}, None


def _point_at(tmp_root, monkeypatch):
    marker = os.path.join(str(tmp_root), ".update_request.json")
    result = os.path.join(str(tmp_root), ".update_result.json")
    monkeypatch.setattr(system_routes, "MARKER_PATH", marker)
    monkeypatch.setattr(system_routes, "RESULT_PATH", result)
    monkeypatch.setattr(system_routes, "require_admin", _adm)
    return marker, result


def test_marker_tmp_is_not_a_fixed_name():
    """静态面：临时名必须由 uuid 派生，且 fsync 先于 replace —— 这三件事缺一半都不算修完。

    口径取 AST 不取文本：解释 D2 的那段注释里就写着旧形状 `MARKER_PATH + ".tmp"`，
    按整行文本比会把「写了注释说明为什么不再这么干」读成「改回来了」——那是假红。
    """
    tree = _fn_tree(system_routes.apply_update)

    old_shape = [n for n in ast.walk(tree)
                 if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add)
                 and isinstance(n.left, ast.Name) and n.left.id == "MARKER_PATH"
                 and isinstance(n.right, ast.Constant) and str(n.right.value).startswith(".tmp")]
    assert not old_shape, "固定名 .tmp 又回来了（D2：并发写同一个临时文件）：%s" % [n.lineno for n in old_shape]

    tmp_assigns = [n for n in ast.walk(tree)
                   if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "tmp_path" for t in n.targets)]
    assert len(tmp_assigns) == 1, "tmp_path 的赋值点应当唯一，实际 %s" % [n.lineno for n in tmp_assigns]
    dumped = ast.dump(tmp_assigns[0].value)
    assert "uuid4" in dumped and "hex" in dumped, "临时名没有随机后缀：%s" % dumped

    fsync = _call_lines(tree, "fsync")
    replace = _call_lines(tree, "replace")
    assert fsync and replace, "rename 前没有 fsync，或 replace 不见了"
    assert max(fsync) < min(replace), "fsync 必须在 replace 之前（%s vs %s）" % (fsync, replace)


def test_wrong_confirm_version_never_touches_the_marker(tmp_path, monkeypatch):
    """DCD Q4=B 的二次确认不许被 D2 的改动带偏：版本不匹配时既不写 marker 也不留临时文件。"""
    marker, _ = _point_at(tmp_path, monkeypatch)
    resp = asyncio.run(system_routes.apply_update(_Request({"confirm_version": "v0.0.0-not-this"})))
    assert resp.status_code == 400
    assert not os.path.isfile(marker)
    assert os.listdir(str(tmp_path)) == []


async def test_apply_update_writes_a_complete_marker_and_leaves_no_tmp(tmp_path, monkeypatch):
    marker, _ = _point_at(tmp_path, monkeypatch)
    monkeypatch.setattr(system_routes, "get_config", lambda: types.SimpleNamespace(update_branch="main"))
    resp = await system_routes.apply_update(_Request())
    assert resp.status_code == 200, resp.body
    with open(marker, encoding="utf-8") as fh:
        written = json.load(fh)
    assert written["ref"] == "main" and written["requested_by"] == "admin"
    assert written["version"] == system_routes.__app_version__
    leftovers = [f for f in os.listdir(str(tmp_path)) if ".tmp" in f]
    assert leftovers == [], "临时文件没被 rename 吃掉：%s" % leftovers


async def test_second_request_while_one_is_in_flight_is_rejected(tmp_path, monkeypatch):
    marker, _ = _point_at(tmp_path, monkeypatch)
    first = await system_routes.apply_update(_Request())
    assert first.status_code == 200
    second = await system_routes.apply_update(_Request())
    assert second.status_code == 409
    with open(marker, encoding="utf-8") as fh:
        assert json.load(fh)["requested_by"] == "admin", "409 那一趟不该把 marker 改掉"


async def test_failed_marker_write_cleans_up_the_tmp(tmp_path, monkeypatch):
    """写中途失败（磁盘满 / 序列化炸）不许留下半截 .tmp 让宿主脚本读到。"""
    marker, _ = _point_at(tmp_path, monkeypatch)
    real_dump = json.dump

    def boom(*a, **k):
        raise OSError("disk full (synthetic)")

    monkeypatch.setattr(json, "dump", boom)
    try:
        raised = None
        try:
            await system_routes.apply_update(_Request())
        except Exception as exc:  # noqa: BLE001 — 断言的就是「异常照原样往上抛」
            raised = exc
        assert isinstance(raised, OSError), raised
        assert not os.path.isfile(marker)
        assert os.listdir(str(tmp_path)) == [], "失败路径漏了临时文件"
    finally:
        monkeypatch.setattr(json, "dump", real_dump)


def test_two_requests_in_two_threads_do_not_share_a_tmp(tmp_path, monkeypatch):
    """强制两趟请求同时越过「已有请求」的检查，验证两趟用的是两个不同的临时名。

    字节级交错在单进程里无法确定性地逼出来（所以这里断言的是修复的**机制**：每趟一个随机名，
    外加 `test_marker_tmp_is_not_a_fixed_name` 那条静态锁）。旧的固定名 `.tmp` 在这一格里
    连「两次两个名字」都交不出来，会直接判红。
    """
    marker, _ = _point_at(tmp_path, monkeypatch)
    # 对齐点选 `request.json()`：它在 apply_update 顶部只被 await 一次（system_routes.py:110），
    # 且在 `os.path.isfile(MARKER_PATH)`（:136）之前 —— 两趟都从那里过去，后到的一趟必然还看不到
    # marker。挂在全局 time.strftime 上做栅栏会二次进入（组装 marker 与结果文件都要取时间），
    # 慢的那一趟等到超时就整条腿断掉（全量回归里实测复现过）。
    barrier = threading.Barrier(2, timeout=60)
    names = threading.local()
    replaced = []
    real_replace = os.replace
    # Windows 上并发 replace 同一个目标会抛 WinError 5（Linux 的 rename(2) 没有这条约束，生产路径
    # 在容器里）。串行化只影响"哪一个先落地"，不影响这一格要量的东西：两趟各自的临时名。
    replace_lock = threading.Lock()

    def recording_replace(src, dst):
        replaced.append(os.path.basename(src))
        with replace_lock:
            return real_replace(src, dst)

    def _adm_user(request):
        return {"username": names.user}, None

    monkeypatch.setattr(system_routes, "require_admin", _adm_user)
    monkeypatch.setattr(system_routes, "get_config", lambda: types.SimpleNamespace(update_branch="b-" + names.user))
    monkeypatch.setattr(system_routes.os, "replace", recording_replace)

    def run(user):
        names.user = user
        try:
            resp = asyncio.run(system_routes.apply_update(_Request(on_call=barrier.wait)))
        except threading.BrokenBarrierError as exc:
            raise AssertionError("两趟请求没能在写 marker 之前对齐（栅栏断了）") from exc
        return resp.status_code

    codes = []
    threads = [threading.Thread(target=lambda u=u: codes.append(run(u))) for u in ("alice", "bob")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(90)
    assert len(codes) == 2, codes
    # 两趟都从 isfile 检查过去了（marker 还没落盘），所以都该是 200，不是一趟 200 一趟 409。
    assert codes == [200, 200], codes

    with open(marker, encoding="utf-8") as fh:
        written = json.load(fh)
    # marker 必须是**某一个请求的完整载荷**，不能是两条腿拼出来的（ref 与 requested_by 同源）。
    assert written["ref"] == "b-" + written["requested_by"], written
    assert written["requested_by"] in ("alice", "bob"), written
    assert len(replaced) == 2, "两趟请求没有各自写过一次临时文件：%s" % replaced
    assert len(set(replaced)) == 2, "两趟请求共用了临时名：%s" % replaced
    assert all(".tmp." in n for n in replaced), "临时名没有随机后缀：%s" % replaced
    assert [f for f in os.listdir(str(tmp_path)) if ".tmp" in f] == []


# ────────────────────────────── D3：health 每路限时 ──────────────────────────────

class _Ret:
    """同步探针桩：可选延迟模拟「VACUUM 期间的 store.stats」。"""

    def __init__(self, value, delay=0.0):
        self.value = value
        self.delay = delay

    def __call__(self, *a, **k):
        if self.delay:
            time.sleep(self.delay)
        return self.value


class _Pinger:
    def __init__(self, value, delay=0.0):
        self.value = value
        self.delay = delay

    async def ping(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.value


def _health_stub(store_delay=0.0, store_payload=None):
    stub = types.SimpleNamespace()
    stub.ha = types.SimpleNamespace(get_status=_Ret({"connected": True, "url": "http://ha.local"}))
    stub.llm = _Pinger({"connected": True, "model": "x"})
    stub.store = types.SimpleNamespace(stats=_Ret(store_payload or {"total_events": 7}, delay=store_delay))
    stub.history = types.SimpleNamespace(chroma_status=_Ret({"connected": True}),
                                         embedding_status=_Ret({"configured": True, "model": "minilm"}))
    stub._nr_status = _Ret({"connected": True, "url": "http://nr.local"})
    stub._ha_db_status = _Ret({"enabled": False, "connected": False})
    stub.mqtt = types.SimpleNamespace(status=_Ret({"connected": False}))
    stub.insights = types.SimpleNamespace(status=_Ret({"loaded": True, "entities": 3}))
    stub.activity = types.SimpleNamespace(status=_Ret({"states": 0}))
    stub.config = types.SimpleNamespace(tz_offset_hours=8)
    stub.tokens = types.SimpleNamespace(list_tokens=lambda: [])
    stub.collector = types.SimpleNamespace(is_running=False)
    return stub


async def test_health_degrades_a_slow_probe_instead_of_hanging(monkeypatch):
    """报告的失效链主体：一路慢（这里 1.5s）不该把 /health 拖过预算，只让那一路降级。"""
    from memory_agent import runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "HEALTH_PROBE_TIMEOUT_SECONDS", 0.3)
    started = time.perf_counter()
    res = await AppRuntime.health(_health_stub(store_delay=1.5))
    elapsed = time.perf_counter() - started
    assert elapsed < 1.2, "health 被慢探针拖住了：%.2fs（每路限时没生效）" % elapsed
    assert res["store"]["timeout"] is True, res["store"]
    assert res["store"]["total_events"] == 0, "降级格必须仍带 fallback 形状：%s" % res["store"]
    assert "超过" in res["store"]["error"], res["store"]
    # 慢的那一路降级，其余六路照旧交实数据 —— 不许整面一起变糊。
    assert res["ha"]["connected"] is True
    assert res["chroma"]["connected"] is True
    assert res["embedding"]["configured"] is True
    assert res["insights"]["entities"] == 3
    assert res["activity"]["states"] == 0
    assert "timeout" not in res["ha"] and "timeout" not in res["chroma"]


async def test_health_returns_real_payloads_when_all_probes_are_fast(monkeypatch):
    from memory_agent import runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "HEALTH_PROBE_TIMEOUT_SECONDS", 5.0)
    res = await AppRuntime.health(_health_stub(store_payload={"total_events": 21, "last_event_ts": ""}))
    assert res["store"]["total_events"] == 21
    assert res["collect_lag_seconds"] is None
    assert res["tokens"] == 0 and res["collecting"] is False
    for key in ("ha", "llm", "chroma", "nodered", "ha_db", "embedding", "store"):
        assert isinstance(res[key], dict) and "timeout" not in res[key], (key, res[key])


def test_health_source_locks_per_probe_timeout():
    """静态面：七路探测每一路都要过 wait_for，且 return_exceptions=True 不许丢。

    少了 return_exceptions，一路超时会把整个 gather 打断（其余六路结果直接没了）；
    少了逐路 wait_for，又回到「最慢的一路决定 /health 耗时」。
    判定取 AST：health() 里那段注释写着「wait_for 到点会取消等待」，按文本比会假绿。
    """
    tree = _fn_tree(AppRuntime.health)
    gathers = [n for n in _call_lines(tree, "gather")]
    assert len(gathers) == 1, "health 的探测面形状变了（预期一个 gather）：%s" % gathers
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "gather")

    kwargs = {k.arg: k.value for k in node.keywords}
    assert "return_exceptions" in kwargs, "gather 少了 return_exceptions：一路炸就整面炸"
    assert getattr(kwargs["return_exceptions"], "value", None) is True, ast.dump(kwargs["return_exceptions"])

    comps = [c for c in ast.walk(node) if isinstance(c, ast.comprehension)]
    assert len(comps) == 1, "逐路限时应该是「对七路各包一次 wait_for」的形状"
    assert isinstance(comps[0].iter, ast.Tuple), "被限时的探测集合形状变了"
    assert len(comps[0].iter.elts) == 7, "七路探测少了一路：%d" % len(comps[0].iter.elts)
    wrapped = _call_lines(node, "wait_for")
    assert len(wrapped) == 1, "wait_for 必须包住每一路（生成式里那一次）：%s" % wrapped

    timeout_attrs = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "TimeoutError"]
    assert timeout_attrs, "超时结果没有被识别成降级格"
    assert any(isinstance(n, ast.Name) and n.id == "HEALTH_PROBE_TIMEOUT_SECONDS" for n in ast.walk(tree)), \
        "health 没有读每路预算"


def test_vacuum_still_runs_under_the_business_lock():
    """把「不采纳 D3 的第三条」钉成可执行锁：VACUUM 必须仍在 `with self._lock` 里。

    挪出去的后果不是假设：本仓 connect() 复用同一条连接，独立连接的 VACUUM 会与业务写事务
    在 SQLite 层相撞，写路径开始吃 SQLITE_BUSY。health 那条链已在两处掐断（每路限时 + 等锁 warning）。
    """
    tree = ast.parse(io.open(store_mod.__file__, encoding="utf-8").read(), filename=store_mod.__file__)
    vacuum_calls = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute" and node.args
                and isinstance(node.args[0], ast.Constant)
                and str(node.args[0].value).strip().upper() == "VACUUM"):
            vacuum_calls.append(node.lineno)
    assert len(vacuum_calls) == 1, "VACUUM 站点数量变了（预期 1）：%s" % vacuum_calls

    locked_lines = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        holds_lock = any(isinstance(it.context_expr, ast.Attribute) and it.context_expr.attr == "_lock"
                         for it in node.items)
        if not holds_lock:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                locked_lines.add(sub.lineno)
    for line in vacuum_calls:
        assert line in locked_lines, "VACUUM 被挪出业务锁了（审计 20261012 · D3 第三条明确不采纳）：store.py:%d" % line


def test_lock_contention_is_warned_but_still_blocks(tmp_path, caplog, monkeypatch):
    """等锁超过阈值只记 warning、不改变阻塞语义（口径与 checkpoint 的 M9 一致但不抛）。"""
    monkeypatch.setattr(store_mod, "DB_LOCK_WARN_AFTER_SECONDS", 0.2)
    fake = types.SimpleNamespace(_lock=threading.RLock())
    holder_release = threading.Event()

    def hold():
        fake._lock.acquire()
        holder_release.wait(5)
        fake._lock.release()

    t = threading.Thread(target=hold)
    t.start()
    time.sleep(0.05)  # 让持锁线程真的拿到锁

    entered = []
    with caplog.at_level(logging.WARNING, logger="memory_agent.store"):
        def waiter():
            with store_mod.Store._lock_with_contention_warning(fake, "_db"):
                entered.append(True)

        w = threading.Thread(target=waiter)
        w.start()
        time.sleep(0.6)  # 超过阈值：warning 应该已经落日志，但仍没拿到锁
        holder_release.set()
        w.join(10)
        t.join(10)

    assert entered == [True], "等锁的那一路最终必须拿到锁（阻塞语义不许变成快速失败）"
    assert not w.is_alive() and not t.is_alive()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "等业务锁超过" in text, "超阈值没有记 warning：%r" % text
    assert "_db" in text
