## 四十七、运行时键 diff 的 12 条读数逐条判定：一处文案漂移当场改掉、一处功能维度丢失呈 DCD（2026-10-07，任务表 #40/#58 Q-D）

### 一、为什么必须逐条判，不能整批"已裁异名切换"收口

§四十五 量出 14 条静态可疑里的 13 条是改名/新增/签名不可比，剩下的判定要靠**同库同种子把两侧真跑一遍**
（`.qoder/tmp-c72-probe.out`，12 行读数）。这张表若整批写成"Q-B 已裁 ⇒ 全部核销"，就会把
**两种性质完全不同的东西混成一个**：改名（键没了、值还在）与功能维度丢失（值本身不再生成）。
下面一行一行给判据，每行都带 file:line。

### 二、判定表（对外那条读法为准，不是 core 内部形状）

| # | 工具（对外名） | 丢的键 | 判定 | 判据（现读 file:line） |
|---|---|---|---|---|
| 1 | `data_coverage` | `days_total` / `days_with_data` | **改名等价** | 新引擎同值改名 `total_days`/`active_days`（`service.py:473-474`）；Q-B 甲把新命名定为正式口径（`decisions/20261005…裁定` §三 Q2，批次含本工具） |
| 2 | `data_coverage` | `first_day_with_data` / `last_day_with_data` | **不是改名**：新键 `start_day`/`end_day` 是**窗口边界**（`service.py:442 start_day, end_day = self._tr_days(tr)`、`:475-476`），legacy 那两格是**有数据的首末日**（`insights_legacy.py:1751-1752 with_data[0]/[-1]`）。值可由 `days[]`/`missing_days` 推出（`service.py:460-464`），命名键没了 | 见第 三 节（文案已改齐，键不补） |
| 3 | `data_coverage` | `note` | **纯丢，无信息损失** | legacy 那句 note 只是把 `days_total`/`days_with_data` 拼成人话（`insights_legacy.py:1741-1746`）；新载荷 `day_coverage`+`missing_days` 是同一事实的结构化版本 |
| 4 | `data_coverage` | `window` | **已被 #73 补** | `service.py:430-439` 走 `_with_window`；台账 §四十六 |
| 5 | `infer_activities` | `behavior_only` / `detector_report` / `signal_inventory` | **未迁清单（已登记），非丢键回归** | `inbox/20261004-MA-裁6落码回执与Q4三项对比读数.md:122` 明列这三项在"未迁清单"里且"MA 不自称迁完"；解释侧按裁5 维持 legacy 路由。新引擎的对位可诊断性是 `excluded_entities`（裁6 Q3=A）+ `rule_sources`（裁6 Q4） |
| 6 | `get_user_persona` | `persona` | **已裁删除** | DCD 20261005 裁定标 deprecated，任务表 #56 已落码 |
| 7 | `get_user_persona` | `window_days` / `summary` | **改名 + 拆分** | `window_days`→`days`（`service.py:1190-1191 defaults`）；`summary`→`traits`+`rhythm` |
| 8 | `get_user_persona` | `most_active_room` | **纯丢，可推** | 新载荷给 `top_rooms`（排序列表，`service.py:1190-1191`），首元素即 legacy 那格；两侧都无消费方（第 四 节） |
| 9 | `get_data_quality` | `data_quality_issues` / `days` | **改名 + 已被 #73 覆盖** | `data_quality_issues`→`issues`；legacy 的 `days` 语义由 `window.days` 承担（`TimeRange.to_dict()` 键集含 `days`，裁5 Q-A 甲） |
| 10 | `get_behavior_insights`（对外 = `get_behavior_insights_compare`） | `comparison`/`current_window`/`previous_window`/`summary`/`compare_days` | **改名等价** | 新 `_compare_insights` 返回 `days/current/previous/delta/trend/filters`；门面 `get_behavior_insights(compare_days)`→`core.compare_insights`（`api.py:682`） |
| 11 | `get_behavior_insights`（同上） | **`climate_comparison`** | **真丢 —— 呈 DCD** | legacy 的温控环比（修复 #9）`insights_legacy.py:2059-2073` 聚合两侧窗口的空调时长/设定/室温；聚合函数已迁到 `insights/utils.py:889 aggregate_climate_sessions` / `:907 compare_climate`，但**新引擎一次都没调**（全仓 `grep _climate_compare\|climate_comparison` 除 legacy 外只命中 utils 的 docstring）。⇒ #9 那个修复被门面切换洗掉了。呈文：
   `关键决策部/inbox/20261007-MA-温控环比维度在新引擎无落点-决策申请.md`（甲/乙/丙三选一） |
| 12 | `device_usage` / `climate_sessions` / `entity_catalog` / `search_events` / `query_behavior_events` | 无（`LOST=-`） | **纯新增** | 探针读数 LOST 列为空，只多出分页/口径键 |
| 13 | `plan_question` | `recommended_tool`/`steps`/`suggested_args`/`answer`/`ok`/… | **待 DCD** | 与 `route_question` 的承诺键是同一条线，已在 `inbox/20261007-MA-route_question承诺键与window回显范围-决策申请.md` Q1 |
| 14 | `anomaly_report` | 探针报错 | **签名不可比** | legacy 是 `(by_day, hourly, …)` 纯格式化助手，与门面不同物；读数里的 `TypeError` 是量具的配对边界，不是产品缺陷 |

### 三、当场改掉的那格：`get_data_coverage` 的 handler docstring

`mcp_server.py:1618-1621`（改前）向 MCP 客户端承诺"返回每天的 events 量与 **has_data** 标记、
**first/last 有数据的日期**、以及 missing_days"——两格都不存在：
逐日格子叫 `empty` 不叫 `has_data`（`service.py:463-464`），`start_day/end_day` 是窗口边界不是"有数据的首末日"。
docstring 就是 MCP 工具描述，是**注入给模型的那句话**，和 §四十五 的 `route_question` 同一类缺陷。

**为什么这格我自办、第 11 行却呈 DCD**：Q-B 甲已经把 `data_coverage` 的**新命名定为正式口径**
（它是四个批次工具之一），且两侧消费方都确认不读旧键（第 四 节）⇒ "改文案对齐载荷"是唯一不与既有裁定冲突的动作；
而补回 `first_day_with_data`/`last_day_with_data` 等于把批次口径改回 Q-A。
第 11 行相反：它要的是**引擎归属**（climate 数据归 legacy 还是新引擎），那是裁5 Q1=A/Q-C 那一层，MA 不替 DCD 定。

新文案逐字对齐载荷：`days[]={day,events,active_hours,hours,empty}`、`missing_days`、
`start_day/end_day` 明确标注"窗口边界 ≠ 有数据首末日"、`day_coverage`/`hour_coverage`/`peak_hours`。

### 四、消费方读键：AF 已确认，DB 这次现读补齐

`AutoForge/docs/handoff/MA-QB异名切换-AF消费方读键确认_20261006.md:37-56` —— AF **不调用任何 MA MCP 工具**，
唯一集成是 `POST /api/metrics/ingest`（AF→MA 单向），字段固定；该文 `:80` 另提示"若另有真实消费方（doubao-butler），
建议 MA 另行发读键确认"。本轮现读 DB：
`doubao-butler/butler/integrations/memory_agent.py` 全部 `call_tool("…")` 命中 **7 个工具**
（`add_semantic_memory / analyze_camera / ask_memory / get_vision_status / list_agent_memories /
retrieve_agent_memories / revoke_memory`），
**四个批次工具一个都不调**；对 `first_day_with_data|last_day_with_data|days_total|days_with_data|
get_user_persona|get_data_quality|data_coverage|get_device_usage` 在 DB 全仓（2704 个 `.py`）
grep **零命中**（该仓含这 7 个工具的引用，所以"零命中"是量出来的不是猜的）。

⇒ 第 1/2/3/7/8/9/10 行的改名与纯丢**对两个已知消费方都零影响**；这也是第 三 节敢自办的前提。

### 五、顺带量具自证：一处假警报，差点被我登记成缺陷

`mcp_server.py` 的手工 `TOOL_CATALOG` 里 `get_behavior_insights` **出现两次**（`:190` 作息版、`:308` 环比版），
48 条 name 里唯一的重号 —— 看起来像"目录里一个名字挂两份说明、后把前洗掉"的活缺陷。
现读否掉：`mcp_server.py:3341/3343` 的注释 + `:3344-3345` 的赋值在导入末尾用 `build_catalog()` / `TOOL_NAMES_FROM_SPEC` **整体覆盖**
这张手工表（注释明写"上方手工 TOOL_CATALOG 为兼容历史保留，实际以 tool_schema.TOOL_SPECS 为准"），
而活表里两者是**不同的名字**：`tool_schema.py:111 get_behavior_insights` 与 `:131 get_behavior_insights_compare`。
⇒ 对外无重号，不进缺陷台账；但**这张死表还在、还会误导下一个读的人**，登记为待清理（不与本轮混判）。

### 六、这一档的三条规矩

1. **"已裁异名切换"不是一张免检票**：同一个工具的 LOST 列里可以同时躺着改名、纯丢、可推丢失和真丢四种，
   必须逐行给 file:line；整批核销会把 #9 那类"修好又被切换洗掉"的功能维度藏进改名堆里。
2. **功能维度丢失与键名口径丢失的处置面不同**：前者要回答"这块数据归哪个引擎"（DCD 层），
   后者若已被批次裁定定口径，就只剩"文案对齐载荷"一种不越界的动作。
3. **死表也会咬人**：判"某条读数是真的"之前，先确认那张表**还在不被覆盖**——`grep` 命中在覆盖前的手工段落里，
   等于命中在注释里。
