# memory-agent 第十六轮审计报告：配置面三方一致性（UI / 可写 / 消费）

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件 + `static/js` 设置页
> 本轮工作流：**v2.15「三方一致性核对」** —— UI 展示集 / HTTP 可写集 / 业务消费集
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

前十五轮扫配置面时，都是**单点看某个键**（第八轮 MA-18 报 24 个数值键无校验）。本轮换成一个更有结构的做法：把三个集合对齐，差集天然就是缺陷候选（lesson 147）。

| 集合 | 规模 |
|---|---|
| `WRITABLE_FIELDS`（HTTP 可写） | 77 |
| UI（`static/js`）出现的配置键 | 64 |
| **三方核对后的差集** | 见下 |

### 确认缺陷

| 编号 | 级别 | 位置 | 一句话 |
|---|---|---|---|
| **MA-32** | 🟠 Medium | `config.py:187` / `config_routes.py:35,191` / `settings.js:274` | `auto_discover_persona`：**UI 有开关 + HTTP 可写 + 回显成功 + 已持久化**，但**业务代码零消费** ⇒ 用户开启后完全无效，且无任何提示 |

### 差集普查结果

| 差集 | 数量 | 结论 |
|---|---|---|
| `可写 − 消费`（静默无效） | **3** | `redis_host`、`redis_port`、`auto_discover_persona` |
| `UI ∩ 可写 − 消费`（用户能改且无效） | **1** | **`auto_discover_persona`** ← 唯一构成缺陷 |
| `UI − 可写`（改了存不进去） | 21 | 经人工筛选全为 CSS/属性名噪音（`flex`/`badge`/`headers`…），**非缺陷** |

---

## 二、方法：v2.15 的迭代点

| 版本 | 做法 |
|---|---|
| v2.13（十四轮） | grep TODO/FIXME 优先 |
| v2.14（十五轮） | 常驻周期任务 |
| **v2.15（本轮）** | **三方一致性：UI 展示 / HTTP 可写 / 业务消费** |

**为什么有效**：单点扫 77 个配置键无从下手；三方核对直接把 77 → 3 → 1。

**lesson 148**：UI 的 label / hint 文本是最强的"声明"来源——用户真的会看到。取材优先级：**UI 文案 ≈ 工具描述 > docstring > 变量名**。

---

## 三、确认缺陷

### 🟠 MA-32　`auto_discover_persona`：UI 有开关、能存能回显，但没有任何代码读它

**UI 承诺**（`static/js/pages/settings.js:274-277`，原文）：

```
[开关] 主动推送生活习惯发现（关闭则仅记录，需用户确认才存档）
提示：开启后，AI 助手在对话中发现某成员的行为偏好（如夜猫子🦉）时
      会主动询问是否写入「生活习惯档案」；无论开关状态，写回前都会先征得确认。
```

#### 实测（真实 `Config` 类）

```
══ 真实 Config 类 ══
   auto_discover_persona 默认 = False
   设为 True 并 save() 后 → True
   重新 get_config() 读回  → True   ← **已持久化**
   /api/config GET 会回显该值（config_routes.py:191）

══ 业务代码消费点（排除 config.py / config_routes.py）══
   命中 0 处
```

⇒ **保存成功、回显成功、UI 有开关，但没有任何一行代码读它。**

#### 完整链路无死角

| 环节 | 状态 |
|---|---|
| `config.py:187` 字段定义 | ✅ 存在，带语义注释"是否主动把发现的标签推送给用户" |
| `config_routes.py:35` 可写 | ✅ 在 `WRITABLE_FIELDS` 内 |
| `update_config_api` 保存 | ✅ 走通用循环，`setattr` + `cfg.save()` + `reload_config()` |
| `config_routes.py:191` 回显 | ✅ 返回给前端 |
| `settings.js:274` UI 开关 | ✅ 渲染，带 label + hint |
| **业务代码读取** | ❌ **0 处** |

⇒ 用户点开开关 → 提示"配置已更新" → 刷新后开关仍开着 → **但 AI 助手不会有任何变化**。这是"声明未兑现"族的第 6 例（MA-26/27/28/29/30/32）。

#### 定 Medium 而非 High

- 方向是**静默不做**，不是做错事：不会误写档案、不会泄露、不会崩
- 但用户被 UI 文案明确承诺了一个行为，且**无任何"未实现"提示**

#### 关键排查：不是"同义键改名残留"吗？（lesson 149）

查了语义相近的键 —— `member_tag_agent_writeback` **才是实际生效的标签写回开关**：

```python
# mcp_server.py:1790
if not bool(getattr(rt.config, "member_tag_agent_writeback", False)):
    return ... "成员标签写回未授权（member_tag_agent_writeback=false）。"
```

⇒ 两者是**不同的语义**（一个管"是否主动询问"，一个管"是否允许写回"），不是改名残留。所以 `auto_discover_persona` 确实是**功能未实现**，而非"旧键残留"。

**修复应二选一**：① 补实现（对话侧读该开关决定是否主动询问）；② 若该功能已放弃，从 UI、字段、回显三处一并删除 —— **不能只留 UI**。

---

## 四、其余 2 个零消费键（不构成缺陷，如实标注）

| 键 | 是否 UI 暴露 | 结论 |
|---|---|---|
| `redis_host` | ❌ 不在设置页 | 低影响：只能经 API/config.json 写入，无人会去改；`candidate_promotion` 的 Redis 缓存走 `create_cache(backend=..., url=...)`，**当前无任何调用方传 redis**（默认 `memory`） |
| `redis_port` | ❌ 不在设置页 | 同上 |

⇒ 两者属于"预留字段"，用户不可见，标为**观察项**不升级（lesson 151：普查后按"是否 UI 暴露"分诊，否则 3 条一起报会稀释重点）。

---

## 五、验证通过（确认无问题）

| 项 | 结论 |
|---|---|
| **`candidate_promotion.py`（1577 行）深度核查** | ✅ 防御齐全：`clamp01`（89 行）、`log_saturate`（100）、`_to_int`（149）、`_to_float`（142）、`_as_mapping`；SQL `LIMIT` 全部由 `policy` 字段供给（`evidence_limit=500`、`scan_limit=100`、`evidence_window_hours=72`），`policy` 为 frozen dataclass ⇒ **无外部可控 LIMIT** |
| **NaN 可达性** | ⚠️ 观察项：`clamp01(nan)→nan`、`log_saturate(nan)→nan`、`score=nan` 时 `score < threshold` 为 False ⇒ 走 else ⇒ `promoted=True`（fail-open）。但实测 **NaN 经 SQLite REAL 列会存成 NULL、读回 `0.0`**，`payload_json` 数值不参与评分 ⇒ **当前不可达**，不升级为缺陷 |
| **`runtime.reload_config` 配置接缝** | ✅ `runtime.py:913` 注释记录"第七轮 CRITICAL-1"：5 个对象（`ha_assist`/`backup`/`semantic_dedup`/`activity`/`researcher`）构造时抓住旧 config 引用，已统一重指向 ⇒ **已修范式，且铺开到位** |
| **`ha_assist_memory_top_k` / `vlm_max_retries`** | ✅ **真实消费**：`ha_assist.py:37`、`vision_service.py:460`（本轮修正了上一轮因 `\b` 词边界导致的漏判） |
| `get_promoter` 单例忽略新参数 | ⚠️ 观察项：现有调用方均只传 `store`，无实际影响 |

### 自查：上一轮统计有误（lesson 151 相关）

上一轮用 `\b$k\b` 词边界 grep，把 `ha_assist_memory_top_k` / `vlm_max_retries` 误判为零消费。本轮改用 `(?<![\w])k(?![\w])` 后确认**两者均真实消费**。⇒ **含下划线的键名用 `\b` 不可靠**，改用前后非单词字符断言。

---

## 六、修复建议

### MA-32（一处，二选一）

**A. 补实现**（若功能要保留）——在对话/助手链路读取开关决定是否主动询问：

```python
if bool(getattr(cfg, "auto_discover_persona", False)):
    ...主动询问是否写入生活习惯档案...
```

**B. 删三处**（若功能已放弃）——同时删除：
- `config.py:187` 字段定义
- `config_routes.py:35` 的 `WRITABLE_FIELDS` 条目 + `:191` 回显
- `settings.js:274-277` 的开关与 hint

**不能只删一处**，否则会退化成"UI 有开关但存不进去"（`UI − 可写` 那一类）。

### 建议加门禁

> `check_writable_consumed.py`：断言 `WRITABLE_FIELDS` 每个键在业务代码中至少有一处读取；
> 若确为预留字段，需在同处登记豁免并注明用途。

这条能一次性封住"设置项静默无效"整族，且与项目"基线只准减少"的机制契合（新增豁免需显式登记）。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | 设置页开启 `auto_discover_persona` → 对话中发现偏好 | AI **主动询问**是否写入档案（或 UI 不再显示该开关） |
| 2 | 关闭该开关 | 仅记录，不主动询问 |
| 3 | 全量 `WRITABLE_FIELDS` 消费点普查 | 0 个未登记豁免的零消费键 |
| 4 | `redis_host`/`redis_port` | 若为预留，登记豁免说明 |

---

## 七、横向观察

### "声明未兑现"第 6 例，且这次的声明来自 UI

| 轮 | 缺陷 | 声明来源 |
|---|---|---|
| 十二 MA-26 | `save_skill` 自增 version | docstring |
| 十二 MA-27 | `list_agent_memories` 自报 all | 响应体 |
| 十三 MA-28 | `teach_signal` dry_run | 工具描述 |
| 十四 MA-29/30 | 动作桩 / nr 路由 | 返回值 |
| **十六 MA-32** | **auto_discover_persona** | **设置页 label + hint** |

共同点：**代码形状完全正常，扫描器一条都报不出来**，只有把声明拿去对照才看得见。本轮的新意是声明来自**前端文案**——提示后续审计应把 `static/js` 纳入声明取材范围。

### 又一次"只做了一半"，但这次是"做了一半就停了"

配置链路**做完了一整条**（字段 → 可写 → 保存 → 回显 → UI），**只差最后一步"被读取"**。这与第十一轮 MA-25（依赖注入了却没调用）、第十四轮 MA-29（TODO 桩）同形：**接口接好了，接上那一步没做。**

### 值得肯定：配置接缝的 CRITICAL-1 修得彻底

`runtime.py:913` 那段注释逐条列出了 5 个"抓住旧 config 引用"的对象并统一重指向，还写清了"为什么不需要重建"（调用时 getattr）。**这是"已修范式铺开到位"的正面样本**——说明被单独立项的族能做到位。

---

## 八、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 用 `auditlib` 原语 | ✅ `all_walk` / `own_walk` / `full_unparse` / `rel_path` |
| **门 2** | 命中多时手工分诊 | ✅ **决定性**：`UI − 可写` 21 处 → 全为 CSS/属性名噪音；`可写 − 消费` 3 处 → 按 UI 暴露分诊出 1 条；同义键排查排除"改名残留"误判 |
| **门 3** | 每条缺陷有实测 + 如实标注 | ✅ MA-32 用真实 `Config` 类 save/重读实测；NaN 明确标注"实测不可达，仅防御性观察"；上一轮 `\b` 误判已修正并记入 lessons |

### 遗留队列（十六轮累积）

| 项 | 状态 |
|---|---|
| **配置面三方一致性** | ✅ 本轮闭合（77 键全量普查） |
| **Store 共享连接 / 分页契约 / 重试语义** | ✅ 十五轮闭合 |
| **常驻周期任务** | ⚠️ 已报 MA-31；`device_feed` 观察项 |
| **MCP 登记表 / 启动期断言** | ✅ 十三轮闭合 |
| **锁覆盖** | ✅ 十二轮闭合 |
| **logging 格式串** | ✅ 十四轮闭合 |
| `candidate_promotion` NaN | ⚠️ 观察项（实测不可达） |
| `get_promoter` 忽略 policy 参数 | ⚠️ 观察项（无实际影响） |
| `recent_audit` 死代码 | ⚠️ 无调用方 |
| MA-03 端到端 | ⚠️ 未复现（`chromadb` 太重） |
| 47 处静默降级中"否定方向"44 处 | ⚠️ 仅抽查 |
| M2 的 30 处无钳制 `LIMIT` 站点 | ⚠️ 抽查（store.py 18 / repository.py 10） |
| `fail-closed` 39 处声明 | ⚠️ 已核两支 |
| 其余 3 处 `dry_run` | ⚠️ 静态确认正确 |

### 环境

| 项 | 值 |
|---|---|
| **本轮新增安装** | `starlette`（几秒完成，解锁 `config_routes` 导入） |
| 已装 | `bcrypt`、`python-jose`、`httpx`、`tomli`、`starlette` |
| 仍未装 | `chromadb`（体积过大）、`pymysql` |
| 门禁 | `run_gates.py . --no-smoke` → rc=0（208 条基线） |
| 新增 lessons | **147–151**（已并入 `lessons-round2.md`，共 1606 行） |
| 仓库状态 | 探针已还原；源码、量具、门禁、基线均未修改 |
