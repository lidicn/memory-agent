# ADM 联动执行计划 · MA（memory-agent）

> 出品：关键决策部（DCD）
> 版本：vMA-1.3 联动落地版（基于 v4.2 路线图 + 全部裁定链）
> 前提：homesdk **0.3.1** 已投递 NAS（含 `time` 模块，sha256 已登记）
> 落点：`E:\NAS\memory-agent\doc\路线图与规划\`
> 依赖裁定：`20260929-ADM三仓联动七问`、`20260929-ADM协议选型MCP与ACP`、`20260929-联动协议修订-事件流与收件箱`、`20261001-MA-service_token与设备事件feed`、`20261001-AF-homesdk接入四问` §1/§2

---

## 一、MA 的角色

MA 是**记忆中枢（数据权威）**：全生态的记忆、成员、行为事实唯一真源。最成熟的一仓，是当前联动的可靠锚点。联动里 MA 负责：
- 发布 `ma/insights` 事件（事实广播，不 retained）；
- 投递 `butler/inbox/*`（请 DB 说话）；
- 发布 presence（`adm/memory-agent/status`+`caps`）；
- 通过 MCP 提供业务查询工具（DB 消费）。

---

## 二、任务卡（按依赖序）

### 第 0 步：homesdk 0.3.1 接入（先决）

| 子任务 | 验收 |
|--------|------|
| ① MA 侧现用 homesdk 0.1.x（仅 consent）——升级到 0.3.1 | `import homesdk; homesdk.__version__` == `0.3.1` |
| ② **时区接入 `homesdk.time`**（裁定 §五 + AF 四问 §2）：MA 现有 `insights/models.py` 的 `HOUSE_TZ`/`house_ts`/`house_now`/`house_dt` **退化为 fallback**，主路径改调 `homesdk.time.house_tz()/house_now()/to_house_iso()` | 键名 `HOMESDK_TZ` 优先；`TZ_OFFSET_HOURS` 作过渡别名；`tests/test_insights_house_timezone.py` 现有断言仍绿 |
| ③ 把 MA `store.now_local(tz_offset_hours)` 的对齐点收敛到 homesdk.time | 全仓 `+8` 硬编码 grep → 只剩 fallback 常量 |

> **落地状态（2026-10-02）**：②③ 完成（`tests/test_insights_house_timezone.py` 绿，
> `ma/*` 载荷 `ts` 走家庭墙钟，生产实测 `ts="2026-10-02T13:51:10"` 而 UTC 是 05:51）。
> ① **只在测试面成立**：容器 ENV `PYTHONPATH=/app/src`，服务进程 `import homesdk` 直接
> `ModuleNotFoundError`——`/tmp/pylibs` 那份从来不在运行面上（不是"重烤才丢"）。
> 交付形态 = DCD Q1 未裁，MA 已按双路径实现（库在场/不在场线上同构），见审计 §十四 更正四。

### 第 1 步：越权清理 + presence（裁定 §六.3 双通道）

| 子任务 | 验收 |
|--------|------|
| ① `butler/trigger/gu_anheng_alert` 直推 → 改**双通道**：告警改投 `butler/inbox/notify`（请 DB 说话）+ 洞察事件发 `ma/insights`（不 retained） | 全仓 grep `butler/trigger/` → 0 |
| ② 发布 presence：`advertise(client, "memory-agent", caps={...})`（homesdk.presence 现成） | retained `adm/memory-agent/status`+`caps` 可见 |
| ③ LWT 保离线 | kill -9 后 broker 自动发 `offline` |

> **落地状态（2026-10-02，HEAD `34b9aa9`）**：三条验收全过。① `src/` 全仓 grep
> `butler/trigger/` = 0，告警改双通道（`ma/insights` + `butler/inbox/notify`，共用一枚
> `trace_id`）；② retained `adm/memory-agent/status=online` + `caps`（`keys=['mcp','tools','version']`，
> 53 个工具，QoS1 retained）实测可见；③ `docker kill`（`Exited (137)`，SIGKILL 不走优雅关停）
> 后 broker 代发 retained `offline`，MA 重启后边沿重发 `online`。
> 读数与坑见 `doc/审计报告/审计核实与修复_20261001.md` §十四；
> 载荷**键名**与契约表 :46-49 不一致属跨仓破坏面，已提
> `20261002-MA-ma载荷键名对齐与收件箱凭证与ts口径-决策申请`（七问）等裁。

### 第 2 步：service_token 合并（MA 先做，裁定 Q1=A）

| 子任务 | 验收 |
|--------|------|
| ① `ServiceTokenStore`（以 `AppTokenStore` 为基座：哈希+前缀+name+source+paths/methods+`kind="service"`） | 新令牌可签发、可吊销、有使用计数 |
| ② 启动迁移：`BUTLER_TOKEN`/`APP_TOKEN` 遗留值自动生成 service 记录（仿 `mcp_tokens.py:58` migrate_legacy） | 旧令牌到期日前仍可用 |
| ③ 白名单粒度按令牌自带清单（裁定 Q2=A，根治 F-2：app_token 不分方法放行 `/api/agent/memories` 写入） | 越界方法 403 |
| ④ 一实例一令牌（Q3=A）；本次只做"可吊销+使用计数"不做 TTL（Q4=A）；`source` 派生保留（Q5） | 回归锁："旧令牌在到期日前必须仍可用" |
| ⑤ **不设 expiry 不写死**——到期日由 SP 定 | 代码里无硬编码到期日 |

> **落地状态（2026-10-03）**：①③④⑤ 成立。`src/memory_agent/service_tokens.py`
> （`ServiceTokenStore`：`generate/revoke/list_tokens/count/verify` + 使用计数写盘节流 15s +
> `kind="service"` 族别位）；③ 由 `scope_matches()` 逐条比对令牌自带的「方法:路径」清单，
> **清单为空一律拒绝**（fail-closed），`app.py:202` 中间件对不在清单内的请求 403；
> ④ 一实例一令牌，只"可吊销 + 使用计数"不做 TTL（Q4=A），`source` 在白名单校验后才采信
> （Q5，签发侧与读侧都要拦）；⑤ 由 `test_issued_record_has_expiry_field_nowhere` 钉住——
> 记录里根本没有 expiry 字段，到期日不进代码。回归锁共 20 条（`tests/test_service_tokens.py`）。
>
> ② **按实现口径偏离计划卡文字**：不是"启动迁移自动生成记录"，而是**只读导入**
> （`legacy_records()`）——把 env 单密钥登记成一条可审计记录，带该通道现有授权面与进程内使用
> 计数，但**不落盘、也不能在这里吊销**（`imported_from="env"`、`persisted=False`）。原因写在
> 模块 docstring：迁移若写 `config.json`，等于在鉴权热路径上改写生产配置（容器回归也会触发），
> 代价大于收益。所以下线旧凭据 = **清 env + 重启**，那是 SP 定的到期动作，不是本服务的能力。
> 遗留通道仍保留各自白名单（`_butler_allowed` 硬编码方法/路径；`_app_allowed` 有 scopes 时用
> scopes、无则回退 `APP_ENDPOINTS`，是叠加不是替换），"统一到一个 store"目前只到鉴权面收敛，
> 没有把旧通道的分支删掉。
>
> 生产读数（只读探针，RC=0）：`config.service_tokens` **0 条**（尚未对任何外部实例签发），
> `count()=1` 全来自 env 导入的 butler 记录（13 条作用域、`persisted=False`、`use_count=0`），
> `BUTLER_TOKEN` 已配置 / `APP_TOKEN` **未配置**（所以 app-env 记录本就不存在），
> `agent_memory_sources=['ma','butler','vision','manual']`，到期字段在场 = False。
> 即：**计划卡 ② 的"旧令牌仍可用"在现实里只有一条腿（butler）成立**，DB/AF 真要接入仍需逐实例
> 签发；30 天双轨的到期日仍未定（任务 #9 in_progress，SP 侧动作）。

### 第 3 步：设备事件进引擎 feed（裁定：B 独立批量扫描器）

| 子任务 | 验收 |
|--------|------|
| ① 独立扫描器按小时轮询 `events` 表，同 entity/时间窗聚成"序列"再评估 | 对在线服务零压力；可干跑对照 |
| ② **降噪门槛三项**：domain 白名单（排除 91% 的 `sensor`）/ 数值型不进 feed / count 窗口 60s+3 次 | 实际进入 feed ≈2k/天（9% 有效流量） |
| ③ `_match_atom` 加实体/状态迁移语义（新条件词汇表） | 新词汇表有单测 |
| ④ `rule_trigger_history` 保留期 7 天 + 行数上限 10 万 | 超限自动裁剪 |

> **落地状态（2026-10-02）**：①② 已上线（`device_feed` 批量扫描器，三项降噪门槛按裁定值写死；
> 总开关默认关，开启后首轮亦 dry_run）。③ 实体半边（`domain/entity_id/tag/state`）此前已落地但
> **零直接测试**（全仓 grep `_match_atom` 命中 0 条），`old_state` 迁移语义本次补齐：
> `{"state":"on","old_state":"off"}` 读作"关→开"。**不能把事件字段直接丢给 `state_matches`**——
> 它把空值判成 off（空不是 on 就算 off），于是"没有迁移信息"会伪装成"从 off 变来"，
> 原子自己拦缺失/空串。单测 `tests/test_rule_atom_vocabulary.py`（25 条，含 feed 生产者↔引擎
> 消费者接缝、`time_range` 按事件墙钟不按"现在"）。④ `Store.purge_rule_triggers` 此前只被包装层
> 间接测（喂假 store，只证明上限值传下去了），真 SQL 直测补在 `tests/test_rule_trigger_retention.py`
> （保留期 / 行数上限 / **误报行豁免且不占额度** / 空表读数）。跨事件"先 A 后 B"仍表达不了，
> `build_condition` 如实记 note，不自造迁移方向。
>
> **补充（2026-10-03，`a192c51`）**：② 的 count 门槛此前只有**入场**门槛、没有**吵人**上限——
> `ActiveRuleEngine` 完全没实现 `cooldown_seconds`（`rule_trigger_history` 只记账不判窗），
> 晋升出的规则在 feed 里每小时都能重复触发。本次补齐冷却：判定按**家庭墙钟**（与 count 窗口同口径，
> 不用 monotonic，否则回放和跨进程重启都会错），实时路径以"现在"为基准并从
> `MAX(triggered_at)` 续窗，批量回放以**事件自身墙钟**为基准（否则一轮历史事件全被判成同一瞬间）；
> 记账在派发**之前**；半开区间（`>=` 放行）。坏值（空/非数字/负数）一律 fail-open 视为无冷却。
> 13 条回归锁在 `tests/test_rule_cooldown.py`。⚠️ 三件未裁事项已提
> `20261003-MA规则冷却默认值与晋升规则吵人上限-决策申请`（Q1 默认 300s 是否该由晋升方显式传、
> Q2 count×cooldown 叠加后单条设备规则的"吵人上限"≈12 条/小时是否可接受、Q3 dry_run 该不该消耗
> 冷却窗口——影响 R3 观察期的计数分母），MA 未自主改默认值。
>
> **补充（2026-10-04，`e3901f9`）：② 的验收数值第一次被量出来，量到的不是裁定写的那个数。**
> 探针入库在 `scripts/probe_device_feed_dryrun.py`（在生产库的**快照副本**上跑，生产连接 `mode=ro`；
> 判据全部取自 `device_feed` 自身实现），按小时切窗跑 7 个自然日 × 24 窗 = 168 轮
> `run_once(dry_run=True)`：**`truncated=0`、`errors=0`、`matched=0`**（`active_rules=0`，通道结构性停在
> "等人晋升"，与 Q3=(i) 一致）。三道门槛的实测通过率：
>
> | 自然日 | events 总数 | `sensor` 占比 | 过白名单 | 过二元翻转 | 进 feed |
> |---|---|---|---|---|---|
> | 09-25 | 33,061 | 94.3% | 1,103 | 939 | 2.84% |
> | 09-26 | 33,795 | 93.2% | 1,069 | 1,017 | 3.01% |
> | 09-29 | 10,828 | 87.3% | 818 | 714 | 6.59%（当天只覆盖 10 个小时） |
> | 09-30 | 33,676 | 91.6% | 1,675 | 1,509 | 4.48% |
> | 10-01 | 29,783 | 95.4% | 847 | 754 | 2.53% |
> | 10-02 | 21,762 | 97.2% | 163 | 135 | 0.62% |
> | 10-03 | 22,234 | 97.4% | 133 | 109 | 0.49% |
>
> 即**日均 739.6 条**（区间 109–1,509），不是裁定写的「≈2k/天（9% 有效流量）」。
> 「sensor 占 ≈91%」这步对得上（实测 87.3%–97.4%），差在剩下那 9% 并不是都归 feed：`event`
> 独占大头（09-26 单日 1,224 条、10-01 523、10-03 441）而它**不在白名单内**——9% 是"非 sensor 占比"，
> 不等于 feed 通过率（实测 0.49%–6.6%，差一个数量级）。**这条要改的是预估，不是门槛。**
> 「过白名单」那一列另用只读生产库上的独立 SQL 对过账（不走探针代码路径）：按天 `GROUP BY domain`
> 相加六个白名单域，09-26=1,069、10-01=847、10-03=133，与探针读数分毫不差。
> count 门槛（60 秒 3 次）的真实可达性：7 天里进过 feed 的实体 **141 个**，够得着的只有 **15 个**
> （最高 10 次/60 秒，全是门磁/占用类；其余 126 个 7 天内最多 2 次/60 秒，按现门槛永不满足）
> ⇒ 单条设备类规则的约束力在 count 那一侧，冷却叠加出的"12 条/小时"是够不着的天花板。
> 顺带查出并修掉一处会让 count 计数虚增的缺陷：**相邻窗口在边界那一秒重叠**——
> `query_events` 的 `ts BETWEEN ? AND ?` 两端闭区间，而 `run_once` 把水位停在窗口终点，
> 下一轮起点与上一轮终点重合，正好落在该秒的事件被喂两遍（本模块 docstring 自己写过这个风险，
> 代码没跟上；原水位用例只放过窗口正中事件，对这个形状完全看不见）。现在推到「读完的下一秒」
> （ts 是秒粒度：生产 1,028,140 行全部 `len(ts)=19`、无小数秒，只读实测），并修掉截断即跳尾：
> 读满 `QUERY_LIMIT` 时停在「最后读到的那条 +1 秒」，`stats` 暴露 `pending_tail` / `watermark`。
> 锁 3 条 + 变异两发各自判红（退回 `end_iso` → 3 红；去掉截断续读 → 只 1 红），
> 留证见 `doc/审计报告/审计核实与修复_20261001.md` §二十二。
> ⚠️ 采集连续性另记一笔（影响 R3 观察期的天数分母，不在本版静默修）：
> 09-27 的 `MAX(ts)=18:55:42`、09-28 全天只有 2 条、09-29 到 07:12 才有数据。

### 第 4 步：MCP 业务查询工具面（供 DB 消费）

| 子任务 | 验收 |
|--------|------|
| ① 现有 `ask_memory` / `get_member_persona` / `analyze_behavior_change` 已生产级——**确认工具签名稳定**（DB 要消费） | 契约测试断言签名 |
| ② 补"空调时长""净水器出水量"类聚合查询（复用既有模板引擎 `run_analysis_template`） | DB 侧能一次调用拿到 |

> **落地状态（2026-10-02）**：① `get_member_persona` / `analyze_behavior_change` 此前
> **有实现无 ToolSpec**，`caps` 少报——已登记（含 fail-closed pitfall），`TOOL_NAMES` 53→**55**；
> 生产 broker retained `adm/memory-agent/caps` 实测 `tools=55`，六件 DB 面工具
> （ask_memory / get_member_persona / analyze_behavior_change / run_analysis_template /
> list_analysis_templates / query_unified_events）全在列。
> ② 新增内置模板 `ac_runtime_daily`（4 台 climate，一次调用拿逐台累计 + 分日明细，
> `water_purifier_daily` 为既有）；climate 域判据取 `value=""` 走 `OFF_STATES` 反判
> （写死 `value="cool"` 会漏计制热/自动模式），`time_range=""` 不裁窗（写 `"00:00-23:59"`
> 每天少一分钟）。
> **实测跨仓缺陷（已修）**：DB 传的 `limit` 被 MA 静默忽略——`retrieve_agent_memories`
> 只读 `top_k`，两边数值恰好都是 5 所以一直没暴露；现在 `limit` 非 0 时覆盖 `top_k`。
> **登记（不在本版静默修）**：MCP 面上 90 个工具里 **35 个无 ToolSpec**
> （`list_members` / `create_member` / `query_device_usage` / `confirm_candidate_rule` /
> `generate_self_diary` 等，判据 = `list_tools()` 实际面 − `SPEC_BY_NAME` 里 `expose∋mcp` 的集），
> `caps` 因此系统性少报：探测方照 caps 建集成会打 404。补齐要逐条定 group/scope/示例文案，
> 是独立批次，登记而非静默吞。
>
> **落地状态（2026-10-03，登记项已清）**：那 35 条已逐条补 `ToolSpec`（全 `service="static"`、
> `generated=False`，不伪造派发路径），`caps` 从 55 少报变 **90/90 对齐**——broker retained
> `adm/memory-agent/caps` 实测 `tools_len=90`、`version=1.2.3`。补登记时另测出两件更硬的：
> ① 自我日记三件套只在 wire 上、没进 `mcp_scopes` 准入表，`list_tools()` 看得见但一发调用必吃
> `NOT_FOUND`（`scope_of('read_self_diary')=='unknown'`），现 read=只读、write/generate=写工具；
> ② 超限响应走字符截断会把 JSON 切在半行（`list_device_health` 生产约 650KB > 512KB，调用侧
> `json.loads` 报 `Expecting ',' delimiter`），现改**结构降级**（裁记录条数 + `_truncated` 交代丢了什么，
> 非 JSON/无列表才退回字符截断）。顺带修 `assign_member_device` 对不存在成员回 `ok=True` 留孤儿行、
> `query_unified_events` 目录里的假参数 `order`、`teach_signal` 缺的真参数 `dry_run`。
> **三面相等不再是靠人记**：`tests/test_mcp_surface_parity.py` 13 条锁用 AST 从源码读 wire 面，
> 逐条比对目录表与准入表（含手写/generated 交叉、同名重复定义、参数含顺序对齐、双向幽灵名），
> 判据跟着实现走而不是跟着计数常量走。全链留证见审计 §十八。
> **仍交 DCD**：`list_device_health` 要不要分页/精简默认投影、超限时裁行还是整条改计数摘要
> （`inbox/20261003-MA超限响应投影与设备健康分页-决策申请.md`）——三条都改 DB 经 MCP 看得见的返回。

### 第 5 步：契约测试（与 DB/AF 同步）

| 子任务 | 验收 |
|--------|------|
| ① MA 侧 `tests/contract/test_ma_db_contract.py`（断言 MCP 工具签名 + 响应 schema + member_id fail-closed） | CI 跑过 |
| ② 部署前必跑 | — |

> **落地状态（2026-10-02）**：`tests/contract/test_ma_db_contract.py` 落地。事实源是 **DB 的真实
> 调用点**（`doubao-butler/butler/integrations/memory_agent.py` 逐条照抄工具名 + 参数键，带 file:line），
> 不是 MA 自己想象的接口。四组断言：签名（DB 发的键必须是 MA 声明过的参数）、响应 schema
> （DB 的 `_extract_list` 取得到列表，取不到会静默变空召回）、member_id fail-closed
> （缺 member_id 且非 admin → `code 403` + 空列表；`member:ghost` → NOT_FOUND 且不返回
> persona/labels/room_insights）、收件箱主题白名单只有 `butler/inbox/{speak,notify,tv}`。
> 跑法：容器权威 **42 passed**；本地无 mcp 时自动降级（不阻塞开发）。
> ② 「部署前必跑」写进了文件 docstring 第一屏，并落成 `scripts/deploy_nas.sh` 的**重启前门禁**：
> 契约测试红 → `exit 1`，不执行 `docker restart`（prod 继续跑旧版本，比"新码上线但 DB 调用面
> 断了"好）。`tests/` 没挂进容器（只挂 `src/data/.env`），门禁在 NAS 侧 `docker cp` 一份 tests
> 进 `/tmp/ma_gate` 跑，被跑的 `src` 就是刚同步过去的那份。逃生口 `MA_SKIP_CONTRACT=1`
> 仅限回滚窗口，用了要补跑并在交付记录里写明。

### 第 6 步：vMA-1.3 VIEW（已过 PoC 门，放行）

| 子任务 | 验收 |
|--------|------|
| ① `CREATE VIEW unified_events`（UNION ALL 三张表）+ `query_unified_events` MCP 只读工具 | PoC 已验证（107 万行，1.09s） |
| ② 只读、不动写入路径；`event_type` 映射写进 doc | 行数契约测试绿 |
| ③ 正式立项条件：≥2 个消费方接入且验证可靠 | 现在只做 MCP 工具（第一档） |

> **落地状态（2026-10-02）**：①② 生产复核——`unified_events` 视图在场，实测 **1,030,381 行**；
> `query_unified_events` 在 MCP 面与 caps 上（read scope，不在 `WRITE_TOOLS`）；十字列契约、
> 三源手工 UNION ALL 等价、视图漂移自愈都在 `tests/test_vma13_unified_events_contract.py`。
> `event_type` 映射确有文档，但 **v1.0 那份把载荷列写成 `raw_json`——视图从未有过这一列**
> （DDL 是 `AS payload`）：按旧文写 SQL 第一步就 `no such column: raw_json`。已就地更正，
> 并注明列名以 `unified_events视图字段语义_20261001.md` + 那条十字列契约为准。
> ③ 第二消费方未接入，维持"只做 MCP 工具"第一档。
> **实测缺陷（本次修）**：`query_unified_events` 的默认窗口用 `datetime.now(timezone.utc)` 取 end，
> 而三张源表的 `ts/server_ts` 由 Store 按 +8 墙钟写入——生产实测"最近 7 天"少掉最近 8 小时的
> **8105 条设备事件**，调用方只看到"这段时间没数据"，看不出少的正是最新一段。与 BUG-TZ1 同源，
> 改判 `now_local(rt.config.tz_offset_hours)`（判据同 `rule_engine._now`）；反例验证：把实现退回
> UTC 口径，新加的墙钟锁当场 `total=0` 变红。

---

## 三、停机窗口（合并，不单独开）

MA 侧三件事**必须同一个 restart 窗**（代码已部署不重启不生效）：
1. **R2 PII 存量回填**（已批准，三件硬要求：先带时间戳备份 → 停机窗口 → 逐条验证脱敏）；
2. **R1 反馈入口问题文本**（vMA-2.0 门 A 裁定的前提）；
3. **R3 候选规则生效通道**（已建，需重启生效）。

与 DB 写面修复窗、AF 镜像重烤窗**合批为一个窗口**。

> **窗口读数（2026-10-03 04:39Z 重启后只读探针，RC=0）**：②③ **本次窗已生效**——
> `agent_memories.feedback_question/feedback_comment` 两列在场、`rule_lifecycle_audit` 表在场、
> `active_rules` 18 列（含 `promoted_at`）；采集链路在跑（`events` `MAX(ts)` = 家庭墙钟当前分钟）、
> `integrity_check=ok`、`mirror_dirty=0`。
> ① **仍未做，且缺的正是工具那半边**：回填脚本已落地（`scripts/pii_backfill_r2.py` +
> 7 条回归锁 `tests/test_vma_r2_pii_backfill.py`，三条硬要求逐条对锁，变异三发各自判红），
> 但**默认只读**：不带 `--window-ok` 直接拒绝写（窗口归 SP 排，脚本不替人判"现在是窗口"）。
> 生产现况 `agent_memories=247 行`，其中含明文姓名的行数由脚本 dry-run 现算，不写死进文档。
> **窗前点数已按这条真跑过一遍**（2026-10-05 03:33+08，只读，`R2_DRYRUN_RC=0` + 逐项点数 `READ_RC=0`，
> 脚本默认不带 `--apply` ⇒ 输出末行自证「一条都没改」）：
> `agent_memories=251 行`、**命中明文姓名 98 行**（`staging` 97 + `revoked` 1、**`live` 0**），
> 生产 sanitizer 对这 98 行**全部有变化**（"命中但不改"= 0 行），命中行文本合计 1388 字符（中位长度 14），
> 全表状态分布 `live 11 / revoked 47 / staging 193`，`mirror_dirty=1` 行数 **0**。
> 两条对窗口有用的结论：① 明文存量**不在 `live`**（写入侧脱敏挡住了新行），回填是清旧账不是救火；
> ② `--reconcile` 的量 = 98 行重刷，不是"全量重建 ≈12 分钟"那条最贵路径——与上面 ⚠️ 的预判一致，
> 但**窗内仍以真跑读数为准**（数字会随采集增长，本段只是把"没量过"变成"量过、且有日期"）。
> ⚠️ 一处**与裁定预估不同**的要紧事：R2 注的"回填触发 Chroma 全量重建（≈12 分钟不可用）"
> 走的是最贵路径。脚本写回时置 `mirror_dirty=1`，而 `AgentMemoryService.reconcile()`
> （`agent_memory.py:689`）是**按脏行逐条重刷**（脏行清单来自 `store.list_dirty_agent_mirrors()`，
> `store.py:4666`）——所以真窗口里大概率不需要 12 分钟冷启动，只需 `--reconcile`。这条差异属对外承诺的时长，**留给窗口执行时实测**，不在此处替它圆场。
> 探针脚本 `scripts/_probe_post_restart.py`（同一份判据，下次重启后直接复用）。
>
> **窗内必须一起落的条目**（第 1-3 件按 DCD 20261002《AgentOps 与 MA 交付面》§问 1+2 / 问 3 已裁，
> **不允许提前落码**；第 4 件 2026-10-04 投递、**待裁**，取哪个值由 DCD 定向）：
>
> | # | 改动 | 现读位置 | 裁定口径 | 为什么不能提前 |
> |---|---|---|---|---|
> | 1 | 安装行加 `"posthog<3"` | `Dockerfile:19`（`pip install … "chromadb==0.5.23" httpx …`） | 裁 **A**：pin 上界 | 裁定书原话：「pin 上界与关遥测是同一枚硬币的两面，**必须同窗做**」，无裁定单独加 = 不允许 |
> | 2 | 加 `ANONYMIZED_TELEMETRY=False` | `docker-compose.yml:11`（`environment:` 块）＋同一枚硬币 | 裁 **A**：显式关遥测 | 「用一个 bug 兜住一条隐私红线」是假安全：今天没发出去只因 `capture()` 签名对不上 |
> | 3 | 删两条 `--ignore` + 去 `-x` | `.github/workflows/ci.yml:38` | **C 打底 + B 下个交付窗** | CI 面属交付面；C 已打底（容器权威全量本来就跑这两文件），B 只能在窗内。缩水面 HEAD 现读 **16 条**（`test_mqtt_bridge.py` 12 + `test_ha_assist.py` 4），与裁定书 §问 3「CI 长期比实际跑的少 16 条」一致 |
> | 4 | 向量面 MiniLM 退路：可写缓存 or 删掉这行 + 摘 `|| true` | `Dockerfile:27`（预热行）＋ `docker-compose.yml:11`（`environment:`）| **待裁**：2026-10-04 投递 `20261004-MA-向量面MiniLM退路的HOME可写性与Dockerfile预热无效-决策申请.md`（Q1 要不要退路 / Q2 两条互斥落点 / Q3 `|| true` 摘不摘） | 实测是**双重无效**：运行面 `HOME=[/]` ⇒ `/.cache` `PermissionError`；且 root 预热本身没成功（`onnx.tar.gz` 只有 7,432,192 字节、`tar tzf` 报 `Unexpected EOF`、sha256 `f4f66635…` ≠ 期望 `913d7300…`，无解包产物），行尾 `|| true` 把它吞成构建成功已 **15 天**（Sep 19→Oct 4）。⚠️ 决定性论据：MiniLM 384 维 vs 现存两集合 1024 维 ⇒ 修好退路对现有数据仍 `InvalidDimensionException`。与 homesdk 进运行面**同一次重烤**，不许烤两遍 |
>
> 四件的**验收读数**（窗后跑，别拿落码当生效；第 4 件按其取值二分）：① 容器内 `python -c "import posthog,httpx,chromadb"` 版本
> 一行现读。**窗前基线已取（2026-10-04，RC=0）：`httpx 0.28.1 / posthog 7.58.0 / chromadb 0.5.23`**；
> 窗后 `httpx` 读数须与此一致，不一致就是重解析动了传递依赖，要单独报出来（镜像重解析会顺带动 `httpx`，
> 第七轮的 HA 连接复用压在上面——裁定书 §副作用预警明写「重建后跑容器权威全量，确认 `httpx` 版本读数与
> 连接池行为不变」）；重烤后 `posthog` 应 <3（当前 7.58.0 是未重烤的证据，不是缺陷）；
> ② 启动日志里 `Failed to send telemetry event ClientStartEvent` **不再出现**（这条是遥测真关掉的自己的状态字，
> 不是"看起来没报错"）；③ 窗后立刻重跑容器全量并记 `passed/skipped` 与 `SUITE_RC`；
> ④ 第 4 件按取值各有一个自己的状态字：取 **A（要退路）** ⇒ 容器内 `echo $HOME` 不再是 `/`，
> 且 `sha256sum …/onnx.tar.gz` 与 `_MODEL_SHA256`（`913d7300…`）**一致**、`onnx/model.onnx` 真在场
> （本轮实测三样全不成立：`HOME=[/]`、`f4f66635…`、解包产物缺失）；取 **B（不要退路）** ⇒
> `Dockerfile` 里那行预热与 `|| true` 一起消失，且 `resolve_embedding_function` 未配置端点时打的
> 是「向量面不可用」而不是「使用本地 MiniLM」（`history.py:115` 在**构造期**就打印这句话，
> 而缓存要到**首次调用**才撞 `/.cache` ⇒ 现网这条日志是承诺兑现不了的）。两种取值都**不能只删 `|| true` 不动其余**——
> 那只会把一个静默失效换成构建期随机红。
> 另两件同窗的执行物已在仓里，不需再准备：`vendor/homesdk-0.3.1-py3-none-any.whl` + `Dockerfile:24`
> （运行面 `import homesdk` 现读仍是 `ModuleNotFoundError`，RC=1 ⇒ 缺的只有重烤），
> 以及 R2 回填 `scripts/pii_backfill_r2.py`（窗口门是硬门，不带 `--window-ok` → `RC=2` 且不产生备份）。
>
> **窗外观测（2026-10-04 05:59–06:06Z，不是窗口，只是代码上线）**：`bash scripts/deploy_nas.sh --full`
> 把 `d598dfd`（向量面）连同此前欠发的 `2a418a4`（`_sanitize_pii` 零残留）一起送进生产挂载目录并重启——
> `DEPLOY_RC=0`，重启前契约门禁 `42 passed in 67.03s`，重启后 `HEALTH 200 {"ok":true,…}`，
> `/app/src` 与 HEAD 的 158 个 src 文件**逐个 blob 哈希全等**（`ONLY_IN_HEAD=0`）。
> 生效凭据（不是落码凭据）：探针 `scripts/probe_conflict_scan_error_source.py` part ③ 对 `/app` 复跑
> `解析到的嵌入函数: _OpenAICompatEmbeddingFunction / query_texts OK ids=3`，与快照读数逐字一致；
> `scripts/prod_sanitize_zero_residue_check.py` 对 `/app/src` 现读 `PROD_SANITIZE_ZERO_RESIDUE=PASS`（`SAN_RC=0`）。
> ⚠️ 同一份探针在 prod 现读仍打 `Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given`
> ⇒ 上表第 2 件的**状态字仍未消**，它等的还是**重烤**，代码侧无欠账。逐条留证见 `审计核实与修复_20261001.md` §二十四「生产生效复跑」。

---

## 四、不在本版做（已裁并登记）

- vMA-2.0 知识图谱（门照旧，等真实 badcase ≥10 条）
- 候选规则观察期自动判据（自造真值，DCD 已否）
- feed 自造真值（同上）

---

## 五、进度快照与下一版细化（DCD 2026-10-04 增补）

### 5.1 当前真实进度（实测）

| 版本 | 完成度 |
|------|--------|
| vMA-1.1.1 工程卫生 | ✅ |
| vMA-1.2.0 场景图 | ✅（十项全闭） |
| vMA-1.2.1 持续学习 | ✅（5.1-5.3 全闭） |
| vMA-1.2.2 召回加固 | ✅ |
| vMA-1.3 多模态 VIEW | ✅（PoC+视图层；第二消费方未接入维持第一档） |

**联动计划**：第 1-6 步基本完成；**第 0 步卡壳**——homesdk 只在测试面接上，生产容器 `import homesdk` 报 ModuleNotFoundError。

### 5.2 唯一阻塞：合并停机窗（三件事同窗）

1. **R2 PII 存量回填**（已批准）：先带时间戳备份 → 停机窗执行 → 逐条验证脱敏；
2. **R1 反馈入口问题文本** + **R3 候选规则生效通道**：代码已部署，重启生效；
3. **service_token 启用**：MA 侧已交付（20 锁），生产未签发——重启窗内签发。

**与 DB 写面修复窗、AF 镜像重烤窗合批为一次窗口。**

### 5.3 下一版 vMA-1.4 细化任务

| # | 任务 | 验收 | 前置 |
|---|------|------|------|
| 1 | homesdk 0.3.1 生产接入（vendored wheel + Dockerfile 一行） | 容器内 `import homesdk` 通 | 合并窗 |
| 2 | R2 PII 回填执行 | 245 条无明文姓名 + 备份在 | 合并窗（先备份） |
| 3 | service_token DB 侧协同（3.3.4） | DB 切换后旧 token 到期日由 SP 定 | DB v2.6 |
| 4 | 规则冷却三问 / FTS 短词召回下限 / `list_device_health` 分页 | 冷却与分页**已按裁定落码**（§六 裁1、裁4）；**FTS 短词那件不在 20261004 裁定书里 ⇒ 仍未裁、未动** | 投 inbox（已投） |
| 5 | 行为推断默认规则（behavior_states 结构性 0 产出） | 待 DCD 裁定 | 投 inbox |
| 6 | **pm4py 许可硬门（DCD 20261006 §四.3 甲）**：extras **保留** pm4py，本版不因许可删功能 | **硬门**：一旦进入"商用 / 对外分发"，发布前必须先过法务确认（AGPLv3 传染面）；未过法务 ⇒ 不得对外分发。乙（从 extras 删除）列为"商用确定时"的默认动作 | 发版前（商用决定落地即触发） |

### 5.4 别再重投（已裁）

- homesdk 交付形态 Q1：已裁 **A vendored wheel**（`20261002-AgentOps与MA交付面-裁定.md` §二 Q1）；
- ma 载荷键名七问：已裁（`20261002-AF锁文件源与MA载荷键名-裁定.md` §二）；
- 时区上收 homesdk：已裁（`20261001-AF-homesdk接入四问-裁定.md` 问题 2），且 **homesdk.time 已随 0.3.1 投递 NAS**。

### 5.5 实测修正

设备事件 feed 实测通过率 **日均 740 条（0.49-6.6%）**，与裁定预估 ≈2k/天（9%）差一个数量级——**预估要改、门槛不改**（降噪门槛仍按 domain 白名单，只是实际量比预估小）。这是健康的：预估偏保守。

> **MA 备注（2026-10-04，按实测校正本节，不静默接受）**：
> 1. **5.3-1 的准备半边已经在仓里**：`vendor/homesdk-0.3.1-py3-none-any.whl` + `Dockerfile:24`
>    （`RUN pip install --no-cache-dir ./vendor/…whl`）随 20261002 Q1=A 那次就落了。
>    刚复跑只读确认**运行中的容器仍 `ModuleNotFoundError: No module named 'homesdk'`（RC=1，Python 3.11.16）**——
>    缺的只有镜像重烤，不是代码。所以 5.3-1 的真实前置是"重烤"，MA 侧无可再推进项。
> 2. **5.2 的 ②③ 已生效，不必再等窗**：2026-10-03 04:39Z 那次重启窗只读实测
>    `agent_memories.feedback_question/feedback_comment` 两列在场、`rule_lifecycle_audit` 表在场、
>    `active_rules` 18 列含 `promoted_at`（见本卡 §三 窗口读数）。同窗还带过了 `trigger_json` 列（本轮复测在场）。
>    ⇒ 合并窗真正等的是三件：**R2 存量回填 + service_token 签发 + 镜像重烤（homesdk 进运行面）**。
>    2026-10-04 追加：重烤那**一件现在装两件事**——homesdk 进运行面 ＋ §三 窗口表第 4 件
>    （MiniLM 退路按 Q1 取值：补齐可写缓存，或把 `Dockerfile:27` 整行删掉并摘 `|| true`）。
>    **同一次烤，不许烤两遍**（烤一次要重跑容器权威全量 + `httpx` 版本对账，见 §三 验收读数 ①）。
> 3. **5.3-2 的「245 条」不写死**：含明文姓名的条数由 `scripts/pii_backfill_r2.py` 默认只读模式现算
>    （2026-10-03 读到的表行数是 247；两者口径不同，执行时以脚本 dry-run 读数为准）。
> 4. **5.3-4/5 的四件早已正式投递**（2026-10-03 同一天，均在本仓 inbox）：
>    `20261003-MA规则冷却默认值与晋升规则吵人上限-决策申请`、
>    `20261003-MA行为推断默认规则与本居词汇表-决策申请`、
>    `20261003-MA关键词检索短词与整句phrase召回下限-决策申请`、
>    `20261003-MA超限响应投影与设备健康分页-决策申请`。
>    按裁定书 §五「先回读裁定书再投新单」的要求，MA 复核结论是：**这四件不在"未投"清单上，缺的是裁定**，
>    已另发回执逐件登记文件名与投递日，请 DCD 直接从 inbox 裁。
> 5. **5.5 的读数精确化**：日均 **739.6** 条（不是 ≈740 的推演），且"过白名单"一列有独立 SQL 对账
>    （09-26=1,069 / 10-01=847 / 10-03=133）。留证见 `doc/审计报告/审计核实与修复_20261001.md` §二十二。

## 六、DCD 20261004 六件裁定 · MA 侧执行台账

裁定书：`E:\NAS\关键决策部\decisions\20261004-AF四件与DB一件与MA五件-裁定.md` §三（含 §三.6 活动识别件）。
**逐件登记"落码/未落码"，不写"已按裁定处理"这类无法复核的话。**

| 件 | 裁定 | 状态 | 落点与判据 |
|----|------|------|------------|
| 裁1 规则冷却 | Q1=A 显式化 + Q2 确认叠加 + Q3 占冷却 | **✅ 本件落码** | 见 §6.1 |
| 裁2 行为推断默认规则 | Q1=C 由 SP 给本居活动清单 + Q1a/b/c 收窄 + Q2=C 按域过滤 + Q3 保持 0 等 DB | ⬜ 未落码 | 等 SP 给清单（Q1=C 的输入在 SP 手里，MA 无法自造）；Q2 的按域过滤读取与 Q1a/b/c 的规则改写可在清单到位后同批改 |
| 裁3 反馈出境面脱敏 | Q1=A 分层 + Q2 label 白名单 + Q3 暂缓 | **✅ 本件落码** | 见 §6.3（`trace_anon.txt` 加产物 + `validate_label()` 收紧入参） |
| 裁4 超限响应 | Q1=A 分页 + Q2=A 默认精简 + Q3 维持裁行 | **✅ 本件落码** | 见 §6.2（含把申请里那句体积估算按实测更正） |
| 裁5 洞察门面 | Q1=A legacy 为对外只读引擎 + Q2=A 并存 + Q3 验收单全认 + Q4=A 如实上报截断 | **✅ 本件落码（Q3 判不齐 ⇒ 不切引擎）** | 见 §6.4。Q3 六条实测**三条齐、三条不齐**，切换开关保持关闭；Q4 三键落在新引擎扫描路径上，Q1=A 期间对外不可见 |
| 裁6 活动识别 | Q1=A 语义回归 + Q2=A 回 8 参数 + Q3=A 硬排除生效 + Q4 认可（交付纪律） | **✅ 本件落码（Q4 三项读数齐；覆盖面两问交 DCD）** | 见 §6.5。Q4 读数②在生产库上抓出一处**假命中**（`tags_json` 被当展示标签），已修并配锁；语义一侧受 `max_scan` 截断现自报 |

**另：`20261003-MA关键词检索短词与整句phrase召回下限` 不在这份裁定书里**（六件里没有 FTS 那件）。
MA 按"未裁不动"处理——`fts` 短词召回下限的门槛数值仍维持现状，等 DCD 单独裁。

### 6.1 裁1 的落码读数（2026-10-04）

**Q1（显式化，缺省即拒）**：
- `store.py`：`candidate_rules` 加 `cooldown_seconds INTEGER` 列——**可空、无 SQL 默认值**。
  有默认值就永远走不到拒绝分支，数值又会回到由 `add_rule` 的形参替裁定说话。
  新写口 `Store.set_candidate_rule_cooldown(rule_id, value)`（传 `None` = 撤回设定）。
- `rule_lifecycle.py`：`parse_cooldown()` 把「没给」和「给了但读不出数」分成两种拒因；
  `eligibility(candidate_id, cooldown_seconds=None)` 按 **参数 → 候选列 → 判拒** 的次序取值，
  blocker 前缀 `cooldown_gate:`；`promote(..., cooldown_seconds=None)` 把定下的值
  **显式传给 `engine.add_rule`**，并回写候选行（来源是参数时）、连同 `cooldown_source` 进审计 detail。
- 对外两入口各多一个入参：HTTP `POST /api/behaviors/rule-channel/promote` 的 `cooldown_seconds`、
  MCP `promote_candidate_rule(cooldown_seconds=None)`（ToolSpec 同步）。两处都**不给默认值**。
- `pending_promotions` 逐条带 `cooldown_seconds` / `cooldown_source`：差一个数值的候选在预演清单上就看得见。
- `add_rule(cooldown_seconds=300)` 一个字没动——裁定只挪走晋升端的隐式默认，人工建规则路径维持原语义
  （`test_manual_rule_path_still_gets_the_default` 锁住这一点）。

**Q2（确认叠加）**：无代码动作。叠加的两道闸各有其锁，且新增一条把两者接在晋升通道上：
`test_promoted_rule_actually_honours_its_own_window`——晋升出的设备规则先要凑满 60 秒 3 次才响第一次，
第二次达门槛时被自己那条 1,800 秒冷却压住，满窗后再次响。**count 管够不够格、冷却管够了之后响几次**，
这条同时是"晋升写进列的数值真的咬住匹配端"的证据（不是只躺在库里）。

**Q3（试运行占冷却）**：上一轮 `a192c51` 已落码，锁 `test_dry_run_also_consumes_the_window` 在册。

**锁与变异红证**：`tests/test_rule_cooldown.py` 从 13 条增至 **22 条**（新增 9 条 = 7 个函数 + 坏值参数化 2 例），
本机全绿；四条变异各自咬到自己的锁后立刻还原（`RESTORED=True`）：

| 变异 | 新红 |
|------|------|
| M1 晋升不再写冷却值（退回 `add_rule` 形参默认） | 3 条（写入值 / 列回退 / 参数覆盖） |
| M2 取消「缺省即拒」判据 | 2 条（拒绝晋升 / 预演清单可见缺口） |
| M3 参数值不回写候选行 | 1 条（有据可查那半句） |
| M4 坏值不再判拒（当成未设定） | 2 条（负数 / 非数） |

**容器权威（把工作区整份快照 `docker cp` 进运行中的容器重跑，不在本机下结论）**：
`/tmp/vs16` 口径，`PYTEST_RC=0` / **`1135 passed, 12 skipped in 173.01s`**；
同快照 pyflakes 门禁 **`当前 0 条，基线 0 条，新增 0，已修 0`** / `GATE_RC=0`（2026-10-04）。
12 条 skip 全部是既有缺口（`hmmlearn`/`pm4by`/`river` 缺依赖 9 条 + `test_signal_learning.py` 门面待适配 3 条），
不是本件新增。

**本件对生产的影响面**：晋升通道当前在生产上是**空跑**（`active_rules=0`、`candidate_rules=23` 全 `staging`），
所以"缺省即拒"不会改变任何现网行为；它改变的是**第一次真晋升时写进库的那个数**由谁说了算。
候选行没有 `cooldown_seconds` 值时晋升会被拒——这是裁定的本意，不是回归。列由 `Store.init_schema`
的 ADD COLUMN 迁移建，**随下一次重启生效**（与 `trigger_json` 同一批窗口事项，见 §三）。

### 6.2 裁4 的落码读数（2026-10-04）

**Q1（分页，与 `query_unified_events` 同口径）**：
- `store.py`：`list_device_health(state, limit=None, offset=0)`——**默认仍是无界**。进程内两处调用点
  （`identity.py:687` 身份层整表重建、`api/identity_routes.py:45` HTTP 面板）语义一字未改；
  把默认值改成 500 会让这两处静默少读。新增 `count_device_health(state)` 供 `total`（分页前的全量条数）。
  排序补 `entity_id` 兜底：批量 upsert 给整片行打的是同一个 `updated_at`，少这一路 LIMIT/OFFSET 会重叠漏行。
- `mcp_server.py`：信封算在**模块级纯函数** `device_health_page()`（MCP 工具本体在 `_build_server` 闭包里，
  测试取不到，判据只能落在这一层）。工具签名
  `list_device_health(state, limit=500, offset=0, fields='lean')`，`limit` 夹在 `[1, 2000]`；
  返回键 `ok/state/health/count/total/offset/limit/has_more/next_offset/fields/logical_devices`。
- 翻页收口判据：`has_more = 本页确实给了行 且 offset+本页行数 < total`。只按 `offset < total` 判会留下
  一条死循环——空页时 `next_offset` 与 `offset` 相等，按 `while has_more` 翻页的消费端原地打转。
- `ok` 那一位**留在工具调用点的字典字面量里**。写成 `payload["ok"] = True` 会让门禁 `fake-ok-const`
  对 `_build_server.list_device_health` 的那条基线"凭空消失"：债没还，只是扫描器看不见这种写法。
  `.gates-baseline.txt` 因此**一字未改**（首轮本机全量确实因这条失配报红 2 条，改回字面量后 `active=()`）。

**Q2（默认精简投影）**：`project_device_health(rows, fields)`（同为模块级纯函数）——
`stable_id` 截 40 字并给被截那条登记 `stable_id_truncated`（没截的不冒这个键）；`note` 仅在
`referenced=1` 时给原值，其余**置空但保留键**（删键 = 消费端 `row["note"]` KeyError，判例见
DCD 20261004 §六.2）；`fields='full'` 一行不改，输入行也不就地改。

**把申请里的体积估算按实测更正**（生产库只读探针，`/data/memory_agent.db`，2026-10-04 07:03Z 家庭墙钟）：

| 读数 | 值 |
|------|-----|
| `device_health` 行数 | **1,776**（投递申请时是 888，数据已翻倍） |
| 全量 JSON 体积 | **562,590 字节 > 上限 512KB** ⇒ 不带分页**今天必超限**，比申请写得还硬 |
| lean 投影后 | **495,219 字节 = 0.880**，即**只省 12%**，**不是申请估的"压到约 1/3"** |
| `note` 字符量 | 22,555 → **130**（省的全在这一侧；`referenced=1` 只有 10 行） |
| `stable_id` 字符量 | 48,616 → 47,084（**几乎没动**：现网平均 27 字，40 字阈值够不着） |
| 默认页 500 行 | 原始 148,983 / lean **132,240 字节 = 上限的 26%** |

⇒ **真正把超限解掉的是分页，不是投影**；投影的收益全部集中在 `note` 一侧
（22,555 → 130 字，因为 `referenced=1` 只有 10 行）。阈值 40 字是裁定给的数，MA 不自作调整，
按 Q2=A 原样落码；但这组读数说明 Q2 兑现不了申请里"1/3"的预期——若仍要那个量级的收益，
得另裁 `stable_id` 的取值口径（例如按字节预算裁、或默认只给 `entity_id` + `state`）。
**这是 MA 自己的估算被实测推翻，登记，不静默接受。**

**Q3（维持裁行）**：`_json_shrink_to_fit` 一字未动，超限仍是合法 JSON + `_truncated.dropped` 明示。
顺带修掉申请 §一 里那条"摘要不可用"：字符截断路径的摘要**不再点名"分页参数"**（当时的工具确实没有它），
改为指向该工具自己的参数面；锁 `test_char_cap_hint_points_at_the_tools_own_parameter_surface`，
变异 M8 证明它会红。

**锁与变异红证**：`tests/test_mcp_contract.py` 测试函数 **15 → 25**、用例 **20 → 30**，本机 `30 passed`；
八条变异各自咬到自己的锁后立刻还原（全部 `RESTORED=True`）：

| 变异 | 新红 |
|------|------|
| M1 lean 不截 `stable_id` | 1（截断那半句） |
| M2 截了却不登记 `stable_id_truncated` | 1（同上，反方向） |
| M3 未引用实体的 `note` **整键删除** | 1（键在值为空那半句） |
| M4 `fields='full'` 开关失效 | 1 |
| M5 `has_more` 退回拿 `offset` 判 | **2**（逐行走查 + 空页必须收口） |
| M7 `has_more` 不看 `total` | 1（末页不该再给 `next_offset`） |
| M6 分页排序少 `entity_id` 兜底 | 1（同时间戳翻页不漏行） |
| M8 摘要退回点名「分页参数」 | 1 |

**容器权威（整份工作区快照 `docker cp` 进运行中的容器重跑）**：`/tmp/vs18` 口径，
`PYTEST_RC=0` / **`1145 passed, 12 skipped in 167.23s`**（上一批 `/tmp/vs16` 是 1,135 passed，
差值 10 恰是本件新增用例数）；同快照 pyflakes 门禁 **`当前 0 条，基线 0 条，新增 0，已修 0`** /
`GATE_RC=0`（2026-10-04）。12 条 skip 仍是那批既有缺口（缺依赖 9 + `test_signal_learning.py` 门面待适配 3）。

**本件对生产的影响面**：`list_device_health` 的默认响应从"全量、无界"变成"500 行一页 + lean 投影"。
这是**对外形状变更**，由裁定 Q1/Q2 授权，MA 侧无可回退项；DB 侧要按 `has_more/next_offset` 翻页取全量。
`count_device_health` 与 `device_health_page` 都是新增，进程内旧调用点（`identity.py:687`、
`api/identity_routes.py:45`）走的仍是无界分支，语义一字未改。代码随下一次重启生效——
不需要新的窗口事项，与 §三 那批合并窗同批即可。

### 6.3 裁3 的落码读数（2026-10-04）

裁定书 §三 裁3（**Q1=A 分层 / Q2 label 白名单 / Q3 三套收敛暂缓**）。本件的约束是「零破坏」：
出境包已经交付给消费端，所以**只加产物、不改已有产物的格式**，唯一允许的收紧在**入参**一侧。

**Q1（分层，S1 本体一字不改）**：
- `feedback_pack.py:195` `build_feedback_pack(..., anon_sanitizer=None, known_rooms=(), member_names=())`。
  `trace.txt` 的字节形状与 meta 旧四键（`label/snapshot_included/trace_included/trace_sanitized`）**一字未动**；
  新增的是同内容走过 S1 之后的 `trace_anon.txt`，以及 meta 的 `trace_anon_included` / `trace_anon_sanitizer` 两键。
- **S1 是注入进来的，不在 pack 里重实现**：路由侧传 `rt.store.sanitize_feedback_text`（`store.py:4786`）。
  重实现会有两份「入库口径」各自漂移，且 `feedback_pack` 反向 import Store 会成环。
  Q3 那一问（三套收敛为一）按裁定**不动**——`sanitize_text`（S3）的 docstring 里写死了这条边界：
  想在这里加姓名脱敏 = 把分层裁掉，先申请裁定。
- **fail-closed 三档**：注入的 S1 入口缺失 / S1 抛异常 / S1 产出空串 ⇒ **不写** `trace_anon.txt`，
  meta 如实记 `trace_anon_included:false`。绝不发一个「名字叫 anon、内容仍带姓名」的文件——
  那比没有这一层更坏。`trace.txt` 与整包照常交付（加的那一层失败不该拖垮原有产物）。

**Q2（label 白名单，收紧入参而非改格式）**：`feedback_pack.py:114` `validate_label()`
- kind 取自闭集 `LABEL_KINDS`（`:52`）= `vlm_failed` / `low_confidence` / `skipped` / `bad_case`；
  段落匹配 `:57` 的日期 / 数字 / `a-z` 起头 ASCII 段；CJK 段**必须是名册里的真房间名**。
- **成员姓名子串检查跑在结构检查之前**，且拒因字符串不回显姓名（`label` 会变成文件名，
  把姓名抄进错误响应 = 换一个面泄露）。名册来自 `behavior_routes.py:572` `_label_rosters()`：
  `insights.room_names()` ∪ `store.distinct_rooms()`，成员名取 `store.list_members()`。
- 分隔符只允许**单个** `-`/`_` 且**不许结尾**（探针实测第一版把 `vlm_failed-` / `vlm_failed-书房-` 放过了）。
- 段落正则带 `re.ASCII`，这是**承重**的：默认 `\d` 认全角 `１` 与阿拉伯-印度数字，白名单会从字符维被绕过（M4 有红证）。
- **被拒的 label 不落盘**，直接 `return None`。旧写法是 `re.sub(r"[^\w\-]", "_", label)` 清洗后照落盘
  ⇒ 结果是拒不了也不干净：目录里留下一个文件名带姓名的包。现在这类入参（含路径穿越 `../../x`）一个文件都不写。

**生产影响面**：两处出口都接了白名单与真实 S1——`api/behavior_routes.py:585`（反馈包）与 `:666`（bad_case 导出）；
后者额外把事件自身的 room 折进名册，否则「该事件所在房间」这个最自然的写法会被自己的白名单拒掉。
名册读取**不套 try/except**：缺名册恰恰就是缺成员名单，静默降级等于放宽白名单，宁可让请求报错。
打包与读名册都走 `asyncio.to_thread`（读名册是同步 SQLite，事件循环门禁零容忍）。
`build_feedback_pack` 的调用点全仓**只有这两处**（已 grep 确认）。

**产物实测**（本机探针，假名册 `张小山/李四/Tom` + 假电话，非现网数据）：

```
members: ['trace.txt', 'trace_anon.txt', 'meta.json']
trace.txt: 张小山在书房，电话 <REDACTED-PHONE>
trace_anon.txt: 成员1在书房，电话 <REDACTED-PHONE>
meta.json: …"trace_sanitized": true, "trace_anon_included": true, "trace_anon_sanitizer": "S1"
no-anon members: ['trace.txt', 'meta.json']        # 不注入 S1 时旧形状原样
name-label pack: None                              # label 带成员名 ⇒ 整包不落盘
```

**锁与变异（本机 `Python313`，`PYTHONPATH=src`）**：`tests/test_feedback_pack.py` 用例 **22 → 39**，
与 `tests/test_perception_ingest.py` 同跑 **`70 passed`**；全量本机 `1149 passed, 25 skipped`（`LOCAL_RC=0`）。
baseline 先跑绿（`[baseline] RC=0 failed=[]`），九条变异各自咬到自己的锁后立刻还原，全部 `RESTORED=True`，`MUT_RC=0`：

| 变异 | 新红 | 咬住的是哪半句 |
|------|------|----------------|
| M1 anon 拿 S3 的结果冒充 | **5** | 分层是不是真过了 S1（含全链那条） |
| M2 `trace.txt` 也过 S1 | 2 | 已交付产物的口径没被改 |
| M3 去掉成员姓名这一维校验 | 1 | 白名单拦得住姓名，且拒因不带姓名 |
| M4 段落正则去掉 `re.ASCII` | 1 | 全角数字过关不了 |
| M5 段落白名单放宽成任意字符 | 3 | CJK 必须是真房间名 + 全角 + 分隔符 |
| M6 删掉尾分隔符检查 | 1 | `vlm_failed-书房-` 这类不收 |
| M7 白名单拒收后照样落盘 | 2 | 拒了就不写文件（含路径穿越那条旧用例） |
| M8 调用点把真 S1 换成 `None` | 1 | 出口接的是注入的那个 S1 |
| M9 调用点把打包放回事件循环 | 1 | 同步打包不在事件循环里跑 |

**另登记一条诚实的空档**：M10（去掉 `anon_sanitizer is not None` 判据）跑出 `RC=0 red=0 RESTORED=True`——
这条**没有锁可咬**，因为去掉判据后行为等价（`None` 本就不是 str，仍会走「不写 anon」分支）。
按纪律如实记为「不被测试观察」，不给它编一把锁。

**容器权威**（整份工作区快照 `docker cp` 进运行中的容器重跑，`/tmp/vs19`）：
`PYTEST_RC=0` / **`1162 passed, 12 skipped in 176.84s`**——上一批 `/tmp/vs18` 是 1,145 passed，
差值 17 恰为本件新增用例数；同快照 pyflakes 门禁 **`当前 0 条，基线 0 条，新增 0，已修 0`** / `GATE_RC=0`。
**`.gates-baseline.txt` 一字未改**（本件不搬函数、不改名，刻意避开裁4 §判据③ 那两次失配）。

**生效条件与待裁项**：代码随下一次重启生效，不需要新开窗口，与 §三 合并窗同批即可。
`/data/feedback_packs` 出境时**按收件方选哪一份文件**（`trace.txt` 还是 `trace_anon.txt`）是跨仓约定，
不在 MA 单方能改的面上（homesdk 契约文档），已在裁3 回执里请 DCD 定口径。

### 6.4 裁5 的落码读数（2026-10-04）

裁定书 §三 裁5：**Q1=A（legacy 为对外只读引擎）/ Q2=A（两代键并存）/ Q3 验收单全认 / Q4=A（如实上报截断，不提高上限）**。
完整判据与红证在 `doc/审计报告/审计核实与修复_20261001.md` §二十七，这里只台账化。

**Q1=A 的落码——对外台账 6 → 11 把**（`LEGACY_OUTWARD_METHODS`）：新增
`query_behavior_events`、`get_last_event`、`climate_sessions`、`explain_insight`、`water_purifier_usage`。
其中前四把是「门面调出去」那一半缺陷的宿主——门面体里 `self.core.X(...)` 指向 `BehaviorService` 上
**不存在**的成员，运行时被 `_degrade` 静默收成空页，调用点扫描器（管「外面调进来」那一半）看不见。
本件补了第二支扫描器 `scripts/scan_insights_engine_attrs.py`：

| 口径 | 引擎指向 | 空指向 | 退出码 |
|---|---|---|---|
| 部署态 `/app/src/…/insights/api.py` | 35 | **6** | `HEAD_RC=1` |
| 本件工作区快照 | 34 | **0** | `WT_RC=0` |

回归锁**按文件路径 import 这支扫描器**（一份实现，不让测试与脚本漂移）；
`tests/test_vma_insights_callsite_binding.py` 用例 **9 → 17**，台账规模钉成 `== 11`（10 把有 ToolSpec）。

**形状与语义的三条取向**（都是实测驱动，不是审美）：
- `query_behavior_events(room, member, days, start, end, limit)`、`get_last_event(entity_id, domain, room, transition, days)`
  形参**逐字回 legacy**——MCP handler 的位置参本来就是按 legacy 顺序传的。
- `get_events` 修掉三条真缺陷：`total` 在切片之后才统计（恒等于本页条数）、返回 `EventRecord` 对象而非 dict、
  降级包用 `days` 键而非 `events/total/offset/limit/has_more`。现走 `_search`，与 `search_events` 同一条扫描路径。
- **不伪造 `total`**：`query_behavior_events` 只补 `count/offset/limit/has_more/time_range`。legacy 的
  `store.list_behavior_events` 在 SQL `LIMIT` **之后**才做 `member` 过滤，引擎手里没有可信匹配总数；
  变异 A6（把 `count` 改名 `total`）由该用例判红。申请里的**附带一问（`total` 语义）裁定书未答**，MA 不替 DCD 定。
  > **本条已被 DCD 20261004 追加裁定 Q-A 取代，落码见 §6.6。** 当时那句"没有可信匹配总数"现在查清是
  > 症状：不可信的根源是"先取一页再筛人"的读取形状，`total` 与 `count` 两个键都被它带歪。

**Q4=A 的落点与边界**：`annotate_scan()` 落在 `_search`（`get_events`）上，`StoreRepository.scan_limit` 只读暴露同一上限，
`_scan_limit()` 本体未动。生产库只读实测：30 天全量匹配 **958,388** ⇒ 门面报 `total=30000` /
`truncated=True` / `scan_limit=30000` / `total_exact=False`，stderr 同期多条 `load_events 命中扫描上限 30000`（截断路径在现网真被走到）。
**Q1=A 期间这三键不对外**：对外 `search_events` 跑 legacy，legacy 的 `total` 来自不带 LIMIT 的 `count_events`
（实测 30 天/客厅 = 149,206，与新引擎被截的 30,000 与 `count_events` 三方互证）。所以 Q4 是**新引擎扫描路径上的承诺**，
切换之后才随对外工具落地——这一条已在回执里向 DCD 说明，避免"裁5 已交付对外三键"的误读。

**Q3 验收单实测：六条里三条不齐 ⇒ 一个方法都不切**（量具 `scripts/probe_insights_q3_acceptance.py`，生产库只读，`Q3_RC=0`）：

| 条 | 读数摘要 | 判定 |
|---|---|---|
| 1 六个过滤/排序位生效 | repo 层 `room`/`domain` 生效；`category`/`query` 门面收下但 `_search` 丢弃（传与不传 `total` 都是 30,000）；`state`/`order`/`summarize`/`domain` 在 `_search` 形参里没有位置 | **不齐** |
| 2 `days` 按天窗口 | 客厅 1/7/30 天 = 5,447 / 31,012 / 149,206 单调，legacy 同口径一致 | 齐 |
| 3 `total` 不被悄悄截 | 命中上限即 `truncated=True`、`total_exact=False` | 齐 |
| 4 legacy 分页/窗口键有对应物 | 五个方法全缺 `count`/`next_offset`；`search_events` 另缺 `window`/`ok`，两代键集交集仅 4/9 | **不齐** |
| 5 「接收但不生效」要么实现要么下架 | 17 个工具里 **13 个**在新引擎无同名实现 | **不齐** |
| 6 三项对比读数 | 键集合 / 语义枚举集合（`domain` 6、`state` 271）/ 旁挂依赖（`activity_rules=5`、`signal_exclusions=1`、`config.excluded_entities=0` 项）全部交出 | 齐 |

一处探针自己的错也登记：第一版把 `excluded_entities` 当**表**读 ⇒ `OperationalError: no such table`，
它是配置键；表侧硬排除落在 `signal_exclusions`。

**门禁与权威读数**：容器快照 `/tmp/cd5s1`（全新目录名，不复用别的会话留在容器 `/tmp` 的 `vsNN`）——
定向三件 **`30 passed` / `TRIO_RC=0`**；pyflakes **`当前 0 / 基线 0 / 新增 0 / 已修 0` / `GATE_RC=0`**；
全量 `1 failed, 1168 passed, 13 skipped`，那 1 条红是**打包时漏了 `.gates-baseline.txt`** 导致 208 条存量全被当新增的
环境伪红，补进同一快照后 `tests/test_quality_gates.py` **`2 passed` / `QG_RC=0`** ⇒ 等效 **1169 passed / 0 failed**；
本机全量 `1157 passed, 25 skipped` / `LOCAL_RC=0`。七条变异（A1–A7）全部咬到自己的锁，
baseline 先跑绿（`17 passed FAILED = []`），逐条 `RESTORED=True`、末态 `RESTORED_FINAL = True`。
**`.gates-baseline.txt` 一字未改**（175 行）。

**生效条件**：与裁3 同批，随下一次重启生效，不需要新开窗口。**仍开着的两问交给 DCD**：
`total` 的对外语义（原附带一问未答）；三条不齐是「新引擎补齐后再切」还是「把这六个过滤位与两代键正式下架」
——后者属对外承诺变更，MA 按"不切"执行，不自行动工具面。

> **这两问已于 2026-10-04 18:35 裁定，本行按 HEAD 复核更正**（DCD 在裁6 补裁 §〇 就指出过：已裁项被挂回"仍待裁"）：
> `total` → **Q-A**（= 匹配总数，授权新增不带 LIMIT 的计数查询，落码见 §6.6）；
> 三条不齐 → **Q-B = A**（新引擎补齐六条，不切、不下架、不假装补齐，任务 #40）。

### 6.5 裁6 的落码读数（2026-10-04）

裁定书 §三.6：**Q1=A（语义活动回归，时段启发式降为"无标签设备兜底"的补充输出）/ Q2=A（`define_activity` 回 8 参数、
注册走 `upsert_activity_rule`、现行引擎真正读 `activity_rules`）/ Q3=A（硬排除在 `activity_matrix` 生效 + 返回体可见字段）/
Q4=认可（每切一个方法留三项对比读数，缺一项判红）**。代码提交 **`0d6ec63`**，
完整判据与红证在 `doc/审计报告/审计核实与修复_20261001.md` §二十八，这里只台账化。
`infer_activities` 不在 `LEGACY_OUTWARD_METHODS`（api.py:68-80 那 11 把）里 ⇒ 本件切换与裁5 Q1=A 不冲突。

**Q2=A 的反面不止"注册了没人读"，还有一个是"读了但读错维"**。改前 `activity_rules` 有 5 条 enabled 规则在库里躺着没人读；
第一版搬运开始读表之后，Q4 读数②在生产库上抓出一处**假命中**——`tags_json` 被当成展示标签，而 legacy 一侧它是判定条件
（`insights_legacy.py:2782`）。后果：`room=''`、`tags_json=["nonexistent_tag"]`、`min_events=1` 的 `bogus_verify`
退化成"全屋任一实体 ≥1 次事件即成立"，把整户事件判成一条并不存在的活动。

| 口径 | 新引擎 30 天窗 `activity_types` | 语义行数 |
|---|---|---|
| 搬运丢了 tags | `away, **bogus_verify**, cooking, door_verify, notag_verify, study, tv, 夜间/日间/晨间活动` | 11 |
| 本件修好后 | `away, cooking, door_verify, notag_verify, study, tv, 夜间/日间/晚间/晨间活动` | **9** |

修法把**判定维**与**展示维**分开：`ActivityRule.require_tags`（新增）+ `ActivityEngine._signal_ids` 对解析出的实体按标签取交集
（任一命中即计入，与 legacy 同口径）+ `_rule_from_row` 把 `tags_json` 同时交给 `require_tags`。
内置规则只留展示维，不被误当成硬实体过滤器。一处**测试自己的错**同步登记：原种子设备 `binary_sensor.bedroom_window`
（"卧室窗台"→`door`）配 `tags=["presence"]` 的规则**当时能过正是因为标签被丢了**，已换成 presence 设备并新增独立锁。

**两处"日界"缺陷**：① `ActivityEngine.infer` 把规则窗口锚在 `split_days` **裁剪后**的起点，`(0,24)` 被整体平移成
「起点~起点+24h」，同一段活动在相邻两天各判一次（改前实测洗澡 2 行）；现锚在该日**家庭零点**（`_day_midnight`）再 `clip()`。
② `split_days` 的日界落在**机器**零点（容器 UTC ⇒ 家庭 08:00 换日），现走 `house_dt`/`house_ts`。

**Q4 三项读数**（`scripts/probe_activity_semantic_readings.py`，生产库只读，`Q4_RC=0`）：

| 项 | 实测读数 | 判定 |
|---|---|---|
| ① 返回键集合 | legacy 8 / 新引擎 9；legacy 独有 `behavior_only`、`detector_report`、`signal_inventory`（未迁）；新增 `filters`、`summary`、`rule_sources`、`excluded_entities`；消费侧要读的 `activities/activity_types/total_activities/window` 四个都在 | 齐 |
| ② 语义枚举值集合 | legacy 8 类 116 行 vs 新引擎 10 类 45 行（`source` 档位 `heuristic=36 / semantic=9`）；行级键同名 6 个 | 读数已交，**词表与覆盖面不等价** |
| ③ 旁挂依赖 | 库里 `activity_rules` 启用/全部 = **5/5**、`signal_exclusions` 生效中 **0**（那 1 行 `revoked=1`；§二十七 记的是 `COUNT(*)`，两条口径都对）；自报 `builtin=5, activity_rules_table=5, custom_applied=5, selected=10, excluded_entity_ids=0` ⇒ **与库里真值一致** | 齐 |

**Q3=A 的作用面如实登记**：生产库当前没有生效中的硬排除行，所以带/不带排除读数相同
（`count_events` 957,153 / 957,153，`activity_matrix` 同）——差值 0 属预期而非失效；
剔除能力由 `test_not_in_drops_only_the_named_entities`（钉 SQL 形状：空集不加 WHERE）与
`test_hard_exclusion_drops_events_and_is_visible`（临时库实测排除 1 个实体后事件 6→2，证据文本不再出现该设备）钉住。
顺带修掉 `_add_not_in` 的空头承诺：`events` 三列全 `NOT NULL DEFAULT ''`（store.py:67-79），那条 `OR col IS NULL` 分支永远走不到，参数与说法一并删。

**语义 9 行 / 兜底 36 行的原因分两层**（`scripts/probe_activity_coverage_gap.py`，生产库只读，`GAP_RC=0`，逐规则逐信号量三段数）：
① **词表**——`bath` 的 `热水器` 解析到 0 个实体，`sleep` 的 `卧室灯` 只 1 个实体 / 30 天 6 条事件，而 `any_of` 要求
"至少一个可选信号命中" ⇒ 这类规则永远不开口；② **扫描口径**——30 天窗语义判定只看得见 30,000 / 957,148（**3.1%**）的事件，
切片只覆盖窗口前 20 小时（09-04 10:02 → 09-05 06:08），而兜底一侧走 `activity_matrix`（SQL 聚合，全窗口）。
本件只做了一件不该省的事：把截断**自报**出来——`rule_sources` 新增 `events_total=957,153` / `scan_limit=30,000` /
`scan_truncated=true`，判据逐字对齐裁5 Q4=A（`scanned >= scan_limit` ⇒ "可能被截"），**上限本身一个字没动**。
锁 `test_rule_sources_reports_the_scan_cap_that_limits_the_semantic_side` 在临时库把上限压到 6（`8/6/6/True`），
并以 `max_scan=50` 作对照组（`8/8/50/False`），排除"恒 True/恒 False"两种假实现。

**另有三处独立缺陷各有自己的锁**：`define_activity` 回执丢了 legacy 的 `coverage_warning`（现原样拼在 message 末尾，
且不再出现"自动套用/尚未套用"空头话术）；`nlquery.py` 的 `route=activity` 从切引擎那天起就没答上来过
（读新引擎没有的 `data["total"]` ⇒ KeyError 被兜成"查询失败"；`summary` 现在是 dict 被 `%s` 进话术），现读 `total_activities`；
`explain_insight` 维持裁5 的 legacy 路由（未迁清单还在 legacy 一侧：静默间隔睡眠/离家检测器、`_noise_entities`、
`detector_report`/`signal_inventory`、`detected_activity` 落库）。

**锁与权威读数**：`tests/test_vma_activity_semantic.py` 新建 **15 个用例**，配套 `test_vma_insights_facade_dead_tools.py` 三条改写；
定向 `19 passed`、邻近三件 `20 passed`（**对 HEAD 重跑后更正**：
两件同跑 `20 passed`，其中新建文件 `15 passed` / `A_RC=0`、改写文件 `5 passed` / `B_RC=0`；
邻近三件（openshs_eval + researcher + dead-tools）`26 passed` / `TRIO_RC=0`。先前那两个数是加第 15 把锁前的旧读数）。变异台账 **16 把锁逐条判红**（`MUT_RC=1` + `RESTORED=True`），其中 `split_days` 时区锁
本机是**等价位**（+8 整小时偏移下 `MUT_RC=0`，诚实登记）、容器（UTC）判红。容器快照 `/tmp/c6snap20261004c`
（全新唯一名，不复用别人的 `vsNN`；打包 336 文件、含 `.gates/`）：pyflakes `当前 0 / 基线 0 / 新增 0 / 已修 0` / `GATE_RC=0`；
全量 **`1185 passed, 13 skipped in 177.04s` / `SUITE_RC=0`**；`.gates-baseline.txt` 一字未改。
仍 skip 的三条 `test_signal_learning.py:173/216/257` 属任务 #23（P2-5 用例翻新），本件不冒充收口。
两个探针全程 SELECT + 内存计算，未对生产库跑任何写测试，载荷读数只到键名与数量层级。

**生效条件**：随下一次合并停机窗生效（与裁3/裁5 同批，不需要新开窗口）。**本件新增两问交 DCD**（都改变判定口径，MA 不擅自动手）：
① 内置规则的**词表缺口**怎么收——改内置关键词 / 给设备补标签 / 把 `any_of` 从"至少命中一个"降为"命中则加分"（第三种改的是规则语义）；
② 30 天窗语义判定只见窗口前 20 小时——裁5 明令不提高 `max_scan`，补齐路径只能是**按天分批扫描**（改读取策略），
还是维持现状、由 `rule_sources.events_total/scan_truncated` 把口径交给消费方判断（不改代码只改承诺）。
裁5 那句**附带一问（`total` 的对外语义）裁定书仍未答**，本件不替 DCD 定口径。

> **上面这三句全部过期，按 HEAD 复核更正**（`20261004-MA-裁6落码回执与两问-裁定.md`）：
> ① → **A′**（内置模板字面**不动**，本居清单生成覆盖行入 `activity_rules`；C 驳回；等 SP 给本居活动清单，
>    给设备补标签由 DCD 同一张单向 SP 提）；② → **A**（按天分批扫描，带三条约束，见 §6.7）；
>    `total` → **已裁**（Q-A，18:35 那批，落码见 §6.6）。
> 另更正一处引用：`define_activity` 的 8 参数签名现读在 **`insights/api.py:550-552`**，
> 我在回执 §1 里引的 `api.py:503-505` 是 `water_purifier_usage` 的 docstring——DCD 的更正成立。

### 6.6 裁5 追加 Q-A 的落码读数（2026-10-04）

裁定：`total` = "匹配的事件总数"，MA 侧授权在 Store 新增**不带 LIMIT** 的计数查询；`count`（本页条数）与
`total`（匹配总数）**分开，不许改名冒充**。落码与现网少报读数记在审计 §二十九，这里只留台账要点：

- **病根不是"缺一条 COUNT"，是读取形状**：旧 `list_behavior_events` 先 `ORDER BY server_ts DESC LIMIT ?` 取页、
  **再**在 Python 里按姓名筛 ⇒ 该有人时页面也能是空的。所以修法是把 `member` 下推进 SQL（`json_each` +
  `EXISTS`，带 `json_valid`/`json_type` 护栏），谓词抽成 `_behavior_event_where`（`store.py:2298`）由
  `list_*`/`count_*` **共用一份**，`list_*` 加 `offset`、删掉 LIMIT 后的 Python 过滤。
- **一个靠反例抓出来的错**：`json_each` 给字符串元素的 `type` 是 **`text`**，不是字面的 `string`。
  写错时现网 8 个姓名里恰好 1 个比对不一致（就是那 1 行旧格式记录）。口径一致性自证现为 **8/8、不一致 0**
  （`SHAPE_RC=0`：4,023 行 / 空 428 / 非法 JSON 0 / 非数组 0 / object 元素 3,595 / text 元素 1）。
- **现网少报量级**（只读探针 `scripts/probe_behavior_event_total.py`，`PROBE_RC=0`）：30 天窗、页宽 50 ⇒
  7 个姓名里 **6 个**读数小于真值，3 个直接归零，最大缺口 **1,635 行**；页宽提到 100 只把最大缺口缩到 1,623
  ——**加大页宽救不了这个形状**。
- **`total` 取不到 ⇒ `None` + `total_exact=False`，不是 0**；`annotate_scan` 新增 `total_exact` 入参，
  于是「切片被截」与「总数精确」可以同时成立（`max_scan` 一个字节没动，裁5 Q4=A 仍生效）。
- **诚实边界**：`total` 精确的范围 = `_search` 实际施加的过滤（窗口/`entity_id`/`room`/`behavior_only`）；
  `category`/`query`/`state`/`order` 仍是 Q-B（#40）未补齐项，**不能说 `total` 覆盖全部入参**。
- **锁与读数**：新建 `tests/test_vma_dcd_20261004b_event_total.py` 6 把（含"截断与总数精确同真"及其对照组）、
  改写 2 把旧裁5 锁（`assert "total" not in out` 那条锁的是被 Q-A 取代的旧承诺）；本机定向 **`23 passed` / `LOCAL_RC=0`**；
  变异 **M1–M9 全咬**（`BITTEN=9/9`、基线 `(0, 39)`、`MUT_RC=0`）；容器快照 `/tmp/c7snap20261004a`（338 文件、含 `.gates/`）
  pyflakes **`GATE_RC=0`**、全量 **`1191 passed, 13 skipped in 249.62s` / `SUITE_RC=0`**；`.gates-baseline.txt` 一字未改。

### 6.7 20261004 同日两批新裁定的 MA 侧台账（裁6 补裁 + 向量面 MiniLM）

**裁6 补裁（`20261004-MA-裁6落码回执与两问-裁定.md`）**：

| 件 | 裁定 | MA 侧动作与状态 |
|---|---|---|
| §二.3 老用例冻结缺陷必须当场清掉 | 追加裁定，且列为**本件第一条交付** | **已完成并现读**（见下） |
| Q6-1 词表缺口 | **A′**：内置模板字面不动，本居清单生成覆盖行入 `activity_rules`；**C 驳回**（改的是规则语义）；B 由 DCD 与活动清单同一张单向 SP 提 | **等 SP 清单**（任务 #38 同一张单）。清单到手后落地 + 交**覆盖率读数**（每活动解析到几个实体、命中几条事件）。**不得**出现"已按现网命名调过内置词表"这类单方改动 |
| Q6-2 扫描口径 | **A**：按天分批扫描，三条约束——① 分批后**总读取量硬上限按日分摊同一预算**（不得新增预算，否则变相提高上限、绕开裁5）；② 改前/改后**30 天窗耗时要成对交**；③ `scan_truncated` 判据要**重新定义** + 一条可判红锁（防恒 True/恒 False） | 任务 **#44**，**单独 commit**（便于与"提高上限"这类越界改动区分）；与裁5 Q-B（#40）同批，沿用裁5 那条"vMA-1.4 窗口前未补齐就回来重议"的时点，不新设死线 |
| §二.4 读数必须标口径 | 追认为 **MA 交付纪律**（`COUNT(*)` vs `revoked=0` 那类） | 已并入本计划卡与审计的写法；后续回执量具与读数一律带口径 |
| §二.5 等价位与漏咬要可区分 | 追认 | 维持：本机 +8 等价位如实登记、容器 UTC 判红 |

§二.3 的现读（对当前工作区重跑，`.qoder/tmp-c7-mut-oldcase.py`，`OLDCASE_RC=0`）：把 `_signal_ids` 的标签判定
整体摘掉以还原缺陷现场，跑整个 `tests/test_vma_activity_semantic.py` ——
基线 `15 passed` / `rc=0`；缺陷现场 **`1 failed, 14 passed`**，FAILED 名单只有
`test_rule_tag_condition_filters_events_instead_of_being_decorative`（新语义锁，`:212`）判红；
曾被冻结的 `test_disabled_rule_rows_are_not_applied`（`:191`）在缺陷现场**仍然绿** ⇒ 它的"不判"来自
`enabled=0`（`:202` 直接断言 `list_activity_rules(enabled_only=True)==[]`、`:206` 断言
`rule_sources.activity_rules_table==0`），不再替旧行为站岗；种子设备已换成 presence 侧
（`_ev("binary_sensor.bedroom_presence", …)`，`:227`），`bedroom_window` 只留在"禁用规则"那条用例里做噪声行。
复原自证 `RESTORED=True`、末态 `15 passed` / `FAILED=[]` / `rc=0`。

**向量面 MiniLM（`20261004-MA向量面MiniLM退路-裁定.md`，Q1=B / Q2 不适用 / Q3 整行删除）**：任务 **#43**。
DCD 现读把 B 的实际面定为 **3 处回退路 + 2 处宣示性注释 + 1 份测试契约 + 1 条契约登记**：
`history.py:116`（未配端点先打印"使用本地 MiniLM"再构造）、`history.py:172`（显式传 `embedding_function=None`）、
`patterns.py:84-86` + `:81-82`（结果为 None 时省略 kwarg；异常被 `except` 吞后同样落默认）——**光改 `history.py` 堵不住**；
`config.py:56` 与 `history.py:47` 两处"零配置走 MiniLM"的承诺同批改；
`tests/test_vma_r8_chroma_error_masking.py:177` 的旧契约（"回退 MiniLM 或返回 None，但绝不打断构造"）要**被替换**
而不是被绕过，补两条可判红判据：① 未配端点 ⇒ 向量面**不可用**且**不落 MiniLM**；② 采集/启动**未被阻断**（要保留的属性）。
`Dockerfile:27` 那行**整行删除**（不是摘 `|| true`），并立通用规则：**构建链里不许用 `|| true` 吞掉本该成功的步骤**。
两条必须随回执交的证据：容器内对"显式 `embedding_function=None` 是否等价默认 MiniLM"的**现读结论**
（DCD 本机无 chromadb，未独立证实这一格），以及 `grep -rn "DefaultEmbeddingFunction" src/` / `"使用本地 MiniLM"`
现读 0 命中、`Dockerfile` 全文无 `|| true`。窗口耦合已确认：删该行与 `vendor/homesdk` 进运行面**同一次镜像重烤**
——**重烤仍未获授权**，本件只落代码与 Dockerfile 变更，不自行动交付面。

### 6.8 豆包批次（#41/#43/#44）核实与补漏（2026-10-05）

**触发**：`doc/审计报告/修复与核实/审计修复整合总报告_20261004.md` 自列未做项「容器内全量回归」。
**逐条核实表 + 变异自证 + 生产现读**见 `doc/审计报告/修复与核实/豆包批次核实与补漏_20261005.md`；此处只记账号。

| commit | 内容 | 门禁读数 |
|---|---|---|
| `98af875` fix(#43) | 向量面残留落点收口：`chroma_selftest` 未配端点**一次建集合都不发**；`embedding_status.reason` 不再声称默认模型；`patterns.py` 展开 `**kwargs` 以支持 AST 锁；新增 4 条锁（含全 `src/` 面"任何建集合调用点必须字面传 `embedding_function`"） | 变异 4/4 判红、`RESTORED=True` |
| `d518ebb` fix(#44) | 截断位 `instance → threading.local()`（门面级共享仓储 + `asyncio.to_thread` 并发下会张冠李戴）；`tests/test_vma_q62_daily_batch_scan.py` 去运行时刻依赖（固定历日锚点 `D0=2026-04-07`）+ 新增两线程截断位锁 | 本机 `6 passed`；容器 `SUITE_RC=0` |
| `e83f9d7` test(#44) | `tests/test_unified_view.py` 打的是在役库，四条独立 COUNT 与并发采集器相撞即假红（c9 实测 `1003987 != 1004477`）⇒ 视图/三源/source 取值域**一次读入**；新增确定性锁（每条 SELECT 之后插一行：单条语句恒等、四条恒破） | 变异 M1 判红、`RESTORED=True` |
| `410bf29` probe(#41,#44) | 只读量具 `scripts/probe_q62_q2_readings.py`（A1 分母 / A2 改前形状 / A3 改后形状 / A4 每日可见时段 / B 页预算对）；`fields='full' 一行不改` 文案改为如实（整页预算对 full 同样生效） | 容器 `PROBE_RC=0` |

**容器权威回归（对完整工作区快照 `c10snap20261005a`）**：
`GATE_RC=0`（pyflakes 当前 0 / 基线 0 / 新增 0）+ `SUITE_RC=0`、
**`1229 passed, 13 skipped in 219.44s`** —— 整合总报告那条"确认基线不破"至此闭合
（c8 判红 2 → c9 判红 1 → c10 全绿；判红原因分别是运行时刻依赖与并发假红，**都不是等价位**）。

**裁6 Q6-2 约束② 的成对读数（交付；此前被记成"生产实测待补"）**：
30 天窗、31 个日键、真值 952,014 条（口径 `COUNT(*)`，`day BETWEEN` + `ts BETWEEN`）；
日配额 `30000//31=967`，占日均真值 **3.15%**；
耗时 **422.4 ms（改前单条 LIMIT）→ 4977.7 ms（改后分批，三次中位）= 11.78×**（首跑 396.0→4275.1=10.8×）；
覆盖日键 **1 个 → 31 个**。⇒ "成本随天数线性"这一条现在可验收。

**新发现的形状问题（不自裁，已提申请）**：分批把"总量被砍"变成"每天各砍一刀"，
且每刀砍的是**当天最前面那段**（`ORDER BY ts ASC` + 日配额 967）。
生产实测：每日真实跨度 24.0 h、实返覆盖 **0.39–1.39 h** ⇒ 傍晚与夜间的语义活动在生产量下**仍不可见**；
改前形状同样残缺（只见窗口首日全天、其余 30 天全盲）。
申请件：`关键决策部/inbox/20261005-MA-Q6-2日配额饿死傍晚与夜间-决策申请.md`（附全部读数 + 三个候选口径）。
**MA 侧不自行动预算**（裁5 Q4=A"不得提高 max_scan"仍是硬边界）。

**裁1/裁4 Q2=甲 的字节对（交付）**：`device_health` 全量 1,776 台、单页取 500 行过投影——
lean `136,210 → 60,239 字节（省 55.8%，实返 240 行）`、full `152,997 → 59,787（省 60.9%，实返 214 行）`，
两档改后均 ≤64KB，单行最大 314–365 字节 ⇒ 生产不会触发"单行超预算"分支。
对照 DCD 现读的"40 字符截断只压 12%"：**甲口径够用，不必退乙**。

**流程偏差自记（不等对方发现）**：`71d18ee` 在 `list_device_health` 信封里新增了出境键
`page_bytes`——跨仓载荷键名变更须走 DCD 登记（`20261002-AF锁文件源与MA载荷键名-裁定` 立的规则），
本次如实登记并待裁"留 / 收进 `_meta` / 撤"；`tests/contract/` 未覆盖该信封，故无用例被打破。

### 6.9 裁5 追加 Q-B 的落码读数（2026-10-05，#40 第一格：Q3-1 六个过滤/排序位）

裁定要点：Q3 六条**不切、不下架、不假装补齐**，六条全部有实测读数才允许切换对外面。
本小节只交 **Q3-1**（`category`/`query`/`domain`/`state`/`order`/`summarize` 六位落到底），commit `160020c`。

- **病根仍是同一族**：`repo.load_events` 形参只有 `entity_ids/rooms/domains/behavior_only/exclude_*`，
  门面收的六位里后四位在新引擎**无处可去**，`state`/`order` 连落点都没有 ⇒ 表征是「传与不传拿到同一个数」
  的**静默放宽**，而返回形状仍然合法（与 #24/#46/#47/#48 同族）。改前 HEAD 现读：门面基线
  `total=144445 count=50 ok=None filters=[]`，同口径旧量具的 `category=climate` 与不传**同为 144143**、
  `query=灯` 同为 144143。
- **回声与取数必须同源**：`order` 在 `_search` 里**归一化一次**再下推 repo，否则「回声说 desc、取数按 asc」
  本身就是这一族的新成员。`total` 由 `_events_total` 用与扫描**逐字同一份**过滤取不带 LIMIT 的 `COUNT(*)`。
- **`ok` 不是字面量**：项目门禁 `fake-ok-const` 对本行报了 WARN「字面量 `ok=True`，不来自任何实际校验」。
  处理是**修掉**（`_envelope_ok()` 从四条不变式推出：events 是列表 / `count==len(events)` / `window` 在位 /
  计数 ≥ 本页条数），`.gates-baseline.txt` 一字未改。
- **比 legacy 多钉的一格（fail-closed，已如实登记为「extra」）**：语义位传了却既解析不出实体也解析不出
  domain 时答 **0 条**并回显 `filters.unresolved`，**不回落成全屋**。legacy 在这一格是漏的
  （`entities or None` + `domains or None` 双双为空 ⇒ 一个查不到的名字反而拿到全屋）。
  这条不是顺手加的严格性，是「静默放宽」这一族里最容易把错答当对答交出去的那一格。
- **现读**（容器内对生产库只读探针 `scripts/probe_insights_q3_acceptance.py`；
  **口径**：`total` = 不带 LIMIT 的 `COUNT(*)` 匹配总数，`count` = 本页条数，「扫描」= 按天分批实读行数）：
  `category=climate` **19**（domains_resolved 4 个域）、`query=灯` **43**（entities_resolved=14）、
  `domain=light` **43**（repo 层同域互证 1186）、`state='28'` **3399**、order asc/desc 首末时刻不同且
  回声 `=(asc,desc)`、`summarize` 有 `summary` 且 `count` 未被 50 条样本冒充、无关设备名 ⇒ `total=0` + `unresolved=True`。
  Q3-2 单调 `[5395, 32249, 148228]`（legacy 同窗 5395 / 148227）；Q3-3 `total=950362`（= 30 天全量匹配）、
  `total_exact=True`、`truncated=True`、`count=5` ⇒「切片被截」与「总数精确」同时成立。**六条收口：Q3-1 齐，
  Q3-4 不齐（缺分页/窗口键），Q3-5 不齐（新引擎无同名实现）——两条按实登记，不折算。**
  （这批是**单跑**读数。后续 §6.10 把量具改成成对跑 + 冻结绝对窗，同一形状现读为
  基线 144765 / `category=climate` 19 / `query=灯` 43 / `domain=light` 43 / `state='28'` **3404** /
  无关设备名 0，Q3-3 `950058 = 前置计数 = 后置计数`。探针收口为 `6c980ff` 后**复跑一遍成对读数**
  （两侧 `PROBE_RC=0`，`.qoder/tmp-c21-c18{pre,snap}20261005a-q3.log`）：判定与上完全一致，
  Q3-3 新冻结窗 `03:21:10` 锚点读 `949951/949951/949951` 仍判齐。
  **漂移分两种，别混**：同一次运行内 `days=30` 滑动右界被采集器推进是 **1–3 行/次**量级（所以 ±1 级差异
  不能再当口径差解读——此前我把 legacy 少 1 行解释成「窗口右界口径」，那是未验证的归因，已更正）；
  而**跨运行**的绝对窗每次重新锚定（右界 = 运行时刻），窗口整体平移，行数差可到百级
  （950362 / 950058 / 949951 是三次不同锚点，不是同窗内的口径差）。
  同一次运行内「前置计数 = 后置计数 = 门面 total」三读全等这一条，两次运行都成立。）
- **锁与验证**：`tests/test_vma_insights_search_filters.py` 新建 **13 条**，每条都配「该不响」对偶档
  （过滤位为空必须等于全量、非空必须不等于全量——只写正向断言的话，一个恒不加条件的实现也能绿）；
  变异 `.qoder/tmp-c17-mut-filters.py` **BITTEN=12/12**、每步 `RESTORED=True`、基线「什么都不改」档 `13 passed`；
  本机全量 `1266 passed, 22 skipped` / `LOCAL_RC=0`；容器权威回归（快照 `/tmp/c17snap20261005a`，351 文件）
  `GATE_RC=0`（pyflakes 当前 0 / 基线 0 / 新增 0）、`SUITE_RC=0`、**`1278 passed, 10 skipped in 214.67s`**。
- **未做的边界（不藏着）**：对外面**一个都没切**（`search_events`/`get_last_event`/`query_behavior_events`
  仍按裁5 Q1=A 转 legacy，`LEGACY_OUTWARD_METHODS` 一字未动）；`max_scan=30000` 一个字节没动（裁5 Q4=A）。

### 6.10 裁5 追加 Q-B 的第二格与量具自纠（2026-10-05，#40）

两件 commit：`6470650`（代码：新引擎三条读路径补 `window` 回显）、`6c980ff`（量具：Q3 验收单四处判定形状）。
**成对读数**（同一支量具跑两遍，改前取 `f22caa3` 快照、改后取工作区快照，两侧 `PROBE_RC=0`）：

| 新引擎路径 | 改前缺 | 改后缺 |
|---|---|---|
| `core.usage` | `['window']` | **无** |
| `core.behavior_insights` | `['window']` | **无** |
| `core.device_health` | 无（legacy 对它给 `window_days`，不是 `window`） | 无 |
| `_search` | `['next_offset','ok','window']` | **无** |
| `repo.entity_catalog` | 无信封可对齐 | 仍无信封可对齐 ⇒ **Q3-4 判「不齐」** |

- **两条不擅自做的边界**：① `coverage`/`data_quality` 不加 `window`——`data_coverage`（`api.py:809`）
  把 `core.coverage(tr)` 原样发出境，加键就是跨仓载荷变更，要登记不要自落；② `entity_catalog` 不假装补齐——
  新引擎只有 `repo.entity_catalog` 的原行列表，信封那几个派生键（重复候选、按房聚合）在新引擎里重写一遍
  等于**再造一个只读引擎**，收益只有切换一致性。
- **量具自纠四处**（逐条有反例，详见审计 §三十一）：①`sorted(list)-set` 让 Q3-4 整段 `PROBE_RC=1`、
  一条读数都没出来；②**哨兵值参与判据 ⇒「无处可去」被读成「生效」**（假绿，旧判据给
  `生效=['domain', room, state]` 而真相是 `TypeError`）→ 改三态判定；③`days=30` 右界随墙钟滑动
  ⇒ Q3-3 在生产库天然判不齐 → 冻结绝对窗 + 「不可判」第三态；④Q3-5 把 5 个异名同职实现记成
  「无实现」（漏咬/等价位混记，违 §二.5）→ `NEWENGINE_ALIAS` 三档，现读 **9 真无 / 4 异名待核**。
- **一处未验证归因已更正**：新引擎与 legacy 差 1 行，此前写成「窗口右界口径差」，按现证据是**采集漂移**
  （同窗口两次独立 `COUNT(*)` 实测差 1–3 行）。
- **验证**：新锁 `tests/test_vma_insights_window_echo.py` **10 条**，变异 `.qoder/tmp-c18-mut-window.py`
  **BITTEN=5/5**（含「只在 ok 时给窗」「降级路径不给窗」两把），基线「什么都不改」档 `10 passed`；
  本机全量 `1276 passed, 22 skipped` / `LOCAL_RC=0`；容器权威回归（`/tmp/c18snap20261005a`，352 文件）
  `GATE_RC=0`、`SUITE_RC=0`、**`1288 passed, 10 skipped in 200.80s`**；最终这支量具另跑一次 `GATE_RC=0`。
- **六条当前判定**：Q3-1/2/3/6 齐，**Q3-4/5 不齐**；不齐的三件事（出境加键、`entity_catalog` 组装法、
  异名等价的切换口径）**都不是 MA 能自判的**，已投
  `关键决策部/inbox/20261005-MA-裁5Q-B两格回执与四问-page_bytes漏投补登记.md`（Q-A/B/C/D 四问）。
  同件补登记 `71d18ee` 的出境键 `page_bytes`——那次是**漏投**，不是等裁。

### 6.11 裁「ma-insights 载荷」的落码口径（#42，`bad9ae4`，2026-10-05 登记）

裁定（`20261004` 系列 §五 Q2）：`insight_id` = MA **应发**的稳定身份，AF 的去重与回灌用它、**不用 `trace_id`**；
`conf` 可选（报了封顶 0.59）、`intent?` 登记。此前 MA 两个 id 都不发 ⇒ AF 侧无落点。

- **稳定性的来源**：`_insight_id(session_id, alert_type, day)` ——复用 MA **自己已经在用**的告警单飞身份
  再加日键。同房间同类洞察当天重复投递 ⇒ 同一枚 id，换天 ⇒ 新洞察。**故意不卷** `trace_id`/快照 URL/随机数
  （卷了就没有稳定身份）；去重键与分发单飞共用同一对变量，避免"被抑制的那条"和"去重的那条"不是同一个洞察。
- **`trace_id` 保持事件级**（每次现场生成），键序按 DCD 追认 `insight_id → trace_id`。
- **甲口径的两条「不发」要当决策读，不要当缺项**：`conf` 不发——陌生人告警的置信度没标定过，
  硬编一个数只会污染 AF 排序；`intent` 不发——当前无结构化意图，**可选字段不得变成消费方硬依赖**。
- **锁**：`tests/test_vma_dcd_20261004_insight_id.py` 6 条（墙钟已钉死，无跨午夜时刻依赖），
  其中一把是**载荷键集封闭**：契约行 ∪ 20261002「只加不减」白名单之外的新键即判红。
  变异 M1 摘 `insight_id` → 3 failed、M2 换 `uuid4().hex` → 1 failed、M3 日键换成 `trace_id` → 1 failed，
  **BITTEN=3/3**、基线 `6 passed`、`RESTORED=True`；本地定向 `103 passed, 1 skipped`。
- **这把封闭锁的实际射程（如实记）**：它守的是 `ma/insights` 这一条契约行的信封，
  **没有拦住** `71d18ee` 在 `list_device_health` 里加的 `page_bytes`——那个信封不在 `tests/contract/`
  覆盖面内。教训是**防线要连着覆盖面一起算**，否则"有锁"会被读成"有网"。`page_bytes` 的处置已随
  §6.10 那件申请正式请裁（Q-B）。

### 6.12 依赖 CVE 首批扫描：把 A5/A7 明记的「未扫」这格变成有读数（2026-10-05，#49）

缺口出处是三处自列：`元宝/A5_依赖供应链审计.md:166`、`A7_最终审计报告.md:121`、
`修复与核实/审计修复整合总报告_20261004.md` §5.1 —— 原文都是
「**不代表无 CVE，是未扫描**」（pip-audit 因 `python3-venv` 不可用起不来）。

- **为什么不走 pip-audit**（现读，不是推测）：生产容器**无出站网络**——
  `urlopen("https://pypi.org/status")` ⇒ `TimeoutError` / `NET_RC=1`（2026-10-05 03:39+08），
  装索引类工具无从谈起；本机侧多解释器机器明令禁 `pip install`。
  所以量具改成 **stdlib-only 打 OSV**：`scripts/dep_audit_cve.py`，在能出网那侧跑，
  输入是容器里 `pip freeze` 的快照（快照要带采集时间）。
- **量具自己先假绿过一次（这一格比读数重要，因为它决定读数可不可信）**：
  第一版只读 OSV 批量接口的 `matches` 键，而该接口把命中放在 `vulns` 键下 ⇒
  **连已知有洞的 `requests==2.19.1` 都报 `命中漏洞=0` 且 `RC=0`**，形状完全合法、零信息量。
  这正是本项目反复出现的那一族：键名对不上 ⇒ 静默归零。修法是三态分开：
  `{"vulns":[…]}`/`{"matches":[…]}` = 命中若干、`{}` = 该包**无已知漏洞**（合法 0 条）、
  非空却两键皆无 = **读数作废 `RC=2`**；出网失败与"响应条数≠查询条数"也一律 `RC=2`，
  绝不把「没扫成」回成 0。正式扫描前先打反例（`requests==2.19.1`），
  **不咬就把整份读数作废**；`--no-canary` 跳过时 0 命中只能印成「未证实」。
- **首批读数**（口径：OSV PyPI；快照 = 2026-10-05 03:35+08 容器 `pip freeze` **108 包**，含镜像内测试期依赖；
  **不含 Debian 基础镜像 OS 层**；PYSEC 与 GHSA 常是同一 CVE 的两份登记，条数≠漏洞个数）：
  `OSV_RC=0`、反例 `命中=10 条 -> 咬`、**命中 14 条 / 涉及 4 个包 / 无上游修复 8 条 / 未取详情 0 条**。

  | 包 | 最高档 | 上游修复 | 可达性（现读） |
  |---|---|---|---|
  | `chromadb==0.5.23` | **CRITICAL** | **无**（`0.4.17 → last_affected 1.5.9`） | **在场**：服务端镜像也是 `chromadb/chroma:0.5.23`，MA 走 `HttpClient`（`history.py:179`、`patterns.py:94`）；chroma 的 auth 类 env **键数=0**（无认证）；但 8000 **未发布**（`docker ps` 只有 `8000/tcp`），可达人群 = `memory-agent_default` 上 **4 个容器** |
  | `ecdsa==0.19.2` | HIGH | 无（上游原文：side-channel 不在项目范围，**no planned fix**） | **不可达**：`auth.py:167/205/215` 三处只允许 `HS256`，EC 签名不在调用链；反向依赖**无人依赖** |
  | `oauthlib==3.3.1` | MODERATE | 4.0.0 | **不可达**：MA 非 OAuth 提供方、`src/` 内 `grep oauthlib` 无命中；只有 `requests-oauthlib` 依赖它 |
  | `PyJWT==2.14.0` | MODERATE | 2.15.0 | 调用点不在本项目（`auth.py:14` 用 `from jose import jwt`，全仓无 `PyJWK`）；但 `mcp`/`oauthlib`/`redis` 三家依赖，升级要同窗验三家用例 |

- **锁与变异**：`tests/test_vma_dep_audit_cve.py` **14 条**，每条配「该不响」对偶档
  （干净包必须 0 条、`{}` 不判红、反例咬了才允许出报告）；
  变异 `.qoder/tmp-c22-mut-dep.py` **BITTEN=4/4**（M1 只读 matches / M2 反例不咬也出报告 /
  M3 出网失败回 0 / M4 丢弃 `database_specific` 档位），每步 `RESTORED=True`、末态字节一致，
  基线「什么都不改」档 `14 passed`。
- **权威回归**（容器工作区快照 `/tmp/c22snap20261005a`，**354 文件**，批次回收 2026-10-05 03:46+08）：
  pyflakes **`GATE_RC=0`**（当前 0 / 基线 0 / 新增 0 / 已修 0）、全量 **`SUITE_RC=0`、`1302 passed, 10 skipped in 226.01s`**；
  本机同批 **`1290 passed, 22 skipped`** —— 两侧**总案例数都是 1312**（差的 12 条是容器没装
  hmmlearn/pm4py/river，记成 skip），所以新锁不是只在本机绿。
  另量「量具在生产容器里跑不跑得起来」：容器内 `--freeze /dev/null` ⇒ **`IN_CONTAINER_RC=2`**（空快照判红）。
  `.gates-baseline.txt` 一字未改。
- **请裁三格**（`关键决策部/inbox/20261005-MA-依赖CVE首批读数与三格定向请求.md`）：
  Q1 chroma 无认证这格现在缓不缓（MA 倾向 **乙** = 只做网络隔离，把 chroma/redis 拆进 MA 专用内部网，
  不重烤镜像）；Q2 chroma 要不要升 1.x（MA 倾向 **甲** = 列入 vMA-1.4，**本窗不升**：
  升级牵动持久化格式与**现存 1024 维集合迁移**，而向量面维度那一格还没裁完，两件事别叠在同一次停机窗）；
  Q3 三格"调用链不可达"的包要不要仍做版本动作（MA 倾向 **甲** = 按不可达登记、随每次扫描复测）。
  不可自判的原因逐格写在件里；**本件不动交付面**（镜像重烤未获授权）。
- **窗内验收读数**（别拿"配了"当"生效"）：`docker network inspect` 的可达容器数从 **4 → 2**
  才是 Q1 乙的唯一有效读数；重跑量具时快照**必须重取**，不能拿本件 03:35 那份。

### 6.13 审计 A8 / A3 两批核销（#50 + #51，2026-10-05）——细节在审计台账，这里只登记口径

两份都不是 DCD 裁定的执行，而是审计目录新增报告的核销，故只留三格可复用的口径：

1. **「返回形状合法、内部把口径悄悄改小」这一族缺陷也长在审计侧**：
   A8 报的 `12 failed, 7 passed` 与我们表头的「锁缺陷条数 / 对照条数」**数字完全一样**——
   它把按角色分类的列读成了运行结果。三棵树实测（HEAD `22 passed`、`ec959b1` `19 passed`、
   整份源码退回 `83d233b^` **`11 failed / 8 passed`**）证明改前会真红、对 HEAD 不成立。
   ⇒ 以后所有计数列一律在表头写死"这不是失败数"。（审计 §三十三）
2. **一句没有位置的审计结论要先变成量具再动手**：A3 §八「少量未卸载的同步 I/O」
   落成 `scripts/scan_unloaded_async_io.py`（自证正 2 反 0，本机与容器 Python 3.11 两侧同读数）。
   量具把 DB/HTTP 层判空，同时**自己承认量不到真身**（改前 `auth_routes.py` 单扫 `HITS=0`）——
   真身是人读出来的：`AuthMiddleware` 每个非公开请求在事件循环上读一遍账号文件，
   Basic 分支还要跑 bcrypt（容器实测 `checkpw` p50 **369ms**，`cost=12` 就是本应用自己的因子）。
   ⇒ 「量具读数 = 0」与"这件事没了"是两回事，两句都要写。（审计 §三十四）
3. **卸载不是免费的，判"该不该卸载"要看有没有拆散读改写**：
   三条热路径卸载；`register` 与三条管理路径**故意不卸载**——
   实测把 `has_users()` 挪进线程后，引导期两个匿名并发注册从"第二个 403"变成两个都建号。
   生产仍未重启（镜像重烤未获授权），生效窗口随下一次合并停机窗、与裁3/裁5 同批。

权威门（快照 `/tmp/c28snap20261005a`，358 文件；c27 之后注释又动过一次，故按"提交的树必须是被测过的树"重放）：
pyflakes `GATE_RC=0`、全量 `SUITE_RC=0` **`1322 passed, 10 skipped in 203.10s`**、
定向 `TARGETED_RC=0` `20 passed`、鉴权链 `AUTHCHAIN_RC=0` `47 passed`，
本机同批 `1310 passed, 22 skipped` ⇒ 两侧总数都是 **1332**（= 上批 1325 + 本批 7 条心跳锁）；
`.gates-baseline.txt` 一字未改。
提交：`49c2989`（#50）+ 本批 #51 一条，均已推 GitHub。

### 6.14 审计 A2/A7「13 项仍为静态结论」首批核销（#52，2026-10-05）——P2-1 zip 配对

同一族的第三格口径（细节见审计台账 §三十五）：

1. **审计自己写的"待验证"表就是施工单**。A2 §五 对 P2-1 的原话是"静态可确认，需构造长度不等的真实输入"
   ——这一句同时给了方法（造 ragged 输入）和验收（要有改前/改后对读数），不必等人派活。
2. **"给 X 全加上一遍"这类整批建议必须先分诊，否则修复本身交付 6 个必然崩溃点**。
   全仓 19 个 `zip` 站点里 5 处是滑窗 `zip(x, x[1:])`（长度天生差 1）、1 处是字符串前缀比对
   （"比到短的尽头即止"就是语义）⇒ 13 处判红、5 处保持滑窗、1 处加 `# zip-pair-ok:` 说明。
   分诊结果由 `scripts/scan_zip_pairing.py` 强制（`unclassified` 必须为 0，
   `CONFLICT:window+strict` / `CONFLICT:marked+strict` 双红灯），不靠散文约定。
3. **"只在缺依赖的机器上错"的分支，加多少用例都抓不到**。`_cosine_similarity` 同输入两条路径两个答案：
   numpy 在场报 `shapes (3,) and (2,) not aligned`，缺席**静默给 0.5976**。
   用 `sys.modules["numpy"]=None` 强制走退路才现形 ⇒ 本批把两路统一到口径命名的错误，
   并把"断言要断到消息"再次验证：M3 第一版只写 `pytest.raises(ValueError)`，
   删掉自写守卫仍被 numpy 自己的异常和退路 `strict=True` 顶上，`BITTEN=False`；
   收紧成 `match=r"维度不一致"` 才咬。

权威门（快照 `/tmp/c29snap20261005a`，**360 文件**，基线 HEAD `8a78485`）：
pyflakes `GATE_RC=0`（`当前 0 / 基线 0 / 新增 0 / 已修 0`，`.gates-baseline.txt` 一字未改）、
全量 `SUITE_RC=0` **`1339 passed, 10 skipped in 207.54s`**、新锁 `TARGETED_RC=0` `17 passed`、
被改模块既有锁 `TOUCHED_RC=0` `66 passed, 6 skipped`、容器量具 `SCAN_RC=0`
（`total_zip=19 marked=1 strict=13 window=5` + `SELFTEST_OK`）、容器探针 `PROBE_RC=0`
（脚本 sha 前 16 位 `9f681245eb0ac2a9`，与本机同值）；
本机同批 `1327 passed, 22 skipped` ⇒ 两侧总数都是 **1349**（= 上批 1332 + 本批 17 条新锁）。
生产仍未重启（镜像重烤未获授权），生效窗口随下一次合并停机窗、与裁3/裁5 同批。

---

### 6.15 A2/A7 静态结论核销第二批（#53，2026-10-05）——days 极值 / 连接池 / 失联路由 / `_degrade` 空列表

细节全在审计台账 §三十六，这里只登记**口径与五条会被复用的规矩**：

1. **"给某个参数收口"的正确单位是站点语义，不是函数名**。同一族 `timedelta(days=…)` 99 个站点，
   查询窗口收成 `[1, 3650]`，保留期/TTL 收成 `[0, 200000]`（≈547 年，只为把算式留在 `datetime` 域内）。
   把 3650 套到保留期上 = 把"永久保留"改成"删掉 10 年前的数据"。本批我自己就违反过一次
   （`store.purge_mcp_audit`），现算的差别是 `cutoff=2016-10-07` 会删掉 2000 年那条审计；
   生产调用点传字面量 30 所以今天不响——**"今天不响"不是豁免理由，是缺陷的潜伏期**。
2. **本机绿不等于交付面绿**。`clamp_days` 第一版写了 `int.is_integer()`（3.12+ 才有），
   而"越界浮点被夹到整数边界"时 `min/max` 交回来的是 int 边界本身 ⇒ 本机 3.13 全绿、
   容器 3.11 `1 failed`。这条只有容器权威门能抓，因此它不是"多跑一遍"，是**唯一能抓的那一遍**。
3. **量具替代对表**。`scripts/scan_day_bounds.py`（终树 18 正 11 反自证；run4 时是 8 正 8 反，
   本批按规矩 5 扩了 13 条覆盖豁免档）与
   `scripts/scan_route_mount.py`（195 个 handler 形状函数：190 挂载 / 1 别处引用 / 4 失联）。
   失联那 4 条不是"忘了挂"：`add/update/delete` 直接落 `active_rules`，而唯一生产写入点是
   R3 通道的 promote（`rule_lifecycle.py:320`）⇒ 挂载等于开一条绕过四条红线的旁路，呈 DCD。
   量具的红读数就是呈件未结的在盘凭据，不在 CI 里判失败。
4. **归属标记的判据只能是"值的性质"，不能是"待裁的状态"**。我给 `learning_api.py` 两处写的
   `# day-ok: 死代码——下架与否见 DCD 呈件` 就是 category error：模块是不是死的是**待裁定**的，
   而标记一存在，量具就永久判绿——DCD 若裁定"接回去"，缺陷立刻复活且门不会响。
   正确理由现成在上一行（`Query(default=7, ge=1, le=90)` 已声明收敛）。
   规矩：**写不出"值为什么不来自外部"，就补 clamp，不要用标记消音。**
5. **豁免档的前提必须双向可证**。`scan_day_bounds.py` 的 `config` 档原判据是"变量名叫 `config`/`cfg`"，
   一条前提就放行整格——于是 `vision_snapshot_retention_days`（在 `config_routes.WRITABLE_FIELDS` 里，
   设置页一个 number 输入框就能写）和一个根本不是应用配置的 dataclass 字段
   （`LearningConfig.window_days`）一起免检。**"不在可写白名单"≠"不可写"，除非先证明它真是应用配置的字段**。
   现在两把前提都要过：键 ∈ `config.Config` 注解字段 **且** 键 ∉ `WRITABLE_FIELDS`，
   两张表都从真源 AST 解析（复制的那份会在下一个人加键时静默失效）；
   读不到表时按保守档处理（整册 `external`，宁可红），**信息缺失不许自动变成豁免**。
   规矩：**每加一档豁免，就同时给这一档一条"前提不成立时必须红"的自检样本**（本批 18 正 11 反里
   `writable/*`、`app-config/*`、`unreadable/*`、`real-source/*` 四组就是干这个的）。

顺带核销：第十四轮 P3-7（`_degrade` 对 `list` 工厂不补 error）改 `DegradedList` 子类留痕，
变异自证两次各咬 2 条、恢复后字节一致；第一轮 P2-3 六处逐条现读，`mqtt_bridge.py:55` 判假阳性，
其余四格（`if True:` / `base_days` 死形参 / `test_rule` 与其死参 / 两条 `CAPABILITY_*` 别名）
连同 `learning_*` 八模块（1526 行、零引用、裸绝对导入）与 `insights/persona.py` 一起呈 DCD
（`20261005-MA-主动规则CRUD挂载与死代码六处处置-决策申请.md` Q1~Q4，MA 每格给倾向）。
另登记一件编号陷阱："P2-3"在四份报告里指四件不同的事，此后引用须带"报告+行号"。

**权威门两侧对得上（终树容器快照 `/tmp/c31snap20261005a`，365 文件，基线 HEAD `2ee9d77`，run4）**：
`GATE_RC=0`（pyflakes 0/0/0/0，`.gates-baseline.txt` 一字未改）/ 容器 `SUITE_RC=0`
`1375 passed, 10 skipped in 209.45s`（`Python 3.11.16`）/ 本机 `LOCAL_SUITE_RC=0`
`1363 passed, 22 skipped in 121.72s` ⇒ **两侧总数同为 1385**（上批 1349 + 本批 36）；
`TARGETED_RC=0` **36 passed**（两套新锁全含）、`TOUCHED_RC=0` 111 passed、
`DAYSCAN_RC=0`（自证 8 正 8 反；`guard_bounded 3→41`、`guard_lo_only 18→0`、`guard_unguarded 78→58`、
`marked 0→4`，站点总数 99 不变）、`ROUTESCAN_RC=0`（`195/190/1/4`，其中 `SCAN_RC=1` 是设计：
DCD 未结前持续判红）、`PROBE_RC=0`（脚本 sha16 `4ff04e5ff1d947a4`，容器内 `sha256sum` 与本机同值）、
本机 `GATES_REQUIRE=1` 门禁自测 `2 passed`（`QG_RC=0`）。
**四轮容器的读数不能混着引用**：run1 `SUITE_RC=1`（§6.15 规矩 2 的 3.11 崩溃）、run2 中间态（1369+10）、
run3 代码终树但定向集只 30 条、**run4 才是交付面**。登记门读数要带 run 号。
（**注意**：run1~run4 是 §6.15 第一批；同日第二批的交付面是 §6.16 的 run5，两侧总数 1389、TARGETED 40、
TOUCHED 141。上面这一整段保留作第一批的过程证据，不要拿它当"当前读数"引用。）

**本机覆盖率的实测量推翻了审计自己的优先级**（`coverage 7.16.1`，全量同一轮取的）：
第十五轮 §九 说 `identity_fusion` / `behavior_predictor` / `api/behavior_routes` 是"0%~10% 覆盖"，
现读 **84% / 84% / 10%** ⇒ 前两格已被 #46/#48 两批的补测抬起来，第三格仍成立（且失联的 4 条 handler 就在里面）。
这条的用处不是驳审计，而是**给下一批选靶**：静态扫描的边际产出审计自己已判为最低，
存量应转向"`behavior_routes` 交付面 + 真实环境长跑"，而不是再开一轮同类扫描。

### 6.16 同族第三处：量具自己的假豁免（#53 第二批，2026-10-05，容器 run5）

细节全在审计台账 §三十六 §九。这里只登记口径，因为这条会被复用的概率最高：

**第五步：豁免档的"前提"必须是可双向证明的，一跳豁免等于没审**。§6.15 立了"值来自外部才收口"之后，
`scan_day_bounds.py` 把"键名出现在 `config.*` 上"当成"运维受控"的**充分**条件——这一个跳板放过了两处：
1. **应用配置里有一批键是 HTTP 可写的**（`api/config_routes.py:20` 的 `WRITABLE_FIELDS`，现读 77 键，
   含 `vision_snapshot_retention_days`），前端对应一个无 `min/max` 的 number 输入框 ⇒
   周期任务里 `now_local(8) - timedelta(days=10**6)` 直接 `OverflowError`。
   **"来自 config"≠"运维侧受控"**，这类键的口径与 MCP 形参完全同级。
2. **叫 `config` 的形参未必是应用配置**（`learning_api.run_learning_cycle(store, config)` 收模块自己的
   dataclass）⇒ "不在可写白名单"不能当成"不可写"，除非先证明这个键真是 `config.Config` 的字段。

改法（也是往后加任何豁免档的模板）：两张表**都从真源解析、不复制清单**
（`load_writable_config_keys()` 括号配平取 `WRITABLE_FIELDS`；`load_app_config_keys()` 走 AST 取
`class Config` 的注解字段）；归属按「可写键 → external」「键不在应用配置字段表 → external」「其余才允许
config」两跳判；**读不到表时不许自动豁免，整册按 external 判红**（信息缺失不能变成绿灯）；
每条新豁免档必须配一个"前提不成立时判红"的自证样本（自证由 8 正 8 反扩到 **18 正 11 反**）；
detail 拆 `attr:`（真键名）/ `var:`（携带键名的局部变量），免得 `keep` 这种变量名被当成配置键。

顺带一条**取数命令自己的规矩**：这批的门新加一格 `SOURCES_RC`（`wc -l` + `grep -c WRITABLE_FIELDS`），
因为量具现在依赖两张真源表在场——少了这一格，"快照漏文件"会被读成"代码判红"，
把自己的取数故障当成缺陷（run4 那次 `PROBESCRIPT_SCP_RC=255` 同一类错，那次是 `sed` 整体改名写坏了路径，
所以 run5 的脚本是手写的、不从旧脚本改名）。

**权威门（终树容器快照 `/tmp/c32snap20261005a`，365 文件，基线 HEAD `2ee9d77`，run5 = 本批交付面）**：
`SOURCES_RC=0`（`config_routes.py 516` / `config.py 544` / `grep -c WRITABLE_FIELDS=2`）、
`GATE_RC=0`（pyflakes 0/0/0/0，`.gates-baseline.txt` 一字未改）、容器 `SUITE_RC=0`
**1379 passed, 10 skipped in 233.86s**（`Python 3.11.16`；skip 清单与 run4 逐字相同 ⇒ 差异仍是环境）、
本机 `1367 passed, 22 skipped` ⇒ **两侧总数同为 1389**（run4 的 1385 + 本批新锁 4）；
`TARGETED_RC=0` **40 passed**（36→40）、`TOUCHED_RC=0` **141 passed**
（**口径**：run5 touched 集 12 个文件、run4 是 8 个，两个数不是同一把尺，不许相减当增量）、
`DAYSCAN_RC=0`（自证 **18 正 11 反 0 漏咬**；`external 45→48`、`config 5→2`、`bounded 41→44`、
`unguarded 58→55`、`marked 4`、站点总数 99 不变；**这四个数是一件事**：恰好 3 条由 config 改判 external
且同一批转 bounded；容器与本机该行逐字相同）、`ROUTESCAN_RC=0`（`195/190/1/4`，`SCAN_RC=1` 仍是设计红，
四条行号与 run4 相同 ⇒ 本批没碰路由挂载）、`CONTAINER_BATCH_RC=0`。
**run5 没有 `PROBE` 这一格**（本批唯一运行时改动已被 3 条 vision 新锁按 cutoff 字符串量过，池/淘汰口径未动）
⇒ 引用 `PROBE_RC=0` 时只能带 run4。

**没做的一件事，理由是它不该由我单方定**：`WRITABLE_FIELDS` 的**写入侧**校验（PUT 时就拒极大值）与
前端 `min/max` 都没加。消费侧已不可能崩、也不可能悄悄改小；而"保留期的合法区间是多少"
（0=关闭、极大=永久保留，中间有没有产品意义上的上限）是**口径决定**，已作为 **Q5** 补投进
`20261005-MA-主动规则CRUD挂载与死代码六处处置-决策申请.md` §八（甲=写入侧按键给区间 / 乙=只夹紧消费侧（当前态）/
丙=只给"会删数据"的那几把键加区间；**MA 倾向丙，但区间数字请 DCD/SP 给**）。前端 `min/max` 同理——API 直写会绕过它，
单加只是装饰，要加就随甲/丙一起加。

### 6.17 DCD 20261005 §二.2 四件落码（2026-10-05，容器 run6，#55 / #56）

裁定原文（`关键决策部/decisions/20261005-AF用户WebUI与MA四件与CVE-裁定.md`，按新纪律一律「报告+行号」引用）：
Q1=**:53**（乙+丁分两步：只读面先挂，写 CRUD 挂进 R3 通道，`add` 落 `candidate_rules` 而非 `active_rules`）、
Q2=**:54**（乙：`learning_*` 八模块移出 `src/` 存档 `attic/learning/`）、Q3=**:55**（乙：`insights/persona.py`
保留 + 标 `deprecated` + 随 Q-B 接回）、Q4=**:56**（乙：实现 `ignore_trigger` 语义并给可达入口；`base_days` 死参删；
`if True:` 与两条 `CAPABILITY_*` 授权 MA 机械自办）。

**Q1 的路径 deviation（先登记，别让人以为裁定没执行）**：裁定给的字面是 `GET /api/behaviors/rules`，
但那条已经被 `behaviors_rules` 占着，而它读的是 `perception_rules.STATIC_RULES`——**另一个数据源**。
同一 URL 两种含义是这一轮最贵的一类缺陷，所以只读面另起一条**路径=数据来源**的
`GET /api/behaviors/active-rules`（`api/behavior_routes.py:1117`，handler `behaviors_list_rules:370`，
deviation 就写在 docstring 里）。写侧三条按裁定挂进通道：
`:1094` `POST /api/behaviors/candidate-rules/add`、`:1095` `PUT .../{rule_id}`、`:1097` `DELETE .../{rule_id}`；
Q4 的入口是 `:1127` `POST /api/behaviors/rule-channel/test-rule`（handler `behaviors_test_rule:486`）。

**四红线一条没放宽**：`rule_lifecycle.manual_add:487` 落 `candidate_rules`，与机器建议同类同规——
一样要 `accepted` + `user_confirmed=1`、一样要跨 `MIN_EVIDENCE` 个独立自然日、一样先 `dry_run` 满观察期、
一样逐步进 `rule_lifecycle_audit`。人工身份只换「建议是谁提的」这一格，不换红线豁免。
返回值永远带 `gate`（`eligibility()` 的逐条判据）：录规则时最坑的不是被拒，而是「录成功了，
三个月后才发现它永远晋升不了」。`manual_edit:545` 两道拒绝（`source != manual` 不许洗机器建议的原始
steps/evidence；`status == promoted` 不许让候选行与引擎行分叉）；`manual_withdraw:604` 只删引擎前的状态
（`store.py:45` `CANDIDATE_PRE_ENGINE` 白名单进 SQL 的 WHERE，不在词表内直接拒）。
同名撞车单独拦（`store.get_candidate_rule_by_name:2798`）：人工录一条与机器建议同名的规则，
`upsert_candidate_rule` 会按 name 判重、把建议的 steps/evidence 原地洗掉，而审计里那条还写着"机器建议"。

**门自己响了一次，读数当场清掉而不是写进基线**：三条 `manual_*` 首版都写了字面 ok=True，
被 homesdk 的 `fake-ok-const` 咬了三条。本仓的正当满足方式有两种真源：`service_tokens.py:240` 从**回读**派生，
`insights/api.py:510` 从**不变量**派生。三条全改成回读派生（`rule_lifecycle.py:529` / `:593` / `:626`），
并且**回读为空时连审计都不记**——留痕里那条"已录入"会比真相更误导人。
`.gates-baseline.txt` 一字未改（run6 `GATE_RC=0`，pyflakes 0/0/0/0）。

**Q2 搬家改的是量具在册人口，不是缺陷数——两个口径必须成对登记**：
day-bounds 站点 99→95（4 条随 `learning_*` 进 attic，attic 侧单独扫 total=4 external=4 bounded=2 marked=2）；
zip 配对 window 5→4（`attic/learning/learning_feedback.py:185`）。
**"册子变薄"不许读成"缺陷清零"**，所以补了两条守恒锁而不是放松断言：
`test_moved_zip_site_is_in_the_attic_not_vanished`（src + attic == 5，attic 侧恰 1）与
`test_learning_sites_moved_to_attic_not_vanished`。attic 不参与门禁口径由读数自证：
`GATE_MENTIONS_ATTIC=0`、`SRC_LEARNING=0 / ATTIC_LEARNING=8`。

**Q4 的 `test_rule` 是独立回放，不是"在线上引擎上跑一遍"**：`rule_engine.py:145`，
判定链与线上一致（strategy gate → cooldown → mark 的顺序），但**从不碰** `self._event_windows` /
`self._cooldown_until`、从不读 `rule_trigger_history`；时间域统一走 `_replay_time:296`
（缺时间戳 ⇒ `would_trigger=None`，不退回墙钟凭空凑窗）。`_replay_verdict:283` 把 expected 三态收成字符串。
构不出触发子的候选返回 `trigger_check={"mode":"not_evaluated","reason":"condition_unbuildable"}`，
不假装"不触发"。

**一条交付纪律违规，自报**：`rule_engine.py` 工作区是 CRLF（CR=1139）而 `git show HEAD:` 是 LF——
`core.autocrlf=true` 让 diff 看起来干净，但 §六.9 要求工作区 CR=0；它同时是上一轮 `M3: PATCH_NOT_FOUND`
的真身（**量具故障被读成了"这处语义改不动"**，字节级 patcher 不认行尾）。按字节重写归一
（53612→52473，CR=0，numstat 仍 120/8）。往后凡有字节级量具/变异脚本，先确认目标文件行尾。

**权威门（终树容器快照 `/tmp/c35snap20261005c`，366 文件含 8 个 attic，基线 HEAD `3826aea`，run6）**：
`SOURCES_RC=0`（`config_routes.py 516` / `config.py 544` / `test_vma_dcd_20261005_rules.py 492` /
`grep -c WRITABLE_FIELDS=2`）、`GATE_RC=0` + `GATE_MENTIONS_ATTIC=0`、
容器 `SUITE_RC=0` **1413 passed, 10 skipped in 249.80s**（`Python 3.11.16`；skip 十条与 run5 逐字相同 ⇒ 环境差异），
本机 `1401 passed, 22 skipped` ⇒ **两侧总数同为 1423**（run5 的 1389 + 本批净新锁 34：新文件 32 + p32 1 + zip 1）；
`TARGETED_RC=0` **85 passed**（40→85）、`TOUCHED_RC=0` **183 passed**
（**口径**：run6 touched 集与 run5 是两套 12 文件，两个数不是同一把尺，不许相减当增量）；
`DAYSCAN_RC=0`（src 侧自证 **18 正 11 反 0 漏咬** + `SELFTEST_REALSOURCE_KEYS writable=77 app_config_fields=167
writable_not_in_config=0`；src total=95 external=44 config=2 bounded=42 unguarded=53 marked=2，
SCAN_RC=0「每个 timedelta(days=) 站点都有归属」）、`ATTICSCAN_RC=0`；
`ROUTESCAN_RC=0`（handler_shaped=196 mounted=195 parse_fail=0 referenced=1 unmounted=0 problems=0，
SCAN_RC=0 ⇒ run4/run5 那条 195/190/unmounted=4 的**设计红已转绿**，P3-1 就此核销）；
变异 M1–M5 **全部 RC=1 -> 咬住了**、`restored_identical=OK` ×5、`MUTATION_BAD=0`、`MUT_RC=0`；
`REMOTE_BATCH_RC=0`、`CONTAINER_BATCH_RC=0`。
**快照 `c35snap20261005a` / `b` 的读数作废**（tar 之后又有两次就地改动：扫描器注释指向 attic、
`rule_engine.py` 行尾归一），run6 只在 `c` 上跑过。run6 无 `PROBE` 格（池/淘汰口径未动）。

**观察项（不是缺陷，登记给下一轮）**：`engine.update_rule` / `engine.delete_rule` 现在没有 HTTP 调用方
（写侧走通道，不直连引擎）；这两个方法留着是有理由的（`revoke` 路径要用），但**「没有 HTTP 调用方」不等于
「死代码」**，不许下一轮顺手清掉。Q5（WRITABLE_FIELDS 写入侧区间）仍待裁，见 §6.16 末段。

### 6.18 DCD 20261005 §二.1 乙′ 落码：日配额从「每天只剩凌晨」改到「每小时都有货」（2026-10-05/06，容器 run9 / run10，任务表 #54）

裁定原文（`关键决策部/decisions/20261005-AF用户WebUI与MA四件与CVE-裁定.md`，按「报告+行号」引用）：
**:34** 标题「乙′（分层但不算窗口函数）」、**:36** 形状「每天 24 个带 `LIMIT` 的子查询 `UNION ALL`（一条语句、
每小时真 LIMIT），k=`967//24≈40`」、**:39** 现象真实（按天分摊 + `ORDER BY ts ASC` ⇒ 24h 真值只剩
1.39/1.25/1.16/0.39h 可见）、**:40** 改前形状同样残缺、**:41** 判据①「任一日 20:00–23:00 必须有事件返回」
＋判据②「约束② 同型成对读数（改前/改后各一次 + 每日可见时段表）」、**:43** 口径重述「总读取量硬上限」→
「**返回行数硬上限**」、**:45** 驳回甲/驳回丙。k=40 不是估的：生产 `scan_limit=30000` ÷ 31 日窗 ⇒ 日配额 967 ⇒
`967//24=40`，与裁定给的字面同一格。

**落码形状是三层，不是一条 UNION 就交差**（`insights/repository.py:262` `load_events` 体内，
`_hour_bounds:335` / `_hour_statement:370` / `_plain_day_rows:403` / `_scan_day:409`）：
① 第一波 24 格各取 `配额+1` 行（**多要一行**：按配额整数取，「本来只有 k 条」和「被砍到 k 条」回来的是同一个
形状，截断位就恒真——这条是对照组抓出来的假红，见 §6.18 末段变异 M4）；
② 轮转分配把日预算**用满**（小时配额只是**公平份额**，不是天花板：一天只集中在两三个小时是常态，
24 格各砍 40 条只交回 160 条 ⇒ 剩下 807 条预算白放着，语义侧证据凭空少 6 倍）；
③ 只在「手上的分完了、预算还没满」时走**一轮**缺口补读（`LIMIT rest+1 OFFSET 手上行数`，分支内 `ORDER BY ts, id`
全序 ⇒ OFFSET 不错位）。生产日量下第一波就把日配额占满 ⇒ **一天仍是一条语句**；
`day_limit < 24` 的极小预算退回整日一条查询（分层只会把「按 ts 的前缀」换成「按小时升序的前缀」，既不填满也不见夜间）。

**本批真正花时间的第二改法：分支里的 WHERE 换成「夹紧后的 `ts` 半开区间」**。
run7 交出过一份**有效但慢 25.82×** 的读数（同一生产库、同一 30 天窗：前缀 743.2ms → 分层 19186.8ms）。
把这条代价追到规划器选错索引，而不是当成乙′ 的必付成本：

| 分支 WHERE 的形状 | 规划器选择 | 一天耗时（现网实测） |
| --- | --- | --- |
| `day = ?`（裁定 :36 的字面形状） | `idx_events_day` + `USE TEMP B-TREE FOR ORDER BY` ⇒ 每格重扫整天 ×24 | **408.2ms** |
| `day = ?` 再合取整窗 `ts BETWEEN` | `idx_events_ts` 的范围被撑回整窗 | **6109.7ms** |
| 只留夹紧后的 `ts >= ? AND ts < ?`（本批） | `idx_events_ts (ts>? AND ts<?)` | **18.5ms** |

三档**返回的是同一批行**，所以这不是"换个写法赌一把"。等价性的地基两条，都在现网量过：
`day != substr(ts,1,10)` 在 1,010,348 行里 **0** 行；每条 `events.ts` 都是 19 字符、`ts LIKE '%.%'` = 0 行 ⇒
`ts <= 终点` 与 `ts < 终点+1s` 等价（`_to_iso` 用 `timespec="seconds"`，窗口边界本来就是整秒）。
`day` 谓词由此**退出取数分支**（分支绑定数 9→4），`day BETWEEN` 留在整日一条查询和窗口解析里。
上界一律**朝外一档**夹紧：宁可多带一档也不漏一行——`'...T09:59:59'` 那种闭区间会把带毫秒的 `09:59:59.500`
同时挡在两格之外，那种行是**静默消失**，不是报错。

**判据口径（第三代）**：`truncated` 只认**已证明的丢失**——手上还有没交出的行 / 有格补读一轮之后仍满额未探底 /
有天根本没扫到。「砍满配额」是嫌疑不是证据，M3 把它当证据就被咬住。门面与 `service.py` 的同源句子已改口径。

**权威门（run9，快照 `/tmp/c37snap20261005c`，368 文件含 9 个 attic，基线 HEAD `d156057`）**：
`SOURCES_RC=0` 且**逐条等于本机基线**（`SCAN_DAY=3 HOUR_BUCKET=3 OFFSET_BIND=1 TS_GE=1 SCAN_HOURS=4
WINDOW_HI=5 SUBSTR_WHOLE=7 SUBSTR_IN_HOUR_STMT=0 BRANCH_FROM_FILT=1 BRANCH_FROM_WHERE=0 HOUR_STMT_LINES=32
TEST_DEFS=13 PROBE_A5=1`，`wc=919/893/1340/554/216 = 3922`）——`BRANCH_FROM_FILT/WHERE` 这一对是替换掉的假量具：
上一版 `grep -c 'day = ?'` 把注释里的字也数进去了，量出 1 当"新代码没生效"。
`GATE_RC=0`（pyflakes 当前 0 / 基线 0 / 新增 0 / 已修 0，`.gates-baseline.txt` 一字未改，`GATE_MENTIONS_ATTIC=0`）。
`SUITE_RC=1`：**1 failed, 1419 passed, 10 skipped in 280.44s**（容器 `Python 3.11.16`）；
`TARGETED_RC=1` **1 failed, 68 passed**；`TOUCHED_RC=1` **1 failed, 276 passed, 3 skipped**。
**三条红是同一格、且不是本批带来的**：`tests/test_vma_p32_day_bounds.py:532` 报
`UNGUARDED-EXTERNAL src/memory_agent/service_tokens.py:434 days=self.DUAL_TRACK_DAYS (param-tainted:self)`，
`DAYSCAN_RC=1` 同一条——HEAD `d156057`（#61 那次提交）落在 `main` 上就是红的，见下面「量具的 self 过度污染」。
`ROUTESCAN_RC=0`（handler_shaped=197 mounted=196 parse_fail=0 referenced=1 unmounted=0 problems=0）。
变异 10/10 **逐个 RC=1 -> 咬住了**、`restored_identical=OK`、`MUTATION_BAD=0`、`MUT_RC=0`
（M1 5 条 / M2 6 / M3 5 / M4 8 / M5 3 / M6 2 / M7 4 / M8 1 / M9 1 / M10 24 失败）。
本机同一棵树：`1 failed, 1407 passed, 22 skipped in 1027.44s`，`q62` 单文件 **13 passed**。

**约束② 的同型成对读数（run9 PROBE，现网 `/data/memory_agent.db`，全程 SELECT）**：
A1 分母——窗口 2026-09-06→2026-10-06（31 日键）真值 **950113** 条，`scan_limit=30000` ⇒ 日配额 967、份额 40，
逐日真值 min=2 / 中位=33450 / max=44573（日配额只占日均真值 **0.0316**）。
A2 改前单条 `LIMIT`：**10071.3ms**、30000 行、**只覆盖 1 个日键**（2026-09-06）。
A3 乙′ 分层：三次 **[14159.6, 2503.5, 3340.4]ms**（中位 **3340.4**）、29012 行、**31 个日键全覆盖**、
`truncated=True`、每个日键实返 min=2 max=967 ⇒ **有配额的日用满了它**；**耗时比（改后中位/改前）=0.33**。
A5 判据①/②：按天前缀 [46175.8, 63386.7, 22004.5]ms 中位 **46175.8**、每日可见小时数中位 **1**、
**夜间出场 0/31 天**；按小时分层中位 **3340.4ms**、每日可见小时数中位 **24**、**夜间出场 28/31 天**
（2026-10-06 是进行中的一日，只到 00:38 ⇒ 那一格 1 小时属预期）；**比值 0.07**。
A4 每日可见跨度：最后 5 天里实返覆盖 **23.0–23.1h**（真值 24h），缺的是当日最后半小时的尾部，不是整段夜间。
B 组（裁1/裁4 Q2=甲 的 64KB 页预算，同库同批行）：lean **133560→65518** 字节（500→236 行）、
full **153271→65320**（500→194 行），改后仍 ≤64KB。

> **跨 run 的绝对耗时不可比，只有同 run 的成对比值可比**（这条口径必须跟着读数走）：
> 同一把尺、未改代码的两个量具在 run7→run9 之间自己漂了——A2 单条 `LIMIT` 408.1ms → 10071.3ms（24.7×），
> A5 的前缀 743.2ms → 46175.8ms（62×）。容器在 08:01 被重建过（CVE 网络隔离那次 recreate），
> OS page cache 从零开始，而 `day = ?` + `ORDER BY ts` 这种"整天重扫"形状最吃冷盘。
> 分层这一侧同库同窗从 19186.8ms 降到 3340.4ms 是**改法的效果**（18.5ms/天那条索引路径），
> 与"缓存变热"两件事必须分开记账。判据按裁定 :41 只取**同 run 成对**：分层 0.07×前缀、0.33×单条 ⇒
> 乙′ 不再带着 25× 的代价，**不需要为这一条再开裁定**。
> **（run9 是冷盘读数，这两个比值不能读成"比旧形状还快"；同 run 成对的正式基准改用 §6.18.1 的 run10：
> 0.67×改前单条 `LIMIT`、3.28×前缀量具。本段保留是为了让"读数带 run 号"这条口径可追溯，不作废数字本身。）**

**run8 整跑作废（量具自己先踩了一次假绿）**：`/tmp/pylibs`（pytest/pyflakes 的家）在**容器可写层**里，
08:01 的 recreate 把它清空了 ⇒ `SUITE/TARGETED/TOUCHED` 三格读的是 `No module named pytest`，
而十条变异因为「RC 非 0 但没有 FAILED 行」全被打印成"咬住了"。戳穿它的是 `MUTATION_BAD=10` 这一格
（脚本里那条"没 FAILED 行的非零 RC 也算缺陷"的断言）。run9 起，远端脚本第一格就量
`PYTEST / PYFLAKES` 的版本（`TOOLCHAIN_RC`），量不到就整跑作废——交接单 §「容器重启后需重建」那句
是**每次都要做**，不是备注。
**而这格自己也是一条假红，红在量具不在树**（run9/run10 两次都打 `ModuleNotFoundError: No module named 'pytest'`
+ `TOOLCHAIN_RC=1`）：我写的是 `cd $L && python -c ...`，**漏了 `PYTHONPATH=/tmp/pylibs`**，而工具链恰好只装在那儿。
同一份快照里 `GATE_RC=0` 与 `SUITE_RC=0` 成立本身就证明工具链在场；补上路径后现读
**`PYTEST=9.1.1 / PYFLAKES=4.0.2 / PY=3.11.16`，`TC_RC=0`**（2026-10-05T17:17Z 在役容器）。
登记成教训：**一格量具只有在"已知坏"的状态下红过一次才算成立**——这格在 run8（真没装）和 run10（装了没带路径）
上给出同一个读数，等于没量（与 WebUI 件 :68 的 OSV 反例档同一条口径）。脚本已修。

### 6.18.1 终树权威门 run10（快照 `/tmp/c37snap20261005d`，368 文件，基线 HEAD `d156057`，17:16→17:26Z）

终树 = 5 个 #54 文件 + 量具 `scripts/scan_day_bounds.py`。**run9 那三条红在这里全部消失**，逐格原样：

| 格 | run10 | 备注 |
|----|-------|------|
| SOURCES | `SOURCES_RC=0`，13 指纹全等本机基线；`SCAN_CLASS_CONST=2 SCAN_RECEIVERS=1 SCAN_DUP_RETURN=1`；`wc=919/893/1340/554/216/744 = **4666**` | 量具进快照（3922→4666） |
| GATE | `GATE_RC=0`，pyflakes 4.0.2 当前 0 / 基线 0 / 新增 0 / 已修 0，`GATE_MENTIONS_ATTIC=0`，基线文件一字未改 | 同 run9 |
| SUITE | `SUITE_RC=0`：**1420 passed, 10 skipped in 305.47s** | run9 是 1 failed/1419 passed |
| TARGETED | `TARGETED_RC=0`：**69 passed in 46.74s** | run9 1 failed/68 |
| TOUCHED | `TOUCHED_RC=0`：**277 passed, 3 skipped in 78.58s** | run9 1 failed/276/3 |
| DAYSCAN | `DAYBATCH_RC=0`；self-test **21 HIT / 12 CLEAN / 0 漏咬 `SELFTEST_RC=0`**；`total=97 external=44 literal=43 config=2 date_math=6 local=2 bounded=42 marked=2` ⇒ **`DAYSCAN_RC=0`**；attic `4/4/2/2 RC=0` | run9 `DAYSCAN_RC=1` |
| ROUTESCAN | `SELFTEST_RC=0`；`197/196/0/1/0/0` ⇒ `ROUTESCAN_RC=0`、`ROUTEBATCH_RC=0` | 未受影响 |
| MUT | `MUT_RC=0`、`MUTATION_BAD=0`、`restored_identical=OK`；M1..M10 失败数 **5/6/5/8/3/2/4/1/1/24**，与 run9 **逐条相同** | 变异打的是读路径，量具改动不参与——数字相同是"两棵树只差量具"的旁证 |
| PROBE | `PROBE_RC=0`，见下表 | — |

**约束② 的同型成对读数（run10 PROBE，现网 `/data/memory_agent.db`，`tz=8.0 scan_limit=30000`，全程 SELECT）**
——同一 run 内的成对比值，**这一份替换 run9 那一份作为口径基准**（原因见下面的更正）：

- **A1 分母**：窗口 2026-09-06→2026-10-06（31 日键）真值 **950486** 条 ⇒ 日配额 967、小时公平份额 40；
  逐日真值 min=2 / 中位=33450 / max=44573，日配额只占日均真值 **0.0315**。
- **A2 改前生产形状**（单条 `LIMIT`，旧实现）：**3620.8ms**、30000 行、**只覆盖 2 个日键**（09-06→09-07，
  首末 ts `2026-09-06T01:23:21 → 2026-09-07T00:04:42`）。
- **A3 乙′ 分层**：三次 **[2625.4, 2415.7, 2393.9]ms**（中位 **2415.7**）、29012 行、**31 个日键全覆盖**、
  `truncated=True`、每日实返 min=2 max=967（有配额的日用满了它 ⇒ 丢失是被证明的，不是猜的）；
  **耗时比（改后中位 / 改前单条）= 0.67**。
- **A4 每日可见跨度**（最后 5 天）：真值 24.0h 的日子实返 **23.0–23.1h**；缺的是当日**最后半小时的尾部**，
  不是整段夜间（进行中的一日 2026-10-06 只到 01:08:39，实返同样到 01:08:39 ⇒ 全覆盖）。
- **A5 判据①/② 成对**：按天前缀（#44 旧形状的量具）三次 **[736.4, 716.6, 849.6]ms** 中位 **736.4**、
  有货日键 31、每日可见小时数 首/中/末 = **1/1/4**、**夜间出场 0/31 天**；
  按小时分层中位 **2415.7ms**、每日可见小时数 **1/24/24**、**夜间出场 28/31 天** ⇒ **比值 3.28**。
- **B 组**（裁1/裁4 Q2=甲 的 64KB 页预算，同库同批行）：lean **133381→65335** 字节（500→235 行，省 51.0%）、
  full **153092→65391**（500→195 行，省 57.3%），改后仍 ≤64KB（单行最大 377 / 556 字节）。

> **更正一条被 run9 读数带偏的结论（这条更正本身也是「跨 run 不可比」口径的第二次应用）**：
> run9 那份「分层 0.07× 前缀、0.33× 单条」**不能读成"分层比旧形状还快"**——run9 的盘是冷的
> （08:01 recreate 之后 page cache 从零开始），吃冷盘的是 `day = ?` + `ORDER BY ts` 那种整天重扫的形状，
> 前缀量具因此在 run9 报 46175.8ms。**缓存变热的 run10 同一把尺报 736.4ms**。
> 两份读数各自成立、各自带 run 号，取数结论按 run10 读：
> ① 对**改前生产形状**（A2 单条 `LIMIT`）分层是 **0.67×**，且把覆盖从 2 个日键变成 31 个 ⇒
>    乙′ 没有引入新的耗时代价，run7 那条「慢 25.82×」的担忧**不成立，不需要为它开裁定**；
> ② 对**按天前缀量具**（A5，只读每日第一条小时格）分层是 **3.28×**，换来的是
>    每日可见小时数 1 → 24、夜间出场 0/31 → **28/31**；
> ③ 判据①（WebUI 件 :41「夜间 20:00–23:00 必须有返回」）与约束②（:41 同型成对读数 + 每日可见时段表）
>    两项**同时满足**，A4 表就是那张时段表。


**量具的 `self` 过度污染（HEAD 上那条红，单独 commit，任务表 #60）**：`_taint_sets` 把方法的第一个形参
（`self`/`cls`）也算进形参污染集，于是 `timedelta(days=self.DUAL_TRACK_DAYS)` 被读成 `param-tainted:self` 判红——
而 `DUAL_TRACK_DAYS = 30` 是**类体顶层的整数字面量**（`service_tokens.py:377`），没有任何 HTTP/形参通道能写它。
这是量具的过度污染，不是代码缺陷：给一个 30 的常量加 clamp 才是错的。修法只放开必要的一格——
`literal` 档新增「同类体内的整型类常量」，且要求**类内没有** `self.ATTR = <非字面量>` 的再赋值；
三条反例（同名实例属性被改写 / 类体里赋的不是字面量 / 只有实例属性）各配一条正样本，仍判红。
本机现读（`scripts/scan_day_bounds.py` 打完补丁）：self-test **21 HIT / 12 CLEAN / 0 漏咬 RC=0**
（打补丁前 18/11/0），src `total=97` 不变、`external 45→44`、`literal 42→43`、`SCAN_RC=0`，attic `4/4/2/2 RC=0`。
顺手核销一处死代码：`self_test()` 末尾重复的 `return 0 if ok else 2`（SOURCES 格用
`SCAN_DUP_RETURN` 盯它，2→1 才算真删）。

**13 把锁**（`tests/test_vma_q62_daily_batch_scan.py`，8→13）：新增 `test_one_statement_per_day_with_24_hour_buckets`
（形状锁：一条语句、24×4 个绑定、每格 `LIMIT=配额+1`/`OFFSET=0`、23 点上界越到次日、格与格首尾相接）与
`test_window_edges_clamped_into_the_hour_range`（窗口不对齐日界时，首尾两截夹紧、窗外两行必须不出现）；
夜间可见锁 `test_night_hours_are_visible_every_day` 就是裁定 :41 判据①。
**口径迁移登记**（本批之后读代码要用新词）：「总读取量硬上限」→「**返回行数硬上限**」（:43）；
`substr(ts,12,2) = 'HH'` → **ts 半开区间 + 夹紧**；小时配额 = **公平份额**（不是天花板）；
`truncated` = **已证明的丢失**；`day` 谓词**不在**取数分支里。本批**零新增出境键**，跨仓契约面无变化。

### 6.19 DCD 20261005 §二.3 CVE 三问 + §二.4 裁5 四问的执行状态与现读（2026-10-05，任务表 #57 / #58 / #59）

引用口径按裁定 :58 的附带条款执行：**只写「报告+行号」**。本节里两份 20261005 裁定件的行号分别是
`20261005-AF用户WebUI与MA四件与CVE-裁定.md`（下称 **WebUI 件**）与
`20261005-AF-ir_non_reversible与DPP遥测与MA集中五问-裁定.md`（下称 **五问件**）。

**CVE Q1 乙（网络隔离）——唯一有效读数已现取（2026-10-05T17:20Z，NAS 192.168.2.200）**。
WebUI 件 :64 写死「`docker network inspect <net>` 可达 chroma 的容器数从 4 降到 2（MA + chroma），
别拿『配了』当『生效』」——本格只认这条读数，现读如下（全部为 `docker network inspect` /
`docker inspect` / `docker port` 的原始输出，未做任何加工）：

| 量 | 现读 | 与裁定的差 |
|----|------|-----------|
| 内部网实际名称 | `memory-agent_internal` | 裁定文写的是 `ma_internal`；compose 给网络加了项目前缀，**网络名以 `docker network ls` 为准** |
| `memory-agent_internal` 成员 | `memory-agent` `memory-chroma` `memory-redis` ⇒ **3** | 裁定预期 2（MA + chroma）。多的那一个是 **redis**：它和 chroma 一样是 MA 自有、不对外发布端口的服务，被一并拆进内部网 |
| `memory-agent_default` 成员 | `memory-caddy` `memory-agent` ⇒ 2 | 拆网前 chroma/redis 都在这张网上，`memory-caddy`（面向公网的反向代理）可达 chroma；**现在不可达** |
| `docker port memory-chroma` / `memory-redis` | 空 | 符合裁定「端口本来就没发布」 |
| 宿主侧发布面 | `192.168.2.200:8086`→agent:8000、`9080`→caddy:80、`9443`→caddy:443 | 见下方端口条目 |

**裁定成立的部分**：chroma 不再与任何 MA 之外的容器同网，`memory-caddy` 与 chroma 之间已无共同网络。
**登记为偏差而不是核销的部分**：可达 chroma 的容器数是 **3 而不是 2**，因为 redis 被拆进同一张内部网。
裁定给的判据（其它容器不可达）满足，字面数字不满足——本格把两个读数都留下，判据按「caddy 不可达」这条实质口径读。

**CVE Q2 甲 / Q3 甲（WebUI 件 :65、:66）**：本窗**不动码**，只做登记——chroma 升 1.x 列入 **vMA-1.4**
（持久化格式与集合迁移不与向量面迁移叠加同一停机窗）；`ecdsa`（HS256 不涉）、`oauthlib`（非 OAuth 提供方）、
`PyJWT`（JWKS 取钥不在本项目）三包按**不可达**登记，随每次 OSV 扫描复测。扫描量具的键名自证
（命中键是 `vulns` 不是 `matches`，反例档先行）见 §6.12。

**裁5 追加四问（WebUI 件 :74-:77）**：
- **Q-A 甲**（`window` 定为洞察类读数的**通用回显键**，一次登记、后续新读法默认带）：代码侧已落到底，
  锁在 `tests/test_vma_insights_window_echo.py`；契约面的一次登记由 DCD 落笔（同件 :55 已明写「要，由 DCD 落笔」）。
- **Q-B 甲**（`page_bytes` 保留并登记，口径 = **整页投影序列化后的字节数，UTF-8，含信封不含 hint**）：
  现读点 `src/memory_agent/mcp_server.py:947`（`_page_bytes(page)`，超 64KB 预算先收缩再回显），
  口径与 §二.1 的 page_bytes 裁定一致，跨仓键名**未新增**。
- **Q-C 乙**（`entity_catalog` 明确为「**永久 legacy 只读面**」，Q3-4 判据改成「**除已裁保留项外全齐**」，
  别让它一直挂着「不齐」当未做项）：登记完成——`entity_catalog` 留在 `LEGACY_OUTWARD_METHODS`
  （`src/memory_agent/insights/api.py:69-81`）即「已裁保留项」，Q3-4 的齐/不齐清点从此按新口径读，
  真问题按裁定 :76 换成「这一格的补齐有没有消费方收益」。
- **Q-D 甲**（切换前先做**参数级语义核对**，每个入参要么有新实现落点、要么显式登记不支持，逐件出**键名对照表**走载荷键名登记再切）：
  与五问件 :59-62 的批次口径合并执行——按消费方依赖度排序 `get_user_persona` → `get_data_quality` →
  `data_coverage` → `device_usage`，切换后 **legacy 保留 7 天双跑**对账，对不上即回退；
  切换前需要 DB/AF 各出一句「我方实际读哪些键」的确认（投 inbox），**MA 侧无法自证消费方依赖，此项等 SP/DCD 转达**。

**端口条目按「计划 vs 实现」核一遍**（五问件 :64-71 判 A=换端口，文里给的数字是 8080/8443）：
在役实现是 **9080/9443**（`docker-compose.yml:62-64`，`docker port memory-caddy` 现读一致），
偏离理由与验证写在 commit `53eb15e` 正文（宿主 80/443 被孤儿端点占用、8443 被 homelab-dashboard 占用，
caddy 代理 `/health` 返回 200 `ok=true`）。**裁定 A 的形状（换端口 + PWA 走 HTTPS）没变，字面端口号变了**——
按判例（五问件 :102「计划与实现相反是硬证据」）这条必须让 DCD 知道而不是让它继续不一致，
已列入下一份 DCD 定向请求等追认；孤儿端点残留按 :71 登记为 NAS 运维待办（不属本仓）。

**Q5（service_token 30 天双轨，五问件 :77-84）**：四条里 MA 能自证的三条（30 天从新令牌签发日起算、
审计面加 `token_kind`、旧令牌使用量监控）已由 commit `d156057` 落码；
第 3 条「DB/AF 切换协同窗口由 DCD 排期（与合并停机窗同批）」**不在 MA 手里**，保持待办。

**编号陷阱登记**（WebUI 件 :58 + 本仓实测）：`#54 / #60 / #61` 这三个号在**三套台账里各指一件事**——
DCD 裁定件里的编号、审计「存量清单」里的编号、本仓会话任务表里的编号。commit `53eb15e` 写 `fix(#60)` 时
`#60` 指的是 caddy 端口那件，而本会话任务表 `#60` 是 day-bounds 量具的 self 过度污染。
本节及后续 commit 一律写成「任务表 #NN」或「WebUI 件 :NN」，**不裸写编号**。

### 6.20 A2/A7 静态结论核销第三批（任务表 #61，2026-10-06，容器 run11）——P4-3 改码、P2-4/P2-5/P2-8 补锁、P4×3 首次处置

审计锚点：第二轮 :121（P2-4）、第四轮 :40（P2-5）、第九轮 :30（P2-8）、A5 :23（P4-1）、
第十三轮 :54（P4-2）与 :108（P4-3）。引用纪律按 WebUI 件 :58（报告 + 行号）。
完整过程与门读数在 `doc/审计报告/修复与核实/审计核实与修复_20261001.md` §三十九。

**一句话结论**：三处"审计只给静态结论"的项现在都有运行时/AST 读数，其中 **P4-3 的评级被实测推翻**
（审计判 P4 观察项，实测是"任何一条非空坏 ts 就把窗口基准换成容器墙钟"⇒ 静默空结果 + 500 双通道），
本批按真缺陷改码；P4-1（pm4py AGPLv3）与内置词表口径呈 DCD，P4-2 核销为"前提已过时"，
**零新增出境键**。

| 件 | 处置 | 锁 | 变异自咬 |
|---|---|---|---|
| P4-3 | `intent_inference.py` 新增 `_event_dt:112`，撤掉 `datetime.now()` 兜底（该兜底在可达输入集上是死代码） | 3（含 `_NoClock` 时钟无关锁） | N1 / N2 |
| P2-4 | 现读已修（按天 `dict` 分组 + 排序），本批补顺序不变性 | 2（升/降/交错三档同读数，按频次字典比） | N3 |
| P2-5 | 现读 `^KEYWORD_DOMAINS`=1（:1104），但**修复只有注释在守** | 6（AST 数定义含 `AnnAssign` / 英文键 / 值 / ASCII 人口 / 中文不回归 / 同名表内容相等） | N4 / N8 |
| P2-8 | src 0 个 / attic 8 个，命名空间不可导入 | 6（含 8 参数量化 + `build_router` 调用点 = 0） | N5 / N6 |
| BOM | 三个在册 .py 去 U+FEFF（各 −3 字节，CR 不碰） | 3 | N7 |

**run11 权威门**（快照 `/tmp/c37snap20261006a`，371 文件，基线 HEAD `c3f2719`，Python 3.11.16，2026-10-05T18:12→18:25Z）：
GATE_RC=0（pyflakes 0/0/0/0）、SUITE **1449 passed / 10 skipped**（307.81s）、TARGETED 61、
PREVBATCH 69（与 run10 同数）、TOUCHED 338/3（= 277 + 61）、DAYBATCH 与 ROUTEBATCH 两份台账与 run10 **一字不动**
（src 97 = 44 外部 + 43 字面 + 2 config + 6 date_math + 2 local，bounded 42 / unguarded 55 / marked 2；
handler_shaped=197 mounted=196 unmounted=0 problems=0）。
MUT11 N1..N8 与 MUT10 M1..M10 全部咬住，`restored_identical=OK`、两套 `MUTATION_BAD=0`，
M1..M10 的条数 **5/6/5/8/3/2/4/1/1/24** 与 run10 逐格相同 ⇒ 本批没渗到 insights 读路径，
故不重跑 PROBE，正式基准沿用 §6.18.1（0.67× 改前单条 LIMIT / 3.28× 前缀量具）。

**呈 DCD**（`关键决策部/inbox/20261006-MA-pm4py许可档位与内置词表三处口径-决策申请.md`）：
Q1 pm4py 商用档位（甲 维持 + 发版前法务门 / 乙 从 extras 删 / 丙 采购许可；MA 建议甲，
现读 `find_spec("pm4py")` 在 `/app` 与 `/app + /tmp/pylibs` 下均为 None ⇒ 在役运行时不可达）；
Q2 两份 `KEYWORD_DOMAINS`（utils :1104 = 26 键 8 ASCII，parser/entity :35 = 33 键 5 ASCII，
ASCII 交集只有 `ac/light/tv`）合不合（MA 建议"保持两版 + 差异锁住"，改内置词表不在自主范围，任务表 #38 明令）；
Q3 追认 `CATEGORY_DOMAINS` 在 utils.py 顶层的两份（:271 与 :1091）逐键逐值相同 ⇒ 删前一份是否算"改内置词表"。

**一条必须在役面登记的读数**：`ls /app/src/memory_agent/ | grep -c learning_` = **8**、`/app` 无 `attic`
⇒ **attic 搬迁（任务表 #56）目前只在仓里，线上镜像仍是旧布局**。这与 #57 的网络隔离、端口 9080/9443 同一格：
出网的是仓，在役的是旧镜像，等**镜像重烤 + recreate 窗口**（未获授权）。凡"线上行为"的判定必须以容器 `/app` 那格为准。

### 6.21 Q-B 参数级落点收口（任务表 #40 ⑤⑥ / #58 Q-D，2026-10-06，容器 run12 → run13d）——四跳量到底，抓到一条"从未挂载"的现行缺陷

裁定来源：DCD `decisions/20261005-AF用户WebUI与MA四件与CVE-裁定.md` **§三 Q2**
（「每个入参必须有『新实现落点』或『显式不支持』的登记，**不允许静默忽略**」）。
完整过程与成对读数在 `doc/审计报告/修复与核实/审计核实与修复_20261001.md` §四十，交接摘要在进度 §十五。
编号纪律按本节 :1428 那条：只写「任务表 #NN」或「报告 :NN」。

**一句话结论**：这句话现在有一把能量化的尺子（`scripts/scan_qb_param_landing.py`，四类判词 + `--self-test` + `--strict`），
终树读数 `api.py` 125 / `service.py` 27 / `nlquery.py` 6 / `repository.py` 77 个形参**全部 lands、`FINDINGS=0`**；
而顺着落点逐跳往下读，在**第四跳**抓到一条现行缺陷——门面四条报告文本面指向一个从未挂载的成员
（`hasattr(BehaviorService,'reports')=False`，调用即 `AttributeError`，被 `@_degrade` 静默收成空页）。

| 跳 | 改前 | 改后（file:line） | 锁 | 变异 |
|---|---|---|---|---|
| 一（门面） | `days` 进死赋值（`_days_to_range` 返回值没人读）、`query` 全函数 0 个 Load | `_tr(…, days=days)`；`query` 解实体集（`api.py:726`） | qb 第 1..8 条 | N1/N2 |
| 二（core） | `_anomaly_report` 的 `_filters(room, category)` **不带实体集**，`filters` 回显恒空 | `service.py:619/630` 收 `entity_id` 并交 `_filters` 三位；echo 改 `",".join(entity_ids)`（:728） | qb 第 8 条（`entity_id_lands_on_repo_scans`，:208） | N3/N4/N5/N6 |
| 三（NL 路由） | 规划好的 `plan.entity_ids` **没交给 core** ⇒ 问「X 有什么异常」拿到全屋 | `nlquery.py:120-121` `entity_id=",".join(plan.entity_ids)` | callsite 族 | N7 |
| 四（报告文本面） | `self.core.reports` 从未挂载（`api.py:892-901` 四条全坏） | `service.py:376` 挂 `ReportBuilder()` | qb 第 9 条（`…_lands_on_the_report_surface`，:273）+ callsite `deep==4` | N10/N11/N12 |
| 门禁那一格 | `ok=True` 字面量被仓内 `fake-ok-const` 判红（run13 首读 `SUITE_RC=1`） | 不变式回读 `_closed_ok:512`（三条同时成立才 `ok`） | `test_vma_qb_param_landing` 第 8 条 + `test_quality_gates.py` 留在口径内 | N8/N9 |

**量具的两处自证**（这一批真正的产出）：
① `scan_insights_engine_attrs.py` 从"只认 `ast.Name` 基座第一跳"扩成整条链（`SUBOWNERS:54` / `init_slots:72` /
`resolve_chain:138`，判词分 `MISS` 与 `UNREGISTERED`），面板改前「**35 指向 / 0 空指向**」是**全绿**的、
四条坏链一条都没进统计 ⇒ 现读「**39 指向（含多跳链 4）/ 0 空指向 / 0 未登记**」+ `SELFTEST=OK`
（五档合成用例必须抓到 `no_member/no_method/unregistered` 三档）。
② 变异必须**非空**：N11 第一版阈值 `len(parts) > 3` 永假（`parts` 里没有 `self`）⇒ 变异等价于不改，
harness 老实报 `RC=0 -> 没咬住`；改 `> 2` 后用新加的 `MUT_ONLY` 单条重跑才是 `RC=1 / 2 failed`。

**run13d 权威门**（快照 `/tmp/c37snap20261006d`，373 文件，基线 HEAD `25d3b1b`，容器 Python 3.11.16）：
`TOOLCHAIN_RC=0`（pytest 9.1.1 / pyflakes 4.0.2，照旧排第一）、`SOURCES_RC=0`（本批指纹 + 七文件 3855 行与仓侧一致）、
`PROBEANOM_RC=0`（四跳成对读数：`days` 四档各自挪窗；带 14 实体时事件总数 **377234 → 309**、异常数 **1 → 5**；
解析不出设备时 **0 条 + `unresolved=True`** 而全屋对照 **1** 条；报告面 `days=14→15 / 2→3`）、
`QBLAND_RC=0`（`FINDINGS=0`）、`QBSELFTEST_RC=0`、`SCANS2_RC=0`（`CALLSITE/ATTRS/ATTRS_SELFTEST` 三格 0）、
`GATE_RC=0`（pyflakes 0/0/0/0，`.gates-baseline.txt` 一字未改）、`SUITE_RC=0`（**1459 passed / 10 skipped**，384.10s，
`SUITE_FAILNAMES_RC=1` = grep 不到 FAILED）、`TARGETED_RC=0`（32）、`PREVBATCH_RC=0`（61，与 run11 同数）、
`TOUCHED_RC=0`（350/3skip）、`DAYBATCH_RC=0`（src 97 项台账与 run10/run11 一字不动）、
`ROUTEBATCH_RC=0`（197/196/0/0，与 run11 逐格相同）；`MUT13_RC=0`（N1..N12 全咬，失败条数 3/2/1/1/1/1/1/1/1/3/2/2，
每格同基数 34）、`MUT11_RC=0`（N1..N8 全咬，条数 2/2/1/2/2/1/3/1）、`MUT10_RC=0`（M1..M10 全咬，条数 5/6/5/8/3/2/4/1/1/24），
三档各自 `restored_identical=OK`、`MUTATION_BAD=0`。区间口径：面板未打时刻，取 `.qoder/tmp-run13d.log` 的
birth/mtime（`+0800` 04:09:02→04:27:32）折算 UTC ⇒ 2026-10-05T20:09:02Z→20:27:32Z。
MUT11/MUT10 是**自证**：本批没动 intent/predictor/utils/attic，也没动 `repository.py`（一字未改），
读数应与 run11/run10 一字不差（M1..M10 = 5/6/5/8/3/2/4/1/1/24），对不上就说明改动渗到了不该渗的地方。

**run12 的 Q3 六条收口**（`.qoder/tmp-q3run12.out`，生产库只读）：Q3-1 过滤/排序位**全生效**（7 位，无"不生效"格）、
Q3-2 `days` 三档单调（1/7/30 → **5789 / 38074 / 149743**）、Q3-3 截断如实上报、Q3-6 三项对比读数齐；
Q3-4 不齐只剩 `entity_catalog`（属任务表 #58 Q-C 的"永久 legacy"，不是补齐项）、
Q3-5 不齐 = **9 个**既无同名无异名的工具 + **4 个**异名等价物语义待逐条核 —— 这 13 条是任务表 #40 ⑤⑥ 的正文，
本批只把读数钉在册上，补齐要动工具面，不在 #58 Q-D 的范围内。

**呈 DCD**（`关键决策部/inbox/20261006-MA-异常面两种查不到口径与unresolved键使用面-决策申请.md`）：
Q1 异常面两种"查不到"（门面 fail-closed 答 0 条 vs NL 回落全屋；MA 建议甲 = NL 也 fail-closed，
判据现成 `plan.params["has_query"]`（`nlquery.py:76`），不需要新出境键，但话术变化是 AF/DB 可见的行为变更）；
Q2 `filters.unresolved` 从 `query_events` 扩到 `anomaly_report` 的 `filters`（`api.py:749`）的使用面追认，
并**更正**上一份呈文"零新增出境键"那句话只适用于任务表 #61 那一批。

### 6.22 `behavior_routes` 输入边界与事件循环收口（任务表 #63，2026-10-06，容器 run14 → run14c）——覆盖率 21% → 69%，现读又引出一条现行缺陷

**这一格在计划里的位置**：第十五轮 §九 优先级 1 点名三个 0%~10% 覆盖模块
（`identity_fusion`、`behavior_predictor`、`api/behavior_routes`，见
`元宝/memory-agent_第十五轮审计报告.md`:208），本批收口的是第三个。
口径来源仍是 **DCD 20261005 §三 Q2**「每个入参必须有『新实现落点』或『显式不支持』的登记，不允许静默忽略」。
交付行原文（任务表 #63）：「改码 + DESC 口径用例 + 覆盖率现读（目标 behavior_routes ≥60%，Q4）+
容器权威门 + 台账 + 三 commit 推 GitHub」。**≥60% 是本批自设验收线**，不是路线图格、不是裁定书指标。

**五族收口**（终树行号，逐条与改前档 `612f67b` 成对量过指纹）：① `_num()`（`api/behavior_routes.py:23-44`）
铺开 **13 个 handler**，替掉两族旧写法——裸 `int(query)` 遇非数字把 handler 打成未捕获 ValueError（Starlette
没有 `exception_handlers` ⇒ 500），`int(body.get(k) or 3)` 把 `0`/`""`/`False` 静默改值并把 config 旋钮顶死
在字面量；同文件里 `days` 一直有守卫而 `limit` 没有，这种"半拉子守卫"比全裸更坑。② 四处重活回
`asyncio.to_thread`（`OFFLOAD_*` 四条 0→1、总数 45→49），运行时证据是 `PROBE` 的**心跳 tick=44**
（handler 占住主循环时这一格必为 0）与线程记录 `asyncio_0`。③ `change_attribution` 的"一个数两种口径"：
`clamp_days` 只管第一个消费点，`delta`/`half_life`/两处 `_description` 用原值 ⇒ 传 `10**9` 时窗口夹成 3650 天
而半衰期按 **5×10⁸ 天**算（`:317` `:331` `:375` `:396` 四处同源）。④ R3 人工审核留痕与规则视图冷却位
**HEAD 就有实现、改前 0 条用例引用**（指纹两侧都 =1），本批补的是"实现有、锁没有"。⑤ **族 5 是覆盖率现读
引出来的现行缺陷**：`behaviors_run` 的 `window_minutes` 原样透传到 `activity_inference.py:260` 的
`int(... or 15)`——`"abc"`→500、`0`→静默换档、`10**12`→`timedelta` OverflowError；现夹在
`1..DAY_WINDOW_MAX*1440`（=3650 天同口径）。这条正好回答"补覆盖是不是涂指标"：把没走过的路走一遍就走出新红。

**成对现读**（同一把尺：本机 Python313 + coverage 7.16.1、全量单轮、`--include=behavior_routes,change_attribution`）：
改前档 **1447 passed / 22 skipped**、`behavior_routes` 642 stmts / 506 miss = **21%**；终树档
**1532 passed / 22 skipped**、678 / 207 = **69%**（`change_attribution` 87%→88%，TOTAL 49%→77%）。
分母会随守卫增加而变，所以跨批趋势只报"未执行条数 506→207"这条绝对量。锁的密度另有一档：本批改动的
13 个 handler 在改前 **12 个没有任何"把 handler 当函数调用"的用例**、其中 **11 个连名字都没在任何测试里出现**
（量法：HEAD 的 128 个测试文件排除本批新增档，`name(` 与 `\bname\b` 两栏分开数）。
盲区随读数一起登记：`behaviors_current` 挂在 `Route("/api/behaviors")`（终树 :1179），按路径 grep 找不到；
`/api/behaviors/rules` 改前只有一条例程形状锁（`tests/test_vma_p32_day_bounds.py:626-633`）。

**四次自伤都在本批内当场改掉并给了现读**（详见台账 §四十一.六）：把变异 harness 当模块 `import` ⇒ 腿被 kill
在工作区留下 `if False:` 死门（→ ast 全表核对 + 驱动预飞格 `MUT63_VERIFY_RC`）；**权威门在飞期间改了树**
⇒ run14b 自动降级中间档、重开 run14c；M21 的替换串少一个冒号 ⇒ pytest collection 期 SyntaxError 被门报成
"没咬住"（→ 新增 `SYNTAX_BAD`：出网前 22 条变异逐个在内存里 `compile()`，负证明读到 `SyntaxError invalid syntax line 2`）；
测试注释里两句"改前也回 200"与 `git show HEAD:` 冲突（HEAD 两条 handler 本来就回 404）⇒ 改成如实登记，
另把"HEAD 侧指纹全 0"这句**推论**换成实测表。**推论不进台账**这条继续有效。

**run14c 权威门**（终树档，快照 `/tmp/c63snap20261006c`，374 文件，基线 HEAD `612f67b`，容器 Python 3.11.16，
区间 2026-10-05T22:36:06Z→22:57:02Z）：预飞 `MUT63_VERIFY_RC=0`（22 条锚点 + `SYNTAX_BAD=0` + 死门扫描 0）、
`TOOLCHAIN_RC=0`、`SOURCES_RC=0`（终树期望表逐字复现，含 `task63 测试 896` / `T63_TESTDEFS=46`）、
`PROBE_RC=0`（`limit=-1`→400、**心跳 tick=44**、合法 `0` 档原样到、`clamp_days(10**9)=3650` 且半衰期同源、
`trigger_id=999999`→404）、`SCANS/QBLAND/CALLSITE/ATTRS_RC=0`、`GATE_RC=0`（pyflakes 新增 0、
`.gates-baseline.txt` 一字未改）、`SUITE_RC=0`（**1544 passed / 10 skipped**，255.47s）、
`TARGETED 100` / `PREVBATCH 148` / `TOUCHED 396` 全 0；`MUT63_RC=0`（控制档 `NOTHING` 85 passed、
22 条全咬、条数 5/13/6/14/1/1/1/4/2/1/2/2/2/1/3/1/1/2/3/6/1/1、`MUTATION_BAD=0`）、
`MUT13_RC=0`（上一批 12 条重跑作自证，条数 3/2/1/1/1/1/1/1/1/3/2/2 与 run13d 一字不差）。
`run14` / `run14b` 留册为**中间档**（降级原因＝§6.22 自伤第 2 条：门在飞期间动了树）。


### 6.23 第二期审计第四批：MA-23 / MA-24 收口，外加"报告 76 vs 现树 32"的口径对撞（任务表 #68/#69，2026-10-07）

**这一格在计划里的位置**：二期十轮（`doc/审计报告/2期/`，第一轮→第十轮）里最后两件是第十轮的
**MA-23 🔴**（`reload_config` 四处同步直调，单次可钉住循环数秒）与 **MA-24 🟠**
（"76 个路由 handler 未卸载同步 store 调用，卸载率 56%"）。MA-23 的代码随**第三批** `2a14651` 落地，
本批收 MA-24 并把第十轮"回归验证清单"四格逐格对上。细节在台账 **§四十二**，这里只登记口径与可复用结论。

**交付**：六支路由 **32 个调用点 / 27 个 handler** 卸载（`+30` 行 `await asyncio.to_thread(`、摘掉的原有卸载 0 行）；
量具 `scripts/scan_unloaded_async_io.py` 从三种面扩到五种（`db`/`module`/`store`+身体门/`subsys`/`transitive`），
`--self-test` 八档、516 行；回归锁 `tests/test_vma_phase2_batch4_offload.py` 570 行 / 20 个 `def test_` /
参数化展开 **49 条**；牙齿 = MUT68 **15 条**变异 + 三档控制腿 + 落点锁的反例档。

**MA-23 终读**（`git blame` 四行同 SHA `2a14651`）：`config_routes.py:274` / `collect_routes.py:132` /
`collect_routes.py:183` / `ha_routes.py:98` 全是 `await asyncio.to_thread(rt.reload_config)`；
AST 锁在 `tests/test_vma_phase2_batch3_shared_ruler.py:672`——`reload_config` 这个属性访问**只许**出现在
`to_thread(...)` 参数位（写回直调多出一个 `Call.func` ⇒ 红；`await rt.reload_config()` 也红）。

**"76 vs 32"不是谁偷懒，是两把尺**（这条最容易被下一个人读成"漏了 44 处"）：
报告口径写在它自己表头——「`api/` 下**触碰 `store.` / `runtime` / `reload_config` 的 async handler**」，
**触碰字样 ≠ 有阻塞调用**。HEAD 侧按行分三类实测：inline（同行 `to_thread`）**45**、
continuation（`to_thread(` 的下一行、写方法引用）**18**、行内真调用 **26**。
- continuation 那 18 处按"这行没有 to_thread"数就全成"未卸载"（`vision_routes` 6、`behavior_routes` 5、
  `collect_routes` 3、`member_routes` 2、`face_routes` 2）——本仓大量写成
  `to_thread(\n    rt.store.method, args…)`，**方法名是引用不是调用**。
- direct 那 26 处里 **18 处本批卸载**（member 12 / llm 5 / mcp 1），**8 处本就不在协程体**：
  `_label_rosters`（同步函数，调用点进线程）、嵌套 `def _find_event()` / `_fetch_rows()` / `_insert_batch()` /
  `_sanitize_and_record()`、以及 `face_routes._resolve_member_id`（`:111`/`:188` 以引用形式进 `to_thread`）。
- 报告的 `insight_routes` 记 5 处未卸载：现读该文件 HEAD 的 9 处 store 调用**全部**是 inline `to_thread`
  （`blame e8cdd25d`，2026-10-02），报告写作时早已收口；`mcp_routes` 记 6 处：真 store 调用只有 1 处，
  其余五个 handler 碰的是 `runtime(request).tokens.*`＝**内存态** `MCPTokenStore`
  （`config.agent_tokens` 字典 + `compare_digest`，只有 `_touch` 在节流窗口才写文件）。
⇒ 反向锁两把，防止下一批照 76 给中间件加线程跳转：`test_token_store_legs_stay_on_loop`、
`test_gauge_does_not_ring_on_in_memory_store_face`。**这是量具第四段历史**（docstring 在册）：
它曾按变量名把中间件的 `store.verify/count/kind/scopes` 报成"协程直调 SQLite"，照做等于每请求多一次
线程跳转去躲一次纳秒级查表，还推翻 `#51` 的现读理由 ⇒ 那一腿回退（备份 `%TEMP%/ma24_auth_offload.reverted.patch`），
从此 store 面**看身体不看名字**。

**验收四格的对上方式**（第十轮 §回归验证清单原文逐格，台账 §四十二.四有表）：
item1 由 MA-23 四处 + AST 锁；item2 = `test_sixty_concurrent_member_list_keeps_loop_beating`（阈值 ≥5，
本机 8 次实测 **19~21**）配控制腿 `test_instrument_detects_a_blocked_loop`（同期 8 次**全 0**，阈值 ≤1）；
item3 = 两把零命中锁 `test_repo_zero_unloaded_direct` / `_transitive`——**零命中即空基线，红只能来自新命中**，
这比"把 76 条塞进基线再只准减"更强；item4（A3 §八"零未卸载"）**不改第三方报告本体**
（`元宝/A3_动态稳定性测试报告.md:149` 原句保留，全仓 grep "核销" 在那批报告里零命中＝惯例是各记各的账），
核销登记在台账。

**`HITS=0` 的边界要写在判据旁边**：`bcrypt.*` / `jwt.*` / 账号文件读写、`paho` / `httpx` 网络动词、
动态派发都不在面表里——"零命中"说的是这三面口径内干净，**不是**"全库无阻塞调用"。

**run17 权威门**（终树档，快照 `/tmp/c68snap20261007`，**400 文件**，基线 HEAD `2a14651`，容器 3.11.16，
区间 2026-10-07T04:38:56+08:00→05:13:51+08:00）：本批把"容器树 == 本机树"的核对从手抄 grep 换成
**逐文件 md5 + 整表 sha256 聚合**，两侧同一份脚本读同一个 `HASH_AGGREGATE=cafb96a7…581112`
（`HASH_MISSING=0`）＋ 43 行锚点 diff 为空；出网前硬闸拦下过首跑（MUT63 预检 `VERIFY_BAD=4`，
因**第二批 `f60811d` 把 `_num()` 从 `behavior_routes.py` 搬进 `api/deps.py`**（首稿误记为"第三批搬进
`day_bounds.py`"，已按 `git log -S` 现读更正）⇒ 锚点跟着函数走、不跟着文件走，任务表 #70 补跑）。
读数：`TOOLCHAIN_RC=0`、七支量具 `SCANS_RC=0`（`FINDINGS=0`、门面面板 39/0/0、
量具八档 `SELFTEST OK` + 直扫/`--transitive` 双档 `HITS=0`）、`GATE_RC=0`（pyflakes 新增 0、基线一字未改）、
**`SUITE_RC=0` 1904 passed / 10 skipped（450.48s）**——与本机 `1890+24=1914` 基数相同；
`TARGETED 49` / `BATCHES 281+1` / `PREVBATCH 302` / `TOUCHED 271` 全 0；
`MUT68_RC=0`（15 腿全咬、条数 7/8/5/5/6/6/2/8/1/1/1/1/6/1/1 **与本机档逐字相同**、`MUTATION_BAD=0`）、
`MUT64_RC=0`（上一批 24 条与 run16 在册读数一字不差 ⇒ 没渗到 `identity_fusion`/`behavior_predictor`/`mcp_server`）。
run16 那 `7 failed` + `GATE_RC=1` 由 `704f603` 收口，本档两格复绿。

**二期账面收口状态**：MA-01~MA-21、MA-23、MA-24 全部有处置（四批 + `704f603`）；
唯一仍开的是 **MA-22 🔴**（`_client_ip` 无条件信任 XFF + 服务直曝 8086）——改的是部署拓扑口径，
呈文 `20261007-MA-登录限速的客户端IP口径与8086直曝-决策申请.md` 在 inbox，`decisions/` 20261007 现读为零。

**run18（任务表 #70 补跑，快照 `/tmp/c70snap20261007`，区间 2026-10-07T05:34:03+08:00→05:44:52+08:00）**：
MUT63 那 22 条重锚后**容器口径全咬**——`MUT63_RC=0`、`NOTHING 85 passed`、`MUT_COUNT=22 MUTATION_BAD=0`、
每腿 FAILED 数与本机档逐腿相同；跑完 `POST_RC=0`（还原后再跑一遍仍 `85 passed`）。
这一档不重跑 SUITE：本机与容器的聚合摘要都等于 run17 的 `cafb96a7…581112`（400 文件、`HASH_MISSING=0`），
"容器树＝2054dde 那棵树"由哈希一票判掉。本机腿档跑在 `git archive HEAD` 的副本里，工作树全程零改动。
读数与更正在台账 **§四十三**（含"首稿把 `_num` 的搬家记成 `day_bounds`"这条勘误）。

**run19 + run20（任务表 #72：门面丢键自查修复，基线 HEAD `8b11437`，快照 `/tmp/c72snap20261007`，400 文件）**：
`get_data_quality` 在门面切换时把 legacy 承诺的 `agent_memory`（`mirror_dirty` 那格）**整块弄丢**，
补回 + 三条判据锁（`insights/api.py:892/910`、`tests/test_insights_facade_contract.py:230/237/246`）。
run19（06:06:21→06:18:02）除门 3 外全绿：`TOOLCHAIN_RC=0`（PYTEST 9.1.1 / pyflakes 4.0.2 / 3.11.16）、
`HASH_AGGREGATE=b750ca63…a0cdbd` 本机=容器逐字同、`GATE_RC=0`、
**`SUITE_RC=0` `1907 passed, 10 skipped in 485.90s`**、`TARGETED_RC=0` 12、`FACES_RC=0` 208、`POST_RC=0` 12。
**但门 3 那一格作废**：控制腿读成 `NOTHING -> RC=1 |` 且 stdout 全空 ⇒ `CONTROL_BAD`。
根因在量具不在产品——`_run_leg` 把 `PYTHONPATH` **覆盖**成 `root/src`，摘掉了容器里 pytest 所在的
`/tmp/pylibs`（本机 3.13 的 pytest 是系统装的，所以同一份腿本机绿、容器死）。
run20（06:23:29→06:24:33）只补这一格：`MUT72_RC=0`、`NOTHING 12 passed`、
L1/L2/L3 FAILED = **3/3/2 与本机 3.13 档逐腿相同**、`MUT_COUNT=3 MUTATION_BAD=0`、restored=OK 全条、
`POST_RC=0`；SNAP 聚合摘要复现 run19 值 ⇒ "作废那次没污染树"是量出来的，不是推出来的。
**合档口径：`8b11437` 的容器证明 = run19（除门 3）+ run20（门 3）；run19 的门 3 不许被引用成"三腿在容器咬住"。**

同一批还量出一处**规划主路径上的声明↔实现脱钩**（`route_question` 的 pitfall 让模型读
已不存在的 `recommended_tool`），与一处**已裁未落**（Q-A 点名的 `coverage`/`data_quality` 仍无 `window`，
→ 任务表 #73 自办）。运行时键集合探针的 12 条读数、以及"静态 diff 在委托形状前是瞎的"这条，见台账 **§四十五**。


---

## 七、下一阶段：更紧密联动（DCD 2026-10-06）

> 依据：`关键决策部/decisions/20261006-ADM下一阶段联动路线图-裁定.md`；契约 v2.0 见 `homesdk/doc/ADM联动主题注册表与消息契约.md` §七。
> 核心：三组联动端到端跑通 + 失败统一降级/错误码（`ADM_ERR_*`）。

| # | 任务 | 验收 | 前置 |
|---|------|------|------|
| 1 | 合并窗：镜像重烤（homesdk 进运行面 + MiniLM 退路删）+ R2 PII 回填 + service_token 生效 | 容器 `import homesdk` 通、245+ 条无明文、svc_ 令牌可用 | 合并窗 |
| 2 | `adm/memory-agent/status` 发 **JSON**（契约 v2.0 §7.1）——**修已登记 bug**（现发字面量 `online` 被 DB 丢弃，见 `回执_MA联动收尾_DB侧三项待办_20261006.md:129`） | status = `{state,ts,degraded,reasons,version}`，DB 不再丢弃 | 1 |
| 3 | MQTT 断连 → status `degraded` + `ADM_ERR_BROKER_UNREACHABLE`（§7.3） | 断 broker → status degraded + 码 | 1 |
| 4 | `ma/insights` 端到端（AF↔MA 硬读数） | MA 发 insights（带 `insight_id` + `conf` 封顶 0.59）→ AF 落 pending → 人批 → `af_draft` | 1 |
| 5 | 投 `butler/inbox/speak` 端到端（DB↔MA 硬读数） | MA 投 → 电视真播报；失败 → DB 降级 + 码 | 无 |
| 6 | 跑 `verify_adm_linkage`（homesdk `scripts/`）三组全绿 | 探针 rc=0（缺一组即红） | 1-5 |

**本仓失败语义**：载荷/鉴权 fail-closed + 码；MQTT 断连、对端离线 degrade-flag + 码；非关键提示 fail-open。**禁止静默丢弃。**

**本仓执行状态（MA 侧自证，2026-10-06 落码时实测）**：

| # | 本仓状态 | 依据／剩余前置 |
|---|----------|----------------|
| 1 | **未授权**——镜像重烤 = 改运行面，属合并窗动作，不在自主决定范围内 | 呈文见 `关键决策部/inbox/20261007-MA-合并窗五件与speak调用点与0.3.2消费时机-决策申请.md`；重启可自决、重烤不可 |
| 2 | **码先站住、线上未翻**：`LinkageJournal.reasons()` 已是 status `reasons[]` 的入参形状并经 `/api/health` 可读；`encode_status` 在 0.3.2 里可用，但运行面装的仍是 `vendor/homesdk-0.3.1`（`Dockerfile:24`），`homesdk.adm.*` 在容器里 `ImportError` | 1（两仓同翻；DB 侧已按契约兼容旧字面量） |
| 3 | 同上的断连档：`publish`/`publish_raw` 每条 False 出口已记 `ADM_ERR_BROKER_UNREACHABLE`，翻线那步（写进 retained status）等库进运行面 | 1 |
| 4 | MA 已发 `insight_id`（#42），AF 侧落 pending→人批→`af_draft` 的硬读数不在本仓可测 | 1 + AF 在场 |
| 5 | **本仓半边已落**（commit `1753cf0`）：`publish_speak()` 按 §E 发（`trace_id`/`text` 必填、`text ≤500` 截断、`role`/`priority`/`expires_at` 空值不写键、**无 `source`**、ts = epoch int），失败三档各带码。**调用点未经裁定**：MA 现用 HA `tts.speak` 直发，改成投收件箱要和它二选一，选错就是两处同时说话 ⇒ 未擅自接线，由 `test_publish_speak_has_no_production_caller_yet` 做"未挂载"会红哨兵；"电视真播报"那半条要 broker + 电视在场 | 呈文 Q1（谁来投）+ 1（端到端硬读数） |
| 6 | 探针在 homesdk `scripts/`，本仓无运行面 | 1-5 |
| 硬伤 1 | **已修**（commit `b3e9665`）：`_mark_stale` 三处出口带 `stable_id`，运行时转发到载荷；锁从库一路量到 MQTT 出口，两条自咬腿各自判红 | 无 |
| 新发现 | **已修**（commit `1753cf0`）：`publish`/`publish_raw` 原先只看"有没有抛异常"，而 paho 把失败写在返回值 `rc`（未连接 = `MQTT_ERR_NO_CONN`）⇒ 没发出去长期报成"已投递"，是"禁止静默丢弃"的另一面。rc≠0 现记码并返回 False | 无 |


### 契约对齐规范 v2.0（逐字版 · DCD 20261006）

> 唯一真源 = `E:\NAS\homesdk\doc\ADM联动主题注册表与消息契约.md`。本节是其**逐字快照**，供本仓执行，不再回查其它仓；两者冲突以契约表为准并提 DCD 复议。

**A. `adm/*/status` 统一 JSON**（取代字面量 `online`/`offline`）：

```json
{"state":"online|offline|degraded","ts":1760000000,"degraded":false,"reasons":[],"version":"<计划号>"}
```

- `reasons` 非空 ⇒ `degraded=true`，元素 = `ADM_ERR_*`；`version` = 计划号（AF 2.6 / MA **1.4** / DB 2.7）；
- 消费端**兼容旧字面量**：非 JSON 的 `online`/`offline` → 按 `{"state":"online|offline"}` 解析，**不得丢弃**。

**B. 统一错误码**：

| 码 | 含义 |
|---|---|
| `ADM_ERR_BROKER_UNREACHABLE` | MQTT broker 连不上 |
| `ADM_ERR_PEER_OFFLINE` | 对端 presence 不在线 |
| `ADM_ERR_PAYLOAD_INVALID` | 载荷 schema/校验失败 |
| `ADM_ERR_AUTH_REQUIRED` | 缺令牌 / 过期 / 越权 |
| `ADM_ERR_UPSTREAM_TIMEOUT` | 调对端超时 |
| `ADM_ERR_INTERNAL` | 未分类兜底 |

落点：status `reasons[]` ／ MCP·HTTP 响应 `{ok:false, code, message}` ／ `inbox_events` 审计。**联动失败必须带码，禁止静默丢弃。**

**C. 降级三档**：fail-closed（写面/不可逆：拒+码+审计）｜degrade-flag（读面/可重试：继续+`degraded`+码）｜fail-open（纯提示：放行+日志）。

**D. 事件载荷（逐字）**：
- `ma/insights` `{trace_id, ts, insight_id, kind, persons[], room?, summary, evidence[], snapshot_url?, conf?, intent?}`（`conf?` 可选封顶 0.59；`intent?` 可选；**MA 发 `insight_id` 稳定身份**）
- `ma/presence` `{trace_id, ts, members:[{name, member_id, room, via, confidence, last_seen, trigger}], total}`（retained。**`via_raw` 不摘**：契约表 §1.2 的 member 子键列的是"对端必读集"，而 `via_raw` 是 MA 多带的一枚归一化前的原始 via，**20261002 Q1 判的是「键名以生产实际为准，不反向要求改名」**，裁定与契约里都没有"摘掉多发键"这一条。它由 `tests/test_vma_dcd_20261002_payload.py:242` 的**闭合键集锁**守着（少键或多键都判红），台账登记见 `doc/审计报告/修复与核实/审计核实与修复_20261001.md:1063`）
- `ma/device-health` `{trace_id, ts, device_id, status, entity_id, from, to, stable_id}`（**`stable_id` 必须非空——现恒空串，是本仓要修的硬伤**；迁移类带 `from`→`to`）
- `af/automation/fired` `{trace_id, ts, automation_id, ref}`（不 retained）
- `af/automation/failed` `{trace_id, ts, automation_id, ref, error}`（不 retained）

**E. 收件箱 schema（对齐后权威版，DB 码必须按此）**：
- `butler/inbox/speak` `{trace_id, ts, text, role?, priority?, expires_at?}`，text ≤500，trace_id 必填
- `butler/inbox/notify` `{trace_id, ts, title, body, channel?, priority?}`，title ≤80 / body ≤500，trace_id 必填
- `butler/inbox/tv` `{trace_id, ts, content, duration_s?}`，content ≤500，trace_id 必填
- **无 `source` 字段**；按通道读 `text`/`title+body`/`content`；**MA 投 notify 用 `title+body`（现已是，DB 未读）**。

**F. MCP 面实名**：AF = `af_draft` + `af_apply(stage∈check|simulate|dry_run|save)`（**无 `verify`/`deploy` 别名**）；ask `GET /api/asks/pending`（read）+ `POST /api/asks/answer`（write + INBOX_KEY，回报 `channel_error`）。

**G. 端到端探针**：`verify_adm_linkage`（homesdk `scripts/`），三组各一条硬读数，缺一 `rc=1`。

---

—— 关键决策部 · DCD
