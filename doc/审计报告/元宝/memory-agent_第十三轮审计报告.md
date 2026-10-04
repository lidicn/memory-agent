# memory-agent 第十三轮审计报告

> 审计对象：https://github.com/lidicn/memory-agent （main 分支）
> 审计日期：2026-10-04
> 本轮方向：**16 个委托方法规范化 + 20 处时区 REVIEW 点**
> 前置：第一~十二轮累计 P1×6、P2×9、P3×6、P4×1
> 本轮为新增 5 轮的第三轮

---

## 一、结论先行

> **本轮无新 P1/P2/P3。新增 2 个 P4 观察项。**
>
> 两个方向**主体都是干净的**，但干净的原因值得说清楚——它们都不是"问题不存在"，而是**项目已有针对性的治理机制**，且治理得相当好。

---

## 二、R13-1：委托方法规范化 —— 6/9 公共接口已规范化

### 结果

`insights/api.py` 中 16 处 `self.legacy.*` 委托：

| 分类 | 数量 | 规范化情况 |
|---|---|---|
| **公共接口** | 9 | 6 个有 Page 封装/to_dict/补分页键 |
| **内部 helper**（`_` 开头） | 7 | 无规范化——**预期行为** |

### 关键：不能只看"有没有规范化"

7 个内部 helper（`_parse` / `_fallback_name` / `_usage_one` / `_usage_by_attr` / `_count_by_filter` / `_iter_all_events` / `_tags_of`）**全部无规范化**，但这是设计如此。代码注释写得很明白：

```python
# 审计 P0-5：legacy 契约成员显式转发（清单见 LEGACY_CONTRACT_MEMBERS）
# 这些方法**不套 ``_degrade``**：调用失败必须抛出真异常，而不是静默变成空结果。
```

`LEGACY_CONTRACT_MEMBERS`（`api.py:33`）是一张**显式契约表**，且项目为此写了专门测试 `tests/test_insights_facade_contract.py`，用"生产侧属性访问扫描"锁定这张表。测试注释还说明了一个很关键的选择：

> 明确**不**做的一件事：用 `__getattr__` 动态转发把缺的方法"补"出来。那正是 P0-5 的藏匿机制本身——属性永远存在，静态扫描与契约测试都会被骗过去。

**这是全项目治理质量最高的一处。** 作者不仅修了 P0-5，还堵死了"用动态转发假装修好"这条路。

### 4 个"无 Page 规范化"的公共接口：逐个判定

| 方法 | 判定 |
|---|---|
| `resolve_range`（L583） | **非缺陷**：返回时间区间元组，本就不是分页结果 |
| `name_map`（L586） | **非缺陷**：返回 `dict[str, dict]`，非分页 |
| `decorate`（L589） | **非缺陷**：返回 `list[dict]`，非分页 |
| `define_activity`（L418） | **P4-2 观察项**，见下 |

### P4-2　`define_activity` 的"部分成功"语义未体现在 `ok` 字段

```python
out = self.legacy.define_activity(...)
if isinstance(out, dict) and out.get("ok"):
    out["message"] = (
        "规则已注册进 activity_rules（rule_id=%s）；"
        "当前活动推断走时段启发式，尚未套用自定义规则，..."
    )
```

**这是正面范例**：作者意识到 legacy 回执说的"下次自动套用"是**过度承诺**，主动改写 message，把"将要生效"改成"已注册但未套用"。

但 `out["ok"]` 仍是 `True`。只读 `ok` 字段的调用方（MCP / LLM）会理解为"操作成功"，而真实语义是**部分成功：已存储，但不生效**。

定 P4：属信息完整性，非功能性 bug，且作者已在 message 里说明。

---

## 三、R13-2：时区点 —— 26 处 naive，但只有 1 处参与计算

### 三轮迭代的过滤过程

| 版本 | 命中 | 问题 |
|---|---|---|
| 第一轮（文本匹配） | 65 | 把注释/docstring 里的**警告文字**也算成命中 |
| 第十三轮初版（AST） | 38 | 未排除注释与 docstring |
| **本版（AST + 排除注释/docstring + 分 aware/naive）** | **38 → naive 26** | — |

38 处中 **12 处是 `datetime.now(timezone.utc)`**——**显式带 tz 的 UTC 时间戳是正确做法**，可与库内时间戳安全比较，**不是缺陷**。

真风险是 26 处 naive（无 tzinfo）。

### 再按"是否参与计算"分轻重

| 危害 | 数量 | 说明 |
|---|---|---|
| **参与时间计算** | **1** | `insights/models.py:95`（且这行是算 UTC 偏移，本身正确） |
| 仅落库/元数据 | 25 | `templates.py` 11、`patterns.py` 4 等 |

**26 处里真正参与时间窗口计算的，只有 `api/nr_routes.py:125`——而那是第一轮已报的 P1-3。**

### P1-3 与其余 25 处的本质区别

```python
# P1-3  nr_routes.py:125 —— now 直接参与计算
start = (datetime.now() - timedelta(days=7)).isoformat()   # 窗口偏 8h → 边界数据错漏

# 其余 —— now 只落库作元数据
"created_at": datetime.now().isoformat(),                   # 时间戳偏 8h → 显示偏差
```

**同为裸 `now()`，危害差一个数量级**：一个影响统计结果正确性，一个只影响元数据展示。

### P4-3　`intent_inference.py:227` 的 `now` 兜底

```python
now = datetime.now()
try:
    latest_ts = max(datetime.fromisoformat(ev["server_ts"]) for ev in events if ...)
except (ValueError, TypeError):
    latest_ts = now          # ← now 只在这里用
window_start = latest_ts - timedelta(minutes=window_min)
```

`now` **只**在"所有事件 ts 都解析失败"时兜底。正常路径用 `max(server_ts)`（库内时间，与容器时钟无关）。且兜底场景下 events 本就无有效 ts，后续过滤必然无结果。

**影响极有限，定 P4。**

### 25 处元数据点的实际影响

家庭时间 00:00–08:00 之间创建的记录，容器 UTC 下**日期会记成前一天**。实测：

```
家庭 2026-10-04 07:30  →  UTC 2026-10-03 23:30  → 差 1 天
```

影响 `created_at` / `updated_at` / `last_verified` / `exported_at` 的**显示**。不参与任何统计计算，故不定级，仅列为一致性改进项。

---

## 四、本轮唯一的方法论教训（反面）

**R13 探针首次注入 `ma_audit.py` 失败**，原因是我用嵌套 heredoc 写 Python 代码时引号转义出错，整个脚本在解析阶段就崩了——**所有替换都没执行，但 `ma_audit.py` 语法检查仍通过**。

症状是：`python3 ma_audit.py r13` **静默输出 0 行结果**，看起来像"这轮没有发现"。

改法：把 R13 两个探针**独立成 `r13_probes.py`**（用文件写入而非 heredoc 注入），单独运行验证。

这与第十二轮的函数名覆盖是同一类问题：**探针静默失败比返回错误更危险**。已固化应对：新探针优先独立成文件，跑通验证后再决定是否并入主脚本。

---

## 五、第十三轮产出小结

| 项 | 结果 |
|---|---|
| 新缺陷 | **P4 ×2**（`define_activity` ok 语义、`intent_inference` now 兜底） |
| 工作流改进 | 2 个探针（`r13_probes.py`：R13-1 委托规范化、R13-2 时区 v3） |
| 证伪 | R13-1：7 个内部 helper 无规范化是预期；3 个公共接口返回类型本就非分页 |
| | R13-2：12 处显式 UTC 正确；25 处仅元数据；1 处参与计算即已报的 P1-3 |

### 收敛判据

本轮**无新 P1/P2/P3**。与第十一轮（P3）、第十二轮（P2）相比，本轮是三条线里**最干净的一轮**。

---

## 六、规划执行状态

- [x] 第十一轮 · 孤儿功能族 triage + 工具契约 → P3×1
- [x] 第十二轮 · 高复杂度函数 → P2×1 + P3×1 + P4×1
- [x] **第十三轮 · 委托规范化 + 时区点 → P4×2**
- [ ] **第十四轮 · 69 处静默降级正确性** ← 下一步
- [ ] 第十五轮 · 缓存/状态一致性 + 收敛总报告

---

## 七、审计覆盖声明

- 本轮为**第十三轮**。
- R13-1：16 个委托方法**全部**逐个核查（9 公共 + 7 内部）。
- R13-2：38 处裸 `now()` 中，**重点核查 4 处**（`nr_routes`、`intent_inference`、`templates` 抽样、`house_time`），其余 34 处基于"仅元数据 / 显式 UTC 正确"规则整体分类，**未逐行阅读上下文**。
- `define_activity` 的判定基于代码与注释阅读，**未运行时验证**其 `ok` 字段实际返回值。
- 时区危害分析假设容器为 UTC、家庭为 UTC+8。若部署环境容器已设为家庭时区，全部时区结论**均不成立**。
- 本轮未做：并发压测、长跑、真实 HA 环境。
- 环境限制：Python 3.10（低于项目要求的 3.11）、Semgrep、radon 未安装；pytest 在当前 shell 不可用（此前轮次可用），故契约测试仅阅读未运行。
