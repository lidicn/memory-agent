"""A3 P2-6（`_CONV` 读→await→写回非原子，同 conversation 并发丢失 87.5%）的回归锁。

裁定与修复都记在台账里（`api/debug_routes.py` 按 conversation_id 上 `asyncio.Lock`），
但**此前没有任何用例锁住它**——这条缺陷靠"改完就认为好了"是不成立的。

口径：`_execute_run` 里读 `_CONV` 与写回之间夹着 `await rt.llm.chat(...)`。
本锁用假 LLM 在 chat 里主动让出事件循环，把交错窗口放大到必然发生：
- 三个任务同一个 `conversation_id` 并发 ⇒ 加锁时 3 条指令全部在档（4 条消息）；
  不加锁时它们都读到"还没有上下文"，最后一个写回赢，前两条**整条消失**（2 条消息）。
- 三个不同 `conversation_id` ⇒ 不该被互相串行化，各自 2 条（这一档是"别修过头"的对照）。

不用 `pytest.mark.asyncio`，直接 `asyncio.run`，与 `tests/test_acp_server.py` 同口径。
"""

import asyncio

from memory_agent.api import debug_routes as dr


class _FakeLLM:
    """chat 里让出事件循环——交错窗口是本锁唯一依赖的机制。"""

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        await asyncio.sleep(0.02)
        return {"content": "收到", "tool_calls": []}


class _FakeRT:
    def __init__(self):
        self.llm = _FakeLLM()


def _mk_run(idx, conversation_id):
    return dr.DebugRun(run_id="run-%d" % idx, instruction="第%d条指令" % idx,
                       model="m", mode="debug", max_rounds=1,
                       conversation_id=conversation_id, temperature=0.2)


def _clean(monkeypatch):
    monkeypatch.setattr(dr, "_CONV", {})
    monkeypatch.setattr(dr, "_CONV_ORDER", [])
    monkeypatch.setattr(dr, "_CONV_LOCKS", {})
    monkeypatch.setattr(dr, "_RUNS", {})
    return dr._CONV


def _gather(runs):
    async def main():
        rt = _FakeRT()
        await asyncio.gather(*[dr._execute_run(rt, r) for r in runs])
        return rt
    return asyncio.run(main())


def test_same_conversation_concurrent_runs_do_not_lose_instructions(monkeypatch):
    conv = _clean(monkeypatch)
    runs = [_mk_run(i, "conv-shared") for i in range(3)]
    _gather(runs)

    kept = [m["content"] for m in conv["conv-shared"] if m["role"] == "user"]
    assert len(kept) == 3, "并发丢失：%r" % (kept,)
    assert sorted(kept) == ["第0条指令", "第1条指令", "第2条指令"]
    # 上下文只有一份 system 头，不是每个并发任务各写一份
    assert sum(1 for m in conv["conv-shared"] if m["role"] == "system") == 1


def test_the_lock_actually_serialises_the_same_conversation(monkeypatch):
    """机制档：同 conversation 的三次 chat 必须串行（时间上不重叠），否则上一条只是巧合。"""
    _clean(monkeypatch)
    overlap = {"max_in_flight": 0, "now": 0}

    class _CountingLLM(_FakeLLM):
        async def chat(self, messages, **kwargs):
            overlap["now"] += 1
            overlap["max_in_flight"] = max(overlap["max_in_flight"], overlap["now"])
            await asyncio.sleep(0.02)
            overlap["now"] -= 1
            return {"content": "收到", "tool_calls": []}

    async def main():
        rt = _FakeRT()
        rt.llm = _CountingLLM()
        await asyncio.gather(*[dr._execute_run(rt, _mk_run(i, "conv-x")) for i in range(3)])
    asyncio.run(main())
    assert overlap["max_in_flight"] == 1, "并发在飞 %d 个，锁没生效" % overlap["max_in_flight"]


def test_different_conversations_are_not_serialised(monkeypatch):
    """对偶档（别修过头）：不同 conversation 各自独立，消息数与隔离都要对。"""
    conv = _clean(monkeypatch)
    _gather([_mk_run(i, "conv-%d" % i) for i in range(3)])
    for i in range(3):
        assert len(conv["conv-%d" % i]) == 2
    assert len(conv) == 3


def test_lock_released_after_run_so_the_next_turn_is_not_deadlocked(monkeypatch):
    """锁没在 finally 里放掉的话，第二轮会永久卡住——这里用"顺序第二轮能跑完"来锁释放。"""
    conv = _clean(monkeypatch)
    _gather([_mk_run(0, "conv-y")])
    _gather([_mk_run(1, "conv-y")])
    kept = [m["content"] for m in conv["conv-y"] if m["role"] == "user"]
    assert kept == ["第0条指令", "第1条指令"]
    assert dr._CONV_LOCKS["conv-y"].locked() is False


def test_conv_order_tracks_each_conversation_once(monkeypatch):
    """FIFO 台账：同一 conversation 并发写回不许在 _CONV_ORDER 里长出重复项。"""
    _clean(monkeypatch)
    _gather([_mk_run(i, "conv-z") for i in range(3)])
    assert dr._CONV_ORDER.count("conv-z") == 1
