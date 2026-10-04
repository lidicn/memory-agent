# memory-agent 第十轮审计报告（终轮）

> 审计对象：https://github.com/lidicn/memory-agent （main 分支）
> 审计日期：2026-10-04
> 本轮方向：**生命周期 / 资源 / 历史修复回归**（V4 迭代 + 规划最后一轮）
> 前置：第一~九轮累计 P1×6、P2×8、P3×3
> 依据：《memory-agent_审计收敛规划.md》第十轮

---

## 一、工作流迭代 V4：三个探针 + 收敛度量

新增 `life_r10.py`：

| 探针 | 检测目标 |
|---|---|
| **R10-1** | 后台任务启停对称性（`task_registry.create` vs `cancel` / `shutdown`） |
| **R10-2** | 长跑资源增长点（运行态模块级容器无淘汰逻辑） |
| **R10-3** | 历史审计修复验证（抽取项目自带 7 轮审计的修复标记，验证落地） |

---

## 二、结论先行

> **本轮确认 1 个 P3（P3-4），无新 P1/P2。**
>
> 终止两个方向：**后台任务生命周期完全干净**，`task_registry` 的收口做得比多数项目都好。
>
> 但发现一个与前九轮性质不同的问题：项目自带 7 轮历史审计**留了 116 处修复标记**，而本轮十轮审计报出的缺陷**与它们几乎零重叠**——两套审计体系在看不同的东西。

---

## 三、R10-1：后台任务生命周期 —— 完全干净，无缺陷

```
task_registry.create 共 25 处（runtime.py 13 处为主）
```

初看比例失衡（create 25 / cancel 3 / shutdown 0），但追查后**全部证伪**：

```python
# task_registry.py
def create(...) -> asyncio.Task:
    task = asyncio.create_task(awaitable, name=name)
    self._pending[task] = name or task.get_name()   # 强引用，防 GC
    task.add_done_callback(self._on_done)
    return task

async def cancel_all(self) -> int:
    for t in list(self._pending): t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    self._pending.clear()
```

调用链完整：

```
runtime.shutdown()  ←  runtime.py:1046 调用
    └─ task_registry.cancel_all()   ←  runtime.py:800
```

`runtime.py` 的 13 个周期任务（retention / sweep / identity / mqtt / backup / tpl_validate / activity / device_feed / livingroom_ai / candidate_promotion / causal_scan / self_diary …）**全部经 `task_registry.create` 收口**，shutdown 时统一取消并等待退出。

`_on_done` 还做了三件正确的事：pop 出 `_pending`（防字典增长）、区分 cancelled 与真异常、异常记 warning 不吞。

**这是全项目工程质量最高的一块。** 强引用防 GC + 统一取消 + 异常不吞，三个常见坑都避开了。

---

## 四、P3-4　`_CLIENT_POOL` 在配置热更新下单调增长

**位置**：`src/memory_agent/ha_client.py:13`

```python
_CLIENT_POOL: Dict[tuple, httpx.Client] = {}

def _pooled_client(base_url, headers):
    key = (base_url, tuple(sorted(headers.items())))   # ← headers 含 Authorization
    with _POOL_LOCK:
        client = _CLIENT_POOL.get(key)
        if client is None:
            client = httpx.Client()
            _CLIENT_POOL[key] = client
        return client
```

### 问题

`key` 包含 `Authorization`（即 HA token）。**每次 token 轮换或配置重载都产生一个新条目**，旧的 `httpx.Client` 连同其 TCP 连接池**永不关闭、永不淘汰**。

实测：

```
固定 base_url + token：池条目 1（有界，无害）
轮换 4 次 token：      池条目 4，旧 Client 的连接不释放
```

### 定级 P3 的理由

- 正常运行（配置不变）**只有 1 条**，完全无害
- 需"token 轮换 / 反复重载配置"才增长，且增长极慢（每次 1 条）
- 单个 `httpx.Client` 空载开销小

**但它是唯一一个真会随时间单调增长的容器**（其余 4 个无淘汰容器见下，均有界）。

### 建议

配置变更时清理旧条目，或让 key 不含 token（只按 base_url，token 走 client.headers 更新）。

---

## 五、其余 4 个无淘汰容器：全部有界，证伪

| 容器 | 键 | 为何有界 |
|---|---|---|
| `circuit_breaker._BREAKERS` | 熔断器名 | 有限集，随代码定义固定 |
| `template_validate._STORES` | `data_dir` | 单实例下恒为 1 |
| `mcp_scopes._REPORTED_UNKNOWN` | 未登记工具名 | 受工具总数限制；仅日志去重 |
| `mcp_tokens._LEGACY_SCOPE_WARNED` | 令牌名 | 有限集；仅日志去重 |

R10-2 初版报 **124 处**，但绝大多数是**模块级常量字典**（`_ON_STATES`、`DEFAULT_*` 等，永不写入）。过滤到"被函数写入的运行态容器"后剩 **15 个**，再筛"无淘汰逻辑"后剩 **5 个**，最终只有 1 个真会增长。

**与第七轮的教训一致：噪音过滤比检测本身更耗工作量。**

---

## 六、R10-3：116 处历史修复标记，抽查 3 处均真落地

项目代码里留存了 **116 处历史审计修复标记**，34 种编号：

| 族 | 数量 |
|---|---|
| 第N轮审计（五/六/七） | 37 |
| WO-MA 外部工单 | 35 |
| 审计 P0/P1/P2 | 27 |
| 稳定性审计缺陷 | 9 |
| CRITICAL | 4 |
| 其他 / NEW-P0 | 4 |

抽查 3 处，**均非"注释声称已修但代码没改"**：

| 标记 | 位置 | 验证 |
|---|---|---|
| 审计 P0-11 | `store.py:528` | `MA_DB_INTEGRITY_CHECK` 三档 + quick 判红升级 full 复核，实现完整 |
| 第七轮 CRITICAL-2 | `history.py:69` | `_CHROMA_RETRY_SECONDS = 30.0` 重试冻结窗口，实现存在 |
| WO-MA-004 ⑤a | `app.py:376` | Basic Auth 与 JWT 汇到同一份爆破计数（`login_allowed`），实现存在 |

**结论：项目自带的历史审计修复是可信的**，不是"写下注释就算修了"。

---

## 七、一个贯穿性的观察：两套审计体系在看不同的东西

本轮十轮审计报出的缺陷，与项目自带 7 轮历史审计**几乎零重叠**：

| 我报的缺陷关键词 | 在历史审计文档中的命中文件数 |
|---|---|
| `identity_fusion`（P1-5） | **0** |
| 到家（P1-4） | **0** |
| `home_profile`（P1-6） | **0** |
| `behavior_predictor` | 1 |
| 时区（P1-3） | 4（唯一有交集的） |

历史审计的主题是：**数据库、启动稳定性、长跑稳定性、隐私脱敏、关节与连接处、FTS5 索引**。

我十轮的主题是：**语义一致性（时区口径 / 排序契约 / 归一化量纲 / 常量覆盖）、并发内存状态、输入边界、死代码**。

**这说明什么**：

- 历史审计偏**基础设施层**（能不能跑、跑得久不久、数据安不安全）
- 本轮审计偏**业务逻辑层**（算得对不对、边界守不守、有没有接上）

两者互补，互不替代。**若只做其中一套，另一半问题完全看不见。**

---

## 八、十轮总结

### 缺陷总表

| 编号 | 缺陷 | 级别 | 轮次 |
|---|---|---|---|
| P1-1 | MCP 错误契约在依赖缺失时静默失效 | P1 | 一 |
| P1-2 | 69 处静默降级为空值掩盖故障 | P1 | 一 |
| P1-3 | 净水器接口绕过统一家庭时钟 | P1 | 一 |
| P1-4 | 到家时间预测取当天最后事件 | P1 | 二 |
| P1-5 | 身份融合未匹配信号稀释已匹配结果 | P1 | 二 |
| P1-6 | 回家基线口径与同文件另一处相反 | P1 | 三 |
| P2-1 | 19 处 `zip()` 无 `strict` | P2 | 一 |
| P2-2 | 6 处 async 未卸载阻塞 I/O | P2 | 一 |
| P2-3 | 6 处死代码 | P2 | 一 |
| P2-4 | 到家后活动预测同日重复计天 | P2 | 二 |
| P2-5 | KEYWORD_DOMAINS 覆盖致 7 个英文关键词失效 | P2 | 四 |
| P2-6 | `_CONV` 对话上下文并发丢失更新 | P2 | 七 |
| P2-7 | MCP 技能读取接口路径遍历（2 个入口） | P2 | 八 |
| P2-8 | `learning_*` 1524 行死子系统（不可达 + 不可导入） | P2 | 九 |
| P3-1 | 4 个路由函数定义但未挂载 | P3 | 五 |
| P3-2 | `days` 极值 Overflow / 负值窗口反转 | P3 | 八 |
| P3-3 | `insights/persona.py` 完全死模块 | P3 | 九 |
| P3-4 | `_CLIENT_POOL` 配置热更新下单调增长 | P3 | 十 |

**合计：P1 ×6、P2 ×8、P3 ×4**

### 收敛判据评估

规划要求三条同时满足：

| 判据 | 状态 |
|---|---|
| ① 6 大类未审项全部 triage 完毕 | **部分满足**。并发、输入边界已完成；状态机/生命周期完成；但 54 个孤儿、11 个高复杂度函数、16 个委托方法规范化、20 处时区点**未逐项完成** |
| ② 连续 2 轮无新 P1/P2 | **未满足**。第七、八、九轮各有新 P2 |
| ③ P1 全有复现用例 + 修复建议 | **满足**。6 个 P1 均有复现与建议 |

**结论：停止条件未达成。** 按规划的诚实约定（见规划第七章），本报告能声称的只是：

> "在既定方法集（图谱 / 覆盖率 / 变体 / 类型 / 并发 / 输入边界 / 生命周期 / 历史回归）下，未发现更多缺陷。"

**不能**声称"已覆盖绝大部分 bug"。

---

## 九、未完成项清单（诚实交代）

| 项 | 规模 | 状态 |
|---|---|---|
| 54 个真·零引用孤儿函数逐个 triage | 54 | **仅核查 1 个**（第五轮） |
| 11 个 F/E 级高复杂度函数深入审查 | 11 | **仅核查 1 个**（第四轮 acp_handle） |
| 16 个 `self.legacy.*` 委托方法返回值规范化验证 | 16 | **仅抽查 1 个**（第六轮 device_usage） |
| 20 处时区 REVIEW 级点位 | 20 | 未逐项复核 |
| mypy 非 no-redef 类型错误 | 178 | 仅分类，未逐一验证 |
| R9-2 / R7-1 探针逻辑缺陷 | 2 | 已知未修 |

### 未纳入范围的维度

前后端契约（PWA / Node-RED）、部署脚本、Docker、认证授权边界、文件上传、SSRF、LLM prompt 注入、真实环境长跑、并发压测。

---

## 十、最高价值的后续动作（如果只做一件事）

**补测试，而非继续扫描。**

理由贯穿十轮：
- 第二轮：属性测试 9 函数 × 200 例**零崩溃**，但最严重的两个 P1 **都不崩溃，只是算错**
- 第三轮：P1-6 被测试"杀死"了，但那个测试喂的是 ASC 数据、生产是 DESC —— **测试绿、生产错**
- 第六轮：P1-4/P1-5/P1-6 所在模块 **0% 覆盖**

**扫描已经接近边际效益递减，测试缺口是真实且可量化的。**

优先补这三个（当前最高价值的测试缺口）：
1. `identity_fusion` — P1-5 所在，0% 覆盖
2. `behavior_predictor` — P1-4 所在，0% 覆盖
3. `api/behavior_routes` — 10% 覆盖

**新增排序相关单测必须用与生产一致的 DESC 数据**，否则重蹈 P1-6 覆辙。

---

## 十一、审计覆盖声明

- 本轮为**第十轮（终轮）**。R10-1 全量核查；R10-2 从 124 处筛至 1 处，中间层未逐一人工复核；R10-3 抽查 3 处 / 116 处。
- 所有缺陷的"实测"均为**复刻结构的独立脚本**，非在真实项目运行时复现。
- 未做：并发压测、长跑验证、真实 HA 环境、运行时模糊测试。
- 环境限制：Python 3.10（低于项目要求的 3.11）、Semgrep 四轮起均未安装。
- 十轮累计：静态分析为主，**动态验证仅占少数**。这是本次审计最大的方法学局限。
