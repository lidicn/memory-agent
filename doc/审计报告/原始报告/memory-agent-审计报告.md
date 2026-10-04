# memory-agent 全面审计报告

> 审计对象：`https://github.com/lidicn/memory-agent`（main 分支）
> 审计日期：2026-09-30
> 审计方式：**沙箱实测**（真实装依赖、真跑测试套件、真起服务、真复现），非静态阅读

---

## 一、审计方法与实测环境

| 项目 | 实际做法 |
|---|---|
| 代码获取 | `codeload` 拉取 main 分支源码（GitHub 直连 403，走 codeload 通道成功） |
| 运行环境 | Python 3.11.14 虚拟环境（项目要求 `>=3.11`，Dockerfile 用 `python:3.11-slim`） |
| 依赖安装 | 按 `pyproject.toml` 与 `Dockerfile` 口径装齐：pydantic / starlette / uvicorn / redis / httpx / bcrypt / python-jose / itsdangerous / pymysql / paho-mqtt / **mcp** |
| 测试执行 | `pytest tests/` 全量跑（CI 口径 + 无 ignore 口径各跑一次） |
| 运行时验证 | Starlette `TestClient` 挂载 `combined_app`，发真实 HTTP 请求穿过完整中间件/路由/鉴权栈 |
| 缺陷复现 | 对每条疑似 bug 写独立复现脚本，构造真实数据对比"期望 vs 实际" |

**实测结果总览**：`77 failed, 484 passed, 13 skipped, 7 errors` —— 共 **84 项失败/错误**。

---

## 二、结论先行

> **CI 实际上是红的，而且红在第一个测试。**

按 `.github/workflows/ci.yml` 原文命令复现（`-x` 遇错即停）：

```
FAILED tests/test_acp_server.py::test_handle_initialize
!!!! stopping after 1 failures !!!!
1 failed, 6 passed
```

仓库配置了 CI，但流水线**从未真正跑通**。这意味着 84 个失败项长期无人处理，代码处于"测试写了但没人看结果"的状态。这是本次审计最严重的元问题——它解释了为什么下面这些 bug 能活到今天。

按影响面排序，实测确认的缺陷如下。

---

## 三、P0 · 功能性失效（实测复现，直接影响核心能力）

### P0-1　`InsightService` 参数顺序错位 —— 设备实体目录永久为空

**位置**：`src/memory_agent/runtime.py:69` vs `src/memory_agent/insights/api.py:69`

定义签名是 `(store, config)`，调用却写成了 `(config, store)`：

```python
# runtime.py:69  实际写法
self.insights = InsightService(self.config, self.store)
#                             ^^^^^^^^^^^^  ^^^^^^^^^^  ← 顺序反了

# insights/api.py:69  定义
def __init__(self, store: Any = None, config: Optional[InsightConfig] = None, ...)
```

于是 `StoreRepository.store` 拿到的是 `Config` 对象，`store.db_query()` 直接 `AttributeError`。

**实测复现**（建真实 `events` 表并注入 5 行数据，含 `light.study_desk`）：

```
A) InsightService(store, config)   → 实体目录 1 条
B) InsightService(config, store)   → 实体目录 0 条   ★runtime.py:69 实际如此
   ↑ 并抛 'Config' object has no attribute 'db_query'
```

**为什么没人发现**：异常被 `_safe_entities()` 的 `except Exception` 静默吞掉，只落一行 WARNING：

```
加载实体目录失败: 'Config' object has no attribute 'db_query'
```

我在**真实启动服务**时就捕获到了这行日志——服务照常起来、首页 200、不报错，用户完全无感。

**影响**：`EntityResolver` 拿到空实体表 → 所有"房间名/设备中文名 → entity_id"的解析失效 → 自然语言查询（`NLQueryEngine`）、行为洞察、设备用量统计的核心输入被掏空。这是项目主打的"家庭行为挖掘"能力的上游。

**修复**：`InsightService(self.store, self.config)`；同时把 `_safe_entities()` 的裸 `except` 改为记录 `LOG.exception`，别再静默降级。

---

### P0-2　MCP 工具「登记了但没实现」—— 5 个工具是空头支票

`tool_schema.TOOL_NAMES` 声明 50 个工具，实测 `mcp_server` 实际注册的少了 5 个：

| 登记但缺失实现的工具 | 说明 |
|---|---|
| `route_question` | **文档里明确要求"拿到用户问题后第一步调用"的入口规划工具** |
| `analyze_camera` | 摄像头分析 |
| `get_vision_status` | 视觉状态 |
| `list_vision_cameras` | 摄像头列表 |
| `query_behavior_events` | 行为事件查询 |

**影响**：`route_question` 在 `tool_schema.py:70-86` 被写成 MCP 调用入口的第一站，`expose=("mcp","builtin")`，还在 `mcp_scopes.REGISTERED_TOOLS` 里登记了 —— 但 `mcp_server.py` 里根本没有对应实现。外部 Agent 按文档调用会直接失败。**README 宣称的"通过 MCP 供 AI Agent 调用"这条主链路第一步就断了。**

---

### P0-3　MCP 权限判定自相矛盾 + 故障注入被短路

**实测输出**（这是同一条报错里的原文，自相矛盾）：

```
DENIED: 令牌 'verify' 无 write 权限，不能调用写工具 'get_member'
       （当前权限 read,write）
                    ^^^^^^^^^^  明明有 write，却说无 write 权限
```

配套日志：

```
WARNING mcp.scopes: MCP 工具未登记到 REGISTERED_TOOLS/WRITE_TOOLS，将被拒绝调用: get_member
WARNING mcp.stats: MCP 权限拒绝: token=verify tool=get_member need=write granted=['read','write']
```

**根因**：`mcp_scopes.requires()` 对未登记工具（`scope == UNKNOWN`）一律返回 `False`，权限层拿它当"需要 write 权限"拒绝，于是生成了上面那句逻辑不通的提示。

**次生后果**：故障注入（fault injection）机制被权限检查提前短路，永远拿不到预期错误码。实测 5 个错误码用例全挂：

```
assert 'NOT_FOUND' in "DENIED: 令牌 'verify' 无 write 权限..."
assert 'UPSTREAM_UNAVAILABLE' in "DENIED: ..."
assert 'RATE_LIMITED' in ... / 'INTERNAL' in ... / 'INVALID_PARAM' in ...
```

即：**MCP 的错误契约（`ErrorCode` 体系）根本无法被验证**，外部 Agent 依赖的错误处理分支全在未测状态。

---

### P0-4　MCP 工具重复注册 —— 5 个工具定义了两遍

导入 `mcp_server` 时实测报出：

```
Tool already exists: report_bug
Tool already exists: list_bug_reports
Tool already exists: write_self_diary
Tool already exists: read_self_diary
Tool already exists: generate_self_diary
```

定位到 `mcp_server.py` 中 `1933` 行与 `2088` 行附近各有**一份完整且逐字相同**的实现块（我 diff 过两处：`【两处完全相同：纯复制粘贴重复】`）。

**影响**：SDK 丢弃后注册的那份，当前"能跑"纯属侥幸。一旦两份实现日后被差异化修改，行为将由导入顺序决定，属于埋在暗处的维护地雷。

---

## 四、P1 · 稳定性缺陷（时序/健壮性，实测复现）

### P1-1　`time.monotonic()` 配 `0.0` 哨兵值 —— 重启后动作被静默吞掉 ★推荐优先修

代码把"从未触发过"记成 `0.0`，但 `time.monotonic()` 返回的是**系统启动至今的秒数**（不是 Unix 时间戳），两者语义不匹配：

```python
# announcer.py:66
if now - self._last.get(ev.kind, 0.0) < self.cooldown_sec:

# perception_rules.py:52-53
last = self._last_triggered.get(rid, 0.0)
return (time.monotonic() - last) < cooldown
```

**实测复现**（沙箱 uptime ≈ 1039 秒）：

```
cooldown_sec=30     → 首次 announce() 返回 True
cooldown_sec=300    → 首次 announce() 返回 True
cooldown_sec=1000   → 首次 announce() 返回 False  ★被误吞
cooldown_sec=3600   → 首次 announce() 返回 False  ★被误吞
cooldown_sec=86400  → 首次 announce() 返回 False  ★被误吞
```

内置规则实测：

```
stranger_alert    cooldown=300.0   首次触发 正常
day_night_log     cooldown=3600.0  首次触发 ★被永久抑制
```

**生产影响**：设备/容器重启后 uptime 归零。只要 cooldown 配得比当前 uptime 大，该规则或播报在 uptime 追平之前**一次都不会触发**，且**无任何报错**——静默丢失。`day_night_log`（1 小时冷却）意味着重启后一整小时内昼夜日志全空。

**修复**：哨兵改用 `float("-inf")`，或 `self._last.get(kind)` 判 `None` 后直接放行。

---

### P1-2　`_acp_kind` 缺 None 守卫 —— ACP 协议入口崩溃

**位置**：`src/memory_agent/acp_server.py:137`，被 `:357 / :444 / :445` 三处调用

```python
def _acp_kind(rt, scope) -> str:
    name = (scope.get("state") or {}).get("acp_token_name")
    #        ^^^^^^ scope 为 None 时直接崩
```

**实测**：`rt = None, scope = None` → `AttributeError: 'NoneType' object has no attribute 'get'`，3 个 ACP 测试挂掉（`test_handle_initialize` / `test_prompt_streams_events`），并直接导致 **CI 在第一个用例就 `-x` 中止**。

**修复**：`scope = scope or {}`。

---

## 五、P2 · 工程质量：回归保护已失效

以下不是单个 bug，而是"测试套件整体失去保护能力"，正是它们放任了 P0/P1 长期存活。

| # | 问题 | 实测证据 | 数量 |
|---|---|---|---|
| 1 | CI 未注入 `JWT_SECRET`，鉴权类测试必然抛错 | `RuntimeError: JWT 密钥未配置…不再接受默认值` | 13 |
| 2 | 测试硬编码容器路径 `/app/src/...`，非容器环境跑不了 | `FileNotFoundError: '/app/src/memory_agent/store.py'` | 5 |
| 3 | 测试 mock 过时，缺 `is_connected` | `'_FakeClient' object has no attribute 'is_connected'` → IndexError | 5 |
| 4 | 无 `pytest.ini`/`conftest.py`，pytest-asyncio 未开 auto 模式 | `async def functions are not natively supported` | 3 |
| 5 | API 改名后测试未同步：`AwayMode` → 实际 `AwayModeManager` | `ImportError: cannot import name 'AwayMode'` | 2 |
| 6 | API 改名后测试未同步：`_detect_activities` → 实际 `infer_activities` | `'InsightService' object has no attribute '_detect_activities'` | 3 |
| 7 | 门禁测试依赖未声明的外部包 `homesdk` | `ModuleNotFoundError: No module named 'homesdk'` | 2 |

补充实测：全项目 `async def test_` 共 12 个，其中仅 9 个带 `@pytest.mark.asyncio`，**3 个从未真正执行**。

---

## 六、P2 · 算法/业务逻辑输出为空（34 项）

这批用例的共同特征是**断言"应该有 N 条结果"却得到 0**，属能力退化而非断言写错：

- `test_activity_inference`（5 项）：序列匹配、置信度、习惯挖掘全 0
- `test_rule_recall`（6 项）：规则召回全部 `assert 0 == N`，部分 `IndexError`
- `test_perception_ingest` / `test_perception_rules`：房间通配符 `assert 0 == 1`、天数统计 `assert 0 == 3`
- `test_process_mining`：`assert 0 == 11`
- `test_run_template`：`KeyError: 'total_seconds'` / `'attribute'`，模板渲染字段缺失
- `test_signal_learning`、`test_identity`、`test_openshs_bench`、`test_causal_scanner` 等

其中相当一部分大概率是 **P0-1（实体目录为空）与 P1-1（冷却哨兵）的下游连带反应**——上游输入被掏空，下游自然全 0。**建议按 P0-1 → P1-1 → 复查本组 的顺序处理，不要孤立地逐个调断言。**

---

## 七、修复优先级建议

| 优先级 | 缺陷 | 改动量 | 收益 |
|---|---|---|---|
| 🔴 立刻 | P0-1 `InsightService` 参数顺序 | 1 行 | 恢复实体解析，解放大批下游 |
| 🔴 立刻 | P1-2 `_acp_kind` None 守卫 | 1 行 | 打通 ACP 入口，解开 CI |
| 🔴 立刻 | P1-1 monotonic 哨兵值 | 2 处 | 消除重启后静默丢动作 |
| 🟠 本周 | P0-2 补 5 个 MCP 工具实现 / 或从登记表移除 | 中 | MCP 主链路可用 |
| 🟠 本周 | P0-3 未登记工具走 `NOT_FOUND` 而非权限拒绝 | 小 | 错误契约可验证 |
| 🟠 本周 | P0-4 删除重复工具定义块 | 小 | 消除维护地雷 |
| 🟡 排期 | 补 `pytest.ini`（`asyncio_mode=auto`）+ CI 注入 `JWT_SECRET` | 小 | **让 CI 真正跑起来** |
| 🟡 排期 | 测试去容器路径硬编码、同步改名后的 API | 中 | 恢复回归保护 |

**最后一句**：这个仓库的问题不在"某个功能写错了"，而在**质量反馈回路断了**——CI 红在第一个用例，却没人看见。修好 CI（让它真实运行且必须转绿）比修任何一个单独 bug 都更值钱，否则下一批 P0 还会以同样方式活下来。

---

## 附：主要复现脚本

沙箱内保留，可自行重跑：

- `/data/workspace/madata/e2e.py` — 真实 HTTP 端到端（捕获到实体目录加载失败日志）
- `/data/workspace/madata/repro4.py` — P0-1 参数顺序对照实验（决定性证据）
- `/data/workspace/madata/repro6.py` — P1-1 announcer 冷却哨兵
- `/data/workspace/madata/repro7.py` — P1-1 规则引擎冷却哨兵
- `/data/workspace/run3.txt` — 全量测试失败清单
