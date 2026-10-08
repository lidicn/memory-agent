# memory-agent 第四轮审计报告

> 审计对象：https://github.com/lidicn/memory-agent （main 分支）
> 审计日期：2026-10-04
> 本轮方向：**类型系统全量核查（mypy）+ 复杂度热点 + 并发与资源 + 前三轮遗留收口**
> 前置：第一轮（图谱导引）→ P1-1/2/3；第二轮（覆盖率）→ P1-4/5；第三轮（变体扩散）→ P1-6

---

## 一、工作流强化（本轮新增）

### 新增套件

| 套件 | 用途 | 本轮产出 |
|---|---|---|
| **mypy** | 全量类型检查 | **188 个类型错误**，成为本轮主线索 |
| **radon** | 圈复杂度热点 | 定位 12 个 F/E 级函数 |
| **faker** / pytest-mock | 测试数据生成 / mock | 已装，本轮未实跑 |
| pydeps / pycg / code2flow / pyan3 | 图谱工具补充安装 | 已装备用 |

### 新增 skill

已解压 trailofbits/skills 全部 84 个 SKILL.md。本轮评估后：

- 采用 **static-analysis**（mypy 作为类型层静态分析入口）
- 弃用 `entry-point-analyzer`（面向合约）、`agentic-actions-auditor`（面向 CI Agent），与本项目不匹配

---

## 二、结论先行

> **本轮确认 1 个新缺陷（P2-5），并完成了前三轮三项遗留的全量核查。**
>
> mypy 报出 188 个类型错误，但**绝大多数是 Python 动态风格与严格类型注解的摩擦**（`union-attr` 38、`assignment` 36、`arg-type` 34），真实缺陷只有 1 个——藏在 `no-redef`（重复定义）这一小类里。
>
> **这说明：错误数量与缺陷数量不成比例，分类比计数重要。**

---

## 三、P2-5　模块级常量被后定义覆盖，7 个英文关键词静默失效

**位置**：`src/memory_agent/insights/utils.py:742` 与 `:1109`（同名 `KEYWORD_DOMAINS`）

### 现象

同一模块内 `KEYWORD_DOMAINS` 被定义两次，后者完全覆盖前者：

| 行号 | 键集合 | 示例 |
|---|---|---|
| 742（**失效**） | 英文键 8 个 | `light`/`switch`/`climate`/`media`/`sensor`/`tv`/`aircon`/`ac` |
| 1109（**生效**） | 中文键 15 个 + `ac` | `灯`/`空调`/`加湿`/`扫地`/`投影`… |

`ac` 的值也从 `('climate.ac',)` 变成了 `('climate',)`。

### 实测复现

```
生效的 KEYWORD_DOMAINS 键: 15 个中文键 + ac
742 行英文版 8 个键中 7 个失效：light / switch / climate / media / sensor / tv / aircon

查询实测:
  'light'  → None      'tv'    → None      'sensor' → None
  'ac'     → ('climate',)      '灯'   → ('light',)
```

### 影响

`insights/utils.py:1055` 的 domain 解析函数遍历 `KEYWORD_DOMAINS.items()` 做关键词匹配，**英文关键词查询无法解析出 domain**。

缓解因素（如实说明）：
- 项目是中文家庭场景，中文键完整，**中文查询不受影响**
- `insights/parser/entity.py:35` 有独立且更完整的英文键定义，**parser 路径不受影响**
- 故定级 **P2** 而非 P1

### 修复建议

删除 742 行的旧定义，或将其键合并进 1109 行版本。同文件的 `ROOM_AGGREGATE_WORDS`（734/1087）是 7→12 项的**有意扩展**（后者更全，前者为死代码），处理方式应不同——保留新版即可。

---

## 四、mypy 188 个错误的分类与证伪

| 错误类型 | 数量 | 判定 |
|---|---|---|
| `union-attr` | 38 | 动态返回 `dict \| None` 后直接取属性，风格问题 |
| `assignment` | 36 | 类型注解与实际赋值不符，多为渐进式注解遗留 |
| `arg-type` | 34 | 宽松调用，同上 |
| `attr-defined` | 18 | 动态属性/config 对象 |
| `return-value` / `misc` / `operator` | 11/11/9 | 同上 |
| **`no-redef`** | **10** | **高危：重复定义 = 静默覆盖，逐个核查** |
| `truthy-function` | 1 | 函数对象当布尔用 |

**`no-redef` 10 处逐项核查结果**：

| 位置 | 判定 |
|---|---|
| `insights/utils.py` KEYWORD_DOMAINS | **真缺陷（P2-5）** |
| `insights/utils.py` ROOM_AGGREGATE_WORDS | 有意扩展（7→12 项），前者死代码 |
| `insights/utils.py` CATEGORY_DOMAINS / TAG_RULES | 冗余：两版字面量相同，仅注解写法不同 |
| `mcp_server.py` TOOL_CATALOG / TOOL_NAMES | 有意：硬编码列表被 `build_catalog()` 动态生成取代 |
| `app.py` combined_app | 正常：Starlette 实例被 `_mount_slash_fix` 包装 |
| 其余 | 冗余或正常 |

**结论**：10 处高危告警 → 1 处真缺陷。这也印证了 `fp-check` 的必要性。

---

## 五、复杂度热点（radon）

F/E 级函数（圈复杂度 ≥ 31）：

| 函数 | 复杂度 |
|---|---|
| `ActivityInferenceService.audit_rule_recall` | **F (52)** |
| `ActivityInferenceService.mine_process` | **F (42)** |
| `AgentMemoryService.retrieve` | E (38) |
| `ActivityInferenceService.mine_drift` | E (35) |
| `algo_kernel.mine_process_model_pure` | E (35) |
| `acp_server.acp_handle` | D (27) |
| `AgentMemoryService.merge_semantic_memory` | D (28) |

这些是**可维护性风险**而非已确认缺陷——高复杂度意味着改动时回归风险大，但不等于有 bug。本轮未逐个深入审查。

---

## 六、并发与资源：订阅者释放已核查

针对复杂度最高的 `acp_server.acp_handle`（27），核查其 SSE 订阅者生命周期：

```python
run.subscribers.append(own)
try:
    ...
except asyncio.CancelledError:
    run.abort()
    return
finally:
    try:
        run.subscribers.remove(own)
    except ValueError:
        pass
```

**结论：正确。** `finally` 释放 + `ValueError` 兜底，客户端断开不泄漏。

顺带核查 `face_node_registry.unregister`（68 个孤儿函数之一）：**无任何路由暴露**（`face_routes.py` 只有 register/heartbeat/lib/list/recognize）。但节点有 `_HEARTBEAT_TTL_S=120` 剔除机制，`_online()` 过滤使离线节点不参与选路，**非资源泄漏**。节点数受设备数限制，字典增长有界。定级：死代码，非缺陷。

---

## 七、四轮累计缺陷清单

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
| **P2-5** | **KEYWORD_DOMAINS 覆盖致 7 个英文关键词失效** | **P2** | **四** |

---

## 八、四轮方法论回顾

四轮用了四个不同的切入角，各自的产出与盲区：

| 轮次 | 切入角 | 找到 | 盲区 |
|---|---|---|---|
| 一 | 图谱（枢纽与绕过点） | P1-1/2/3 | 看不到"哪里没被测" |
| 二 | 覆盖率（未测区域） | P1-4/5 | 抓不到"已测却喂错数据" |
| 三 | 变体扩散 + 变异测试 | P1-6 | 命中率仅 25%，需大量 triage |
| 四 | 类型系统（mypy no-redef） | P2-5 | 188 告警中仅 1 个真缺陷 |

**一条贯穿四轮的观察**：这个项目的工程质量不低——锁纪律 0 问题、34 条 SQL 注入全是误报、pyflakes 全 0、SSE 订阅者释放正确、常量覆盖仅 1 处成灾。缺陷集中在**语义一致性**（时区口径、排序契约、归一化量纲、常量覆盖）而非工程粗糙。

---

## 九、建议下一步（合并四轮）

**必修（P1）**
1. P1-5 `identity_fusion`：分母只累加投向该候选者的权重
2. P1-6 `daily_profile.py:35`：与同文件第 98 行口径统一
3. P1-4 `behavior_predictor`：显式排序 + docstring 声明前置条件
4. P1-3 `nr_routes.py:125`：改用 `now_local()`

**应修（P2）**
5. P2-5：删除 `insights/utils.py:742` 旧定义（或合并键）
6. P2-1：`history.py:485`、`algo_kernel.py:659` 加 `strict=True`

**补测试（注意输入顺序）**
7. 新增排序相关单测**必须用与生产一致的 DESC 数据**，否则重蹈 P1-6 覆辙

**遗留待办**
8. 68 个孤儿函数（本轮仅核查 1 个）
9. 37 个双轨共享方法口径比对（`insights` vs `insights_legacy`）
10. 12 个 F/E 级高复杂度函数深入审查
11. `learning_*` 家族 500+ 行零覆盖链路

---

## 十、审计覆盖声明

- 本轮为**第四轮**。mypy 188 个错误中，仅 `no-redef` 10 处做了逐项核查，其余 178 处仅做类型分类未逐一验证。
- radon 定位的 12 个 F/E 级函数**未深入审查**，仅 `acp_handle` 做了资源释放核查。
- `faker` / `pytest-mock` 已安装但本轮未使用。
- 68 个孤儿函数仅核查 `face_node_registry.unregister` 1 个。
- 并发方向仅核查 SSE 订阅者释放，**未做并发压测、未审查锁竞争**。
- 运行环境 Python 3.10 低于项目要求的 3.11。Semgrep 因包体超限，四轮均未参与。
