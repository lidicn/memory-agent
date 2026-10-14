# memory-agent ACP 对接文档

> 面向 **autoflow 开发者**：本文档描述 memory-agent 暴露的 ACP（Agent Client Protocol）端点，
> 供你实现兼容的 ACP 客户端 / 服务端，达成 **拓扑 X（peer-to-peer，2 容器）** 的双向互通。
>
> 版本：memory-agent ACP v1 · 传输：JSON-RPC 2.0 over HTTP + SSE

---

## 1. 拓扑与角色

```
┌──────────────────────────┐         ACP JSON-RPC (HTTP+SSE)        ┌──────────────────────────┐
│   memory-agent (ACP)    │ <───────────────────────────────────> │       autoflow (ACP)     │
│   /acp  server+client   │                                        │       /acp  server+client│
└──────────────────────────┘                                        └──────────────────────────┘
        ↑ OpenCode / Zed 也可直连任一侧                                      ↑ 反之亦然
```

- 两端都是 **ACP agent（server 侧）**，同时内嵌 **ACP client**。
- 互通不需要中心 hub；任一端都可以用 `delegate` 工具主动调用对端。
- 跨容器只能走 **HTTP**（stdio 无法跨容器 spawn 进程）。

---

## 2. 端点与传输

| 项 | 值 |
|---|---|
| Base URL | `http(s)://<memory-agent-host>/acp` （`/acp/` 也接受） |
| 协议 | JSON-RPC 2.0 |
| 请求 | `POST` + `Content-Type: application/json`，body 为单个 JSON-RPC 请求 |
| 非流式方法响应 | `application/json`（JSON-RPC response / error） |
| `prompt` 响应 | `text/event-stream`，每个事件：`event: message\ndata: <JSON-RPC 通知>` |
| `GET /acp` | 返回服务说明（非 JSON-RPC），便于探活 |

> `prompt` 的响应本身就是 SSE 流；流结束 = 本轮会话结束。**不**再发送单独的
> JSON-RPC `result`。完成状态通过 `session_update` 通知的 `status` 字段表达。

---

## 3. 鉴权

所有 `/acp` 请求必须携带 **kind=`acp`** 的令牌（与 WebUI JWT、MCP 的 `mcp_` 令牌完全隔离）：

```
Authorization: Bearer acp_<REDACTED-Bearer字面量 len=17>
# 或
x-acp-token: acp_xxxxxxxxxxxxxxxx
```

### 生成 ACP 令牌

通过 memory-agent 既有的令牌接口（需管理员登录）：

```bash
curl -X POST 'https://<host>/api/mcp/tokens' \
  -H 'Authorization: Bearer <管理员JWT>' \
  -H 'Content-Type: application/json' \
  -d '{"name":"autoflow-peer","kind":"acp","prefix":"acp_"}'
```

响应中的 `token` 字段即为 `acp_xxx`，**明文仅此一次返回**，请妥善保存。

未携带 / 非法 / 非 `acp` 用途的令牌会被拒绝，并返回 ACP 错误体
（`{"jsonrpc":"2.0","id":null,"error":{"code":-32000,"message":"..."}}`）。

---

## 4. JSON-RPC 方法

所有方法均为 JSON-RPC 2.0。请求示例：

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
```

### 4.1 `initialize`

返回 agent 元信息、能力声明，以及**权威工具清单**（工具随版本更新，请以运行时返回为准）。

```jsonc
{
  "jsonrpc": "2.0", "id": 1,
  "result": {
    "agent": {"name":"memory-agent","version":"1.0.0","vendor":{"name":"memory-agent"}},
    "capabilities": {"streaming":true,"cancellation":true,"sessions":true,"tools":true},
    "tools": [
      {"name":"get_member_persona","description":"...","inputSchema":{...}},
      {"name":"get_behavior_insights","description":"...","inputSchema":{...}},
      {"name":"delegate_to_autoflow","description":"...","inputSchema":{...}},
      "..."  // 其余 builtin 类工具（只读查询类）
    ]
  }
}
```

- `tools` 默认包含 **builtin 暴露的只读/查询类工具**（家庭记忆检索、画像、洞察等）
  以及 **`delegate_to_autoflow`**（委派工具）。
- 如需让 agent 拥有写/变更类能力，可在 memory-agent 侧调整 `build_acp_tools()` 的
  工具面（改为暴露 mcp 类）；默认保守只给只读集。

### 4.2 `prompt`

驱动一次 agent 会话，**流式返回**。支持多轮：传入上一次的 `sessionId` 即可续接上下文。

```jsonc
{
  "jsonrpc":"2.0","id":2,"method":"prompt",
  "params":{
    "sessionId":"acp_...（可选；省略则服务端新建）",
    "messages":[{"role":"user","content":"帮我看看小明昨天的作息有没有异常"}],
    "model":"（可选）覆盖模型",
    "maxRounds":6
  }
}
```

响应为 SSE 流，每条 `session_update` 通知形如：

```jsonc
{"jsonrpc":"2.0","method":"session_update",
 "params":{
   "sessionId":"acp_xxx","id":2,"status":"running",
   "content":[{"type":"tool_call","name":"get_behavior_insights","arguments":{...},"result":null}]}}
```

`status` 取值：`running` → `completed` / `aborted` / `error`。
`content` 是内容块数组，块类型见 §5。

> 多轮约定：客户端应缓存首次 `prompt` 返回的 `sessionId`，后续追问复用同一 `sessionId`。

### 4.3 `cancel`

中止某个进行中的会话（按 `sessionId`）。

```json
{"jsonrpc":"2.0","id":3,"method":"cancel","params":{"sessionId":"acp_xxx"}}
```

返回 `{"sessionId":"acp_xxx","status":"cancelling"}`。真正的 `aborted` 事件会经该会话的
SSE 流下发（若仍连接）。

### 4.4 会话管理（ACP 扩展方法）

| 方法 | 参数 | 说明 |
|---|---|---|
| `session.new` | `{sessionId?}` | 预建会话，返回 `sessionId` |
| `session.list` | `{}` | 列出本服务已知会话 |
| `session.history` | `{sessionId}` | 返回该会话的对话历史（`messages`） |
| `session.delete` | `{sessionId}` | 删除会话及其上下文 |

---

## 5. Content Block（内容块）格式

`session_update.params.content` 数组元素：

| type | 字段 | 说明 |
|---|---|---|
| `text` | `text: string` | 自然语言输出 / 最终答案 |
| `tool_call` | `name: string`, `arguments: object`, `result: string\|null` | 工具调用；`result` 为 `null` 表示尚未拿到返回值 |
| `status` | `status: string`, `message: string` | 仅状态提示（如正在选择 LLM 后端） |

示例（最终完成）：

```jsonc
{"jsonrpc":"2.0","method":"session_update",
 "params":{"sessionId":"acp_xxx","id":2,"status":"completed",
   "content":[{"type":"text","text":"小明昨天 23:40 才入睡，偏离基线约 2 小时……"}]}}
```

---

## 6. 错误码

| code | 含义 |
|---|---|
| `-32700` | 解析错误（非合法 JSON） |
| `-32600` | 非法请求（非 JSON-RPC 2.0） |
| `-32601` | 方法不存在 |
| `-32602` | 参数错误（如 `prompt` 缺指令） |
| `-32001` | 会话不存在 / 无进行中任务 |
| `-32000` | 鉴权失败（令牌缺失 / 无效 / 用途非 acp） |

---

## 7. 快速接入示例（Python · httpx）

```python
import json, httpx

ACP_URL = "https://<memory-agent-host>/acp"
ACP_TOKEN = "acp_xxxxxxxxxxxxxxxx"

def acp_request(method, params=None, id_=1, stream=False):
    payload = {"jsonrpc":"2.0","id":id_,"method":method,"params":params or {}}
    headers = {"Authorization": f"Bearer {ACP_TOKEN}",
               "Content-Type":"application/json",
               "Accept":"text/event-stream" if stream else "application/json"}
    with httpx.Client(timeout=300) as c:
        if not stream:
            r = c.post(ACP_URL, json=payload, headers=headers)
            return r.json()
        # prompt：流式消费 SSE
        with c.stream("POST", ACP_URL, json=payload, headers=headers) as r:
            sid = None
            event = ""
            for line in r.iter_lines():
                if not line:
                    continue
                if line.startswith("event:"):
                    event = line.split(":",1)[1].strip()
                elif line.startswith("data:"):
                    data = line.split(":",1)[1].strip()
                    if event == "message":
                        msg = json.loads(data)
                        p = msg.get("params", {})
                        sid = p.get("sessionId")
                        for blk in p.get("content", []):
                            if blk["type"] == "text":
                                print("AGENT:", blk["text"])
                        if p.get("status") in ("completed","aborted","error"):
                            print("STATUS:", p.get("status"))
                            break
            return {"sessionId": sid}

if __name__ == "__main__":
    tools = acp_request("initialize")
    print("TOOLS:", [t["name"] for t in tools["result"]["tools"]])
    acp_request("prompt", {"messages":[{"role":"user","content":"总结本周家庭作息"}]}, stream=True)
```

---

## 8. Peer-to-Peer 委派约定（memory-agent ↔ autoflow）

拓扑 X 的核心是「agent 自主委派」：当一端判断任务更适合对端处理时，调用
**`delegate_to_autoflow`**（memory-agent 侧）/ 对等的 `delegate_to_memory_agent`
（autoflow 侧）工具，把自然语言任务交给对端 agent。

- **调用方式**：委派工具内部通过 HTTP 调用对端的 `/acp` `prompt`，并消费其 SSE 流，
  将对端最终 `completed` 的 `text` 块作为委派结果返回。
- **配置（memory-agent → autoflow）**：环境变量
  - `AUTOFLOW_ACP_URL=https://<autoflow-host>/acp`
  - `AUTOFLOW_ACP_TOKEN=acp_xxx`（autoflow 为 memory-agent 生成的 kind=acp 令牌）
- 若未配置，工具会返回友好提示而非报错，避免强耦合。
- **对称要求**：autoflow 侧需提供等价的 `/acp` 端点，并为 memory-agent 签发 kind=acp 令牌。
  两端协议完全一致（同一份本文档），谁实现 server、谁实现 client 互相对称。

### 委派消息约定（建议）

```jsonc
// memory-agent 调用 autoflow 的 prompt
{"jsonrpc":"2.0","id":1,"method":"prompt",
 "params":{"messages":[{"role":"user","content":"<自然语言任务>"}],"context":{...}}}
// autoflow 回包：session_update(status=completed, content=[{type:"text",...}])
```

若需带结构化上下文，可放在 `messages` 的 user content 中，或在扩展字段 `context` 透传
（★ **更正 2026-10-14**：`delegate_to_autoflow` 当下⛔ 转发 `context`——工具 schema 两处都声明了这个键，
但实作只读 `task`，详见 §10.4。当前唯一可靠的带上下文方式＝把它写进 user content 文本里）。

---

## 9. 限制与说明

- `prompt` 的完成以 SSE 流结束为准；部分客户端库若要求 `prompt` 也返回 JSON-RPC `result`，
  请基于 `session_update` 的 `status` 自行判定完成。
- 会话存储为**单进程内存**实现，服务重启后会话上下文丢失（与现有 debug 会话一致）。
- 工具面默认只读，变更/写入类能力可按 §4.1 说明在 memory-agent 侧放开。
- 本端点的任何改动都不影响既有 `/mcp`、`/api/debug/llm/*`、WebUI JWT 与 Node-RED Basic Auth。

---

## 10. 窄切片的适用判定与现状自查（DCD 已裁；2026-10-14 补本节）

依据：`关键决策部\decisions\20260929-ADM协议选型MCP与ACP-裁定.md` §三「ACP 的正确位置：窄切片 + 四条护栏」（`:46-57`）与 §五 落地顺序第 3 条「ACP 窄切片文档化（MA 侧）…现状的 MA→autoflow 委派按此规范自查」（`:83`）。
**ACP ⛔ 作为联动标准协议，只作为「语义委派」的窄切片保留。**

### 10.1 四条护栏：四条**同时**满足才许用 ACP 式委派

| # | 判据 | 反例（应当走 MCP） |
|---|---|---|
| 1 | 任务本身是**模糊**的 | 能写成结构化参数的查询 |
| 2 | 需要**对端 LLM 的判断力** | 纯数据搬运（token 成本＋幻觉＋⛔ 可审计） |
| 3 | 结果**允许不确定性** | 任何安全链路——委派结果⛔ 作设备写入的直接依据 |
| 4 | 带 **token 预算**与**降级** | 裸奔：无上限、超时/失败后⛔ 退回结构化查询 |

**反面清单（⛔ 许走 ACP 委派）**：取成员列表、查事件、下发自动化、改任何配置/状态。这些都是确定性操作，LLM 中介只会引入幻觉与延迟。
**场景边界**：ACP 的体验收益只花在**建议类／叙事类**（如生成一段周报），⛔ 花在**事实类**——委派链路里"对端 LLM 把数据整理错了"事后⛔ 可复算，而本生态最值钱的纪律是诚实报告／fail-closed／可复算。

### 10.2 现状自查（本仓实作逐条对撞，取法随读）

取法：`grep -n "delegate_to_autoflow" src/memory_agent/{acp_client,acp_server}.py` ＋ 读 `acp_client.py:43-121`。

| 护栏 | 现读 | 结论 |
|---|---|---|
| 1 模糊任务 | 唯一调用点＝ACP 循环内的工具派发（`acp_server.py:276`），由对端 LLM 主动发起；`delegate_to_autoflow`（`acp_client.py:105`）⛔ 出现在任何确定性代码路径里 | ✓ 满足（就 MA 侧射程而言） |
| 2 需判断力 | 入参实际只取 `task`（自然语言，`acp_client.py:107`）；schema 另 declare 的 `context` ⛔ 进 payload ⇒ 见 §10.4 | ✓ 满足（就"任务是否模糊需判断"这条而言） |
| 3 ⛔ 进安全链路 | MA 侧把返回原样包成 `{"delegated":True,"result":…}`（`:119`），⛔ 接线到设备写入／配置变更 | ✓ 满足（**对端**是否拿委派结果去下发自动化＝⛔ 在 MA 射程，本仓⛔ 能背书） |
| 4 token 预算＋降级 | 超时**有**：`acp_prompt_remote(timeout=300.0)`（`:49`，`httpx.AsyncClient(timeout=timeout)` `:68`）；失败**出声**：`except` → `{"error": …}`（`:120-121`），未配置对端时返回提示而非异常（`:111-116`）。★ **但两处⛔ 满足**：① **无 token 预算**——出向 payload（`:52-61`）里⛔ `budget`/`max_tokens` 类字段，收集面（`:67-102`）也⛔ 上限；② **无降级**——失败只出声，⛔ 退回任一 MCP 结构化查询 | ✗ **半条**（超时✓／预算✗／降级✗） |

⇒ **本仓现状对四条护栏是「3 ✓ ＋ 1 半」**。补④的那两手（预算上限取多少、降级回哪一条结构化查询）＝**跨仓接口变更**（要动 payload 形状、且要 AF 侧同步），按契约表口径须先过 DCD 评审，⛔ 本仓自决数值，故本节只登记缺口、⛔ 落码。挂账＝任务表 **#126**。

### 10.3 给未来接入方的两句硬话

- 新增任何 `delegate_to_*` 之前，先按 §10.1 逐条打勾；四条⛔ 齐 ⇒ 改走 MCP 工具。
- ⛔ 配置对端时委派**必须**是可选路径（现状✓：`AUTOFLOW_ACP_URL`／`AUTOFLOW_ACP_TOKEN` 缺失即友好返回，⛔ 强耦合）。

### 10.4 ★ 自查副产品：`context` 三面承诺 ↔ 实作丢键（一处假承诺，本仓自有面）

同一次自查顺带量出来的，形状＝在册判例「对外文案⛔ 承诺载荷里没有的键」那一族，只是方向相反：**schema 承诺了、实作⛔ 做**。

| 面 | 现读（取法随附） | 说了什么 |
|---|---|---|
| ACP `initialize` 工具表 | `build_acp_tools()`：`acp_server.py:226-229` | `"context": {"type":"object","description":"可选的补充上下文（任意 JSON）"}` |
| ACP 循环的 LLM 工具表 | `ACP_TOOLS`：`acp_server.py:265`（`required` 同表只含 `task`，`:267`） | `"context": {"type": "object"}` |
| 本文档 §8 | `:260-261`（已就地更正，原字⛔ 留） | 曾写「已支持 `context` 参数，会随任务一并转发」 |
| **实作** | `acp_client.py:105-121`；`grep -n "context" src/memory_agent/acp_client.py` ⇒ **0 命中** | 只取 `args["task"]`，`context` **静默丢弃**；出向 payload（`:52-61`）里⛔ 该字段 |

⇒ 对端 LLM 若照 schema 传 `context`，会**没有任何出声**地丢——比"⛔ 支持"更糟的是"支持了但⛔ 生效"。测试面现读：`grep -rn delegate_to_autoflow tests/ scripts/` 命中的三条都只断言**工具在场**（`test_acp_server.py:29/:93`、`test_arena_api.py:173`、`scripts/_acp_smoke.py:51`），⛔ 一条锁转发 ⇒ 现网无人能测出这枚。

**三案（⛔ 本仓自决，等 DCD）**：甲＝实作里真的把 `context` 并进出向请求（**动跨仓 payload 形状**，须 AF 同窗，按契约表口径走评审）；乙＝两处 schema 删掉 `context`（承诺收窄到与实作一致，代价＝对外工具面形状变更，同样⛔ 是本仓单方能动）；丙＝维持现状、只保留本节更正后的诚实文案（零代码零 schema 改动）。本席倾向**乙或丙**（⛔ 制造"看起来能用"的键），但两案都动对外面 ⇒ 挂 **#126** 等裁。
