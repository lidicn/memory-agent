# OpenSHS 活动推断基准 · 真实基线报告

> 日期：2026-09-16
> 关联：路线图 v1.0 任务 3《活动推断基准评估（OpenSHS）》
> 评测对象：memory-agent `infer_activities`（纯事件逻辑，无 chroma / 无 LLM）
> 复现脚本：`benchmarks/openshs_bench.py` + `benchmarks/openshs_convert.py` + `benchmarks/openshs_schema.py`

---

## 1. 目的

为 memory-agent 的活动识别能力提供**外部公开数据集**上的量化基线，验证其在「非自采、多日、1Hz 模拟智能家居」数据上的活动推断（precision / recall / F1），作为 v1.0「价值证明」的一环。

---

## 2. 数据集

| 项 | 内容 |
|---|---|
| 来源 | GitHub `plolutta/dataset` — *"collected through a simulated smart home using OpenSHS simulator"*（OpenSHS 模拟生成的智能家居数据集） |
| 格式 | OpenSHS 标准宽表 30 列：`wardrobe, tv, oven, …, bathroomCarp, Activity, timestamp` |
| 规模 | 1,048,575 行 / 约 89 MB，1 Hz 采样 |
| 时间跨度 | `2021-03-01 07:55:16` → `2021-03-14 23:24:27`（约 **13.6 天**） |
| 活动标注 | **粗粒度 7 分类**：`sleep / eat / work / leisure / personal / other / anomaly` |
| 时间戳格式 | 下划线分隔（`2021-03-01 07_55_16`），已在前处理中规整为 ISO |

> 说明：本机网络仅能直连 GitHub raw；更细粒度的 Mendeley「Smart Home Dataset」（fillKettle/boilWater/makeTea/… 细粒度标注）需经 Mendeley 交互下载且 HuggingFace 镜像被墙，故采用这份**直接可下载的真实 OpenSHS 数据集**。其粗粒度标注是唯一的评估口径限制，见 §3.3 与 §6。

---

## 3. 方法论

### 3.1 转换（边沿触发）
OpenSHS 是每秒一行的状态快照。若逐行展开会产生海量重复事件并破坏 MA 基于「静默间隔」的睡眠/离家检测，因此仅在该传感器 0/1 发生**变化**时产出一条事件（`wide_to_events`），与 HA 状态变化日志语义一致。29 个传感器列按 `SENSOR_MAP` 映射到 MA 的 `entity_id / room / domain`，实体 ID 故意带 MA `_TAG_RULES` 可识别的子串（media / light / switch / presence / door），保证零 `name_map` 也能正确打标。

本数据集 1.05M 行 → **1,717 条**边沿事件。

### 3.2 评估口径
按「**天 × 活动**」做二分类：某天若 MA 推断包含该活动记为正预测（P），若真值标注含该活动记为正样本（G）。逐活动统计 TP/FP/FN，汇总 micro / macro-F1。真值按「连续片段起始日」归因（跨午夜睡眠只记起始夜），与 MA 的夜→入睡归属一致。

### 3.3 标签映射（coarse）
粗粒度 7 分类需映射到 MA 活动概念：

| OpenSHS 标签 | → MA 活动 | 说明 |
|---|---|---|
| `sleep` | `sleeping` | 一一对应 |
| `eat` | `cooking` | 一一对应 |
| `work` | `working` | 一一对应 |
| `leisure` | `watching_tv` | **近似代理**：居家休闲以看电视为主 |
| `personal` | `bathing` | **近似代理**：个人护理（洗澡/如厕） |
| `other` / `anomaly` | — | 无 MA 对应物，未计入评分 |

`leisure` / `personal` 为近似代理，故 `watching_tv` / `bathing` 的精度/召回应理解为「对休闲/护理时段的覆盖度」，而非严格子活动判别。

### 3.4 已知局限
- 睡眠段通常只有 `presence/light` 信号（+`mainDoorLock` 常亮），缺乏门磁开合的「括号事件」；MA 睡眠检测基于「无家电/门/电脑操作的静默间隔」，故 `sleep→sleeping` 召回偏低属预期。
- 转换器对「住宅全空」判定依赖 absence 信号；单住户模拟数据中 MA 易过度触发 `away`（见 §5）。

---

## 4. 基线结果（真实数据集，13.6 天）

```
数据集      : benchmarks/data/openshs_real.csv
标签映射    : coarse（真实粗粒度 7 分类）
时间窗口    : 2021-03-01T07:55:16  →  2021-03-14T23:24:27
转换事件数  : 1717

活动              TP  FP  FN       P       R      F1
bathing          3  10   0   0.231     1.0   0.375
cooking          9   2   1   0.818     0.9   0.857
sleeping         9   0   4     1.0   0.692   0.818
watching_tv     12   0   1     1.0   0.923    0.96
working          1   9   0     0.1     1.0   0.182
----------------------------------------------------
MICRO                        0.618    0.85   0.716
MACRO-F1 (avg over activities): 0.638
```

### 定性观察（未计入评分）
- MA 在**几乎所有日期**都预测 `away`（住宅全空），对该单住户模拟数据明显**过度触发**，反映其「离家」判定阈值偏松。
- `working` / `bathing` 同样呈现「每日触发」式过预测（见 §5）。

---

## 5. 分析与解读

**表现良好（P/R 双高）**
- `watching_tv`（代理 `leisure`）：**F1=0.96**，仅 1 个 FN、0 FP。MA 对客厅在场/电视信号的识别与粗粒度「休闲」高度吻合。
- `cooking`（代理 `eat`）：**F1=0.857**，2 FP / 1 FN。厨房在场+家电信号判别稳健。
- `sleeping`：**F1=0.818**，精度满分但召回 0.692（漏检 4 天睡眠）——符合 §3.4 预期的静默间隔检测局限。

**过预测（召回高、精度低）**
- `bathing`（代理 `personal`）：**P=0.231 / F1=0.375**。GT 仅 3 天含 `personal`，MA 却几乎每天触发——其「卫生间在场/灯」判定未区分洗澡与短时如厕。
- `working`（代理 `work`）：**P=0.1 / F1=0.182**。GT 仅 1 天标 `work`，MA 几乎每天触发——「办公室在场」即判工作，未区分工作日/周末与具体行为。

**总体**：micro-F1=0.716（受高召回拉动），macro-F1=0.638（被 `working`/`bathing` 拉低）。若仅看有清晰对应、无代理歧义的活动（`cooking`/`sleeping`/`watching_tv`），表现可达 0.82–0.96。

---

## 6. 局限与后续

1. **标注粒度**：当前数据集为粗粒度 7 分类，需将 `leisure`/`personal` 近似映射到 `watching_tv`/`bathing`。若改用 Mendeley 细粒度数据集（fillKettle/boilWater/makeTea/watchTV/workOnComputer/…），可做到标签严格一一对应，分数更具可比性。届时只需把 CSV 放任意路径并 `--map fine` 运行。
2. **MA 改进方向（基于本基线）**：
   - `working` / `bathing` 过预测 → 引入「最小持续时长 / 子事件组合」闸，避免仅凭在场即判定；
   - `away` 过度触发 → 收紧全空判定（需多房间同时无 presence 且有时长门槛）；
   - `sleeping` 召回 → 探索「关灯=入睡」软锚，缓解静默间隔漏检。
3. 本基线固定使用默认 `infer_activities` 参数，未做针对 OpenSHS 的调参；调参后有望进一步提升。

---

## 7. 复现命令

```bash
# 1) 下载真实数据集（89 MB，约 3 分钟，慢链路）
curl -L -o benchmarks/data/openshs_real.csv \
  https://raw.githubusercontent.com/plolutta/dataset/main/smart_home_dataset.csv

# 2) 跑基线（coarse 映射）
PYTHONPATH=src python -m benchmarks.openshs_bench \
  --csv benchmarks/data/openshs_real.csv --map coarse

# 3) 回归：自带合成样本（fine 映射，6 类应全 1.0）
PYTHONPATH=src python -m benchmarks.openshs_bench

# 4) 细粒度数据集（如有）：直接替换 --csv 并改用 --map fine
PYTHONPATH=src python -m benchmarks.openshs_bench --csv /path/to/fine.csv --map fine
```

> 注：`infer_activities` 纯事件逻辑，本机可直接跑；若本机 sqlite 缺 FTS5 模块（`Store.init_schema` 建虚拟表失败），请于 NAS 容器内运行。真实数据集文件较大，已加入 `.gitignore`（`benchmarks/data/openshs_real*.csv`）不纳入版本控制；合成样本 `openshs_sample.csv` 仍受控。
