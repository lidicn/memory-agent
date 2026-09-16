# Changelog

版本号遵循语义化（`主.次.修订`），对外里程碑以**主版本号**体现。标签规范：里程碑打
`v<主>.<次>.<修订>`（如 `v1.0.0`）；日常修复走修订号，无需新建 tag。

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
