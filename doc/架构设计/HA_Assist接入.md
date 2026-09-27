# HA Assist 接入（v1.0-2）：MA 作为家庭记忆增强的会话后端

> 创建：2026-09-16
> 目标：让 Home Assistant 的原生语音 / Assist 直接用上 MA 的家庭记忆。
> 关联路线图：`路线图_v0.7-v1.0_20260910.md` §六-2。

## 一、总体思路

MA 暴露一个 **OpenAI 兼容** 的会话端点，HA 的会话集成（推荐 HACS 的
`extended_openai_conversation`，也支持其他可自定义 Base URL 的 OpenAI 兼容集成）
把 Base URL 指向 MA。用户对 Assist/语音说的话，MA 会：

1. 用 `agent_memories`（live）做混合检索，取出与问题相关的家庭记忆；
2. 把记忆拼进 system prompt，交给 MA 的 LLM 后端回答；
3. 返回 OpenAI 格式的回答。

MA **只做「记忆增强问答」**，不做设备控制（设备控制仍由 HA 自身负责）。

## 二、MA 侧配置

| 配置项 | 说明 | 默认 |
|---|---|---|
| `ha_assist_token` | HA 调用凭据（Bearer）。**留空则端点关闭（401）** | `""` |
| `ha_assist_memory_top_k` | 注入的家庭记忆条数（0 = 不注入） | `6` |

在 WebUI「系统设置」填写 `ha_assist_token` 并保存（或写入 NAS `data/config.json`）。

> 端点路径：`POST /api/...`? 否 —— 为兼容 OpenAI 客户端，端点为
> `POST /v1/chat/completions` 与 `GET /v1/models`，与 MA 自身的 `/api/*` 隔离，
> 由 `ha_assist_token` 独立鉴权。

## 三、HA 侧配置（extended_openai_conversation 示例）

```yaml
# configuration.yaml
conversation:
  - platform: extended_openai_conversation
    api_key: "<ha_assist_token>"        # 与 MA 配置一致
    base_url: "http://192.168.2.200:8086/v1"
    model: "ma-family"
    prompt: "你是家庭助手，请简洁口语化地回答。"
    max_tokens: 300
```

保存后重启 HA，Assist 对话框 / 语音即可使用。

## 四、接口契约

### POST /v1/chat/completions

请求（OpenAI 兼容；`stream` 可选）：

```json
{
  "model": "ma-family",
  "messages": [
    {"role": "user", "content": "客厅电视一般几点开？"}
  ],
  "stream": false
}
```

响应（非流式）：

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1789548682,
  "model": "ma-family",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "..."},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

`stream=true` 时返回 `text/event-stream`，分块为 `chat.completion.chunk`，末尾 `data: [DONE]`。

### GET /v1/models

```json
{"object": "list", "data": [{"id": "ma-family", "object": "model", "owned_by": "memory-agent"}]}
```

### 鉴权

所有 `/v1/*` 请求需带 `Authorization: Bearer <ha_assist_token>`；未配置令牌则一律 401。

## 五、快速自测

```bash
curl -s http://192.168.2.200:8086/v1/chat/completions \
  -H "Authorization: Bearer <ha_assist_token>" \
  -H "Content-Type: application/json" \
  -d '{"model":"ma-family","messages":[{"role":"user","content":"家里晚上一般几点睡？"}]}'
```

## 六、说明 / 待办

- 首个版本仅「回答」，不含 HA 设备控制（tool calling）；如需语音控设备，可后续把
  HA 的 `tools` 透传/代理（roadmap 亦未要求）。
- 记忆检索依赖 embedding / FTS5；embedding 不可用时会自动回退关键词/纯 LLM（见 v0.9 离线降级）。
