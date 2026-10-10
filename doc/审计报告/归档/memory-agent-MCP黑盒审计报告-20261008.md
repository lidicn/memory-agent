# memory-agent MCP 黑盒审计报告

| 项 | 内容 |
|---|---|
| 审计对象 | memory-agent MCP |
| 审计日期 | 2026-10-08 |
| 审计方式 | 黑盒 / 使用者视角，不读源码 |
| 审计令牌 | deepseek++（read-only） |
| 总体结论 | 权限与写保护有效；只读面存在 8 处缺陷，其中 3 处会产生静默错误结果 |

---

## 一、审计方法

以纯使用者身份调用 MCP 工具，从返回体的正确性、一致性、可解释性推断缺陷，不依赖源码阅读。覆盖四条通道：权限边界、数据正确性、治理审计、跨工具一致性。

## 二、高优先级：静默误导类缺陷（P0/P1）

### D1【P0】list_device_health：非法枚举值静默返回空集，伪装成设备全健康

请求：list_device_health(state=all, fields=full, limit=5000)

返回：ok=true, health=[], total=0, count=0, has_more=false, limit=2000(被夹), logical_devices=1267

问题：state 仅接受 active/unknown/stale，传文档外的 all 时系统既不报错、也不回落为全部，而是返回 ok=true + 空清单；同一响应却承认存在 1267 个逻辑设备。

危害：使用者会直接读成没有任何设备异常，而真相是过滤条件把所有行筛空了。错误不报错、结果反向解读——本次审计危害最高的一条。

期望：非法枚举值返回 INVALID_PARAM 并列出允许值；或 state 缺省即全量。

### D2【P0】ask_memory：路由解析正确，执行层空转并回吐硬编码话术

请求：ask_memory(question=昨天谁在家做饭, route=semantic, days=2)

返回：plan room=厨房 activity=cooking 时间=2026-10-07 全天（解析完全正确）；answer=我还不确定你想问什么。试试：最近 7 天有哪些活动；试试：昨天洗澡了吗；data={hints:[同上两条]}，total=1，无任何实际数据。

问题：意图、房间、活动、时间窗全部解析正确，但 answer 是固定兜底文案。语义副驾依赖向量库召回，而 retrieve_agent_memories 在缺 member_id 时被拒，此处静默降级为空。

危害：用户看到的是 AI 不知道，而非权限/召回失败，故障被伪装成能力不足。

期望：语义路由失败必须显式告知降级原因，禁止回吐兜底话术。

### D3【P1】get_user_persona：未剔除遥测，用户画像被传感器轮询刷屏

请求：get_user_persona(days=3)

返回要点：traits 作息类型=日间活跃型(peak_hour=20)，设备交互强度=38978.3 次/天，最常活动房间=卧室(25997)；top_entities 前 10 全为 sensor.*（线圈温度/CO2/功耗/存储空间）；hourly 24 小时分布 3639~6194，几乎均匀。

问题：top_entities 前 10 全是 sensor 域自动上报，不是人的操作，38978 次/天 绝大部分是轮询噪声；据此推出的最常活动房间很可能是卧室传感器上报最频繁；作息判定自相矛盾（23 点=6025、1 点=5497，与峰值 20 点=6194 几乎齐平，全天无低谷）。对照 get_behavior_summary 明确提供 telemetry_excluded 剔除遥测，两个画像工具口径不一致。

期望：get_user_persona 复用 telemetry_excluded 过滤；作息结论需与分布形状自洽。

## 三、数据完整性与审计轨迹

### D4【P1】read_self_diary：日记空壳入库

read_self_diary(days=7) / (days=3)：count=2，diaries=[{date:2026-10-06,text:空},{date:2026-10-07,text:空}]。记录存在（有日期）但 text 全为空字符串。写入侧失败时写空壳而非拒绝，读取侧也不校验空正文。使用者看到有 2 篇日记，打开却空白。

### D5【P2】硬排除撤销后 revoked_at 未回填

list_signal_rules(include_revoked=true)：exclusion_id=5e13db3466e79f2b，revoked=true 但 revoked_at=null、reason 为空。该 exclusion_id 与历史 bug 库中 bug_6baaadccbd 完全一致（teach_signal 的 dry_run 残留）。可回滚红线要求留痕，此处留痕残缺。

### D6【P2】记忆治理：staging 积压，自动晋升近乎停摆

agent_memory_health：staging=500（明显截断上限值），live=13，revoked=47，pending_review=0，chroma=true。被撤销的记忆是被晋升记忆的 3.6 倍；真正参与检索的 AI 记忆仅 13 条，与 500 条待晋升形成巨大落差。

### D7【P3】规则学习闭环从未跑通

list_rule_channel：promoted_rules={dry_run:[],live:[],revoked:[]}，audit=[]，min_evidence=3，dry_run_days=3.0。生效通道三档全空、审计全空，无任何候选规则走到试运行；DCD R3 四红线设计再完备，无数据流过即等于未验证。

## 四、一致性问题

### D8【P3】隐私收窄策略仅部分执行

| 工具 | 缺 member_id 时行为 |
|---|---|
| list_agent_memories | INVALID_PARAM 拒绝（通过） |
| retrieve_agent_memories | INVALID_PARAM 拒绝（通过） |
| get_session_trust | 传空串/空格均 ok=true，无校验（失败） |

附：query_unified_events 中 07:37:25 识别陌生人、07:37:54（29 秒后）同一客厅摄像头识别熟人 Emily，两源事件并存无去重/合并，会向画像层注入互相矛盾的人员在场记录。

## 五、表现良好、应予肯定的部分

| 项 | 表现 |
|---|---|
| 写保护 | teach_signal 被 read 令牌明确 DENIED，错误信息给出 WebUI 修复路径 |
| 分页契约 | query_unified_events 返回 total/count/offset/limit/has_more/next_offset |
| 参数校验 | explain_insight 对不存在 id 返回结构化 NOT_FOUND + found:false |
| 数据质量 | get_data_quality score=1.0，9 项完整性检查全绿，噪声占比 12.3% 低于 60% 上限 |
| 双层库状态 | get_collect_status 明确区分关系库(主)/向量库(副) |

## 六、修复优先级汇总

| 优先级 | 缺陷 | 修复方向 |
|---|---|---|
| P0 | D1 设备健康静默空集 | 非法枚举值必须报错；state 缺省=全量 |
| P0 | D2 ask_memory 空转 | 召回失败显式降级提示，禁止回吐兜底话术 |
| P1 | D3 画像遥测污染 | get_user_persona 复用 telemetry_excluded 过滤 |
| P1 | D4 空日记 | 写入侧校验正文非空；读取侧过滤空壳 |
| P2 | D5 撤销无时间戳 | 撤销时回填 revoked_at 与 reason |
| P2 | D6 晋升停摆 | 排查 sweep 调度与晋升门槛 |
| P3 | D7 规则闭环空 | 用真实数据端到端验证一次 dry_run 到 live |
| P3 | D8 权限不一致 | 统一空 session_id 校验 |

## 七、总体判断

这是一套架构意图清晰、契约设计规范的 MCP 服务——分页、双层库、写保护、审计留痕的骨架都在。

但缺陷高度集中在同一个模式：失败路径不报错。

- 非法参数 → 返回空集而非报错
- 召回失败 → 回吐兜底话术而非降级提示
- 写入失败 → 落空壳而非拒绝
- 遥测污染 → 画像照出而非过滤

对使用者而言，最危险的从来不是报错，而是看起来成功的错误答案。建议把 D1/D2 两个静默误导项作为下一轮迭代的首要目标。

## 附录 A：关键复现请求

list_device_health(state=all, fields=full, limit=5000)
ask_memory(question=昨天谁在家做饭, route=semantic, days=2)
get_user_persona(days=3)
read_self_diary(days=7)
list_signal_rules(include_revoked=true)
agent_memory_health()
list_rule_channel()
get_session_trust(session_id=空)

## 附录 B：画像小时分布（疑似遥测污染）

```xychart-beta
title "get_user_persona 24小时事件分布"
x-axis ["0","1","2","3","4","5","6","7","8","9","10","11","12","13","14","15","16","17","18","19","20","21","22","23"]
y-axis "事件数" 0 --> 7000
bar [4985,5497,5411,5039,4769,5162,4343,5360,4912,5241,4342,4684,4696,4542,4312,3639,4034,4356,5169,4727,6194,4781,4715,6025]
```

若剔除遥测，曲线应呈明显昼夜落差；当前近乎水平，说明画像建立在噪声之上。

---

报告生成：2026-10-08 · 审计方式：黑盒使用者视角 · 令牌：deepseek++ (read-only)
