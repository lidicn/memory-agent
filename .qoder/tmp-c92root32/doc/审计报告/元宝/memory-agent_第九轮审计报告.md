# memory-agent 第九轮审计报告

> 审计对象：https://github.com/lidicn/memory-agent （main 分支）
> 审计日期：2026-10-04
> 本轮方向：**未测区域收口**（规划中最大的遗留块）
> 前置：第一~八轮累计 P1×6、P2×7、P3×2
> 依据：《memory-agent_审计收敛规划.md》第九轮

---

## 一、工作流迭代：两个新探针

| 探针 | 检测目标 |
|---|---|
| **R9-1** | 包内模块用「绝对导入」引用同包兄弟模块（`from learning_xxx import` 而非 `from .learning_xxx import`） |
| **R9-2** | 死子系统：一组模块互相引用，但整个组无任何包外调用方 |

---

## 二、结论先行

> **本轮确认 1 个 P2（P2-8）、1 个 P3（P3-3）。**
>
> 最重要的发现不是"某行代码写错了"，而是**一整块 1524 行的子系统，既没人调用，也根本导入不了**。
>
> `learning_*` 家族：8 个模块、722 语句、0% 覆盖，唯一入口 `learning_api` 无调用方，且家族内 18 处绝对导入使其**以包方式导入时必然 ModuleNotFoundError**。

---

## 三、P2-8　`learning_*` 子系统：不可达 + 不可导入

### 规模

| 模块 | 行数 | 语句数 |
|---|---|---|
| `learning_api.py` | 230 | 110 |
| `learning_feedback.py` | 234 | 134 |
| `learning_optimizer.py` | 233 | 108 |
| `learning_evaluator.py` | 186 | 94 |
| `learning_store.py` | 222 | 85 |
| `learning_models.py` | 191 | 91 |
| `learning_analyzer.py` | 135 | 58 |
| `learning_report.py` | 93 | 42 |
| **合计** | **1524** | **722** |

### 问题一：完全不可达

```
learning_api（唯一入口）
  ├─ learning_analyzer
  ├─ learning_evaluator
  ├─ learning_feedback
  ├─ learning_optimizer
  │    └─ learning_analyzer
  ├─ learning_report
  │    ├─ learning_analyzer
  │    ├─ learning_evaluator
  │    └─ learning_optimizer
  └─ learning_store
       └─ learning_optimizer
```

全项目检索 `learning_api` / `build_router`：**无任何调用方**（`src/` 与 `tests/` 均无）。

### 问题二：即便接线也立即崩溃

家族内 18 处使用**绝对导入**：

```python
# learning_optimizer.py:26
from learning_analyzer import WeakSpot          # ← 不是 from .learning_analyzer
```

以包方式导入时：

```
>>> from memory_agent import learning_store
ModuleNotFoundError: No module named 'learning_models'
>>> from memory_agent import learning_api
ModuleNotFoundError: No module named 'learning_models'
```

### 铁证：同项目内存在正确写法

`llm_client.py:27-30`：

```python
try:
    from .task_registry import task_registry       # ← 先尝试相对导入
except ImportError:
    from task_registry import task_registry        # ← 失败才回退绝对导入
```

**作者知道正确的跨兼容写法**，并在 `llm_client` 里用了。但 `learning_*` 家族写的是**裸绝对导入、无 try 兜底**。

这不是"风格不统一"，是**这组代码从未被真正加载过**——否则第一次 import 就会炸。

### 定级 P2 而非 P1

对当前线上行为**零影响**（没人调用，不会崩溃）。定 P2 的依据是：

- 占 1524 行 / 722 语句，是仓库最大的不可达块
- 处于"看起来像功能、实际跑不起来"的状态，与第五轮 P3-1（4 个未挂载路由）同族但规模大两个数量级
- 若后续有人按 `learning_api.build_router()` 接线，会立即失败

### 建议

二选一：
1. **启用**：改 18 处为相对导入（或加 try/except 兜底），接线并补测试
2. **删除**：1524 行死代码是持续维护负担

**不建议维持现状。**

---

## 四、P3-3　`insights/persona.py` 完全死模块

`PersonaBuilder` 类（63 语句）：全项目**无任何引用**，未被 `insights/__init__.py` 导出。

原因可推断：Phase 3 迁移把 `_synthesize_persona` 迁到了 `insights/utils.py::synthesize_persona`，`persona.py` 是被取代的旧实现。

```python
# insights/__init__.py:44 —— 导出的是 utils 版，不是 persona.py
from .utils import ..., synthesize_persona, ...
```

定 P3：规模小（63 语句），不影响运行。

---

## 五、0% 覆盖模块的逐个核查（证伪为主）

### 已核查且安全

| 位置 | 风险点 | 判定 |
|---|---|---|
| `learning_optimizer.py:170` | `share = weight / total`，total 可能为 0？ | **安全**：`total = sum(reason_weights.values()) + EPS`；`signal_weight()` 各项因子（KIND_BASE_WEIGHT、`clamp(confidence,0,1)`、`decay>0`、`repeat_boost≥1`）**均非负**，故 sum ≥ 0，total ≥ EPS > 0 |
| `learning_evaluator.py:86-90` | 多处 `/denom` | **安全**：`denom = total + EPS`；`usage_rate` 另有 `(used+unused) > 0` 守卫 |
| `behavior_predictor.py:150` | `count / total_windows` | **安全**：`total_windows` 仅在 `if window_events:` 内自增，而 `activity_counts` 也只在同块内累加 → 有 counts 必有 total_windows ≥ 1 |
| `identity_fusion.py:213/317` | `/len(stranger_signals)` | **安全**：均有 `if stranger_signals` 守卫 |
| `identity_fusion.py:210` | `sorted_scores[0] - sorted_scores[1]` | **安全**：有 `if len(scores) >= 2` 守卫 |
| `llm_client.py:29` | 绝对导入 | **安全**：try 相对 / except 绝对，是正确范例 |

### 一处遗留（未定级）

`identity_fusion.py:134` `temporal_decay()` 中 `/tau` 无守卫。默认 `tau=300.0`，仅当配置显式设为 0 时触发 `ZeroDivisionError`。配置项路径未在本次审计中确认是否可达，标记为待核。

### 已确认可达（排除死代码嫌疑）

- `intent_action.py` → 被 `behavior_routes.py:858`、`mcp_server.py:2680` 调用 ✅
- `acp_client.py` → 被 `acp_server.py:266` 调用 ✅

---

## 六、R9-2 探针的已知缺陷（如实记录）

`probe_dead_subsystem` 报出 2 个"无包外调用方"的模块组：`api.*`（24 模块）和 `scripts.*`（2 模块）。

**这两个都是误报**——探针用 `rev.get(m.split(".")[-1])` 查调用方，对 `api.behavior_routes` 查的是 `behavior_routes`，与成员全名比对失败，导致所有调用方都被判为"外部"后又因名字不匹配漏计。

`api/*_routes.py` 通过文件末尾 `Route(...)` 列表集中挂载，是可达的（第五轮已确证）。

**探针逻辑待修正**，本轮的 `learning_*` 结论**不依赖此探针**，而是通过直接 grep 调用方 + 实测导入两条独立证据链确认。

---

## 七、第九轮产出小结

| 项 | 结果 |
|---|---|
| 新缺陷 | **P2-8 ×1**（learning_* 1524 行死子系统）、**P3-3 ×1**（insights/persona.py） |
| 工作流改进 | 2 个探针（R9-1/2），其中 R9-2 有已知逻辑缺陷待修 |
| 证伪 | 5 处疑似除零安全、3 个模块确认可达、1 处绝对导入有兜底 |

### 收敛判据

第九轮出现新 P2，**判据 2（连续 2 轮无新 P1/P2）仍未满足**。

但要注意本轮 P2-8 的性质：它是**死代码**，不是运行时缺陷。与前几轮的语义缺陷（时区、排序、口径）性质不同。

---

## 八、规划执行状态

- [x] 第五轮 · 孤儿 triage + 路由接漏 → P3×1
- [x] 第六轮 · 双轨口径比对 → 0
- [x] 第七轮 · 并发与竞态 → P2×1
- [x] 第八轮 · 外部输入边界 → P2×1 + P3×1
- [x] **第九轮 · 未测区域收口 → P2×1 + P3×1**
- [ ] **第十轮 · 生命周期 / 资源 / 历史修复回归（V4）** ← 最后一轮

**第九轮遗留**（未在本轮完成，顺延或声明放弃）：
- 54 个孤儿函数逐个 triage（仅第五轮核查 1 个）
- 11 个 F/E 级高复杂度函数（仅核查 acp_handle 1 个）
- 16 个 `self.legacy.*` 委托方法的返回值规范化验证（仅抽查 device_usage 1 个）
- 20 处时区 REVIEW 级点位

工作流迭代：
- [x] V2 · 遗留项可追踪化 + 孤儿 triage 修正
- [x] V3 · 并发 / 输入边界探针
- [ ] V4 · 收敛度量 + 历史审计修复验证器（第十轮前）

---

## 九、审计覆盖声明

- 本轮为**第九轮**。15 个 0% 覆盖模块中，**重点审计了 learning_* 家族 8 个**（722 语句），其余 7 个（`identity_fusion`、`behavior_predictor`、`acp_client`、`intent_action`、`insights/persona`、`scripts/*` ×2）仅做了关键点抽查，**未逐行审查**。
- `learning_*` 的"不可导入"通过**实测导入**确认；"无人调用"通过 grep 确认。二者相互独立。
- `identity_fusion.py:134` 的 `/tau` 仅静态确认无守卫，**未确认 tau=0 是否可通过配置真实触发**。
- R9-2 探针存在已知逻辑缺陷，其输出**未被采信**为结论依据。
- 本轮未做：并发压测、运行时模糊测试、真实 HA 环境验证。
- 环境限制照旧：Python 3.10（低于项目要求的 3.11）、Semgrep 未安装。
