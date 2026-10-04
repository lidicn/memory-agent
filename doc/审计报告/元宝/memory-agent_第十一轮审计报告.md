# memory-agent 第十一轮审计报告

> 审计对象：https://github.com/lidicn/memory-agent （main 分支）
> 审计日期：2026-10-04
> 本轮方向：**孤儿函数功能族 triage + 工具契约接漏**
> 前置：第一~十轮累计 P1×6、P2×8、P3×4
> 本轮为新增 5 轮（第十一~十五轮）的第一轮

---

## 一、新增 5 轮规划

前一轮建议"补遗留而非继续开新坑"，本轮起执行。新增 5 轮主题：

| 轮次 | 主题 | 来源 |
|---|---|---|
| **十一** | 54 个孤儿函数功能族 triage + 工具契约接漏 | 第五轮遗留 |
| 十二 | 11 个 F/E 级高复杂度函数深入审查 | 第四轮遗留 |
| 十三 | 16 个 `self.legacy.*` 委托方法规范化 + 20 处时区 REVIEW 点 | 第六/一轮遗留 |
| 十四 | 错误处理与降级正确性（69 处静默降级逐个验证） | P1-2 只数了数量 |
| 十五 | 缓存/状态一致性 + 收敛验证与总报告 | 新维度 + 收口 |

---

## 二、结论先行

> **本轮确认 1 个 P3（P3-5），并把第五轮的 P3-1 从"4 个函数"扩充为"7 个函数、整条链路"。**
>
> 核心发现：**Phase 3.1 主动规则引擎是一整条未接线的功能链路**——路由层 4 个 + 引擎层 3 个，共 173 行。第五轮只看到了路由层的 4 个。
>
> 另一个方向是好消息：**93 个 MCP 工具中 22 个走 dispatch，契约接漏 0 个**，工具契约层完整。

---

## 三、P3-5（扩充 P3-1）　Phase 3.1 主动规则引擎整条链路未接线

### 构成

| 层 | 函数 | 位置 |
|---|---|---|
| 路由层 | `behaviors_list_rules` | `api/behavior_routes.py:370` |
| | `behaviors_add_rule` | `:382` |
| | `behaviors_update_rule` | `:412` |
| | `behaviors_delete_rule` | `:430` |
| 引擎层 | `test_rule` | `rule_engine.py:144` |
| | `simulate_events` | `rule_engine.py:199` |
| | `get_overall_stats` | `rule_engine.py:275` |

**合计 7 个函数、约 173 行。**

### 第五轮的认知偏差

第五轮只报了路由层 4 个（P3-1），因为 R5-1 探针**只覆盖 `api/*_routes.py` 的路由函数层级**，看不到引擎层。

本轮通过"同族接漏"分析发现：那 4 个未挂载路由内部调用的是 `list_rules` / `add_rule` / `update_rule` / `delete_rule`（这些引擎方法有其他调用方），而 **`test_rule` / `simulate_events` / `get_overall_stats` 三个是全项目零引用**——连那 4 个未挂载路由都没调它们。

```
grep -rn "test_rule|simulate_events|get_overall_stats" --include=*.py .
→ 无结果（除定义处）
```

### 这意味着什么

这三个是**规则质量评估工具链**：测试规则 → 生成模拟事件 → 看整体统计（precision / recall / effect 三档评级）。

它们的 docstring 完整、返回结构定义清晰（`{"precision":…, "recall":…, "effect": "good"|"tune"|"review"}`），说明是**认真设计并写完的实现**，只是从未接线。

**这不是零散死代码，是一整个功能族的三分之二没接上。**

### 定级 P3（与 P3-1 同级，但范围扩大）

理由同第五轮：前端全项目无调用，用户不会遇到 404，无运行期功能受损。

**但建议开发者把它作为一个整体决策**：Phase 3.1 主动规则引擎是"要启用"还是"要删除"？当前状态——路由没挂、调优工具没接、但引擎本身在跑（`behaviors_rules` 路由存在且挂载，只读 STATIC 规则）——是最难维护的半吊子状态。

---

## 四、54 个孤儿的完整功能族聚类

不再零散计数，按功能族整理：

| 功能族 | 数量 | 约行数 | 性质 |
|---|---|---|---|
| **Phase 3.1 主动规则引擎** | 7 | 173 | 整条链路未接线 |
| **patterns CRUD** | 5 | 118 | 类方法层接漏（R5-1 覆盖不到） |
| **identity 系列** | 6 | 212 | `fuse` / `elimination_signals` / `is_match` 等 |
| **store 遗留查询** | 5 | 67 | `get_arena_result` / `delete_activity_rule` 等 |
| insights.api 报告 | 4 | 8 | 3~4 行的薄委托 |
| insights.parser.entity | 4 | 19 | 查询辅助 |
| candidate_promotion 诊断 | 3 | 16 | `cache_stats` / `recent_audit` |
| history | 2 | 17 | `add_event` / `query_range` |
| perception_ingest | 2 | 19 | `from_vlm` / `from_sensor` |
| llm_client 内部 | 2 | 25 | `_mask_secret` / `_payload` |
| template_validate | 2 | 8 | |
| insights.models TimeRange | 2 | 7 | `clip_ts` / `shift` |
| 其余零散 | 10 | — | |

**最大三族（规则引擎 7 + patterns 5 + identity 6 = 18 个，约 503 行）**是主要决策点。

`patterns.py` 的 5 个 CRUD（`update_pattern` / `delete_pattern` / `confirm_pattern` / `reject_pattern` / `import_patterns`）与第五轮判定的路由接漏同性质——**PatternManager 有一整套增删改查，一个都没接**。

---

## 五、R11-2：工具契约完整（好消息，含一个潜在坑）

### 检测结果

```
tool_schema 声明工具 93 个
走 dispatch 的（expose 含 builtin/arena）：22 个
契约接漏：0
```

`tool_schema.py:1813 dispatch()` 的逻辑是 `getattr(rt.<service>, <method>)`，若 method 不存在会返回"后端未实现工具"。22 个走这条路径的工具**全部有对应实现**。

### 探针必须先按 expose 过滤（重要教训）

**未过滤时误报 3 个**：`save_skill` / `list_skills` / `get_skill`。

原因：`dispatch()` 第 1822 行有

```python
if not ({"builtin", "arena"} & set(spec.expose)):
    return {"error": f"工具 {name} 未对内置对话/竞技场开放（仅 MCP 可用）。"}
```

这三个 `expose=['mcp']`，在这一行就被拦下，**根本不会走到 `getattr`**。

**若不看 dispatch 的实现就按 (service, method) 硬匹配，会造出 3 个假 bug。** 这与第七轮（async 无 await 不竞态）、第十轮（create/cancel 数字失衡）是同一类教训：**必须理解被检测系统的实际执行路径**。

### 一个真实的元数据不一致（不定级，记为潜在坑）

这三个工具的 `service="agent_memory"`，但：

- `rt.agent_memory` 确实存在（`runtime.py:77`，`AgentMemoryService`）
- `AgentMemoryService` **没有** `save_skill` / `list_skills` / `get_skill`
- 实际实现在 `mcp_server.py` 的 `@mcp.tool()` 独立函数（2937 / 3012 / 3038）

当前 `expose=['mcp']` 使其无害。**但若将来给这些工具加 `builtin` expose（很自然的改动），dispatch 会立即返回"后端未实现工具"。**

修复成本极低（改 service 字段或补委托方法），建议顺手处理。

---

## 六、第十一轮产出小结

| 项 | 结果 |
|---|---|
| 新缺陷 | **P3-5 ×1**（规则引擎链路，含 P3-1 范围扩充 4→7） |
| 工作流改进 | 2 个探针（R11-1 同族接漏、R11-2 工具契约） |
| 证伪 | R11-2 未过滤时 3 个假阳性；54 个孤儿中其余族未逐个人工复核 |

### 收敛判据

本轮**无新 P1/P2**（仅 P3）。按判据 2（连续 2 轮无新 P1/P2），第十轮（仅 P3）+ 第十一轮（仅 P3）= **已达 2 轮**。

但判据 1（6 大类未审项 triage 完毕）仍未满足——第十二、十三轮正是为此。

---

## 七、规划执行状态

- [x] 第一~十轮（原规划）
- [x] **第十一轮 · 孤儿功能族 triage + 工具契约** → P3×1
- [ ] 第十二轮 · 11 个 F/E 级高复杂度函数
- [ ] 第十三轮 · 16 个委托方法规范化 + 20 处时区点
- [ ] 第十四轮 · 69 处静默降级正确性
- [ ] 第十五轮 · 缓存/状态一致性 + 收敛总报告

---

## 八、审计覆盖声明

- 本轮为**第十一轮**。54 个孤儿中，**仅 Phase 3.1 规则引擎族（7 个）做了完整确证**，其余 47 个仅做功能族聚类统计，**未逐个阅读代码**。
- `patterns.py` 的 5 个 CRUD 判定为"类方法层接漏"，依据是第五轮同族模式 + `sib_called` 比例，**未逐个验证是否有动态调用**。
- R11-1 的 58 个候选**存在已知误判源**（常见名 `fuse` / `shift` / `enrich` 会因同名方法被调用而误判），报告中的 54 个来自更严格的 R5-2（已排除有 evidence 的）。
- 本轮未做：并发压测、长跑、真实 HA 环境、运行时验证。
- 环境限制照旧：Python 3.10（低于项目要求的 3.11）、Semgrep 未安装。
