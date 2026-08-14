---
name: insight
description: 家庭行为洞察分析技能。当需要从家庭传感器/设备事件中提取作息节律、设备用量、异常行为，或把分析结论沉淀为可复用模板与技能时使用。通过 memory-worker 的 MCP 工具获取服务端已算好的洞察，不要自己拉原始事件硬算。
category: insight
version: 4
updated_at: 2026-08-06T00:00:00
---

# 家庭行为洞察分析

你连接了 **memory-worker**（忆家管家）的 MCP 服务器，它是「行为洞察层」而非「事件数据层」：
服务端已经把时长、作息、异常算好，调用方只需拿结论。

> **技能来源（重要）**：本技能由 memory-worker 网关作为唯一真源维护。Agent 应通过
> MCP 工具 `get_skill(name="insight")` **从网关拉取最新版本**，不要在各处维护本地副本。
> 每次使用前先拉取并与本地缓存的 `version` 比对，版本号更大即为有新版本，需更新。

## 何时使用本技能
- 用户要「周报 / 作息分析 / 谁几点睡 / 设备用了多久 / 有没有异常 / 在做饭还是在看电视」。
- 用户要「哪些设备失联了 / 这几天到底有没有数据 / 空调设定几度室温多少」。
- 用户想用口语问法查记忆：「上周三晚上在干嘛」「昨天谁在家做饭」。
- 用户想把某条行为模式（如「主卧 23:30 后关灯即入睡」）沉淀给 Node-RED 自动化。
- 用户想把某条分析经验固化成可复用 skill。
- 用户说「用 xxx 模板分析 / 按 xxx 模板看…」，希望直接套用已沉淀的模板出结果。

## 存储分层（先搞清数据从哪来）
- **关系库（主，sqlite）**：承载全部事件查询、时长计算、节律/动线/异常分析。绝大多数工具走这里。
- **向量库（辅，chromadb）**：只对「天×房间聚合摘要」做镜像，并供语义检索副驾。**没有**裸向量查询入口——
  不要试图直接问向量库要原始事件，那是退步。
- 想知道两层状态？`get_collect_status()` 的 `storage.relational` / `storage.vector` 字段一目了然。

## 标准工作流
1. `get_entity_catalog(room="主卧")` —— 用人话找设备，拿到 `entity_id` 与友好名。
2. `get_behavior_insights(days=7)` —— 直接拿作息、活跃时段、房间分布、异常（含语义异常）。
3. `get_device_usage(query="书房空调", days=7)` —— 开了多久、开关几次，无需手算。
4. `search_events(room="客厅", category="media", summarize=true)` —— 语义过滤 + 摘要。
5. `save_analysis_template(...)` / `export_insight(...)` —— 把结论沉淀给 Node-RED。
6. `save_skill(name, content)` —— 把本次分析经验写回网关，网关会自增 `version`，
   Agent 下次通过 `get_skill` 即可拉到更新后的最新版本。

## 新增能力（取数更准、更直接）
- `get_device_health(stale_days=3)` —— 主动揪失联/没电/长期静默设备。
  返回 `no_data`（从未采到）/ `stale`（最近 N 天静默）清单。`no_data` 多半是实体 ID 错或未启用。
- `get_data_coverage(days=7)` —— 取数前先调它确认窗口完整性。返回每天 `events` 量与 `has_data` 标记、
  `first/last` 有数据日期、`missing_days` 列表，明确告诉你「这 7 天其实只有 2 天有数据」。
- `get_climate_sessions(query="主卧空调", days=7)` —— 拼出「设定温度 + 室温 + 运行时长」会话。
  室温/设定温度来自事件属性（采集时已镜像 `current_temperature` / `temperature`），解决原只有 `hvac_action`。
- `infer_activities(days=7)` —— 规则引擎识别做饭/洗澡/睡眠/看电视/离家，带置信度与证据。
  直接拿语义层结论，别再自己拼原始动线。
- `ask_memory('昨天谁在家做饭')` —— 口语问法映射到既有工具（关系库主力）；问法太模糊时
  回落向量库语义检索（副驾）。先用它，比你自己拼查询省事。
- `run_analysis_template(template_id, days=模板默认天数)` —— 用户说「用 xxx 模板分析」时直接执行模板：
  服务端按模板声明的实体条件+指标（duration/count/numeric_sum/state_share）复用既有算力，
  返回每实体累计值、分日明细、会话时间轴与解读话术 `summary_text`。Agent 只需转述结论，不要另写查询。

## 家庭成员与生活习惯档案（persona）
把从数据中看出的行为偏好沉淀到具体「人」。Agent 可自主推断标签，经用户确认后写回。

- `list_members()` / `create_member(name, avatar_emoji, avatar_bg, note)` —— 建家庭成员（导航叫「家庭成员」，详情叫「生活习惯档案」）。
- `assign_member_room(member_id, rooms=[...])` / `assign_member_device(member_id, entity_ids=[...])` —— 绑定 TA 的房间与专属设备（多成员家庭先用房间粒度区分）。
- `get_member_persona(member_id, days=14)` —— 拿该成员档案 + 全屋/房间定向行为画像 + 已存档标签；也可用全局 `get_user_persona(days=14)` 看整屋画像。
- `confirm_member_tag(member_id, tag, category, emoji, confidence, evidence)` —— **仅当用户明确确认后**把推断标签写回（同名覆盖刷新）。
- 建议工作流：先 `get_member_persona`/`get_user_persona` 拿结构化画像（出现天数/频次/典型时段/房间/置信度/证据）→ 由你（Agent）自主推断标签（如主卧 23:00–02:00 活跃高→夜猫子🦉；厨房≥5天/周→家庭主厨🍳）→ 展示证据并询问是否存档 → 确认后 `confirm_member_tag`。
- 用户也可在 WebUI「家庭成员」页手动增删成员、房间、设备与标签。

## 取数避坑
- 调 `search_events` / `get_behavior_insights` 前，先 `get_data_coverage` 确认窗口真的有数据，
  避免把「空白区间」误当「一切正常」。
- 设备没数据先 `get_device_health` 区分是「从未启用」还是「最近失联」。
- 别一上来就 `query_events`：它要精确 `entity_id`，先用 catalog 或 search_events。
- 功率/温湿度每分钟一条会把小时分布拍平。洞察工具默认 `behavior_only=true`，除非要看遥测否则别关。
- 时间统一用 `days`（最近 N 天）或 `start`/`end`（本地时区 ISO），返回带 `window.timezone`，别自换时区。

## 用模板直接出结果（run_analysis_template）
- 用户说「用 xxx 模板 / 按 xxx 模板看…」时，**直接套用模板出结果**，不要重新统计或自己拼查询。
- 调用 `run_analysis_template(template_id="<模板id>", days=<模板默认天数>)`，服务端会算好
  每实体累计值、每日明细、会话时间轴，并给出可直接转述的 `summary_text` / `entities`。
- 不确定有哪些模板？`list_analysis_templates()` 列出全部（含 `default_days` / `interpretation`）。
- 想沉淀新模板？`save_analysis_template(name, entities, metric, default_days, interpretation, ...)`
  声明 `metric`（duration/count/numeric_sum/state_share）后模板才可被直接执行。
- 路由工具 `route_question` 已能识别「用/按 xxx 模板」意图，规划里会直接推荐 `run_analysis_template`
  并带好 `template_id` 与 `days`，照计划调用即可。

## 已知数据缺口
- `person` 字段来自 HA 状态历史，通常为空——原始事件**不支持自动人员归属**；但可通过「家庭成员」体系用 `assign_member_room` 绑定房间、`get_member_persona` 收敛到具体成员来分析，或让 Agent 推断标签后 `confirm_member_tag` 写回。
- 媒体/语音类实体若无事件，多半是采集未启用该实体；`media_player` 的节目/App/频道本就不上报。
- 睡眠数据依赖卧室占用传感器事件，若该实体无事件则睡眠识别会缺席。
- 部分 8 位计数器传感器（如「无人持续时长」）可能在 255 处回绕，属数据源编码限制，非网关错误。

## 把经验写回网关（维护技能）
调用 `save_skill(name="<唯一标识>", content="<markdown 正文>")` 会把内容写入网关的
`<skills_dir>/<name>/SKILL.md` 并将 `version` 自增 1、刷新 `updated_at`。
- `name` 只能含字母、数字、`_`、`-`，作为目录名。
- `content` 为完整 markdown；若不带 `---` frontmatter，会自动补 `name`/`description`/`category`。
- 读取网关技能用 `list_skills()` / `get_skill(name=...)`。
