# OpenSHS 活动推断基准 · 基线报告（v2 · 可解释口径）

> 日期：2026-09-17
> 关联：路线图 v1.0 任务 3《活动推断基准评估（OpenSHS）》
> 评测对象：memory-agent `infer_activities`（纯事件逻辑，无 chroma / 无 LLM）
> 复现脚本：`benchmarks/openshs_bench.py` + `openshs_convert.py` + `openshs_schema.py` + **`openshs_eval.py`**

> **v2 修订说明**：v1 只报了一个孤立的 macro-F1=0.638，没有参照基线。实测发现该口径下
> 「每天预测所有活动」的傻瓜基线高达 **0.649 > MA 的 0.638** —— 即 v1 的结论不成立。
> v2 补上退化基线对照（lift / MCC）与更严格的时段级、段级口径，本文所有结论以 v2 为准。
>
> **v2.1 修订说明（P0 修复）**：`bathing` 检测现已输出真实时间区间（此前占用推断兜底
> 不产出 `start_ts/end_ts`，被时间定位评估静默剔除）；`watching_tv` 在 OpenSHS 上走 media 路径
> 本就带区间（旧文误记为遥测零长度，已更正）。修复后「无区间 MA 记录」由 13 条降为 0。
> 但 OpenSHS 粗粒度 `bathing`/`working` 标签与 MA 可用信号（卫生间占用脉冲 / 书房在场）不对应，
> 故两项分数仍受数据局限；宏观结论（Level 2a LIFT +0.057 为正）不变。

---

## 1. 目的

为 memory-agent 的活动识别能力提供**外部公开数据集**上的量化基线。核心要求是可解释：
任何分数都必须回答「**比傻瓜强多少**」，否则无法作为价值证明。

---

## 2. 数据集

| 项 | 内容 |
|---|---|
| 来源 | GitHub `plolutta/dataset`（OpenSHS 模拟器生成的智能家居数据集） |
| 格式 | OpenSHS 标准宽表 30 列：`wardrobe, tv, oven, …, bathroomCarp, Activity, timestamp` |
| 规模 | 1,048,575 行 / 约 89 MB，1 Hz 采样 → 边沿触发后 **1,717 条**事件 |
| 时间跨度 | `2021-03-01 07:55:16` → `2021-03-14 23:24:27`（**14 个自然日**） |
| 活动标注 | **粗粒度 7 分类**：`sleep / eat / work / leisure / personal / other / anomaly` |
| 时间戳 | 下划线分隔（`07_55_16`），已在前处理规整为 ISO |

标签映射（`other`/`anomaly` 无 MA 对应物，不参与评分）：

| OpenSHS | → MA 活动 | 性质 |
|---|---|---|
| `sleep` | `sleeping` | 一一对应 |
| `eat` | `cooking` | 一一对应 |
| `work` | `working` | 一一对应 |
| `leisure` | `watching_tv` | **近似代理** |
| `personal` | `bathing` | **近似代理** |

---

## 3. 为什么 v1 口径不成立（关键）

v1 按「**天 × 活动**」做二分类：当天是否发生某活动。但真实家庭里这些活动几乎天天发生：

| 活动 | 真值基础率（有活动的天数 / 14 天） |
|---|---|
| sleeping | **13/14 = 93%** |
| watching_tv | **13/14 = 93%** |
| cooking | 10/14 = 71% |
| bathing | 3/14 = 21% |
| working | 1/14 = 7% |

在 93% 的基础率下，**「永远回答『有』」本身就是高分策略**。实测：

```
Level 1 macro-F1:  MA=0.638   always_positive=0.649   majority=0.552   random=0.571
最强基线 = 0.649  =>  LIFT = -0.011   [无增益 / 不如傻瓜基线]
```

**结论：v1 的 0.638 不能证明任何能力。** 它既没有超过退化基线，也无法区分活动。
根源不是样本量（105 万行够用），而是**任务定义太简单 + 缺参照**。

---

## 4. v2 评估协议（三层）

由 `benchmarks/openshs_eval.py` 实现，纯标准库。

### Level 1 · 天级（保留原口径 + 对照）
原口径保留作历史对照，但主指标改为 **lift = MA_F1 − 最强基线_F1** 与 **MCC**（对基础率不敏感）。
基线：`always_positive`（永真）、`majority`（按基础率选择永真/永假）、`random`（以基础率为概率）。

### Level 2a · 时段级（严格 ∩ 主判据）
时间轴切成固定槽（默认 **5 min**，本数据集 3930 槽/活动），逐槽判二分类：
真值 = 槽中点落在 GT 片段内；预测 = 槽中点落在 MA 活动 `[start_ts, end_ts]` 内。
基础率骤降到「活动时长占比」量级（如 bathing 2%、working 0.9%），傻瓜基线随之崩塌 → **才有鉴别力**。

### Level 2b · 段级事件检测（对 MA 更公平）
把 GT 每个连续片段视为「待检事件」，MA 只要有同名活动区间与之**时间重叠**即算检出。
因 MA 的 `start_ts/end_ts` 是「**首末证据包络**」（会把多次做饭连成一片），
Level 2a 会惩罚它"窗口过宽"，Level 2b 只问"有没有盖到"，二者互补。

> 数据完整性：v2.1 修复后，本数据集 **MA 活动记录已 100% 携带 start_ts/end_ts**（原 13 条
> `bathing` 占用推断记录现已按「占用脉冲 + 开灯」重建真实区间）。评测协议对极少数仍缺区间的
> 记录如实计数并剔除，而非默认命中。

---

## 5. v2 基线结果（真实数据集，14 天）

### Level 1 · 天级

| 活动 | 基础率 | MA F1 | 最强基线 | MA MCC |
|---|---|---|---|---|
| bathing | 0.21 | 0.308 | 0.353 | -0.055 |
| cooking | 0.71 | 0.857 | 0.833 | 0.440 |
| sleeping | 0.93 | 0.818 | 0.963 | 0.372 |
| watching_tv | 0.93 | 0.960 | 0.963 | 0.679 |
| working | 0.07 | 0.182 | 0.133 | 0.175 |

**MA=0.625 vs 最强基线 0.649 ⇒ LIFT −0.024（无增益；bathing 区间化后日级略降，见 §6）**

### Level 2a · 时段级（槽=5 min，3930 槽/活动，0 条记录无区间）

| 活动 | 基础率 | MA F1 | 最强基线 | MA MCC |
|---|---|---|---|---|
| sleeping | 0.2575 | **0.846** | 0.410 | **+0.797** |
| cooking | 0.0173 | 0.108 | 0.034 | +0.156 |
| watching_tv | 0.1323 | 0.068 | 0.234 | **−0.099** |
| bathing | 0.0204 | 0.000 | 0.040 | 0.000 |
| working | 0.0092 | 0.000 | 0.018 | −0.026 |

**MA=0.204 vs 最强基线 0.147 ⇒ LIFT +0.057（有增益，但几乎全部来自 sleeping）**

### Level 2b · 段级事件检测

| 活动 | GT 段 | MA 条 | TP | FP | FN | P | R | F1 |
|---|---|---|---|---|---|---|---|---|
| cooking | 41 | 11 | 36 | 3 | 5 | 0.923 | 0.878 | **0.900** |
| sleeping | 25 | 9 | 13 | 0 | 12 | 1.000 | 0.520 | **0.684** |
| watching_tv | 71 | 12 | 29 | 5 | 42 | 0.853 | 0.408 | **0.552** |
| working | 2 | 10 | 0 | 10 | 2 | 0.000 | 0.000 | **0.000** |
| bathing | 7 | 0 | 0 | 0 | 7 | 0.000 | 0.000 | **0.000** |

**MACRO-F1 (segment-level) = 0.427**

---

## 6. MA 真实能力画像（纠正 v1 结论）

| 活动 | v1 说法 | v2 真相 |
|---|---|---|
| `sleeping` | F1 0.818「召回偏低」 | **真有战力**：时段级 F1 0.846 / MCC 0.797，时间定位准确。段级召回 0.52 是因为 GT 含 25 个片段（含白天小睡），MA 只识别夜间主睡眠 |
| `watching_tv` | F1 0.96「表现最好」 | **虚高，实为最差**：OpenSHS 上走 media 路径、区间真实存在，但其 media 开/关事件稀疏，与 GT `leisure` 块不对齐 → 时段级 MCC **−0.099（不如随机）**、段级仅召回 29/71。（注：用户真实家庭若无 media_player，`watching_tv` 才走 `_tv_from_telemetry` 遥测兜底、确为日级无区间——那是另一处真实局限，非 OpenSHS 此处情形） |
| `cooking` | F1 0.857 | **能力真实、定位粗**：段级 0.90（11 条记录覆盖 36/41 段），但时段级仅 0.108 —— MA 把一天多次做饭并成一条「首末证据包络」 |
| `working` | F1 0.182「精度低」 | **基本失效**：GT 仅 2 段，MA 报 10 条且**全部不重叠**（TP=0） |
| `bathing` | F1 0.375→0.308 | **标签不对应信号**：v2.1 已输出真实区间（无区间记录 13→0），但 OpenSHS 粗粒度 `personal→bathing` 是 3 天、各约 3 小时的占用长块，与卫生间「占用脉冲+开灯」片段在时间与天数上均不重合，故时段级/段级仍近 0——属数据/标注局限，非代码缺陷 |

**净结论**：在公平的可比口径下，MA 只有 **sleeping（强）** 和 **cooking（检出强、定位弱）** 有真实信号；
`watching_tv`（media 事件稀疏）、`working`（书房在场即判）仍未通过退化基线检验；`bathing` 经 v2.1 已输出真实区间，
但受 OpenSHS 粗粒度标签与信号不对应所限分数仍低（**数据/标注局限，非代码缺陷**）。

---

## 7. MA 改进优先级（由证据驱动）

| 优先级 | 问题 | 改进 |
|---|---|---|
| **P0 ✅ 已完成** | `bathing` 占用推断不产出区间；`watching_tv` 旧文误记为遥测零长度 | `bathing` 现按「占用脉冲+开灯、≥20min」输出真实 `start_ts/end_ts`；`watching_tv` 在 OpenSHS 走 media 路径本就有区间（已更正）；无区间记录 13→0 |
| **P1** | `working` 过触发：办公室在场即判工作（GT 2 段 → MA 10 条全 FP） | 加「子事件组合 + 最小持续时长」闸门 |
| **P2** | `sleeping` 白天/短睡眠片段漏检（12/25） | 放宽夜间限定，或加「关灯」软锚 |
| **P3** | `cooking` 多次做饭被并成一条宽包络 | 按 session 拆分为多条记录，而非一条混合区间 |

> **v2.1 P0 小结**：P0 是**正确性修复**——`bathing` 现在输出真实时间区间、评估不再静默丢弃记录，
> 但 OpenSHS 粗粒度 `bathing`/`working` 标签与 MA 可用信号不对应，故这两项分数未因修复而上升
> （bathing 日级反倒从 0.375 降到 0.308，因为旧口径靠「每天全预测」虚高了召回）。
> 想要**真正拉升分数**，需做 **P1（working 闸门）/ P3（cooking 拆分 session）**，或换用
> 细粒度数据集（Mendeley）——在那之上 `bathing` 才有水/电证据可与标签对齐。
> 所有结论仍以 **LIFT（而非裸 F1）** 为准。

---

## 8. 局限

1. **标注粒度粗**：`leisure→watching_tv`、`personal→bathing` 是近似代理，两者分数应理解为「对该类时段的覆盖度」。细粒度数据集（Mendeley）需交互下载且 HF 镜像不可达，故暂用本数据集。
2. **单住户单户型**：不能外推到其他家庭格局。
3. **MA 区间是「证据包络」**：时段级天然偏严，已用 Level 2b 段级做公平补偿，两者应合并解读。
4. **13 条无区间记录**：`bathing` 全部无法参与时间评估，其天级 0.375 属「有检出但无法定位」。

---

## 9. 复现命令

```bash
# 1) 下载真实数据集（89 MB，约 3 分钟，慢链路）
curl -L -o benchmarks/data/openshs_real.csv \
  https://raw.githubusercontent.com/plolutta/dataset/main/smart_home_dataset.csv

# 2) 完整三层协议（Level 1 天级+基线 / Level 2a 时段级 / Level 2b 段级）
PYTHONPATH=src python -m benchmarks.openshs_bench \
  --csv benchmarks/data/openshs_real.csv --map coarse --slot-seconds 300

# 3) 回归：自带合成样本（fine 映射）
PYTHONPATH=src python -m benchmarks.openshs_bench

# 4) 细粒度数据集（如有）：替换 --csv 并改用 --map fine
PYTHONPATH=src python -m benchmarks.openshs_bench --csv /path/to/fine.csv --map fine

# 5) 单测（16 项，含协议本身的确定性校验）
PYTHONPATH=src python -m pytest tests/test_openshs_eval.py tests/test_openshs_bench.py -q
```

> 注：`infer_activities` 为纯事件逻辑，本机可直接跑；若本机 sqlite 缺 FTS5 模块
> （`Store.init_schema` 建虚拟表失败），请于 NAS 容器内运行。
> 整个 `benchmarks/data/` 已被 `.gitignore` 的 `data/` 规则忽略：89 MB 真实数据集与
> 9 MB 合成样本均不入库；合成样本由 `benchmarks/gen_sample.py` 确定性生成，
> 单测在样本缺失时自动重建（`ensure_sample` fixture），故全新 clone 直接 `pytest` 即可。
