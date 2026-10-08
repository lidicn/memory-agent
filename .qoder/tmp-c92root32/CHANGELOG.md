# Changelog

版本号遵循语义化（`主.次.修订`），对外里程碑以**主版本号**体现。标签规范：里程碑打
`v<主>.<次>.<修订>`（如 `v1.0.0`）；日常修复走修订号，无需新建 tag。

## [1.1.2] - 2026-09-30

### insights P2 批次修复（mimo pro2.6 审查剩余项）
- **anomaly**：缺失数据报最严重 gap（原报最早）；`days_silent` 下限 0（原可为负）；
  flapping 重叠窗口合并去重（原逐 tail 重复计数）；`_active_minutes` 口径对齐
  `min_session_seconds`。
- **activity**：Signal/Rule 脏数据 float·int 容错；`infer` 入口事件排序；
  `night_ratio_percent` 改用配置跨夜窗（21:00-次日 11:00）替代硬编码 0-6 点。
- **nlquery**：停用词长词优先替换（"怎么样"先于"怎么"，原"怎么"拆碎"怎么样"）；
  PERSONA 分支尊重用户时间窗（原强制 max(days,14)）；DEVICE_USAGE 正则收紧泛词。
- **parser.entity**：房间别名按长度降序替换（原短别名先把长别名拆碎）；
  纯数值遥测值不再误判为开关 on/off；`unavailable`/`unknown` 归 other 不再算 off。
- **persona**：`explain` 未命中时补齐与命中分支一致的字段结构。
- **utils**：`parse_time_range` 异常收窄；`finalize_climate_session` 解析失败加 note
  不再静默归零；清理与 parser.entity 口径分裂的旧 OFF_STATES 常量。
- **service**：补 `compute_sessions` 函数（原 anomaly/activity import 缺失，一调即
  ImportError）。
- **repository**：`list_entities` 从最新事件 attrs_json 解析 friendly_name/unit
 （原读不存在的字段导致全空串）。

### vMA-1.2.2 召回加固收口
- **list_agent_memories fail-closed**：对外空 member_id 只返回公共记忆（`member_id=''`），
  与 retrieve 路口径对齐（原 list 路空 member_id 返回全家记忆，是隐私缺口）。
  store 层新增 `exact_member` 参数，内部 sweep 不受影响。
- 生产容器召回基线跑通：成员隔离 20/20、schema 字段 20/20（`ma-recall/1`）。

## [1.1.1] - 2026-09-29

### 工程卫生（DCD 裁定 6）
- **git 收口**：52 个未推送提交已推送到 origin/main；36 个 tmp_* 临时脚本清理（2 个有价值的迁入 `scripts/`）。
- **mem0 正式收口**：mem0 探索阶段已评估完毕，结论为**放弃**——memory-agent 自有的 SQLite + FTS5 + 嵌入向量方案已覆盖需求，mem0 不引入。mem0 相关容器（mem0-postgres / mem0-redis）保留但不接入。
- **MCP token 轮换**：旧 mcp_auth_token 已在 QA-MA-003 报告中泄露，已轮换为新 token；历史文档中的明文 token 已脱敏。
- **MQTT 越权修正**：巡检异常推送主题从 `butler/trigger/gu_anheng_alert` 改为 `ma/insights/security_alert`（各仓只推自己域名）。
- **DB 启动自检**：新增 `check_and_recover()` 启动时跑 `PRAGMA integrity_check`，损坏则自动从最近 .bak 恢复。

### 端口现状
- chroma：无主机端口绑定（仅容器内网）。
- MA：`192.168.2.200:8086`（绑 LAN IP，非 0.0.0.0），MCP token 鉴权。

## [1.0.0] - 2026-09-16

### 对外与闭环（v1.0 里程碑）
- **arena 分析闭环**：新增 `GET /api/arena/analytics`，按「是否使用家庭记忆 / 洞察工具」分
  cohort 对比成功率与 token 消耗；定向洞察页新增「竞技场闭环分析」卡片（量化 MA 价值 ROI）。
- **HA Assist 集成（MA 侧）**：暴露 OpenAI 兼容 `POST /v1/chat/completions` +
  `GET /v1/models`，HA 的 `extended_openai_conversation` 即可让 Assist / 语音直接用上家庭记忆
  （配置 `ha_assist_token` + `ha_assist_memory_top_k`；支持 `stream=true` SSE）。
- **顾安恒专属对话整合**：`/api/vision/analyze` 返回 `snapshot_url`（go2rtc 帧外链）；VLM 调用
  归集到固定 `conversation_id`（不再每次新建对话）；新增 `GET /api/vision/latest` 并对管家令牌
  放行；巡检异常 MQTT 推送钩子（默认关）。

### 发布打磨
- 版本统一为 `1.0.0`（`src/memory_agent/__init__.py` 与 `pyproject.toml`），
  `GET /api/system/version` 现返回 `version` 字段。
- 补全 PWA `static/manifest.webmanifest` + 图标，WebUI 可「添加到主屏幕」。
- 新增本文档与 GitHub Release 模板（`.github/release.yml`）。
- `README.md` 新增「对接指南」（管家 / HA Assist / 竞技场三张接入图）。
- `install.sh` 已支持 Docker / 非特权用户 / 自定义安装目录的一键安装。

### 说明
- Task 3「OpenSHS 活动识别基准评估」依赖外部公开数据集，未在本次纳入；后续单独评估。
