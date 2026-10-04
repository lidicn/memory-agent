# memory-agent 第五轮审计报告

> 审计对象：https://github.com/lidicn/memory-agent （main 分支）
> 审计日期：2026-10-04
> 本轮方向：**孤儿函数全量 triage + 路由接漏检测**
> 前置：第一~四轮累计 P1×6、P2×5
> 依据：《memory-agent_审计收敛规划.md》第五轮

---

## 一、工作流迭代 V2：修正了一个会造假的探针缺陷

本轮最先做的不是找 bug，而是**修掉第二轮 R2-1 探针的一个根本缺陷**。

### 缺陷：跨模块导入的调用被全部漏计

R2-1 用"函数名在全项目是否出现"判断可达性，但只统计了 `ast.Name` 和 `ast.Attribute` 节点。

**问题在于**：`from .identity_fusion import fuse` 里的 `fuse` 是 `ast.alias` 节点，**不是 `ast.Name`**。

结果：凡是通过 `from X import Y` 跨模块调用的函数，全部被误判为"零引用孤儿"。

### 修正（V2）

补三类可达性证据：
1. `ast.alias` —— 导入名与 `as` 别名
2. 字符串引用 —— 动态分发（`getattr(obj, "name")`、字符串表驱动）
3. 装饰器注册 —— `@mcp.tool()` / `@router.get()` 等

### 修正效果

| 版本 | 判为"真·死代码" |
|---|---|
| R2-1（第二轮） | 56 |
| **R5-2（修正后）** | **54**（68 个候选中 14 个被证伪为可达） |

修正幅度不大，但**方向是关键**：如果不修，后续所有基于"孤儿"的判断都在一个错误基线上。

> 这类"探针自身缺陷"比"漏掉一个 bug"更危险——它系统性地污染一整类结论。第二轮报告的孤儿数（68）虽然数值接近修正后的 54，但那是巧合；修正后的 54 是**可靠的**，68 不是。

---

## 二、R5-1　路由接漏：4 个接口定义了但从未挂载

**位置**：`src/memory_agent/api/behavior_routes.py`

| 函数 | 行号 | 用途 |
|---|---|---|
| `behaviors_list_rules` | 370 | 列出主动规则 |
| `behaviors_add_rule` | 382 | 添加主动规则 |
| `behaviors_update_rule` | 412 | 更新主动规则 |
| `behaviors_delete_rule` | 430 | 删除主动规则 |

### 确证

该文件的路由是**文件末尾 `Route(...)` 列表集中注册**（非装饰器）。路由表中只有：

```python
Route("/api/behaviors/rules", behaviors_rules, methods=["GET"])
```

—— 注意挂载的是 `behaviors_rules`（另一个函数，只读 STATIC 规则列表），**不是** `behaviors_list_rules`。

全项目检索这四个函数名：**无任何 Route 挂载、无装饰器、无 import、无调用**。

### 定级：P3（未完成功能 / 死代码），非 P1/P2

**为什么不是缺陷**：前端全项目检索无调用（`/api/behaviors/rules` 仅一处 GET），用户不会遇到 404，无功能受损。

**但它确实是接漏**：Phase 3.1 主动规则引擎的 CRUD 接口，写了四个完整实现（含鉴权 `require_user`、参数校验、返回值封装），**一个都没接线**。这是明确的功能未完成。

**风险**：若后续有人按函数名以为接口存在去对接，会拿到 404。且这 4 个函数零测试覆盖。

### 建议

要么接线（补 Route 注册 + 前端调用），要么删除。**留着不接线的完整实现是最坏状态**——它看起来像功能，实际不是。

---

## 三、R5-1 探针的通用价值

这个探针填补了前三轮的一个盲区。

前三轮检测"未接线"的方式都有局限：

| 方法 | 局限 |
|---|---|
| PyCG 调用图（第一轮） | 报 1346 个孤儿，看不见装饰器注册，噪音极大 |
| R2-1 装饰器感知（第二轮） | 修掉了装饰器误报，但漏了 `ast.alias` |
| 两者共同盲区 | **都无法区分"路由 helper"和"未挂载的路由处理函数"** |

R5-1 的做法：直接比对「定义了 request 处理函数」与「Route 表挂载名」的差集，并排除 `_` 开头的内部 helper。

**全项目结果**：13 个未挂载 → 排除 9 个 `_` 开头 helper（`_check_device_token`、`_bearer_or_cookie_token` 等，正常）→ 剩 **4 个真接漏**，全部集中在 `behavior_routes.py`。

命中率高、噪音低，性价比优于前两轮的孤儿检测。已固化为 `ma_audit.py` 的 `probe_route_leak`。

---

## 四、第五轮产出小结

| 项 | 结果 |
|---|---|
| 新缺陷 | **1 个 P3**（4 个未挂载路由接口） |
| 工作流改进 | **2 项**：R5-1 路由接漏探针、R5-2 孤儿 triage（修 `ast.alias` 漏计） |
| 修正前轮结论 | 第二轮 R2-1 的"68 个孤儿"基线不可靠，修正后为 54 个真·零引用 |

**本轮无新 P1/P2。**

按规划的收敛判据（连续 2 轮无新 P1 **且**无新 P2），目前是第 1 轮零 P1/P2 产出。

---

## 五、54 个真·零引用清单（未逐个人工复核）

按模块聚合：

| 模块 | 数量 | 代表 |
|---|---|---|
| `store.py` | 5 | `get_arena_result`、`latest_behavior_state`、`delete_activity_rule` |
| `patterns.py` | 5 | `update_pattern`、`delete_pattern`、`confirm_pattern`、`reject_pattern`、`import_patterns` |
| `insights/parser/entity.py` | 5 | `behavior_entities`、`telemetry_entities`、`is_telemetry`、`enrich` |
| `candidate_promotion.py` | 3 | `check_promotion_criteria`、`cache_stats`、`recent_audit` |
| `api/behavior_routes.py` | 4 | **已确认为 R5-1 接漏** |
| 其余 | 32 | 见 `audit_out/orphan_triage_v2.json` |

**这些多为"功能预留"或"未接线实现"**，与 R5-1 那 4 个同性质。逐个判断"该接线还是该删"需要业务上下文，本轮未做——列入规划第九轮收口。

值得注意的是 `patterns.py` 的 5 个（`update_pattern`/`delete_pattern`/`confirm_pattern`/`reject_pattern`/`import_patterns`）：PatternManager 有一整套 CRUD 方法全部零引用，与 R5-1 是同族问题（**类方法层级的接漏**，R5-1 只覆盖路由函数层级）。

---

## 六、规划执行状态

- [x] 第五轮 · 孤儿函数与死代码 triage + 路由接漏
- [ ] 第六轮 · 双轨实现口径比对（37 个共享方法）
- [ ] 第七轮 · **并发与竞态**（新维度，预期产出最高）
- [ ] 第八轮 · **外部输入边界**（新维度）
- [ ] 第九轮 · 未测区域收口（15 个 0% 模块 + 11 个高复杂度 + learning_* + 54 个孤儿）
- [ ] 第十轮 · 生命周期 / 资源 / 历史修复回归

工作流迭代：
- [x] V2 · 遗留项可追踪化 + 孤儿 triage 修正（`ast.alias`）
- [ ] V3 · 并发 / 输入边界 / 生命周期探针（第七轮前）
- [ ] V4 · 收敛度量 + 历史审计修复验证器（第十轮前）

---

## 七、审计覆盖声明

- 本轮为**第五轮**，完成孤儿函数 triage 与路由接漏检测。
- 54 个真·零引用函数**未逐个人工复核**，仅对 4 个路由接漏做了完整确证。
- R5-1 只覆盖 `api/*_routes.py` 的路由函数层级，**未覆盖类方法层级**（如 `patterns.py` 的 5 个 CRUD）。
- 本轮未做并发压测、未审查锁竞争、未触及外部输入边界。
- 环境限制照旧：Python 3.10（低于项目要求的 3.11）、Semgrep 未安装。
