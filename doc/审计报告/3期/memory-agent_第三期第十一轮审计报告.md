# memory-agent 审计报告 · 第三期第十一轮

- **审计对象**：`lidicn/memory-agent`
- **轮次**：`round-011`
- **报告日期**：2026-10-09

---

## 一句话结论

**本轮新增确证缺陷 0 项，判 4 条假阳性并逐个修掉根因（W103–W107）。high 从 11 降到 4，剩下 4 条全部是已闭环项（M15 已修、M6 已修 ×2、1 条假阳性）。这是收敛轮——不是"没找到东西"，是"把之前报的东西核实清楚了"。**

---

## 一、high 收敛曲线

| 阶段 | high | 说明 |
|---|---|---|
| 第十轮末 | 11 | — |
| W103 后 | 10 | `fetch_frame` 假阳性消除 |
| W104 后 | 6 | CONC-08 ×4 假阳性消除 |
| W105 后 | 5 | CONC-03 ×1 假阳性消除 |
| W106 后 | 4 | 一次性脚本排除 |
| W107 后 | **4**（其中 3 条已修复 / 1 条假阳性） | — |

**剩余 4 条的处置：**
- `AFS-01 identity._health_allows` → **M15，已修复**（补丁副本上已消失）
- `DO-01 auth.py:161 / 211` ×2 → **M6，已修复**
- `AF-AST-MIXED-RETURN identity._pinned_entities` → **假阳性**

⇒ **high 4 条，0 条待处理。**

---

## 二、4 条假阳性与根因修复

### W103：`fetch_frame` —— 有限重试循环不会 fall-through

规则说"末尾是 for 循环 ⇒ 可能 fall-through 到隐式 None"。

实际末尾是 `for attempt in range(retries+1): try: return … except: if attempt < retries: continue else: raise`
—— **每次迭代必终止于 return / continue / raise，循环不可能自然耗尽**。

实测：三次重试全失败 ⇒ 抛 `ConnectError`，不返回 None。

修法：加 `_loop_always_terminates()`，只认"有限 range + try 内 return + handler 末尾 raise"这一种最强形态（宁可漏，不可误判为安全）。

### W104：CONC-08 —— `_load` 只在 `__init__` 调用

`template_validate._load` 与 `templates._load_templates` 都只在 `__init__` 里调一次，
**构造期根本没有并发**。CONC-08 原本只排除"裸写直接写在 `__init__` 里"，
看不见 `def __init__: self._load()` 而 `_load` 里裸写。

⇒ **与 doubao-butler 第七轮 CONC-08 是同一个假阳性，第二次遇到。** 收进判据。

### W105：CONC-03 —— `to_thread` 与 `Popen`

`system_routes.apply_update` 两条理由都不成立：
① `_git` 用 `await asyncio.to_thread(subprocess.run, …, timeout=120)`；
② `subprocess.Popen` 只启动进程立即返回，无 `wait`。

⇒ **与 doubao-butler 第六轮 `docker_tools` 同型，第二次遇到。**

### W106：`scripts/` 一次性脚本混入

`scan_*` / `probe_*` / `verify_*` / `compare_*` / `bench*` 共 43 个文件是各轮审计
留下的一次性脚本，它们**故意**写得不符合生产规范。

**按前缀排除，不整目录排除** —— `db_health_check.py` / `check_config.py`
这类运维脚本会在容器里真实执行，缺陷是真缺陷，必须保留。

### W107：`_pinned_entities` —— None 是中止信号，不是静默降级

调用方 `_cluster` 第 429 行 `if pinned is None:` → 所有实体独立成簇、不合并。
注释明写"宁可少合并，不可撤销用户手动拆分"。

**行为比返回空集合更安全，只有类型标注不准。**

修法：加 `_none_handled_by_caller()`，调用方显式判空 ⇒ 降 low。

---

## 三、数字

| 项 | 值 |
|---|---|
| 新增确证缺陷 | **0** |
| 假阳性判定 | **4 条**（全部修掉根因，不是加抑制） |
| 全量命中 | 970 → **927** |
| high | 11 → **4**（全部已闭环） |
| 门禁 | ok=True / drift=0 |

---

## 四、负向测试记录

本轮全部改动都做了负向测试，共 **12 例全过**：

```
W103  循环终止性              3 例
W104  _only_called_from_init  2 例
W105  to_thread / Popen       4 例
W107  _none_handled_by_caller 3 例
```

其中 W105 第一版测试失败（`to_thread(subprocess.run, ...)` 里 `subprocess.run`
是**参数**不是调用节点），是我构造用例的错误，修正后全过。
**这条要记：负向测试的用例本身也可能是错的。**

---

## 五、台账现状（无变化）

| ID | 严重度 | 状态 |
|---|---|---|
| M1 / M2 | high | 已修复验证 |
| M5 | high | 已修·待确认磁盘开销 |
| M6 / M7 / M9 / M13 / M15 | medium | 已修复验证 |
| M3 | medium | 待你确认部署路径 |
| M12 / M14 | low | still_open |
| M8 / M10 | low | still_open |
| M4 | info | 已核实历史修复 |

---

## 六、如实说明

- **本轮新增确证缺陷为 0**。产出全部是判据收敛与假阳性根因修复。
- **W103/W107 的判据都做得"宁可漏，不可误判为安全"** ——
  可能漏掉真实缺陷，这是有意的取舍。
- **W106 排除了 43 个文件**，若其中有你认为属于产品的，需告知我加回。
- 927 条命中里的 **707 条 medium 仍未逐条分诊**。
- 依赖 CVE 面仍未扫（pip-audit 无法解析 pyproject、PyPI/OSV 403）⇒ 风险未知。
- 补丁只在只读副本 `repos/ma-patched`，**原仓库未改动**。

---

## 七、本轮最该记住的一条

**4 条假阳性里，有 2 条是"第二次遇到"——CONC-08 在 doubao-butler 第七轮
判过一次，CONC-03 在第六轮判过一次。**

也就是说，**我明明已经在另一个项目上把这些坑修过一遍了，
换到 memory-agent 又报出来，说明修复没有真正沉淀。**

原因值得说清楚：doubao-butler 那两次，我是**在报告里判它为假阳性**，
然后继续往下走——**没有回去改规则**。所以规则本身一点没变，
换个项目原样再报一次。

这跟第十轮 W102 是同一个病：
> W102 = 探针按名字取数（FO-01），换项目就取不到
> 本轮 = 规则按结构判据（for 循环 / `__init__` 内裸写），换项目就误报

**共同的根因：我把"人工核实过"当成了"问题解决了"。**
人工核实只对那一条有效，规则的行为一点没变。

所以本轮改法与之前不同——**4 条全部改了规则，没有一条是靠加抑制清单绕过去**。
抑制是把这次的噪声藏起来，改判据是让它在别处也不再出现。
代价是本轮产出 0 个新缺陷，收益是下一轮不用再判这 4 条。
