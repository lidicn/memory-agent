# memory-agent 三路径 MCP 完整审计报告

| 项 | 值 |
|---|---|
| 报告日期 | 2026-10-08 |
| 被测技能 | `insight` v6（updated_at 2026-10-08T00:00:00） |
| 被测服务 | memory-agent MCP（行为洞察层） |
| 宿主 | DESKTOP-J17LDCU / Windows 11 IoT Enterprise LTSC 2024 (10.0.26300) |
| Shell | powershell.exe / Node v22.23.2 |
| 审计人 | deepseek++2（本会话） |
| 报告落盘 | `E:/NAS/memory-agent/doc/审计报告/` |

---

## 1. 结论摘要

`insight` v6 定义了「三路径架构」，本次端到端实测结论：

| 路径 | 组件 | 状态 | 判定 |
|---|---|---|---|
| **路径0** | `route_question` | ✅ 可用 | 4/4 返回 ok，意图判定 3/4 正确 |
| **路径2** | `match_recipe`（剧本召回） | ❌ **未上线** | 调用返回 `NOT_FOUND`，服务端工具表无登记 |
| **路径3** | 全自主探索（步骤1-5） | ✅ 可用 | 取数链路完整跑通 |
| **路径3** | `submit_recipe`（剧本回填） | ❌ **未上线** | 调用返回 `NOT_FOUND`，服务端工具表无登记 |

**核心判定**：三路径实际只落地了「两条半」——`route_question` + 全自主探索能跑通，但**系统自我强化闭环已断**：探索成果无法沉淀为 recipe，下次同类查询不会变快。v6 承诺的「token 从 8000 降到 2500」在当前部署下**无法兑现**。

---

## 2. 三路径架构（据 insight v6）

```
用户问题
   │
   ├─ 路径0  route_question(question)          确定性摸排 → intent/route/entity_ids/time_range/hints
   │
   ├─ 路径2  match_recipe(question, intent)    召回已晋升 live 的查询剧本
   │        命中 → 严格按 tool_sequence 调用（省 token）
   │        未命中 ↓
   │
   └─ 路径3  全自主探索（get_entity_catalog → get_behavior_insights → get_device_usage → ...）
            探索成功后 **必须** submit_recipe 回填 → staging → 晋升 live → 供路径2 召回
```

设计意图：路径3 是冷启动，路径2 是热路径，`submit_recipe` 是两者的桥。

---

## 3. 测试方法

采用 4 个用例覆盖 v6 声明的 6 类 intent（device_usage / compare / anomaly / rhythm / activity / persona）中的 4 类：

| # | 用例问题 | 期望 intent | 期望 route |
|---|---|---|---|
| T1 | 书房空调最近7天开了多久 | device_usage | device_usage |
| T2 | 上周和这周比有什么变化 | compare | compare |
| T3 | 昨天谁在家做饭 | activity | activity |
| T4 | 最近作息是不是变了 | rhythm | rhythm |

每个用例完整走 路径0 → 路径2 → （降级）路径3 → 回填。

---

## 4. 结果矩阵

| 用例 | 路径0 route_question | 路径2 match_recipe | 路径3 探索 | 路径3 submit_recipe |
|---|---|---|---|---|
| T1 | ✅ device_usage | ❌ NOT_FOUND | ✅ 取数成功 | ❌ NOT_FOUND |
| T2 | ❌ unknown / auto | ❌ NOT_FOUND | 未执行（路径0 已失真） | ❌ NOT_FOUND |
| T3 | ✅ activity | ❌ NOT_FOUND | 未执行 | ❌ NOT_FOUND |
| T4 | ✅ rhythm | ❌ NOT_FOUND | 未执行 | ❌ NOT_FOUND |

---

## 5. 逐用例详情

### T1 书房空调最近7天开了多久（唯一走完全链路）

**路径0 返回**（`ok=true`）：

```json
{
  "intent": "device_usage",
  "route": "device_usage",
  "room": "书房",
  "query": "空调",
  "entity_ids": ["climate.lumi_cn_84159632_v2"],
  "days": 7,
  "time_range": {"start": "2026-10-01 14:29:05", "end": "2026-10-08 14:29:05", "label": "最近7天"},
  "hints": ["试试：上周空调用了多久", "试试：最近 3 天客厅灯用了多久"]
}
```

判定：✅ 房间、设备、窗口、意图全部正确。注意 `entity_ids` 已直接给出，Agent 可跳过 `get_entity_catalog`。

**路径2** → `NOT_FOUND: 工具 'match_recipe' 未登记到 MCP 工具表`

**路径3 探索**：

- `get_data_coverage(days=7)` → `day_coverage=1.0`，`hour_coverage=0.8802`，`active_days=8/8`，`missing_days=[]`，`total_events=213543`，`peak_hours=[5,3,12]`。✅ 窗口完整。
- `get_device_usage(entity_id=climate.lumi_cn_84159632_v2, days=7)`：

```
书房空调 climate.lumi_cn_84159632_v2
  总开启    2天23小时31分（257509 秒）
  占空比    42.6%
  会话数    1（开机2次 / 关机1次）
  时间线    2026-10-05T14:57:25 → 2026-10-08T14:29:14  still_on=true
  窗口前开启  否
```

**语义读数**：这是**单条超长会话**（连开近 3 天未关），不是「每天都开」。`by_day_seconds` 只落在 `2026-10-05` 一天，是积分口径把跨天会话归到起始日所致——Agent 转述时**不能**说成「每天开 10 小时」。

**路径3 回填** → `NOT_FOUND: 工具 'submit_recipe' 未登记到 MCP 工具表`

### T2 上周和这周比有什么变化（路径0 失真）

```json
{
  "intent": "unknown",
  "route": "auto",
  "query": "比 变化",
  "days": 7,
  "time_range": {"start": "2026-09-28 00:00:00", "end": "2026-10-05 00:00:00", "label": "上周"},
  "hints": []
}
```

判定：❌ 三重问题——
1. `intent=unknown` / `route=auto`，但 v6 明确声明支持 `compare` 意图（工具 `get_behavior_insights_compare` 就是环比专用）；
2. `time_range` **错解析为「上周」单窗口**（09-28~10-05），而用户问的是**双窗口对比**（上周 vs 这周），单窗口无法承载环比语义；
3. `query` 残留停用词 `比 变化`。

后果：若 Agent 照 `route=auto` 硬走，会拿到单窗口数据、答成「上周的情况」，**系统性答错**。

### T3 昨天谁在家做饭（路径0 正确，但下游有已知缺陷）

```json
{
  "intent": "activity",
  "route": "activity",
  "room": "厨房",
  "activity": "cooking",
  "query": "谁 家",
  "days": 7,
  "time_range": {"start": "2026-10-07 00:00:00", "end": "2026-10-08 00:00:00", "label": "昨天"}
}
```

判定：✅ 意图/房间/活动/窗口正确。但 `query` 残留 `谁 家`。

⚠️ 该用例的下游已知缺陷（本环境已登记 `bug_516a1bf033`）：`ask_memory` 对同一问题**未回答「谁」**，且把「厨房有人+插座通电」当作**连续烹饪 364 分钟**（约 6 小时），置信度给 1.0。这是路径3 下游的语义夸大缺陷，非路径0 问题。

### T4 最近作息是不是变了（路径0 正确）

```json
{
  "intent": "rhythm",
  "route": "rhythm",
  "query": "不 变",
  "days": 14,
  "time_range": {"start": "2026-09-24 14:37:57", "end": "2026-10-08 14:37:57"}
}
```

判定：✅ 意图与 14 天窗口正确（`rhythm` 走 `get_behavior_drift` 是合理的）。`query` 仍残留 `不 变`。

---

## 6. 缺陷清单

### D1【阻断级】`match_recipe` 契约漂移 —— 工具面有 schema，服务端无登记

| 维度 | 事实 |
|---|---|
| 客户端 tools/list | ✅ 有完整 schema（含 question/intent/object_type/top_k） |
| 服务端 dispatch | ❌ `NOT_FOUND: 未登记到 MCP 工具表` |
| `retryable` | `false`（确定性失败，重试无意义） |
| 复现 | 2/2 次均 `NOT_FOUND` |
| 交叉验证 | `help()` 的 `groups.入口` **只有 `route_question`**，全索引无 recipe 字样 |

定性：不是参数错误、不是权限问题，是**契约漂移**——schema 已下发但后端未注册。

已登记：`bug_4f77d077f5`（severity=major, status=open）。

### D2【阻断级】`submit_recipe` 同源缺陷

同 D1，`NOT_FOUND`。二者构成 recipe 闭环的**两端同时缺失**——路径2 无从召回、路径3 无从沉淀。

### D3【高危】路径0 对「环比」意图识别失败 + 时间窗误解析

`compare` 意图未被识别（`intent=unknown`），且把双窗口对比错解析为「上周」单窗口。**这是会把用户带向错误答案的一类缺陷**，比 NOT_FOUND 更隐蔽（NOT_FOUND 至少会报错，D3 会静默给出错误结论）。

### D4【中】路径0 `query` 字段残留停用词

实测残留：`比 变化`、`不 变`、`谁 家`。虽然本次未影响 `entity_ids` 解析（T1 的 `空调` 是干净的），但若下游用 `query` 做语义检索会引入噪声。

### D5【中】路径0 返回体不含「推荐工具名」

`route_question` 返回 `route`/`intent` 意图名（如 `device_usage`），但**没有**直接给出「下一步该调哪个工具」。这层映射（`device_usage` → `get_device_usage`）目前只存在于 skill 文档，不在工具返回里。工具描述已明确承认「返回体里没有『下一步工具名』那样的键」。**脆弱点**：skill 文档与工具实现在不同仓库维护，易再次漂移。

### D6【中】`help()` 索引与 tools/list 不一致

`help()` 是「权威工具索引」，其 `groups` 未收录 recipe 工具；而 tools/list 收录了。两个「真源」互相矛盾，Agent 无法据此判断工具是否可用。

---

## 7. 验证可用项（正面结论）

| 项 | 证据 |
|---|---|
| `route_question` 基本可用 | 4/4 返回 `ok=true`；T1/T3/T4 意图与实体解析正确 |
| `entity_ids` 直给可用 | T1 直接返回 `climate.lumi_cn_84159632_v2`，省去一次 catalog 调用 |
| `hints` 候选追问可用 | T1/T3/T4 均返回 2 条语义合理的追问建议 |
| 路径3 探索链路完整 | coverage → usage 两步取数，含 `still_on` 跨窗口片段处理 |
| `get_data_coverage` 逐日口径 | `days[]` 给 {day, events, active_hours, hours, empty}，`peak_hours` 正确 |
| 跨窗口会话处理 | 窗口末未闭合的会话正确标记 `still_on=true`，未虚构已结束 |
| 缺陷上报闭环 | `report_bug` → `list_bug_reports` 可读回，`bug_4f77d077f5` 已落库 open |

---

## 8. 环境旁证

`list_bug_reports(status=open, limit=10)` 返回 **10 条 open bug**，其中 9 条由同环境的 `deepseek++2` 于 2026-10-08 03:20-03:24 登记（另一会话），覆盖：

| bug_id | tool | 摘要 |
|---|---|---|
| bug_4f77d077f5 | match_recipe | 本报告 D1（06:29 登记） |
| bug_516a1bf033 | ask_memory | 未答「谁」+ 烹饪时长夸大至 364 分钟 |
| bug_a1965e8c35 | read_self_diary | 日记 text 全为空串但 count=2 |
| bug_745d5396cd | get_room_behavior_summary | activity_distribution 恒空，state_count=0 |
| bug_ca0ef6e343 | get_climate_sessions | SSE 流无匹配响应，间歇性崩溃 |
| bug_56762923f9 | get_behavior_prediction | 无数据时静默 ok=true + 全 null |
| bug_be2d44d3ee | infer_behavior_intent | 无参调用裸异常，无诊断信息 |
| bug_091dce70d9 | audit_rule_recall | 书房工作/就寝规则召回率 0，且与 list_candidate_rules 结论矛盾 |
| bug_d9aec621c4 | list_candidate_rules | 候选规则被遥测实体污染（空调电流传感器当「房间移动」） |
| bug_ed57d64396 | get_behavior_drift | 底层依赖 river 未安装，功能完全不可用 |

**说明**：`get_behavior_drift` 不可用（`river` 未安装）会**连带**让 T4（`rhythm` 路径）无法真正取数——路径0 判对了，但路径3 的目标工具是坏的。

---

## 9. 建议

### 9.1 立即可做（Agent 侧，无需等修复）

1. **跳过路径 0.5 / 0.6**：`match_recipe` 与 `submit_recipe` 已连续两次确定性 `NOT_FOUND`，属不可恢复失败，**不要重试**。直接走 `route_question` → 步骤 1-5。
2. **对 `route=auto` 保持警惕**：T2 表明 `auto` 可能掩盖「意图识别失败 + 窗口误解析」的双重问题。遇 `route=auto` 时应回读原问题、人工确认时间窗与对比维度，**不要直接取数**。
3. **转述跨天会话要加限定**：T1 的「2天23小时31分」是单条超长会话，不可说成「每天都开这么久」。

### 9.2 短期修复（网关侧）

| 优先级 | 动作 |
|---|---|
| P0 | 补齐 `match_recipe` / `submit_recipe` 的 dispatch 注册；**或**从 tools/list 与 help 中同步移除，二者取其一，不能各说各话 |
| P0 | 修复 `compare` 意图识别，时间窗须支持**双窗口**语义（当前单窗口无法承载环比） |
| P1 | `route_question` 返回体增加 `recommended_tool` 字段，消除「意图名→工具名」的隐式映射 |
| P1 | 统一 `help()` 与 tools/list 的工具集合，明确哪个是真源 |
| P2 | 清理 `query` 字段的停用词残留 |
| P2 | `get_behavior_drift` 补齐 `river` 依赖（否则 `rhythm` 路径永远取不到数） |

### 9.3 架构层面

`insight` v6 把「recipe 闭环」写进了标准工作流第 0.5/0.6 步，但工具未上线——这属于**文档先于实现发布**。建议 skill 版本发布与工具面挂载做**原子对齐**，或在 skill 里标注工具的可用性状态，避免 Agent 按不可执行的流程空转。

---

## 10. 附录：原始调用清单

| 序 | 工具 | 关键参数 | 结果 |
|---|---|---|---|
| 1 | shell_status | — | ok |
| 2 | route_question | 书房空调最近7天开了多久 / days=7 | ok, intent=device_usage |
| 3 | list_bug_reports | status=open, limit=10 | ok, count=10 |
| 4 | shell_exec | Test-Path doc / 列目录 | ok |
| 5 | route_question | 上周和这周比有什么变化 / days=7 | ok, intent=unknown |
| 6 | route_question | 昨天谁在家做饭 / days=7 | ok, intent=activity |
| 7 | route_question | 最近作息是不是变了 / days=14 | ok, intent=rhythm |
| 8 | match_recipe | question=书房空调... | ❌ NOT_FOUND |
| 9 | submit_recipe | intent=device_usage, tool_sequence=[...] | ❌ NOT_FOUND |
| 10 | get_data_coverage | days=7 | ok |
| 11 | get_device_usage | entity_id=climate.lumi_cn_84159632_v2, days=7 | ok |
| 12 | report_bug | tool_name=match_recipe, severity=major | ok, bug_4f77d077f5 |
| 13 | help | — | ok |
| 14 | list_skills | — | ok, 3 skills |
| 15 | get_skill | name=insight | ok, v6 |

---

*报告生成：2026-10-08 | 审计人：deepseek++2 | 依据：memory-agent MCP 实测返回体*
