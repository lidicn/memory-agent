# 调研笔记：Xiaomi Miloco（小米全屋智能 AI 开源方案）

> 调研时间：2026-09-17
> 仓库：https://github.com/XiaoMi/xiaomi-miloco （MIT/Apache 类，License 文件为 `LICENSE.md`，协议口径为 "Other/NOASSERTION"，商用前需自查）
> 基本信息：3.3k stars / 300 forks / 697 commits / 主分支 `main` / 创建于 2025-11-06，2026-09-16 仍有活跃推送
> 关联：本文档与 `memory-agent`（家庭行为记忆中枢）对照，标注哪些设计可借鉴、哪些已对齐、哪些不适配

---

## 1. 一句话定位

Miloco 是**小米官方开源的"全屋智能 AI 大脑"**：以**米家摄像头音视频为全模态感知网关**，以**自研 MiMo 多模态大模型**为智能内核，作为 **Agent 插件运行在 OpenClaw（或 Hermes Agent）运行时**上，编排全屋米家设备提供主动智能。

核心卖点：通用常识（免规则自动识别隐患并分级告警）、身份识别（人脸/姿态融合识别家庭成员）、家庭记忆（从感知与交互提炼长期习惯）、家庭任务（长期目标拆解为可追踪任务）、主动智能（像管家一样观察-推理-干预）、家庭仪表盘。

**与 memory-agent 的关系**：Miloco 是"感知→记忆→决策→执行"全栈，而 memory-agent 当前定位是**"权威记忆 + 行为发现"层**（不依赖摄像头、以 HA 事件流为主）。Miloco 的感知/执行是我们的上游/下游，其**记忆、身份、任务、规则、反馈**机制与我们的能力高度同源，最值得借鉴的是它的"记忆晋升与消费"工程化套路。

---

## 2. 架构总览

### 2.1 五层后端架构（Router → Service/Runner → Repo → 外部代理）

| 层 | 职责 | 典型模块 |
|----|------|----------|
| **Router** | 接收 HTTP、参数校验、鉴权前置 | `miot/perception/rule/person/home_profile/task` 各自的 `router.py`，按 prefix 挂 `/api` |
| **Service** | 业务编排，跨域协调 | `MiotService` / `PerceptionService` / `RuleService` / `PersonService` / `HomeProfileService` / `TaskService` |
| **Runner** | 异步后台循环，驱动持续任务 | `PerceptionRunner` / `RuleRunner` / `TerminateEvaluator` |
| **Repo** | 数据持久化，隔离 SQLite 细节 | `KVRepo` / `RuleRepo` / `PersonRepo` / `PerceptionLogRepo` / `TaskRepo` / `TokenUsageRepo` |
| **外部代理** | 封装第三方 API | `MiotProxy`（`miot/client.py`）/ `PerceptionEngineProxy`（`perception/client.py`） |

`manager.py` 是**进程内依赖注入中心**（类似 memory-agent 的 `runtime` 装配）。

### 2.2 代码模块布局（`backend/miloco/src/miloco/`，20 个子包）

`admin` / `agent_platform` / `config` / `database` / `dispatch` / `home_profile` / `middleware` / `miot` / `node_monitor` / `observability` / `perception` / `person` / `pet` / `rule` / `schedule` / `schema` / `state` / `task` / `task_record` / `utils`
顶层 `main.py`（~39KB 入口）/ `manager.py`（~20KB DI 中枢）。

### 2.3 四条数据流（闭环）

1. **设备控制**：`CLI/Agent Skill → POST /api/miot/devices/{did}/control → MiotService（scope 校验）→ MiotProxy → 小米云 HTTP API → 设备`（控制写入走云，LAN 仅用于发现）。
2. **感知→规则→设备**：`摄像头码流 → MultimodalCollector（帧缓冲）→ PerceptionRunner → PipelineProcessor → Gate（帧差分+音频能量）→ Identity（DeepSORT→person_id）→ Omni（VLM：caption+matched_rules+speeches+suggestions）→ Proxy 后处理 → RuleService.update_state → RuleRunner 状态机 → 设备`。STATIC 规则直达 `MiotProxy`；DYNAMIC 规则经 `AgentDispatcher → OpenClaw Webhook → Agent`。
3. **Agent 指令**：`用户 → OpenClaw Agent → Skill（miloco-*）→ miloco-cli → POST /api/*（Bearer）→ Service`。
4. **家庭记忆注入**：`感知日志/对话 → Cron（perception-digest/home-patrol/home-dreaming）→ HomeProfileService → profile.md → before_prompt_build Hook 注入 Agent system 上下文`；同时 `home_profile_loader.py` 把档案注入 Omni（感知 VLM）动态层。

**闭环特征**：感知结构化 → 记忆注入上下文 → 规则/Agent 决策 → 设备控制 → 新数据沉淀记忆，形成持续进化的**本地家庭 AI 闭环**（强调本地运行、隐私不出户）。

---

## 3. 核心机制拆解

### 3.1 感知管线：`MultimodalCollector → Gate → Identity → Omni`（四层）

- **采集与缓冲**：`CameraDeviceAdapter` 接入单摄像头；`MultiTrackSyncBuffer` 按时间窗对齐音视频；本窗无视频帧时 hold 不生效（零帧窗口只走 audio 路由，否则直接跳过）。
- **Gate（变化门控）**：视觉帧差分 + 音频峰值能量双模态判定，过滤静止窗口，大幅降下游成本；带**滞回（Hold）**——刚通过后一段时间内即使本窗无变化也继续放行并打标，避免人短暂静止时 video/audio 路由抖动产生冗余处理。
- **Identity（身份）**：`DeepSortTrackingService` + `IdentityEngine` 输出 `{track_id → person_id}`，让下游"知道谁在场"。
- **Omni（VLM 推理）**：OpenAI 兼容协议调 MiMo；输出结构化 `OmniOutput`（`caption` / `speeches` 语音指令 / `matched_rules` / `suggestions` 主动建议）。`prompt_builder.py` 以 `FieldSpec` 为 schema 唯一来源；注入**部署时区时钟**（防宿主时区错乱）；**仅允许给本轮真正识别出的成员安姓名**（防幻觉）。
- **并发**：`PerceptionEngine` 按房间分组，`asyncio.gather` 并发；推理在专用单线程 `ThreadPoolExecutor(perception-infer)` 执行，不阻塞主循环。
- **多相机融合 = 分布式独立推理 + 全局身份共享**：每摄像头独立调一次 Omni（不合并多摄像头像素，因合并时 prompt 只带首摄视频导致准确率低）；各相机有独立 Tracking/Identity 实例，但 `IdentityLibrary` 样本库**全局共享**，陌生人分配 `unknown_` 编号并跨摄像头/跨帧保持一致。

### 3.2 身份识别：人脸/姿态融合 + 状态机 + 三层样本库

- **特征融合**：`face`（人脸）+ `body_appearance`（身体外观）两类视觉特征对应成员档案；实时识别走 Fused 路径，将 gallery composite 注入 Omni 主调用由 VLM 返回 `identity_assignments`（跨模态融合比对，省一次识别请求）。
- **识别状态机**：每个 track 维护五态 `none/pending/confirmed/unknown/no_person`；结果需**多次 Omni 一致**才晋升提交态，避免单帧误识；连续判定无人则落 `no_person` 抑制 VLM 幻觉。
- **三层样本库**：
  - `tier_a`：用户主动登记，**永久保留**（最可靠参照）；
  - `tier_c`：系统在线推理自动积累，**FIFO 滚动更新**，适应外观变化；
  - `tier_u`：全内存陌生人池，重启即清，为主动注册提供候选。
- **主动注册**：两条路径——①用户经 Agent Skill 上传照片/视频预览勾选写 `tier_a`；②从 `tier_u` 陌生人池选高质量 crop 聚类结果确认后升级。
- **与记忆联动**：`person_id` 写回后让 caption 以成员名代匿名编号；成员增改/删除**级联** `HomeProfileService.commit()` 重渲染档案，保证归属正确。

### 3.3 家庭记忆/画像：候选区晋升 + 加权截断 + 原子写 + 双路消费

- **多源提取，硬约束防噪**：
  - 感知日志：`miloco-perception-digest`（分钟级高频摘要）+ `miloco-home-dreaming`（每日深夜）从感知/交互记忆提取候选知识；
  - **用户直告优先**：对话中 `source=user_told` 权重最高、不过期，Agent 被动感知到家人喜好时无需明确要求即静默写入；
  - 初始化访谈 `miloco-onboarding` 分环节建档案。
- **候选区护栏**：新知识先进 `candidates.json` 积累证据，经 `Promote` 达标晋升为正式档案 `profile.json`，避免单次偶然观察污染长期记忆；同一结论全程只留一个候选（禁重复）；人物身份只照搬记忆明确写出的（陌生人绝不冒名）；正式区只做 merge（仅+证据）不改内容。
- **存储与并发安全**：封装在 `home_profile/store.py`；写入用**文件锁串行化**，落盘走**临时文件 + rename 原子替换**，无锁读者永远读到完整版本。
- **权重计算与截断**：`HomeProfileService.commit()` 重算权重（时间衰减 + 来源加成 `user_told` 最高 + 证据数量），`home_profile/render.py` 按权重降序做 **token 截断**（超上限时高权重优先保留），重渲染 `profile.md`。
- **双路消费**：
  - 主 Agent 按需自取（`home-profile list`，主上下文改注入"今日感知日志"而非每轮塞全量档案）；
  - Omni Prompt 动态注入（每次感知推理把档案注入 VLM 动态层）→ 形成**感知→记忆→感知正反馈**（档案越丰富 VLM 识别越准）。

### 3.4 规则引擎：STATIC/DYNAMIC 拆分 + 四层状态机 + 滑窗去抖

- **自然语言规则**：用户说"当 X 时做 Y"；方向分 `enter`（进入，单次触发）/`exit`（退出）/`session`（会话中，进入与退出互为反面）；多条 `enter` 同 task 为 OR，`session` 独占 task。
- **措辞约束**：`condition.query` 不能以"检测到/识别/感知到"等断言性词汇开头（VLM 会视为已发生事实导致连续误触发），应写进行时状态（"有人坐在书房桌前"）。
- **STATIC 规则（确定执行）**：低延迟、全程无 LLM；动作三形态 `prop..`（属性直控）/ `action..`（method call，如 TTS）/ `scene`（米家场景，强制 `idempotent=false`+冷却）；执行前做**幂等检查**（已达目标跳过）+ **冷却检查**。
- **DYNAMIC 规则（智能代理）**：只写意图，触发时交 Agent 结合上下文决定具体操作；经 `AgentDispatcher → OpenClaw Webhook`，在 `isolated` 会话运行，文字输出不进主对话流、须经 `miloco-notify` Skill 才让用户感知。
- **四层 RuleRunner 状态机**（`rule/runner.py`）：
  1. 源层：帧级抗抖（针对 True→False 疑似漏识，单帧翻转不立即采信）；
  2. 条件层：名下各源 OR 成布尔，可选 `duration_seconds` + `duration_ratio` **滑窗采样**（比例达标才触发，防单帧误判）；
  3. 确认层：与上一拍比边沿（ENTERED/EXITED/STILL_IN/STILL_OUT），按 direction 映射进"对 task 的意图"；
  4. task 层：`TaskStateMachine` 决定边沿是否真执行（STATIC→MiotProxy；DYNAMIC→AgentDispatcher）。
- **累计达标（milestone）**：服务端按 task 配置自动派生 `direction=milestone` 规则（用户主动建会被拒），用于"当日累计达标"类通知。

### 3.5 任务管理：持久意图 + 周期归档 + 三类统计

- **持久意图主体**：`task`（task_id+描述）长期存在，可 `active`/`paused`；挂载**规则/定时(cron)/记录**三类能力，不依赖单次触发；暂停后累积操作静默 `noop` 不计数。
- **周期归档**：后台 daemon `_rollover_daily_loop` 每日凌晨 `rollover_daily_job` 单事务归档旧行+开新活跃行；**自愈 + 重复跑无副作用（唯一索引）**；事件型记录长期累积、不参与归档。
- **三类行为统计**（`task_record`）：
  - `progress`（进度型，有目标值+单位，达标即完成，如每天喝 8 杯水）；
  - `duration`（时长型，支持开始/结束计时段，如练琴 60 分钟）；
  - `event`（事件型，只记次数与时间线，无目标、长期不归零，如记录老人起夜）。
- 派生量：进度型出剩余/百分比；时长型出今日累计/剩余分钟/活跃计时段；事件型出总次数/今日次数/最近时间。

### 3.6 事件反馈：fail-closed 脱敏 + bad-case 包

- **低门槛指认**：Web `ActivityFeed` 仅当事件有推理记录（`has_trace`）才显示"反馈"入口；用户勾选错误类别（人物/宠物/动作/设备/语音/误触发/其他）+ 一句补充。
- **隐私默认保守**：人员画廊（含人脸）默认**不勾选**，需 opt-in 才打包。
- **fail-closed 脱敏**：对 `omni_trace`/事件 `text`/用户补充说明做 PII 正则替换；**若脱敏异常，宁可整段丢弃 trace 也不打未脱敏内容进包**（"宁可缺 trace 也不泄 PII"）。
- **bad-case 闭环**：系统读 `snapshots/{event_id}/` 下推理时旁路落盘的 clip+omni_trace+元数据（反馈模块只消费不生产，保证字节级一致），打包本地 `tar.gz`，给飞书问卷链接由用户手动提交；预留 `uploaded`/`upload_key` 字段。把散落坏例攒成**可复现 bad-case 数据集**喂模型/规则迭代。

### 3.7 Agent 调度：`AgentDispatcher` 单飞 + 会话路由 + 建议去重

- **统一收口**：所有后端→Agent 投递经 `AgentDispatcher`，保证同一 `session_key` 单飞、同类批量合并、队列超长按优先级淘汰。
- **会话路由**：交互/绑定/onboarding 共用主会话；规则、建议各独立会话；按合并类型各自单飞互不混入。
- **建议去重（Suggestion Dedup）**：用**句向量语义相似度**（`EventEmbedder`，bge-small-zh-v1.5）而非精确文本匹配——措辞差异也能识别为重复，短期内同类建议只报一次；embedder 失败降级为精确文本匹配。

### 3.8 设计原则（WebUI，Mi Console v3 铁律）

白底主线 + 小米橙 `#FF6700` 仅作功能色（link/active/CTA/focus，不铺底）；"白面板→灰画布→白卡片"层次靠灰白交替；**状态用 5px 圆点 + 3px 半透明光环替代色块 badge**；IID/device_id/token/时间戳/计数/百分比/价格**强制走 mono 字体**（`tabular-nums`）；颜色全走 CSS 变量禁 hex 内联；响应式只用 Tailwind `md/lg/xl`；动效克制（hover 仅改 border+`scale(0.99)`，≤320ms）；**无 emoji**（用 lucide 风 SVG 图标）；LLM 默认无权改 design token/铁律（加 token 先写 `theme.css` 双轨再回填文档，改铁律需 Tech RFC 人类拍板）。

---

## 4. 与 memory-agent 的对照

| 能力 | Miloco | memory-agent 现状 | 结论 |
|------|--------|-------------------|------|
| 分层架构 | Router/Service/Runner/Repo/Proxy + `manager.py` DI | `runtime` 装配 + `api/*_routes` + `store` + `service` | **已对齐**（我们更扁平，可接受） |
| 长期记忆晋升 | `candidates.json` 候选区 → 权重达标晋升 `profile.json` | `agent_memories` 有 `staging`/`pending_review` + mem0 式 merge + `valid_from/valid_to` 时间切片 | **已对齐且更强**（我们已有时间有效性切片，Miloco 无） |
| 记忆消费 | 双路（Agent 自取 + Omni 注入感知 VLM） | 记忆仅注入对话检索（`retrieve`），活动推断是纯规则未吃成员习惯 | **可升级**：把成员习惯注入 `activity_inference` |
| 身份 | face/body 融合 + 五态机 + 三层样本库 + ReID | `identity.py` 按名/房聚类合并（`IdentityReconciler`） | **可升级**：缺 tier 样本库与状态机，但我们有 HA 实体而不依赖视觉 |
| 规则引擎 | STATIC/DYNAMIC + 四层状态机 + 滑窗去抖 | `define_activity` 自定义规则 + 覆盖预检 + 序列规则 `activity_inference` | **部分对齐**：缺 STATIC/DYNAMIC 拆分与 duration 滑窗 |
| 任务/统计 | 持久意图 + 周期归档 + 三类统计 | 无独立任务模型（有 `behavior_states`/`candidate_rules`） | **可学习**：周期性习惯追踪是空白 |
| 建议/洞察去重 | 句向量语义相似度（bge） | 有 embedding 但洞察去重靠 mem0 相似度阈值 | **可对齐**：复用现有 bge 做"重复洞察抑制" |
| Agent 调度 | 单飞 + 会话路由 + 优先级淘汰 | 有 idempotency key + circuit breaker + MCP 统计 | **可升级**：缺"同类请求合并/单飞" |
| 反馈闭环 | fail-closed 脱敏 + bad-case tar 包 | 有 👍👎 反馈存 `agent_memories.feedback_*` | **可升级**：缺"推理 trace 旁路落盘 + 可复现 bad-case 包" |
| 感知 | 摄像头四层管线（视觉核心） | **不依赖摄像头**，以 HA 事件流为主 | **不适配**：视觉管线无法直接搬 |

---

## 5. 结论：值得学 / 可照搬 / 机制学习

### 5.1 值得学（理念与工程哲学）
1. **记忆是中枢，不是附属**：Miloco 把家庭记忆同时注入"对话 Agent"和"感知 VLM"，形成感知→记忆→感知正反馈。记忆-agent 应把"成员习惯"显式喂给活动推断/行为洞察，而非仅服务于问答。
2. **长期记忆必须"候选区晋升"**：任何单次观察不能直接污染长期记忆，必须经证据积累 + 权重阈值才晋升。我们已有 staging，但可借鉴其 `candidates.json` 显式候选区 + 权重降序 token 截断的渲染策略。
3. **STATIC 快路径 / DYNAMIC 慢路径 拆分**：高确定性自动化不应每次都调 LLM。我们活动推断本就是规则，但"用户自然语言规则"应借鉴此拆分。
4. **本地优先 + 隐私默认保守**：控制走云但感知/记忆全本地；人脸默认不打包。与我们的本地 NAS 部署哲学一致。
5. **fail-closed 脱敏**：脱敏失败宁可丢数据也不泄露 PII——值得作为我们任何"反馈/导出"功能的铁律。

### 5.2 可直接照搬（具体、低风险、能落地）
1. **`home_profile/store.py` 的原子写 + 权重重算 + token 截断渲染器**：我们若引入"可读家庭档案 `.md`"，直接套此模式（临时文件 + rename 原子替换 + 权重降序截断）。
2. **`prompt_builder.py` 的 `FieldSpec` 作 schema 唯一来源**：我们 `InsightService` 拼 prompt 散落多处，可借鉴"单一 FieldSpec 驱动 schema"，并强制注入部署时区、仅命名已识别成员（防幻觉）。
3. **规则 `duration_seconds + duration_ratio` 滑窗去抖数学**：直接搬进我们 `activity_inference` 的序列规则与 `define_activity` 自定义规则，防单事件误触发。
4. **建议/洞察去重 = 句向量语义相似度**：复用现有 bge embedding，对"主动建议/重复洞察"做语义去重抑制（Miloco 的 `EventEmbedder` 思路），embedder 失败降级精确匹配。
5. **`AgentDispatcher` 单飞 + 会话路由 + 优先级淘汰**：我们 MCP/arena 调用已有幂等键与断路器，可补一层"同 session_key 单飞 + 同类合并"。
6. **事件反馈 `snapshot_context.py` 旁路落盘 + `build_feedback_pack`**：我们洞察推理时旁路落盘输入/输出 trace，配合现有 👍👎 反馈，攒可复现 bad-case 数据集。
7. **WebUI 设计令牌**：白-灰-白层次、状态用圆点不用色块、ID/计数走 mono 字体、无 emoji——直接套进我们 WebUI 规范（我们目前有手册页/仪表盘，可统一）。

### 5.3 机制学习（概念可迁移，但需适配）
1. **感知四层 `Gate→Identity→Omni`**：我们不依赖摄像头，但其"先门控过滤静止、再识别、再语义"的分层思想可迁移到**事件流管线**——先噪声门控（我们已有 `_noise_entities` 封顶），再身份归属（`identity` 层），再语义标注（VLM/规则），与我们"实时反应层(管家)/权威记忆层(MA)"边界一致。
2. **三层样本库（tier_a/tier_c/tier_u）**：可迁移到"成员/设备识别置信"——用户显式确认的永久保留、在线自动积累的 FIFO 滚动、陌生人/未识别的内存池，比单纯相似度聚类更稳。
3. **五态识别状态机 + 多次一致才提交**：防单帧/单事件误识，可直接用于我们"成员在场/活动识别"的去抖（已有 PIR 去抖，可升级为状态机）。
4. **持久意图 + 周期归档（rollover_daily_job 自愈、唯一索引幂等）**：这是我们的空白，适合做"周期性习惯追踪任务"（如"每日饮水达标""老人起夜记录"），用 `task`+`task_record` 模型。
5. **STATIC/DYNAMIC 规则 + 四层 RuleRunner**：我们 `activity_inference` 可借鉴"源层抗抖→条件层 OR+滑窗→确认层边沿→task 层执行"四层，尤其他把"规则"从"活动识别"中独立成可挂载动作的状态机，比我们当前把动作硬编码在洞察里更灵活。

---

## 6. 风险与不适配点（照搬前务必注意）

- **视觉中心，强依赖摄像头与 MiMo 多模态模型**：Miloco 的感知/身份/规则语义判断几乎都靠 VLM。memory-agent 定位为**事件/传感器中枢、不依赖摄像头**，其视觉管线（DeepSORT、VLM caption）不可直接搬；可搬的是结构而非模型。
- **模型绑定**：默认 MiMo（小米自研），我们走可配置 LLM 后端（new-api 网关）。其 `prompt_builder` 思路可搬，模型调用层不同。
- **部署形态**：Miloco 跑在家庭本地设备（NUC/树莓派/Mac Mini，≥4GB RAM），我们是 NAS 常驻服务——算力/延迟预算不同，其"每摄像头每窗调一次 VLM"的频次在我们这边对应的是"周期性批量活动推断"，不宜逐事件调 LLM。
- **协议/法律**：License 文件为 "Other/NOASSERTION"，`THIRD-PARTY-NOTICES.md` 列了 BGE(bge-small-zh-v1.5, MIT)、Silero VAD(MIT)、jMuxer(MIT) 等；**直接复制其代码前需确认许可证兼容性**，建议只借鉴机制、自写实现（我们已自研同类能力，更稳妥）。
- **规模差异**：Miloco 697 commits / 3.3k stars 是大型项目；我们取其"记忆晋升""规则状态机""反馈闭环"等**局部机制**即可，不必整体对齐其摄像头栈。

---

## 7. 参考链接

- 仓库：https://github.com/XiaoMi/xiaomi-miloco
- 概览：`knowledge/01-overview/overview.md`
- 感知管线：`knowledge/03-features/perception-pipeline.md`
- 身份识别：`knowledge/03-features/person-identity.md`
- 家庭画像/记忆：`knowledge/03-features/home-profile.md`
- 规则自动化：`knowledge/03-features/rule-automation.md`
- 任务管理：`knowledge/03-features/task-management.md`
- 事件反馈：`knowledge/03-features/event-feedback.md`
- 设计原则：`knowledge/07-design/design-principles.md`
- 后端模块：`backend/miloco/src/miloco/`（perception/rule/person/task/home_profile/dispatch 等 20 子包）

> 注：本文档基于仓库公开文档与目录结构调研整理（2026-09-17），未执行任何安装/运行命令；具体实现细节以仓库源码为准。
