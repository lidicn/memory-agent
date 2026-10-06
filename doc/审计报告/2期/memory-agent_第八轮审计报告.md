# memory-agent 第八轮审计报告：配置面写入的边界校验

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.7「门禁自己声明的折算口径 → 新维度」**
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

新维度不是我想出来的，是**项目门禁文档自己写出来的**。`scripts/scan_day_bounds.py` 的注释里有一句：

> `config` 归属的前提是"只有运维能改"。这个前提在本项目**不成立**：
> `api/config_routes.py` 的 `WRITABLE_FIELDS` 里有一批键可以从 HTTP（设置页的 number 输入框）
> 直接写入……所以这些键按 `external` 计。

**工具作者已经在注释里标出了他们认为危险的东西。** 于是本轮就审这一件事：**这些 HTTP 可写的配置值，写入时有没有校验？**

**答案：只有类型收敛，没有任何范围校验。**

### 确认缺陷

| 编号 | 级别 | 位置 | 一句话 |
|---|---|---|---|
| **MA-18** | 🔴 High | `api/config_routes.py:240-252` `update_config_api` | **24 个 HTTP 可写数值键全部无范围校验**；`tz_offset_hours` 极值 → **全服务时间计算崩溃** |
| **MA-19** | 🟠 Medium | 同一批键的**两个写入端点** | `/api/config` 无校验 vs `/api/collect/config`（+`/api/poller/config`）有下界 —— **同键、两入口、口径不一致** |

---

## 二、方法：v2.7 的迭代点

| 版本 | 做法 |
|---|---|
| v2.5（六轮） | 已确认缺陷形状清单 → 逐条全量反查 |
| v2.6（七轮） | 解除环境阻塞 + 注释关键词反推新维度 |
| **v2.7（本轮）** | **门禁自己声明的"折算口径" → 新维度** |

**lesson 106**：工具作者已经在注释里标出了他们认为危险的东西——读它，别自己想。
（这是 lesson 85「口径外清单当维度」、lesson 101「注释关键词反推」的第三次验证。）

---

## 三、确认缺陷

### 🔴 MA-18　`update_config_api`：24 个 HTTP 可写数值键无任何范围校验

**`WRITABLE_FIELDS` 共 77 键，其中数值型 24 个：**

```
chroma_port  data_retention_days  first_run_lookback_hours  ha_assist_memory_top_k
ha_db_port  ha_db_query_batch  ha_db_query_timeout  llm_max_tokens  llm_temperature
llm_timeout  mcp_response_max_bytes  polling_interval  redis_port  scene_graph_sample_rate
tv_capture_timeout_s  tv_mqtt_port  tv_mqtt_timeout_s  tz_offset_hours  vision_cooldown_s
vision_max_per_hour  vision_no_tv_interval_s  vision_snapshot_retention_days
vlm_max_retries  vlm_timeout_s
```

**写入侧的全部校验**（`api/config_routes.py:240-252）：

```python
try:
    if isinstance(current, bool):    value = ... in ("1","true","yes","on")
    elif isinstance(current, int):   value = int(value)
    elif isinstance(current, float): value = float(value)
except (TypeError, ValueError):
    return error(f"字段 {key} 类型非法")
```

⇒ **只保证类型，不保证范围**（lesson 110：看到"类型非法"的 400 就以为安全，是常见误判）。

#### 实测 A：`tz_offset_hours` 极值 → 全服务时间计算崩溃

关键一步是**真的调用消费函数**（lesson 108），而不是只算 `timedelta`：

```
now_local(8.0)      → 2026-10-07 00:05:46        ✅
now_local(12.0)     → 2026-10-07 04:05:46        ✅
now_local(-999.0)   → ❌ ValueError: offset must be a timedelta strictly between -24h and 24h
now_local(1e5)      → ❌ ValueError （同上）
now_local(1e6)      → ❌ ValueError
now_local(1e9)      → ❌ ValueError
```

**限制条件藏在标准库内部（±24h），不在业务逻辑里**——所以只算 `timedelta(hours=1e6)` 会漏判（那个算式本身成立）。

`now_local` 是全仓 **50+ 处**时间计算的入口（`activity_inference` / `agent_memory` / `store` / `analysis` …）。一个 HTTP 请求写坏这个值，服务所有时间相关功能崩溃。

**可达性**：`tz_offset_hours` 在 `WRITABLE_FIELDS`；`POST /api/config` 需 `require_admin`。

#### 实测 B：其余极值键（类型收敛全部通过）

| 键 | 输入 | 后果 |
|---|---|---|
| `vlm_timeout_s` | `0` / `-1` | **HTTP 挂死到底**（等同无超时） |
| `polling_interval` | `0` | 轮询间隔 0 ⇒ 忙循环 |
| `vision_max_per_hour` | `1000000000` | 限流上限失效 |
| `mcp_response_max_bytes` | `-1` | 响应裁剪阈值失效 |
| `first_run_lookback_hours` | `1000000000` | 首轮回溯全量历史 |
| `data_retention_days` | `-5` | 见下 |

#### 实测 C：`data_retention_days` 两个相反方向的失效（lesson 109）

```
30    → cutoff 2025-12-02              ✅ 正常
-5    → clamp 到 1 → cutoff 2025-12-31 ❌ 只留 1 天，历史被删
1e9   → 收口到 200000 → cutoff 1478-06-03  ❌ 什么都不删
1e12  → 同上                            ❌ 什么都不删
```

**两个方向后果完全相反但都是错的**：一端把历史删光，另一端把保留期静默改写成"永不清理"。

---

### 🟠 MA-19　同一批配置键的两个写入端点，校验口径不一致

`polling_interval` 与 `data_retention_days` **同时**出现在两个端点的可写清单里：

| 端点 | Handler | 校验 |
|---|---|---|
| `POST /api/config` | `config_routes.update_config_api` | **无** |
| `POST /api/collect/config` | `collect_routes.collect_config` | ✅ `max(900, ...)` / `max(0, ...)` |
| `POST /api/poller/config` | 同上（同一 handler，两个路径） | ✅ 同上 |

**实测同输入不同结果**：

```
键                       输入    /api/config   /api/collect/config
─────────────────────────────────────────────────────────────
polling_interval         0       0            900
polling_interval         60      60           900
polling_interval         10      10           900
data_retention_days      -5      -5           0
data_retention_days      0       0            0
```

⇒ 通过 `/api/config` 可以把 `polling_interval` 写成 0（忙循环），而同一键走采集端点会被钳到 900 秒。

**注意方向**：`/api/config` 是**更通用、更可能被前端设置页调用**的那个端点，恰好是没校验的那个。

---

## 四、验证通过（确认无问题）

| 项 | 结论 |
|---|---|
| `api/collect_routes.py:152-166` | ✅ **写对了**：`interval_minutes` 有 `max(15, ...)`、`polling_interval` 有 `max(900, ...)`、`data_retention_days` 有 `max(0, ...)`，且错误消息点名下界 |
| `_is_masked` 掩码判定（config_routes:68） | ✅ 注释记录了真实事故（`go2rtc_pass` 被掩码覆盖导致 401），已放宽为"含 8 连星即掩码" |
| `SECRET_FIELDS` 掩码返回 | ✅ 密钥不回传明文 |
| `Config.save()` 原子写 | ✅ 第七轮已验证 |
| `require_admin` | ✅ `update_config_api` 有；`collect_config` 用 `require_user`（采集配置） |
| `clamp_days` 对保留期用 `LONG_WINDOW_MAX` | ✅ 设计正确（`day_bounds.py` 文档明确区分查询窗口与保留期） |
| `insights/models.py:82` `set_house_tz_offset` | ✅ 有 `if not (-12.0 <= offset <= 14.0): return HOUSE_TZ` —— **同仓已有 tz 范围校验的正面对照** |

**最后一条很重要**：`insights/models.py:82` 明明写着 `if not (-12.0 <= offset <= 14.0)`，说明**项目知道 tz 偏移的合法范围**，只是没把这个校验放在 HTTP 写入入口。

---

## 五、修复建议

### 一次性修法：把 `_num` 范式铺到配置面

项目已有现成范式（`behavior_routes.py:24` 的 `_num(name=, default=, lo=, hi=)`，注释还记录过"改前"的三种错法）。建议：

```python
# 1) 把 _num 提到公共位置（如 api/_num.py 或 deps.py）
# 2) 给 WRITABLE_FIELDS 的每个数值键声明合法区间
_NUMERIC_BOUNDS = {
    "tz_offset_hours":              (-12.0, 14.0),      # 对齐 insights/models.py:82
    "polling_interval":             (900, 86400),       # 对齐 collect_routes
    "data_retention_days":          (0, 3650),
    "vlm_timeout_s":                (1, 600),
    "llm_timeout":                  (1, 600),
    "vision_max_per_hour":          (0, 10000),
    "mcp_response_max_bytes":       (1024, 50_000_000),
    "first_run_lookback_hours":     (1, 24 * 30),
    ...  # 其余 16 个键同理
}

# 3) update_config_api 里
value, bad = _num(value, name=key, lo=lo, hi=hi)
if bad:
    return error(bad)     # 400，而不是静默改值
```

### 更彻底：让两个端点共用同一个校验表

MA-19 的根因是"两个入口各写各的"。把 `_NUMERIC_BOUNDS` 作为**唯一真源**，两个端点都查它 ⇒ 口径自动一致，且和项目现有的 `WRITABLE_FIELDS` 单一真源思路一致（config_routes 的注释就写着"清单从真源解析，不复制一份"）。

### 建议加门禁

> `check_config_bounds.py`：断言 `WRITABLE_FIELDS` 中每个数值键，在**所有**写入端点都出现范围校验。
> 新增可写键而忘了加边界即判红。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | `POST /api/config {"tz_offset_hours": 1000000}` | **400**，且不落盘 |
| 2 | `POST /api/config {"polling_interval": 0}` | **400**（或钳到 ≥900） |
| 3 | `POST /api/config {"data_retention_days": -5}` | **400** |
| 4 | `POST /api/config {"vlm_timeout_s": 0}` | **400**（0 = 无超时） |
| 5 | `/api/config` 与 `/api/collect/config` 传同一个非法值 | **返回一致**（都 400 或都钳到同一值） |
| 6 | 合法值（`tz_offset_hours: 8`） | 正常保存 |

---

## 六、横向观察

### "两个入口、一个校验一个不校验"是本项目第三次出现

| 轮 | 形状 | 入口 |
|---|---|---|
| 三 MA-09 | `list_rule_lifecycle` 的 limit | API 钳制 vs MCP 裸下推 |
| 四 | `timedelta` 同族 | days 族有门禁 vs hours/minutes 族无 |
| **八 MA-19** | **配置项写入** | **`/api/collect/config` 有下界 vs `/api/config` 无** |

三次都是**同一个能力有多个入口，防护只做在了其中一个**。

### 八轮下来，配置面是第一次系统审计

前七轮覆盖了：数据解析（一轮）、成员收窄（二轮）、`LIMIT`（三轮）、时间窗（四轮）、静默降级（五轮）、形状闭合（六轮）、文件写入（七轮）。**配置面（HTTP 可写的运维值）此前完全未审**——而它恰恰是 `scan_day_bounds.py` 作者明确标注为"这个前提不成立"的地方。

⇒ 又一次印证：**工具作者写在注释里的"已知局限/已知危险"，比自己想维度可靠得多。**

### 与 AutoForge 第九轮的对照

AutoForge 第九轮报的 `AUTOFORGE_BLAST_RADIUS` 负数 → 护栏静默失效，与本轮 MA-18/MA-19 **是同一族**：配置面数值无范围校验。

差别在于：AutoForge 那边**已有 `_env_number(lo=, hi=)` 范式但只铺开 1 处**；memory-agent 这边**已有 `_num(lo=, hi=)` 范式（HTTP 参数）和 `_NUMERIC` 下界（采集端点）**，但没铺到通用配置端点。

⇒ 两边都可以用同一句话总结：**范式写对了，没铺到配置面。**

---

## 七、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 用 `auditlib` 原语 | ✅ 本轮用 `all_walk` / `rel_path` |
| **门 2** | 命中多时手工分诊 | ✅ 24 个数值键 → 逐个找消费点 → 3 条有实测后果 |
| **门 3** | 每条缺陷有实测 PoC + 同仓对照 | ✅ MA-18 三组实测（含真的调用 `now_local`）；对照 `insights/models.py:82` 已有 tz 范围校验；MA-19 双端点同输入对照 |

### 遗留队列（八轮累积）

| 项 | 状态 |
|---|---|
| `timedelta` 同族剩余 11 处 | ✅ **本轮抽查闭合**：全部为 `self.policy.*` / `config.*` 派生，非外部直接输入（`candidate_promotion` 4 处、`device_feed`/`ha_db`/`house_time`/`insights` 各 1-2 处） |
| MA-03 端到端 | ⚠️ 未复现（`chromadb` 太重，未装） |
| 47 处静默降级中"否定方向"44 处 | ⚠️ 仅抽查 |
| M2 的 34 处未钳制 `LIMIT` 站点 | ⚠️ 抽查，未逐一确认 |
| `save_skill` 并发 | ⚠️ 观察项，未复现交叠 |
| **24 个数值键中其余 16 个** | ⚠️ 只实测了 6 个有明确后果的；其余（`chroma_port`/`redis_port`/`llm_temperature` 等）未逐一找消费点 |

### 环境

| 项 | 值 |
|---|---|
| 已装 | `bcrypt 5.0.0`（七轮）、`tomli`（门禁依赖） |
| 仍未装 | `chromadb`（体积过大）、`starlette` |
| 门禁 | `run_gates.py . --no-smoke` → rc=0（208 条基线） |
| 新增 lessons | **106–110**（已并入 `lessons-round2.md`，共 1234 行） |
| 仓库状态 | 探针已还原；源码、门禁、基线均未修改 |
