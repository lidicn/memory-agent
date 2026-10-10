# memory-agent 审计报告 · 第三期第十轮

- **审计对象**：`lidicn/memory-agent`
- **轮次**：`round-010`
- **报告日期**：2026-10-09

---

## 一句话结论

**确证 1 项缺陷 M15（中）：`identity._health_allows` 读健康表失败时 `return True`（fail-open），会让已下线实体混入故障转移候选。已修复并三场景对照实测。另判 1 条假阳性（`_pinned_entities`）。工作流迭代 W102：失败开放 PoC 只认 FO-01 一个规则族，而 M15 是由 AFS-01 报出的——探针按规则名取数而不是按语义取数，与 W91 同源。**

---

## 一、M15【中·已修复验证】健康状态读取失败 → 放行已下线实体

### 缺陷

`identity.py:195 _health_allows`：

```python
except Exception:
    return True        # ← 读失败 = 未知 = 放行
```

**与"无健康记录返回 True"返回同一个值，但两者语义相反：**

| 情况 | 语义 | 正确处置 |
|---|---|---|
| 无健康记录 | 尚未对账 | 放行（docstring 明说，**有意为之**） |
| 读取失败 | 状态未知 | **不该放行** |

### 可达性实测（三场景）

| 场景 | 结果 |
|---|---|
| DB 完全挂 | `_find_device` 先挡住 ⇒ **`_health_allows` 不可达** |
| **部分故障**（logical_devices 可读 / device_health 读失败） | **可达**：已下线的 `sensor.old_removed` 被放行 |
| 正常（健康表标记 stale） | 正确排除 `sensor.old_removed` |

⇒ 危害要精确描述：**不是"一定发生"，而是"部分故障时发生"**。
后果也不是丢数据，而是**用已下线实体算出空/旧数据**（正是 docstring 声明要避免的）。

### 对照实测

| 场景 | 原版 | 补丁版 |
|---|---|---|
| 部分故障 | 放行 `sensor.old_removed` ❌ | 排除 + warning 日志 ✅ |
| 正常（已下线） | 正确排除 | 正确排除（无回归） |
| 无记录（未对账） | 放行 | 放行（**保留原行为**） |

### 修复与取舍

异常分支 → `return False` + `logger.warning`；无记录分支保持 `True`。

**需要你定夺的取舍**：健康表损坏时，补丁版会让身份解析整体返回 `stale`（功能不可用），
原版是继续工作但可能用到已下线实体。这是**「显式不可用」vs「静默错误数据」**的选择。
我选前者的理由是：静默错误数据会污染下游所有行为挖掘结论，且无从察觉。

---

## 二、假阳性判定：`_pinned_entities` 返回 None 是有意的

`AF-AST-MIXED-RETURN` 报 `identity.py:390` high：标注 `set[str]` 却 `return None`。

**实为假阳性。** 源码注释（元宝第十四轮 P2-10）写明：

> 查询失败返回空集合会让用户手动拆分的实体重新进入相似度合并（拆分被静默撤销），
> 且无日志无法追溯。保守策略：失败时中止本次聚类（返回 None）

且调用方 `_cluster` 第 425 行明确 `if pinned is None:` 走"所有实体独立成簇、不合并"。

⇒ **行为正确且更安全，只有类型标注不准。** 与 doubao-butler `mcp handle_request` 同型。

（`vision_service.fetch_frame` 那条同族 high：函数末尾是 for 重试循环，
所有失败路径均 raise，无 fall-through —— 待逐条核验后确认，本轮先记为待查。）

---

## 三、W102【工作流】失败开放 PoC 只认一个规则族

### 问题

`poc_failopen.py` 按 `RULE = "FO-01-failopen-guard"` 取数。

memory-agent 上：**FO-01 命中 0，AFS-01 命中 125** —— 而 M15 就在这 125 条里。
探针输出 `no_adapter`，看起来"本项目没有可测目标"，实际是**取数口径不对**。

**与 W91 同源**：W91 是适配器按项目硬编码（doubao-butler 回退跑 AutoForge 探针），
这次是**规则族硬编码**。两次都是"按名字取数，而不是按语义取数"。

### 修法

`RULE_ALTS = ["FO-01-failopen-guard", "AFS-01-silent-failure"]`，
并在 `no_adapter` 输出里带上 `rules_scanned` 与 `findings_seen`。

修后跑 memory-agent：

```
findings_seen: [_health_allows, ...]   ← 目标浮出来了
rules_scanned: [FO-01, AFS-01]
no_adapter: true                        ← 仍然没有适配器，如实报
```

**目标浮出来是关键**：从"0 个目标"变成"看到目标但没有适配器"，
后者能被人工接手，前者会被当成"没问题"。

---

## 四、数字

| 项 | 值 |
|---|---|
| 第十轮全量命中 | 976（high 11 / medium 739 / low 226） |
| 新增确证缺陷 | **1**（M15，medium，已修复） |
| 判为假阳性 | **1**（`_pinned_entities`） |
| high 11 条处置 | DO-01×2（=M6 已修）、CONC-03×1（已判假阳性）、CONC-08×4（已判）、AF-AST-MIXED-RETURN×2（1 假阳性 + 1 待查）、RSC-01×1、AFS-01×1（=M15） |
| 门禁 | ok=True / drift=0 |

---

## 五、台账现状

| ID | 严重度 | 状态 |
|---|---|---|
| M1 / M2 | high | 已修复验证 |
| M5 | high | 已修·待确认磁盘开销 |
| M6 / M7 / M9 / M13 | medium | 已修复验证 |
| **M15** | medium | **已修复验证（本轮）** |
| M3 | medium | 待你确认部署路径 |
| M12 / M14 | low | still_open |
| M8 / M10 | low | still_open |
| M4 | info | 已核实历史修复 |

**M15 与 M6/M7 同族**（读路径的失败被当成"没有数据"），
区别是后果不同：M6/M7 是**丢数据**，M15 是**算出错误结果**。

---

## 六、如实说明

- **`vision_service.fetch_frame` 那条 high 未逐条核验**，只确认末尾是重试循环、
  失败路径 raise，记为待查而非"已排除"。
- **W102 后仍没有 memory-agent 的失败开放适配器**，M15 靠手写 PoC 确证。
- **M15 的取舍需你确认**：显式不可用 vs 静默错误数据。
- 依赖 CVE 面仍未扫（pip-audit 无法解析 pyproject、PyPI/OSV 403）⇒ 风险未知。
- 976 条中的 medium/low 仍未逐条分诊（739 + 226）。
- 补丁只在只读副本 `repos/ma-patched`，**原仓库未改动**。

---

## 七、本轮最该记住的一条

**M15 差点就这么漏掉了——不是因为规则没报出来，而是因为探针取错了数。**

AFS-01 在 memory-agent 上命中 125 条，`_health_allows` 就在里面。
但失败开放 PoC 只认 `FO-01` 这一个规则族，于是照旧输出 `no_adapter`。
如果我信了这个输出，结论会是"本项目没有可测的失败开放缺陷"——
**而实际有一个真缺陷，且已经被静态规则指名道姓地报出来了。**

这已经是"按名字取数"第二次出问题（W91 按项目名、W102 按规则名）。
两次的失效方向都一样：**输出看起来完全正常，与"确实没有目标"无法区分**。

W102 的修法本身很小（加一个规则名列表），真正的价值在于
**`no_adapter` 现在会带上 `findings_seen`** ——
从"0 个目标"变成"看到 125 个候选但没适配器"。
前者会被当成结论，后者会逼着人去看。

**推论：任何"没有目标 / 无需测试"的输出，都应该附带"我看了什么"的证据。**
光说"没找到"是不可验证的；说"看了这 125 个，都不匹配"才可被推翻。
