# memory-agent 第十九轮审计报告：清理与保留路径（prune / 保留期）

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.18「清理路径：先问谁触发它」** —— 迁移 AutoForge 盘点中未审计的盲区 B-03
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

前十八轮从未系统审过**清理/保留路径**。本轮把它作为独立维度，判据不是"清理函数写对了吗"，而是"**有没有周期触发器**"（lesson 162）。

**关键结论：本仓 17 个清理函数，实现全部正确；但其中 3 个没有周期调用者。**

### 确认缺陷

| 编号 | 级别 | 位置 | 一句话 |
|---|---|---|---|
| **MA-35** | 🔴 High | `runtime.py:179-182` | `purge_old`（events 保留期）/ `purge_idempotency` / `purge_mcp_audit` **只在容器启动时执行一次**，运行期再无调用点 ⇒ 声明的保留期（默认 90 天）在运行期不生效；而 `store.purge_old` 的 docstring 明确写着它是「周期任务…**定时跑**」 |

---

## 二、方法：v2.18 的迭代点

| 版本 | 做法 |
|---|---|
| v2.17（十八轮） | 只读契约 + 跨入口对表 |
| **v2.18（本轮）** | **清理路径：扫出 prune/purge/retention 函数 → 列全部调用点 → 判断是否在 `while True` 内** |

**lesson 163**：清理函数的**实现正确 ≠ 策略生效**。本仓被漏掉的三张表对应的 `purge_*` 实现全都对（分批删、持锁粒度已按审计 P0-7 优化、幂等可重跑），问题 100% 出在触发缺失。

**lesson 164**：docstring 里写"周期任务/定时跑"是最直接的声明来源，可直接 grep。

---

## 三、确认缺陷

### 🔴 MA-35　保留期清理只在启动时跑一次，运行期策略失效

#### 唯一调用点位于 `startup()`

```python
# runtime.py:177-182
# 数据清理移到后台异步执行，不阻塞启动
# events 表 100 万行时 DELETE 可能需数分钟，同步执行会卡死 startup
if self.config.data_retention_days > 0:
    self._retention_task = task_registry.create(
        self._run_retention_cleanup(), name="runtime.retention_cleanup"
    )
```

`_run_retention_cleanup()`（runtime.py:137-147）内部依次调用：

| 表 | 函数 | 调用点 |
|---|---|---|
| `events` | `store.purge_old(data_retention_days)` | **仅 runtime.py:141（startup）** |
| `idempotency_keys` | `store.purge_idempotency()` | **仅 runtime.py:144（startup）** |
| `mcp_audit` | `store.purge_mcp_audit(30)` | **仅 runtime.py:145（startup）** |

⇒ 经全量 grep 确认，这三个函数**在代码库中各只有这一个调用点**，且该调用点不在任何 `while True` 周期任务内（AST 已验证）。`reload_config()` 也不会重建该任务。

#### docstring 明确声明它是周期任务 —— 与实现不符

`store.py:4579 purge_old` 的注释（原文）：

> 代价：不再是一个原子事务。保留清理是幂等的**周期任务**（``runtime.py:137`` 走 ``to_thread`` **定时跑**），中途失败下次接着删即可，不值得为它牺牲全局可用性的两分钟。

⇒ **文档说"定时跑"，实际只在启动时跑一次。** 这是"声明未兑现"族的第 7 例。

#### 实测：模拟常驻容器运行一年（冻结时钟推进）

```
[启动 T0=2026-01-01] events = 365 行
   purge_old(90) 删除 274 行 ⇒ 余 91 行            ✅ 保留 90 天，机制正确
   ⇒ 但 _run_retention_cleanup 只在 startup() create 一次，之后无调用点

[运行一年后 T1=2027-01-01] events = 456 行（未再清理）
   实际保留跨度 = 456 天      声明 = 90 天

⇒ 超出 366 天（5.1×）
```

⇒ 若每周重启一次：约累积到 ~97 天（可接受）；**若按家庭服务器常态长期不重启：随运行时长线性增长，无上限**（lesson 165）。

#### 可达性：这是用户可配置项，且 UI 直接暴露

```
config.py:87                data_retention_days: int = 90  # 数据保留天数
api/config_routes.py:30     "data_retention_days",           ← WRITABLE_FIELDS
static/js/pages/collect.js  <label>数据保留（天）</label>
                            placeholder="0 表示永久保留"
```

⇒ 用户在设置页填 90，UI 不提示"仅重启时生效"，系统也不告警 —— **静默失效**。

#### `mcp_audit` 是增长最快的一张

每次 MCP 工具调用都落一行（`mcp_server.py:716 _record_mcp_call` → `store.log_mcp_audit`），实测模拟 2000 次调用 → 2000 行。30 天保留同样只在启动时应用一次。

#### 定 High 的四条理由

1. **用户可配置、UI 暴露的文档化功能失效**，且无任何提示
2. **无界**：随运行时长线性增长，不收敛
3. **docstring 自证设计意图就是周期的** ⇒ 是遗漏，不是有意设计
4. 无告警、无健康面提示，只能靠观察磁盘发现

#### 修复方向

把三张表的清理纳入一个每日周期任务（照抄 `_periodic_agent_memory_sweep` 的形状，已有 `_periodic_*` 模板可循）：

```python
async def _periodic_retention_cleanup(self) -> None:
    interval = max(3600, int(getattr(self.config, "retention_cleanup_interval_seconds", 86400)))
    while True:
        try:
            await asyncio.sleep(interval)
            if self.config.data_retention_days > 0:
                n = await asyncio.to_thread(self.store.purge_old, self.config.data_retention_days)
            n_idem = await asyncio.to_thread(self.store.purge_idempotency)
            n_audit = await asyncio.to_thread(self.store.purge_mcp_audit, 30)
            print(f"[Runtime] 保留清理：events -{n}，幂等键 -{n_idem}，MCP 审计 -{n_audit}")
        except asyncio.CancelledError:
            return
        except Exception as exc:
            print(f"[Runtime] 保留清理失败: {exc}")
```

并同步修正 `purge_old` 的 docstring（或让声明成真）。

---

## 四、验证通过（确认无问题）

| 项 | 结论 |
|---|---|
| **真正周期的清理（6 个）** | ✅ `purge_behavior_anomalies`（runtime.py:698）、`purge_behavior_drifts`（:723）、`purge_rule_triggers`（device_feed → runtime.py:628）、`_cleanup_snapshots`（vision patrol loop）、`_prune_revoked`（auth.py:226）、`_evict_if_needed`（alert_dispatcher.py:53）—— **全部在 `while True` 内** |
| **`expire_overdue_agent_memories`** | ✅ 经 `sweep_and_reconcile` → `_periodic_agent_memory_sweep`（runtime.py:842）周期触发 |
| **软上限是否有界** | ✅ `_evict_if_needed`（1000 sessions，超限删最旧 10%）与 `_trim`（200 sessions）都挂在**插入路径**上（`should_send` / `new`），虽不阻止新增但实际把容器约束在上限附近 ⇒ **非缺陷**（lesson 166） |
| **`purge_old` 实现质量** | ✅ 分批删 + 每批只锁到提交（审计 P0-7：原实现持锁 143 秒，已修）；`DELETE ... rowid IN (SELECT ... LIMIT ?)` 规避 SQLite 非标准语法 |
| 17 个清理函数 | ✅ **实现无一错误**，问题只在触发 |

---

## 五、修复建议

### MA-35（一处）

见上节。两条都要做：**加周期任务** + **修正 docstring**（否则声明继续误导下一个人）。

### 建议加门禁

> `check_cleanup_scheduled.py`：断言所有 `purge_*` / `prune_*` / `expire_*` 函数，
> 其调用点中**至少有一个**位于 `while True` 循环内（或显式豁免登记）。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | 启动后连续运行 7 天不重启 | events 表跨度 ≈ 90 天（而非 97+） |
| 2 | 模拟运行一年 | 保留跨度仍 ≈ 90 天 |
| 3 | `mcp_audit` 连续调用 | 30 天前的记录被清理 |
| 4 | 清理失败 | 有日志，且下次周期重试 |
| 5 | `purge_old` docstring | 与实际触发方式一致 |

---

## 六、横向观察

### "实现做对了，只差接上" —— 第 19 次同形，但这次是最纯粹的一次

前几轮的同形是"接口接好了、依赖注入了、只差一行"（MA-25、MA-29）。本轮更纯粹：**清理函数本身写得很好**（还专门优化过锁粒度），**只差一个周期调用者**。

⇒ 这类缺陷的共同点是：**写它的人完成了"这个函数"，但没有完成"这个机制"**。机制 = 函数 + 触发器，而触发器不在这个人的视野里。

### AutoForge 盲区迁移第二次奏效

第九轮把 AutoForge 的"无界状态增长/软上限"族迁移过来，命中 MA-20/21/22 三条。本轮迁移 B-03（prune/GC 清理路径），命中 MA-35。**跨项目缺陷族清单在有第二个项目后变成了双向资产。**

### 又一次"防护做对了但只做在到达的地方"

17 个清理函数里 14 个有周期触发（含 6 个明确周期 + 挂插入路径的软上限），**只有 3 个漏了**——而漏掉的这 3 个恰好都在同一个 `_run_retention_cleanup` 里（它们是一起被漏的，不是各自独立遗漏）。

⇒ 说明不是"每个函数各自忘了接"，而是"**当年把它设计成启动任务时，就没有设计对应的周期版本**"。这与第十五轮 MA-31（把清理移到后台不阻塞启动时，顺手选了一次性任务）在时间线上也吻合——**很可能就是那次改动引入的**。

---

## 七、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 用 `auditlib` 原语 | ✅ `all_walk` / `own_walk` / `full_unparse` / `rel_path` |
| **门 2** | 命中多时手工分诊 | ✅ **决定性**：17 个清理函数 → 逐个列调用点 → 判断是否在 `while True` 内 → 3 个无周期触发；软上限挂插入路径的 2 个排除 |
| **门 3** | 每条缺陷有实测 + 如实标注 | ✅ 冻结时钟模拟一年（456 天 vs 声明 90 天 = 5.1×）；AST 验证无 `while True` 包裹；UI/config 可达性确认；**明确写出"机制正确、触发缺失"** |

### 遗留队列（十九轮累积）

| 项 | 状态 |
|---|---|
| **清理/保留路径** | ✅ 本轮闭合（17 个函数全量核查） |
| **MCP 只读契约** | ✅ 十八轮闭合 |
| **跨仓 outbound trace_id** | ✅ 十七轮闭合 |
| **配置面三方一致性** | ✅ 十六轮闭合 |
| **Store 共享连接 / 分页 / 重试** | ✅ 十五轮闭合 |
| **MCP 登记表 / 启动期断言** | ✅ 十三轮闭合 |
| **锁覆盖** | ✅ 十二轮闭合 |
| **logging 格式串** | ✅ 十四轮闭合 |
| 常驻周期任务 | ⚠️ 已报 MA-31；`device_feed` 观察项 |
| `candidate_promotion` NaN | ⚠️ 观察项（实测不可达） |
| `redis_host` / `redis_port` | ⚠️ 预留字段 |
| `recent_audit` 死代码 | ⚠️ 无调用方 |
| MA-03 端到端 | ⚠️ 未复现（`chromadb` 太重） |
| 47 处静默降级中"否定方向"44 处 | ⚠️ 仅抽查 |
| M2 的 30 处无钳制 `LIMIT` 站点 | ⚠️ 抽查 |
| `fail-closed` 39 处声明 | ⚠️ 已核两支 |

### 环境

| 项 | 值 |
|---|---|
| 已装 | `bcrypt`、`python-jose`、`pymysql`、`starlette`、`httpx`、`tomli` |
| 仍未装 | `chromadb`（体积过大）、`mcp` SDK（模块可导入，仅打印警告） |
| 门禁 | `run_gates.py . --no-smoke` → rc=0（208 条基线） |
| 新增 lessons | **162–166**（已并入 `lessons-round2.md`，共 1738 行） |
| 仓库状态 | 探针已还原；源码、量具、门禁、基线均未修改 |
