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

### 第 1 步：越权清理 + presence（裁定 §六.3 双通道）

| 子任务 | 验收 |
|--------|------|
| ① `butler/trigger/gu_anheng_alert` 直推 → 改**双通道**：告警改投 `butler/inbox/notify`（请 DB 说话）+ 洞察事件发 `ma/insights`（不 retained） | 全仓 grep `butler/trigger/` → 0 |
| ② 发布 presence：`advertise(client, "memory-agent", caps={...})`（homesdk.presence 现成） | retained `adm/memory-agent/status`+`caps` 可见 |
| ③ LWT 保离线 | kill -9 后 broker 自动发 `offline` |

### 第 2 步：service_token 合并（MA 先做，裁定 Q1=A）

| 子任务 | 验收 |
|--------|------|
| ① `ServiceTokenStore`（以 `AppTokenStore` 为基座：哈希+前缀+name+source+paths/methods+`kind="service"`） | 新令牌可签发、可吊销、有使用计数 |
| ② 启动迁移：`BUTLER_TOKEN`/`APP_TOKEN` 遗留值自动生成 service 记录（仿 `mcp_tokens.py:58` migrate_legacy） | 旧令牌到期日前仍可用 |
| ③ 白名单粒度按令牌自带清单（裁定 Q2=A，根治 F-2：app_token 不分方法放行 `/api/agent/memories` 写入） | 越界方法 403 |
| ④ 一实例一令牌（Q3=A）；本次只做"可吊销+使用计数"不做 TTL（Q4=A）；`source` 派生保留（Q5） | 回归锁："旧令牌在到期日前必须仍可用" |
| ⑤ **不设 expiry 不写死**——到期日由 SP 定 | 代码里无硬编码到期日 |

### 第 3 步：设备事件进引擎 feed（裁定：B 独立批量扫描器）

| 子任务 | 验收 |
|--------|------|
| ① 独立扫描器按小时轮询 `events` 表，同 entity/时间窗聚成"序列"再评估 | 对在线服务零压力；可干跑对照 |
| ② **降噪门槛三项**：domain 白名单（排除 91% 的 `sensor`）/ 数值型不进 feed / count 窗口 60s+3 次 | 实际进入 feed ≈2k/天（9% 有效流量） |
| ③ `_match_atom` 加实体/状态迁移语义（新条件词汇表） | 新词汇表有单测 |
| ④ `rule_trigger_history` 保留期 7 天 + 行数上限 10 万 | 超限自动裁剪 |

### 第 4 步：MCP 业务查询工具面（供 DB 消费）

| 子任务 | 验收 |
|--------|------|
| ① 现有 `ask_memory` / `get_member_persona` / `analyze_behavior_change` 已生产级——**确认工具签名稳定**（DB 要消费） | 契约测试断言签名 |
| ② 补"空调时长""净水器出水量"类聚合查询（复用既有模板引擎 `run_analysis_template`） | DB 侧能一次调用拿到 |

### 第 5 步：契约测试（与 DB/AF 同步）

| 子任务 | 验收 |
|--------|------|
| ① MA 侧 `tests/contract/test_ma_db_contract.py`（断言 MCP 工具签名 + 响应 schema + member_id fail-closed） | CI 跑过 |
| ② 部署前必跑 | — |

### 第 6 步：vMA-1.3 VIEW（已过 PoC 门，放行）

| 子任务 | 验收 |
|--------|------|
| ① `CREATE VIEW unified_events`（UNION ALL 三张表）+ `query_unified_events` MCP 只读工具 | PoC 已验证（107 万行，1.09s） |
| ② 只读、不动写入路径；`event_type` 映射写进 doc | 行数契约测试绿 |
| ③ 正式立项条件：≥2 个消费方接入且验证可靠 | 现在只做 MCP 工具（第一档） |

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