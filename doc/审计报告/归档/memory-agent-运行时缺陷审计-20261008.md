# memory-agent 运行时缺陷审计报告

- **审计日期**：2026-10-08
- **审计对象**：memory-agent MCP 服务（90 个工具）
- **审计方式**：使用者视角运行时实测（三路径 + 设备用量 + 事件下钻 + 目录复核）
- **数据窗口**：2026-10-01 ~ 2026-10-08（8 天；`day_coverage=1.0`，`missing_days=[]`，窗口完整）
- **结论**：**发现 8 个缺陷 —— 2 个 Critical、4 个 Major、2 个 Minor。核心问题集中在 `get_device_usage` 的语义定位与时长口径。**

---

## 0. 缺陷总览

| 编号 | 严重度 | 工具 | 一句话 |
|---|---|---|---|
| BUG-1 | Critical | `get_device_usage` | `query` / `category` 语义定位全面失效，仅 `room` 与 `entity_id` 可用；与 `route_question` 推荐参数直接冲突 |
| BUG-2 | Critical | `get_device_usage` | 时长口径错误：非「开启」语义事件（`idle->paused`）与零事件实体被计成 7 天全开、`duty_cycle=100%` |
| BUG-3 | Major | `get_device_usage` | 未闭合会话一律从窗口左沿算到右沿，导致几乎全部实体 `duty_cycle=100%`、`still_on=true` |
| BUG-4 | Major | `get_device_usage` | `room` 语义定位把遥测/存在类实体（sensor、binary_sensor）当开关设备统计时长 |
| BUG-5 | Major | `get_device_usage` | 部分参数组合触发 `MCP SSE stream ended without a matching response`（服务端无响应） |
| BUG-6 | Major | `get_device_usage` / `_summary` | 同一实体两工具口径矛盾（`switch_on_count=0` vs `on_off_count=1`） |
| BUG-7 | Minor | `route_question` | 规划输出 `entity_ids=[]`、推荐 `query` 参数（已证明失败），且 `days` 与 `time_range.days` 语义不一致 |
| BUG-8 | Minor | `get_entity_catalog` | 同房间加 `category` 过滤后实体数反而变多（13 → 22），结果不稳定 |

---

## BUG-1（Critical）`get_device_usage` 语义定位失效

### 现象

| 调用 | 结果 |
|---|---|
| `get_device_usage(query="书房电脑", days=7)` | ❌ `INTERNAL: 没有定位到任何设备` |
| `get_device_usage(query="电脑", days=7)` | ❌ 同上 |
| `get_device_usage(query="电视", days=7)` | ❌ 同上 |
| `get_device_usage(query="空调", days=7)` | ❌ 同上（并伴随 SSE 中断，见 BUG-5） |
| `get_device_usage(room="书房", query="电脑", days=7)` | ❌ 同上 |
| `get_device_usage(room="书房", days=7)` | ✅ `device_count=40` |
| `get_device_usage(entity_id="switch.lemesh_cn_1088333045_sw0a04_on_p_2_1")` | ✅ 正常返回 |

### 反证：目标设备确实存在

`get_entity_catalog(room="书房")` 明确返回：

```json
{ "entity_id": "switch.lemesh_cn_1088333045_sw0a04_on_p_2_1",
  "friendly_name": "书房电脑  开关 开关", "room": "书房",
  "category": "appliance", "has_data": true, "events_in_window": 1 }
```

### 影响链（最重要）

`route_question("昨天书房电脑开了多久")` 的规划输出是：

```json
{ "intent": "device_usage", "room": "书房", "query": "电脑",
  "entity_ids": [], "recommended_tool": "get_device_usage" }
```

即 **路径1 规划推荐 `query` 形式，而路径3 执行时 `query` 必然失败** —— 三路径衔接处存在真实断点。使用者按技能文档「步骤3 `get_device_usage(query="书房空调")`」操作会直接踩坑。

### 建议

1. 修复 `query` 语义解析（当前对中文友好名/别名匹配疑似未生效）；
2. 失败时降级：`query` 无命中应回退到 `get_entity_catalog` 做一次模糊匹配并返回候选，而不是直接 `INTERNAL`；
3. 让 `route_question` 输出真实 `entity_ids`（见 BUG-7）。

---

## BUG-2（Critical）时长口径把「非开启事件」和「零事件」算成全开

### 证据 A：媒体播放器从未 playing，却报 7 天全开

`get_device_usage(entity_id="media_player.xiaomi_x08a_1648_play_control", days=7, include_timeline=true)`：

```json
{ "raw_event_count": 13, "switch_on_count": 13, "switch_off_count": 0,
  "total_on_seconds": 604800, "total_on_human": "7天0小时0分",
  "duty_cycle_percent": 100.0, "still_on": true }
```

而 `query_events(entities=["media_player.xiaomi_x08a_1648_play_control"], days=7)` 显示这 13 条事件**全部**是：

```json
{ "action": "idle->paused", "old_state": "idle", "new_state": "paused" }
```

**没有一条是 `playing`。** 按工具文档「media_player 的 playing 才会被判定为开启」，该设备窗口内应记为 0 时长，实际却报 7 天全开。

### 证据 B：零事件实体报出 1 次开关 + 7 天全开

`binary_sensor.e4aaec34e80f_light`（书房）：

- `get_entity_catalog` → `has_data: true`，`events_in_window: 0`
- `get_device_usage_summary` → `events_in_window: 0`，但 `on_off_count: 1`，`total_on_minutes: 10080`（=7 天）
- `get_device_usage(room="书房")` 中该项 → `raw_event_count: 0`，`total_on_seconds: 604800`，`still_on: true`

**窗口内 0 条事件，却同时报「开关 1 次」与「7 天全开」** —— 逻辑自相矛盾。

### 建议

- 严格按 `on_states` 语义过滤：`idle->paused` 不构成「开」；
- 窗口内零事件的实体应返回 `no_data=true` / `null`，**不得**反推为「一直开着」；
- 增加断言：`events_in_window==0` 时 `on_off_count` 必须为 0。

---

## BUG-3（Major）未闭合会话一律算满整个窗口

`room="书房"` 返回 40 个设备，其中绝大多数呈现同一模式：

```
total_on_seconds = 604800 (= 7 天整)
duty_cycle_percent = 100.0
still_on = true
timeline[0] = { start: 窗口左沿, end: 窗口右沿, still_on: true }
```

出现该模式的包括：`light.philips_cn_245001446_cbulb_s_2_light`、`light.yeelink_cn_555003624_lamp22_s_2`、`switch.lemesh_cn_1088333045_sw0a04_on_p_2_1` 等。

文档说明「窗口前已开启从左沿起算」本身合理，但当窗口内**仅有一条事件、且后续无关闭**时，直接把时长补满到窗口右沿，会产生「几乎全部设备 100% 占空比」的失真结论。以 `switch.lemesh_cn_1088333045_sw0a04_on_p_2_1` 为例：`raw_event_count=1`，`switch_on_count=1`，`switch_off_count=0` → 时长 604800s。

**风险**：基于该时长的所有洞察（周报、能耗推断、`save_analysis_template` 沉淀）都会系统性偏高。

---

## BUG-4（Major）`room` 定位纳入遥测/存在类实体

`get_device_usage(room="书房")` 的 40 个设备里包含：

- `sensor.0x00158d0001a2520d_illuminance`（照度遥测）→ 报 `total_on=604800s`、`duty_cycle=100%`
- `sensor.0x00158d0001157509_action`（遥测）→ 同上
- `binary_sensor.e4aaec34e80f_light`（`domain=presence`）→ 同上
- `binary_sensor.lumi_cn_lumi_158d0001a2520d_aq2_motion_state_p_2_1`（存在感应）→ 同上

把照度传感器、人体存在传感器当成「开关设备」统计开关时长，语义错误。技能文档强调洞察工具默认 `behavior_only=true` 剔除遥测，但 `get_device_usage` 的 `room` 分支未做同等过滤。

---

## BUG-5（Major）部分参数触发 MCP SSE 流中断

```json
// get_device_usage(category="media", days=7)
{ "ok": false, "code": "mcp_tool_call_failed",
  "message": "MCP SSE stream ended without a matching response.", "retryable": true }
```

`get_device_usage(query="空调", days=7)` 亦复现同一错误。同轮其他调用正常，说明是**特定参数路径**导致服务端未返回响应（疑似未捕获异常或长阻塞），而非传输层整体故障。

---

## BUG-6（Major）同实体两工具口径矛盾

实体 `binary_sensor.e4aaec34e80f_light`，同一 7 天窗口：

| 字段 | `get_device_usage` | `get_device_usage_summary` |
|---|---|---|
| 事件数 | `raw_event_count: 0` | `events_in_window: 0` |
| 开关次数 | `switch_on_count: 0` | `on_off_count: 1` |
| 总时长 | `604800s` | `10080min`（=604800s） |

事件数一致，**开关次数一个 0 一个 1**。两工具对同一实体的同一窗口给出互斥结论，调用方无法判断哪个可信。

---

## BUG-7（Minor）`route_question` 规划与执行脱节

```json
// route_question("昨天书房电脑开了多久", days=2)
{ "intent": "device_usage", "route": "device_usage",
  "room": "书房", "query": "电脑", "entity_ids": [],
  "days": 2,
  "time_range": { "days": 1.0, "label": "昨天" },
  "recommended_tool": "get_device_usage",
  "params": { "has_room": true, "has_query": true } }
```

三个问题：

1. `entity_ids: []` —— 已知设备存在却未解析出 ID（根因即 BUG-1）；
2. 推荐参数 `query` 已证明不可用；
3. 顶层 `days: 2` 与 `time_range.days: 1.0` 语义冲突（前者是兜底值，后者是解析结果），调用方按哪个取窗口不明确。

---

## BUG-8（Minor）`get_entity_catalog` 结果不稳定

同房间、近似同一时刻两次调用：

| 调用 | `total` | `raw_entity_count` | `duplicates_merged` | `categories` |
|---|---|---|---|---|
| `get_entity_catalog(room="书房")` | **13** | 13 | 0 | `[appliance, telemetry]` |
| `get_entity_catalog(room="书房", category="appliance")` | **22** | 26 | 4 | `[appliance]` |

加了 `category` 过滤后，实体数从 13 变为 22（**过滤后反而更多**），且去重数、raw 数都变化。`category=appliance` 是全集子集，其结果不应超过无过滤的 13。

**可能原因**：两次调用走了不同数据源/缓存路径，或 `only_enabled` / window 默认值在两次调用间不一致。需复现确认后修复，否则依赖 catalog 的一切下游（含 `get_device_usage(entity_id=...)` 的取参）都不可靠。

---

## 附：三路径正向结论（非缺陷）

| 路径 | 结果 |
|---|---|
| 路径1 `route_question` | 3/3 正确识别意图（device_usage / rhythm / compare），规划本身可用；缺陷在输出参数（BUG-7） |
| 路径2 `match_recipe` | 3/3 返回 `count=0`（当前无 live 剧本），按设计**正确降级**到路径3 |
| 路径3 探索 | 6 步取数成功（coverage / catalog / insights / activities / compare / usage） |
| 路径3 回填 | `submit_recipe` 成功：`memory_id=98e6d9b0a72293f8`，`state=staging`，`auto_promote_blocked=0`（符合「恒 staging」红线） |
| 技能真源 | `list_skills` = 3 个（autoflow v1 / insight v6 / presence-detection-rules v1）；`get_skill("insight")` = v6，`is_latest=true`，本地缓存无需更新 |

---

## 优先级建议

1. **立即修**：BUG-1（阻断三路径衔接）、BUG-2（数据错误，污染所有时长洞察）
2. **本迭代修**：BUG-3、BUG-4、BUG-6（口径一致性）
3. **排期修**：BUG-5（稳定性）、BUG-8（catalog 一致性）
4. **顺手修**：BUG-7（规划输出对齐执行可用参数）

---

*本报告全部结论均来自运行时实测返回体，未修改任何线上数据；`submit_recipe` 的 staging 写入为技能文档要求的标准回填动作。*
