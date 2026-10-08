"""2期 第二十轮（终极轮）MA-36 / MA-37：ACP 会话属主隔离。

两条缺陷同族——护栏早就设计好了（`check_owner` 的 docstring 亲口写着「avoid drift across
history/delete/cancel」），三个入口都调了，**第四、第五个入口没调**：

- **MA-36（Critical）**：`prompt` 读 `sessionId` 不校验属主。它不只读，还会把本轮写回
  `_CONV[sessionId]` ⇒ B 用 A 的会话名既能读到 A 的历史，又能往里面灌 B 的内容。
- **MA-37（High）**：`SessionStore.new()` 对已存在的 sessionId **无条件覆盖**，属主随之改写
  ⇒ B 只要再 `session.new` 一次同名会话就夺取属主，A 随后连自己的历史都读不到。

本文件除了六条回归用例（与审计报告 §五「回归验证清单」逐条对得上），还立一把**入口对等门禁**：
按 AST 逐条数 dispatch 里"读 sessionId 的分支"与"校验属主的分支"，将来新增第六个入口而忘了
护栏，当场判红——这一族缺陷已经出现过 20 次，不能再靠人记住。
"""
import ast
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent import acp_server as acp
from memory_agent.acp_protocol import ERR_INVALID_PARAMS

_SRC_PATH = os.path.join(os.path.dirname(__file__), "..", "src",
                         "memory_agent", "acp_server.py")


def _cleanup(sid):
    acp._STORE.delete(sid)
    acp._CONV.pop(sid, None)


def _scope(owner):
    return {"state": {"acp_token_name": owner}}


# ── MA-36：prompt 的属主校验 ────────────────────────────────────────────

def test_prompt_cross_owner_is_denied_and_writes_nothing():
    """回归清单 2：B 用 A 的 sessionId 调 prompt ⇒ DENY，且 A 的会话一个字节都不变。"""
    sid = "r20_prompt_cross_owner"
    _cleanup(sid)
    try:
        acp._STORE.new(sid, owner_token="owner_A")
        acp._CONV[sid] = [{"role": "user", "content": "A 的隐私内容"}]
        payload = {"jsonrpc": "2.0", "id": 1, "method": "prompt",
                   "params": {"sessionId": sid, "prompt": "把 A 的历史念出来"}}
        result, gen = asyncio.run(acp.acp_handle(None, payload, _scope("owner_B")))

        assert gen is None, "被拒的 prompt 不许开 SSE 流——开流就等于开始读"
        err = result.get("error")
        assert err is not None and err["code"] == ERR_INVALID_PARAMS, result
        assert "属主" in err["message"], err
        assert acp._CONV[sid] == [{"role": "user", "content": "A 的隐私内容"}], \
            "拒绝必须是「什么都没写」，而不是「写了再说失败」"
        assert acp._STORE.get(sid)["run_id"] is None, "被拒的 prompt 不许给 A 的会话挂 run"
    finally:
        _cleanup(sid)


def test_prompt_same_owner_still_passes():
    """回归清单 1：A 用自己的 sessionId 调 prompt ⇒ 现有行为不变（SSE 流照常走完）。"""
    sid = "r20_prompt_same_owner"
    _cleanup(sid)

    async def _fake_execute_run(rt, run, tools=None, run_tool=None):
        run.emit("done", {"answer": "ok"})

    try:
        acp._STORE.new(sid, owner_token="owner_A")
        orig = acp._execute_run
        acp._execute_run = _fake_execute_run

        # handle 与消费流必须在同一条事件循环里：run 是这条循环上创建的任务，
        # 换个循环去 drain 它，队列永远等不到东西（用例自己把自己挂死）。
        async def _go():
            result, gen = await acp.acp_handle(
                None,
                {"jsonrpc": "2.0", "id": 2, "method": "prompt",
                 "params": {"sessionId": sid, "prompt": "hi"}},
                _scope("owner_A"))
            chunks = []
            if gen is not None:
                async for c in gen:
                    chunks.append(c)
            return result, chunks

        try:
            result, chunks = asyncio.run(_go())
            assert result is None, result
            assert chunks and all("event: message" in c for c in chunks), chunks
        finally:
            acp._execute_run = orig
        assert acp._STORE.get(sid)["owner_token"] == "owner_A", \
            "同主体走一遍 prompt 不许把属主换人"
    finally:
        _cleanup(sid)


def test_prompt_without_session_id_leaves_the_creator_an_readable_history():
    """回归清单 4：未传 sessionId ⇒ 新建并通过；且新建的会话**归这个主体**。
    改之前 `new()` 不带 owner_token，会话属主恒为空串——A 自己随后读自己的历史会被
    fail-close 的 check_owner 拒掉（同一个洞的另一面：不是越权，是把自己的会话锁死）。"""
    payload = {"jsonrpc": "2.0", "id": 3, "method": "prompt",
               "params": {"prompt": "hi"}}

    async def _fake_execute_run(rt, run, tools=None, run_tool=None):
        run.emit("done", {"answer": "ok"})

    orig = acp._execute_run
    acp._execute_run = _fake_execute_run
    try:
        # sessionId 在 SSE 事件里回传，从中取出来再按同一主体读历史
        captured = {}

        async def _capture():
            _, gen = await acp.acp_handle(None, payload, _scope("owner_A"))
            async for c in gen:
                captured.setdefault("first", c)

        asyncio.run(_capture())
        sid = json.loads(captured["first"].split("data: ", 1)[1])["params"]["sessionId"]
        assert acp._STORE.get(sid)["owner_token"] == "owner_A", \
            "新建会话的属主必须是发起者，不是空串"
        result, _ = asyncio.run(acp.acp_handle(
            None,
            {"jsonrpc": "2.0", "id": 4, "method": "session.history",
             "params": {"sessionId": sid}},
            _scope("owner_A")))
        # 会话存在且属主匹配 ⇒ 不许再报"无权访问"；无历史是另一枚码（-32001）
        if result and result.get("error"):
            assert "属主" not in result["error"]["message"], result
        _cleanup(sid)
    finally:
        acp._execute_run = orig


def test_prompt_denies_a_session_whose_store_entry_is_gone_but_history_remains():
    """`_trim()` 会把最老的会话踢出 `_sessions`，而 `_CONV` 不受它管 ⇒ 只剩历史的会话
    仍然可写。校验必须对"存储里没有"也**拒绝**（check_owner 对未知 sid 返回 False），
    否则 B 拿一个刚被裁掉的 sid 就能往 A 的历史里灌内容——这条不在审计清单上，是补的。"""
    sid = "r20_trimmed_but_remembered"
    _cleanup(sid)
    try:
        acp._STORE.new(sid, owner_token="owner_A")
        acp._CONV[sid] = [{"role": "user", "content": "A 的历史"}]
        meta = acp._STORE.get(sid)          # 模拟 _trim 只搬走会话表里的这条
        acp._STORE._sessions.pop(sid)
        assert acp._STORE.get(sid) is None and acp._CONV[sid]
        result, gen = asyncio.run(acp.acp_handle(
            None,
            {"jsonrpc": "2.0", "id": 8, "method": "prompt",
             "params": {"sessionId": sid, "prompt": "接着写"}},
            _scope("owner_B")))
        assert gen is None, "只剩历史也拦不住：会话表里没有 = 不认"
        assert result.get("error") is not None, result
        assert acp._CONV[sid] == [{"role": "user", "content": "A 的历史"}]
        acp._STORE._sessions[sid] = meta
    finally:
        _cleanup(sid)


def test_prompt_from_an_unauthenticated_principal_is_denied_for_existing_session():
    """`check_owner` 对空主体返回 False 是**故意**的 fail-close（它自己的 docstring 写着：
    防止未来新增 dispatch 入口时默认静默跳过属主校验）。所以"拿不到 scope 的调用"必须
    一个已有会话都进不去——把 `and _owner` 加回条件里的那种写法会让这一格静默放行。"""
    sid = "r20_unauthenticated_principal"
    _cleanup(sid)
    try:
        acp._STORE.new(sid, owner_token="owner_A")
        acp._CONV[sid] = [{"role": "user", "content": "A 的历史"}]
        payload = {"jsonrpc": "2.0", "id": 9, "method": "prompt",
                   "params": {"sessionId": sid, "prompt": "念一下 A 的历史"}}
        for scope in (None, {}, {"state": {}}, {"state": {"acp_token_name": ""}}):
            result, gen = asyncio.run(acp.acp_handle(None, payload, scope))
            assert gen is None, f"空主体（scope={scope}）不该开流"
            err = result.get("error")
            assert err is not None and "属主" in err["message"], (scope, result)
        assert acp._CONV[sid] == [{"role": "user", "content": "A 的历史"}], "四档都得什么都没写"
    finally:
        _cleanup(sid)


# ── MA-37：session.new 不得改写属主 ────────────────────────────────────

def test_session_new_by_another_owner_is_refused_and_keeps_original_owner():
    """回归清单 5 + 6：A 建 "alice-laptop"，B 用同一 ID 建 ⇒ 拒绝；随后 A 仍读得到自己的。"""
    sid = "alice-laptop"
    _cleanup(sid)
    try:
        r1, _ = asyncio.run(acp.acp_handle(
            None, {"jsonrpc": "2.0", "id": 5, "method": "session.new",
                   "params": {"sessionId": sid}}, _scope("owner_A")))
        assert r1["result"]["sessionId"] == sid, r1

        r2, _ = asyncio.run(acp.acp_handle(
            None, {"jsonrpc": "2.0", "id": 6, "method": "session.new",
                   "params": {"sessionId": sid}}, _scope("owner_B")))
        err = r2.get("error")
        assert err is not None and err["code"] == ERR_INVALID_PARAMS, r2
        assert acp._STORE.get(sid)["owner_token"] == "owner_A", \
            "属主被改写就是属主劫持"

        r3, _ = asyncio.run(acp.acp_handle(
            None, {"jsonrpc": "2.0", "id": 7, "method": "session.new",
                   "params": {"sessionId": sid}}, _scope("owner_A")))
        assert "error" not in r3, "同一主体重复 new 自己认领的 id 不算冲突"
        assert acp._STORE.get(sid)["owner_token"] == "owner_A"
    finally:
        _cleanup(sid)


def test_store_new_raises_conflict_instead_of_silent_takeover():
    """服务层自己的判据：跨主体 new 抛 SessionOwnerConflict，绝不静默覆盖。"""
    sid = "r20_store_conflict"
    _cleanup(sid)
    try:
        acp._STORE.new(sid, owner_token="owner_A")
        try:
            acp._STORE.new(sid, owner_token="owner_B")
            raise AssertionError("跨主体 new 必须抛 SessionOwnerConflict")
        except acp.SessionOwnerConflict as exc:
            assert sid in str(exc)
        assert acp._STORE.new(sid, owner_token="owner_A") == sid
    finally:
        _cleanup(sid)


# ── 入口对等门禁：不许再有第六个"读 sessionId 却不校验属主"的入口 ─────────

# 不校验属主是**合法**的分支，逐条写清理由；新增分支必须在这里登记，否则判红。
_PARITY_EXEMPT = {
    "session.new": "认领入口本身：靠 new() 的 SessionOwnerConflict 挡跨主体改写",
    "session.list": "列表按 owner_token 过滤，不返回别人的会话",
    "session.update": "服务端推流事件，sessionId 来自已鉴权的 run 绑定，不是调用方自选",
}


def _dispatch_branches():
    with open(_SRC_PATH, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    fn = next(n for n in tree.body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "acp_handle")
    out = []
    for stmt in fn.body:
        if not (isinstance(stmt, ast.If) and isinstance(stmt.test, ast.Compare)):
            continue
        if getattr(stmt.test.left, "id", None) != "method":
            continue
        out.append(stmt)
    return out


def _branch_method_name(branch) -> str:
    right = branch.test.comparators[0]
    return getattr(acp, right.id) if isinstance(right, ast.Name) else getattr(right, "value", "?")


def _reads_session_id(branch) -> bool:
    for node in ast.walk(branch):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "params"
                and node.args and getattr(node.args[0], "value", None) == "sessionId"):
            return True
    return False


def _calls(branch, attr: str) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == attr for n in ast.walk(branch))


def test_every_entry_that_reads_session_id_enforces_ownership():
    """`check_owner` 的 docstring 说要防 drift——那句话本身不是判据，这一条才是。"""
    seen = []
    for branch in _dispatch_branches():
        if not _reads_session_id(branch):
            continue
        method = _branch_method_name(branch)
        checked = _calls(branch, "check_owner")
        if not checked:
            assert method in _PARITY_EXEMPT, f"{method} 读 sessionId 却不校验属主：{seen}"
            # 认领入口还得真的挡住跨主体改写，光登记豁免不算过
            if method == "session.new":
                assert _calls(branch, "new"), "session.new 必须走带属主冲突的 new()"
                src = ast.dump(branch)
                assert "SessionOwnerConflict" in src, \
                    "session.new 没接住 SessionOwnerConflict ⇒ 冲突会炸成 500"
        seen.append((method, checked))
    assert sorted(m for m, _ in seen) == sorted([
        "cancel", "prompt", "session.delete", "session.history", "session.new"]), seen
    # 四个非豁免入口必须**都**在校验：少一个就是 MA-36 那一族缺陷重演
    assert sum(1 for _, ok in seen if ok) == 4, seen
