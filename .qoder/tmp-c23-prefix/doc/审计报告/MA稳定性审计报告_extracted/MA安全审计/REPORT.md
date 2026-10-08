# memory-agent 审计报告（稳定性 / 功能性 scoped run）

> 工具：cloudflare/security-audit-skill（六阶段流程）
> 目标：`/data/workspace/memory-agent-main`（zip 快照，无 `.git`，mtime `2026-09-30T13:15`）
> 运行 ID：`ma-sec-audit-run-1`　profile：`standard`　状态：`complete`
> 校验器：`coverage-ledger` PASS（21 单元）｜`findings` PASS（7 记录）

---

## ⚠️ 先说一件重要的事：技能口径与你的诉求不完全一致

你要求「找出影响**稳定性跟功能性**的 bug」。而 `security-audit` 这个技能的纪律是**只确认跨越信任边界的安全发现**——它明确禁止把「稳定性缺陷」「缺失最佳实践」「generic crash」提升为 confirmed 安全漏洞（见 SKILL.md「Require a boundary and result」与反模式第 1/2/5 条）。

所以本轮采用**双轨交付**：

| 产物 | 口径 | 内容 |
|---|---|---|
| `findings.json` | 技能严格口径 | 7 条事实全部成立，但均**不构成安全边界违反**，按纪律记 `rejected` |
| **`STABILITY-BUGS.md`** | **你的诉求** | **7 条稳定性/功能性缺陷，按严重度排序，含实测证据与修复方案** |

**你要的东西在 `STABILITY-BUGS.md`**，不在 findings.json。这不是偷换概念——如果我把它们硬塞进 confirmed，就违反了该技能"severity 不能超过已证实影响"的核心纪律，报告也就失去了它最值钱的东西。

---

## 实测环境（先交代清楚，因为它影响结论可信度）

| 项 | 情况 |
|---|---|
| Python | **3.10.12**（项目要求 `>=3.11`，存在版本差异） |
| 依赖 | 已装齐 pydantic / starlette / uvicorn / redis / httpx / bcrypt / python-jose / itsdangerous / pymysql / paho-mqtt / **mcp** |
| 测试基线 | 装齐 mcp 后：**6 failed, 543 passed, 16 skipped** |

> **一个值得注意的发现**：未装 `mcp` 时是 **24 failed**，装上后降到 **6 failed**。说明仓库 CI 必须装 `mcp`（pyproject 已声明 `mcp>=2.0.0`），否则 18 个测试会假失败。**如果你本地跑测试看到 20+ 失败，先确认 mcp 装了没。**

剩余 6 个失败中，5 个是 `test_unified_view.py`（依赖外部已有 `/data/memory_agent.db`，沙箱内被 `sqlite3.connect` 自动创建空库导致"DB 不存在则 skip"守卫失效而硬失败），1 个是 `test_openshs_bench`（算法输出，已有报告覆盖，本轮不重复）。

---

## 本轮 7 条发现（均为**新增**，已比对仓库内 4 份既有审计报告）

| # | 缺陷 | 严重度 | 实测 |
|---|---|---|---|
| 1 | `runtime.shutdown()` 遗漏 4 个常驻后台任务的 cancel | **高** | ✅ 实测 |
| 2 | `debug_routes._CONV` 会话上下文无上限、驱逐不同步 | 中高 | ✅ 实测 |
| 3 | `ha_db._get_conn()` 无锁并发连接泄漏 | 中（可触发性存疑） | ✅ 实测（合成场景） |
| 4 | purge 失败被 `except Exception: pass` 静默吞掉 | 中 | 静态确认 |
| 5 | `reload_config()` 覆盖 `ha_db` 前未关旧连接 | 中低 | 静态确认 |
| 6 | `LLMProvider.close()` 创建未跟踪 task | 低 | 静态确认 |
| 7 | `insight_routes` 的 `create_task` 无引用无兜底 | 低 | 静态确认 |

详见 `STABILITY-BUGS.md`。

---

## 一条被证伪的候选（诚实记录）

delegate  hunter 曾报「`except asyncio.CancelledError: pass` 不 break 导致 `while True` 循环继续」。**经我复核证伪**：

- `except asyncio.CancelledError` 缩进 8，`while True` 缩进 12 → except 在 while **外层**，`pass` 后函数即返回
- 实测：cancel 后新增循环轮数 **0**，`task.done()` = **True**

任务会正常结束，不会无限循环。该候选不成立，已从结论中剔除。

---

## 独立验证者的两处纠正（已采纳）

我请了独立验证者复核，两处**我原本的表述夸大**已修正：

1. **`_CONV`「100% 孤儿」夸大** —— `acp_server.py:89` 的 `SessionStore.delete()` 会 `_CONV.pop(sid)`，ACP 路径**有**清理入口。泄漏面应限定为 **HTTP debug 入口**（且需 `require_user` 认证）。
2. **`ha_db`「16 线程并发」是合成场景** —— 源码中 `ha_db` 的实际调用点是**单点**（`poller.py` 附近一处 `asyncio.to_thread`），不存在实体级扇出并发。代码缺陷成立，但**实际可触发性低于我初始声明**。

这两处我都改了。夸大的危害不亚于漏报。

---

## 未覆盖（scoped run 声明）

21 个单元中 **12 个 `out_of_scope` 未审**，绝不等同于"无问题"。未审面包括：JWT/鉴权与 `require_admin`、MCP stdio 工具与 scope 门、ACP 协议、HA 出向 HTTP、`config.py` 密钥加载、备份恢复、视觉服务、MQTT 桥接、SQL 注入面、依赖供应链。

**本轮聚焦稳定性/功能性，不代表安全面已审计。**
