# 交接卡：视觉识别光线门槛实体选择改为手动填写

## 变更摘要
视觉识别设置页的光线门槛从「自动发现房间全部 light.* / switch.* 实体并展示复选框」改为「用户手动填写 entity_id 文本」。同时把保存按钮样式加强并增加底部保存按钮，解决用户找不到保存入口的问题。

## 涉及文件
- `src/memory_agent/static/js/pages/vision.js`：重写光线门槛 UI；移除 `roomLights` / `loadLights`；新增 `light_entities_text` 与保存解析逻辑。
- `src/memory_agent/static/sw.js`：Service Worker 缓存版本 `mw-shell-v3` → `mw-shell-v4`。
- `src/memory_agent/vision_service.py`：401 错误提示增加「保存配置」引导。

## 行为变化
- 每个摄像头的「光线门槛」展开后只有一个文本域，支持逗号/空格/换行分隔多个 entity_id。
- 留空仍保持原语义：自动使用房间注册表下全部 `light.*` 实体。
- 保存时前端把文本解析为 `light_entities: string[]` 后提交，后端数据结构与行为不变。
- 保存按钮从 `btn-ghost btn-xs` 改为 `btn-primary btn-sm` 并加图标；页面底部新增「保存视觉识别配置」大按钮。

## 验证步骤
1. 进入「视觉识别」页，展开某个摄像头的「光线门槛」。
2. 确认不再显示大量复选框，而是一个可填写 entity_id 的文本域。
3. 填写 `light.xxx` 或 `switch.xxx`，点击保存（顶部或底部按钮）。
4. 刷新页面，确认填写内容仍在。
5. 运行状态卡片中该房间的光线门槛状态应正确反映已配置实体。
6. 若 go2rtc 密码未填，点「测试取帧」应弹出包含「保存配置」的 401 提示。

## 已知风险 / 注意事项
- 此前通过复选框勾选过的 `light_entities` 会被正确加载为文本；首次保存后格式变为逗号分隔文本，不影响功能。
- 用户需要知道准确的 entity_id；填写错误会导致光线门槛判定失败（HA 会报找不到实体），但只会退化为「无法判定→放行」。
- 后端 `visionLights` API 仍保留，但 UI 不再调用。

## 追加修复：硬刷新后测试取帧 401
### 根因
测试取帧接口 `test_camera` / `test_llm` 的凭据回退逻辑有缺陷：
- 硬刷新后前端从 `/api/config` 拿到的是 **明文用户名 + 掩码密码（`********`）**。
- 路由层把掩码密码解析为 `None`，意图是「回退已保存配置」。
- 但 `test_camera` 里判断的是 `if go2rtc_user:`，只要用户名存在就会用表单值，把 `None` 当成空密码传过去，导致 go2rtc 返回 401。
- 重新在表单里填写密码后，表单密码是明文，测试成功；一刷新又变回掩码，再次 401。

### 修复
`vision_service.py` 中 `test_camera` / `test_llm` 改为：仅当 **用户名和密码同时存在（密码不是 `None`）** 时才用表单值，否则回退 `self.config` 已保存凭据。

### 新增诊断
- `fetch_frame` 增加日志，记录请求的流名、URL、是否携带凭据、HTTP 状态码。
- `test_camera` 错误提示区分 401/404，并显示「已携带凭据」/「未携带凭据」。
- 拉帧前对流名做 `.strip()`，避免首尾空格导致失败。

## 合并影响
- 主要改动：`src/memory_agent/static/js/pages/vision.js`、`src/memory_agent/static/sw.js`、`src/memory_agent/vision_service.py`。
- 无数据库/schema 变更。
- 需要客户端刷新（建议 `Ctrl+F5`）以让新的 Service Worker 生效。

## 追加功能：视觉识别对接 MCP / 内置 LLM
### 新增三件套（tool_schema.py + vision_service.py + mcp_server.py）
- `list_vision_cameras(only_enabled=True)`：`VisionService.list_cameras`，列出摄像头。
- `get_vision_status()`：复用 `VisionService.status`，只读诊断。
- `analyze_camera(room, stream, prompt_preset, prompt, bypass_limits=True, include_preview=False)`：
  `VisionService.analyze_scene`，取帧→VLM→仅返回文字描述（隐私优先）。
  - `prompt_preset` 枚举：`people`(人员活动)/`security`(安全异常)/`object`(物品宠物快递)/`custom`。
  - 预设提示词集中在 `vision_service.py` 的 `_PROMPT_PRESETS`，便于调优。
  - 默认 `bypass_limits=True`：直接调 `fetch_frame` 已绕过巡逻调度的光线门槛/冷却。
  - 失败遵循 spec §10，降级为缺数据文字说明，不抛出到请求链路。
- 三者 `expose=("mcp","builtin")`、`generated=True`，故 MCP（自动注册 `@mcp.tool`）与内置 LLM（function-calling）共用同一份定义，`dispatch(rt, name, args)` 经 `service="vision"`→`rt.vision` 同进程调用。
- `mcp_server.py` 的 `SERVER_INSTRUCTIONS` 增加「视觉识别」入口与用法示例。

### 修复：密钥掩码漏判导致真实密钥被覆盖（二次修正）
- 现象：`config.json` 里 `vlm_api_key` 短密钥被存成 `"*******"`（掩码占位）→ VLM 401；
  `go2rtc_pass` 长密钥（如 `longyin1003`）re-save 时被覆盖成掩码占位 `long********in1003`
  → 取帧 401，且「每次新增摄像头保存后都要重新填用户名/密码」。
- 根因：`mask_secret` 对 ≤8 字符输出整串 `*`，对长密钥输出 `<前4>********<后4>`。
  旧 `_is_masked` 要求「整串为星号」或「**以星号开头**且含 8 连星」才判掩码；
  长密钥掩码以明文前 4 个字符开头（如 `long...`），不以 `*` 开头，故误判为非掩码，
  被当成真实值写回，下次取帧即 401。
- 修复：`_is_masked` 放宽为「整串为星号」**或「含 8 连星」**即判为掩码。
  现已验证：`long********in1003` / `********` / `*******` 均判为掩码，明文 `longyin1003` 判非掩码。
- 注意：已损坏的密钥只需在设置页重新填写真实值并保存一次；修复后正常 re-save（不改密钥）不会再被覆盖。

### 关键修复（MCP / LLM 实际未更新的根因）
- MCP 端点实际只通过 `register_simple_tools(mcp_server, get_runtime, names=["route_question"])`
  注册了 `route_question` 一个 schema 工具，其余都是手写 `@mcp.tool()`。因此虽然
  `tool_schema.TOOL_SPECS` 已加入视觉工具，但 **MCP 工具列表并不会自动更新**。
  已改为 `names=["route_question", "list_vision_cameras", "get_vision_status", "analyze_camera"]`。
- 内置 LLM 的 `route_question` / `plan_question` 把「看看谁在客厅」这类问题误路由到
  `get_behavior_insights`（因为它命中了「有人/在/活动」等关键词）。已在 `insights.py`
  的 `plan_question` 中加入视觉识别分支，优先识别「看看/摄像头/监控/画面/谁/异常/快递/宠物」
  并返回 `analyze_camera(room=..., prompt_preset=...)`。
- `skills.py` 的 `_STRATEGY` 增加视觉识别规则与示例，让轻量模型更清楚何时调用摄像头。

### 验证
- MCP `tools/list` 返回 **54** 个工具，已含 `list_vision_cameras/get_vision_status/analyze_camera`。
- `plan_question("看看谁在客厅")` → `analyze_camera(room="客厅", prompt_preset="people")`。
- `plan_question("门口有没有异常")` → `analyze_camera(prompt_preset="security")`。
- `plan_question("玄关有没有快递")` → `analyze_camera(prompt_preset="object")`。
- **端到端已跑通**：`analyze_camera(room='客厅', prompt_preset='people')` 成功取帧并返回 VLM 描述
  （fetch 3.3s / VLM 7.9s，结果：客厅无人、光线明亮）。

### 用户侧还需操作
1. **刷新 MCP 客户端**：MiMo 等 MCP 客户端会缓存工具列表，需要断开重连或刷新。
2. **强刷 WebUI**：内置 LLM 页面 `Ctrl+F5` 刷新，确保拿到新的系统提示词与工具表。
3. **VLM API Key**：如后续 VLM 返回 401，请检查设置 → 视觉识别 → VLM API Key 是否为真实 key
  （此前掩码 bug 可能把它变成占位符，已修复但需人工重新填一次真实值并保存）。
