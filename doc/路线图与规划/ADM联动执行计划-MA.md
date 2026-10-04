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
| 4 | 规则冷却三问 / FTS 短词召回下限 / `list_device_health` 分页 | 待 DCD 裁定后执行 | 投 inbox |
| 5 | 行为推断默认规则（behavior_states 结构性 0 产出） | 待 DCD 裁定 | 投 inbox |

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
| 裁3 反馈出境面脱敏 | Q1=A 分层 + Q2 label 白名单 + Q3 暂缓 | ⬜ 未落码 | `trace_anon.txt` 产物 + `vlm_failed-{room}-{date}` 白名单校验 |
| 裁4 超限响应 | Q1=A 分页 + Q2=A 默认精简 + Q3 维持裁行 | ⬜ 未落码 | `list_device_health` 加 `limit`(默认 500)/`offset`，`stable_id` 截 40 字，`note` 仅 `referenced=1` 给，保留 `fields=full` |
| 裁5 洞察门面 | Q1=A legacy 为对外只读引擎 + Q2=A 并存 + Q3 验收单全认 + Q4=A 如实上报截断 | ⬜ 未落码 | Q3 的六条**要有实测读数**才算齐；Q4 返回体加 `truncated`/`scan_limit`/`total_exact` |
| 裁6 活动识别 | Q1=A 语义回归 + Q2=A 回 8 参数 + Q3=A 硬排除生效 + Q4 认可（交付纪律） | ⬜ 未落码 | Q4 那条纪律是**门面每切一个方法必须留三项对比读数**（返回键集合 / 语义枚举值集合 / 旁挂依赖），缺一项判红 |

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

---

—— 关键决策部 · DCD