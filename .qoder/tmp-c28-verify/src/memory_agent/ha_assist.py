"""HA Assist 集成（v1.0-2）：把家庭记忆注入 HA 会话回答。

MA 暴露 OpenAI 兼容的 ``/v1/chat/completions``（见 api/ha_assist_routes.py），
HA 的会话集成（extended_openai_conversation 等，base_url 指向本服务）即可让
Assist / 语音直接用上家庭记忆。

职责边界：
* 只做「记忆增强问答」——检索 agent_memories（live）拼进 system prompt，再交给
  LLM 回答；**不做** HA 设备控制（Assist 的设备控制仍由 HA 自身/其他集成负责）。
* 检索失败 / 未配置记忆条数 → 退化为普通 LLM，不报错。
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

_SYSTEM_PREFIX = (
    "你是这个家庭的智能助手，回答要简洁、自然、口语化，适合语音播报（一般不超过三句）。"
    "下面是与用户问题相关的家庭记忆（来自长期观察，可能有时效差异），"
    "请优先据此回答；若记忆不足以回答，再依据常识谨慎作答，不要编造事实。"
)


class HaAssist:
    """家庭记忆增强的会话回答器（供 HA Assist 调用）。"""

    def __init__(self, config, agent_memory, llm, insights=None):
        self.config = config
        self.agent_memory = agent_memory
        self.llm = llm
        self.insights = insights

    # ── 记忆检索 ──────────────────────────────────────────────────────────

    def _memories(self, query: str) -> list[dict]:
        k = int(getattr(self.config, "ha_assist_memory_top_k", 6) or 0)
        if k <= 0 or self.agent_memory is None:
            return []
        try:
            return list(self.agent_memory.retrieve(query, top_k=k) or [])
        except Exception as exc:  # noqa: BLE001 - 检索失败不得影响回答
            _log.warning("HA Assist 记忆检索失败: %s", exc)
            return []

    def build_messages(self, user_text: str) -> tuple[list[dict], list[dict]]:
        """构造发给 LLM 的 messages，并把家庭记忆拼进 system。返回 (messages, memories)。"""
        mems = self._memories(user_text)
        lines = [f"- {str(m.get('text') or '').strip()}" for m in mems if str(m.get("text") or "").strip()]
        system = _SYSTEM_PREFIX
        if lines:
            system = system + "\n\n【相关家庭记忆】\n" + "\n".join(lines)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ]
        return messages, mems

    # ── 回答 ──────────────────────────────────────────────────────────────

    async def answer(self, user_text: str) -> dict:
        user_text = (user_text or "").strip()
        if not user_text:
            return {"content": "请问你想了解什么？", "memories": []}
        messages, mems = self.build_messages(user_text)
        resp = await self.llm.chat(messages, temperature=0.3)
        content = (resp.get("content") or "").strip() or "抱歉，我暂时没想到合适的回答。"
        return {"content": content, "memories": [m.get("text") for m in mems]}
