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

---

## 四、不在本版做（已裁并登记）

- vMA-2.0 知识图谱（门照旧，等真实 badcase ≥10 条）
- 候选规则观察期自动判据（自造真值，DCD 已否）
- feed 自造真值（同上）

---

—— 关键决策部 · DCD