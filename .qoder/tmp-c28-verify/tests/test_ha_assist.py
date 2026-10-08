"""v1.0-2 HA Assist 单测（无需网络/DB）。"""
import asyncio
import os
import sys
from types import SimpleNamespace

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.ha_assist import HaAssist  # noqa: E402
from memory_agent.api.ha_assist_routes import _last_user_text  # noqa: E402


class _FakeMemory:
    def retrieve(self, q, **kw):
        return [{"text": "客厅电视通常在晚上使用"}, {"text": "主卧空调设定 16 度"}]


class _FakeLLM:
    def __init__(self):
        self.called = None

    async def chat(self, messages, **kw):
        self.called = messages
        return {"content": "好的"}


def test_build_messages_injects_memories():
    svc = HaAssist(SimpleNamespace(ha_assist_memory_top_k=6), _FakeMemory(), _FakeLLM())
    msgs, mems = svc.build_messages("客厅电视几点开")
    assert len(mems) == 2
    assert "客厅电视通常在晚上使用" in msgs[0]["content"]
    assert msgs[1]["content"] == "客厅电视几点开"


def test_answer_uses_llm():
    llm = _FakeLLM()
    svc = HaAssist(SimpleNamespace(ha_assist_memory_top_k=6), _FakeMemory(), llm)
    res = asyncio.run(svc.answer("在吗"))
    assert res["content"] == "好的"
    assert llm.called is not None
    assert len(res["memories"]) == 2


def test_top_k_zero_no_memory():
    svc = HaAssist(SimpleNamespace(ha_assist_memory_top_k=0), _FakeMemory(), _FakeLLM())
    msgs, mems = svc.build_messages("x")
    assert mems == []
    # 不注入记忆段落（基础 system 提示本身含「家庭记忆」字样，故断言注入段头）
    assert "【相关家庭记忆】" not in msgs[0]["content"]


def test_last_user_text():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "你好"},
        {"role": "user", "content": "再问"},
    ]
    assert _last_user_text(msgs) == "再问"
    assert _last_user_text([{"role": "user", "content": [{"type": "text", "text": "图问"}]}]) == "图问"
    assert _last_user_text([]) == ""
