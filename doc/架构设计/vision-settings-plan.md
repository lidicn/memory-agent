# 视觉识别设置界面 · 完善计划

> 目标:为 memory-agent WebUI 新增「视觉识别」设置页,包含①摄像头设置②多模态 LLM 设置③识别调用频率设置,并落地**「房间开灯才轮询视频流做识别」的光线门槛**。
> 依据:`docs/vision-behavior-spec.md`(v1.0,2026-08-30 实测规格)。当前 `src/` 无任何视觉后端代码,本计划同时定义**配置契约**供后端实现。

---

## 一、现状与摸底结论

| 事项 | 现状 |
|---|---|
| 视觉后端 | **不存在**,spec 文档仅为设计;设置页需先定义配置契约 |
| 配置体系 | `config.py` dataclass + `config_routes.py` `WRITABLE_FIELDS` 白名单(漏加=静默丢弃)+ `SECRET_FIELDS` 掩码 |
| 房间→灯 | `cfg.rooms[room].entities` 已含 `light.*` 实体;灯态可实时查 `rt.ha.get_state` |
| 前端新增页套路 | `store.js` ROUTES → `main.js` import/NAV/Alpine.data → `index.html` 模板块 → `sw.js` 预缓存 → `api.js` 方法(参照 `signal_rules.js`) |
| 多模态网关 | doubao2api `:9090/v1/images/analyses`,与内置 llm_backends **相互独立**,需单独一组配置 |

**页面决策**:不塞进现有「系统设置」页(已拥挤),新建顶级导航页**「视觉识别」**(id=`vision`),三个设置块 + 运行状态面板,与 spec 的 P1~P3 阶段解耦推进。

---

## 二、配置契约(config.py 新增字段)

```python
# ── 视觉识别(多模态行为识别,vision-behavior-spec) ──────────────
vision_enabled: bool = False
vision_device_token: str = ""            # SECRET:TV/巡检设备上报令牌

# go2rtc 取流
go2rtc_base_url: str = "http://192.168.2.200:1984"
go2rtc_user: str = ""
go2rtc_pass: str = ""                    # SECRET

# 多模态 LLM(doubao2api)
vlm_base_url: str = "http://192.168.2.200:9090"
vlm_api_key: str = ""                    # SECRET
vlm_model: str = "doubao"
vlm_timeout_s: int = 25
vlm_max_retries: int = 2

# 摄像头注册表:每房间一条
# [{room, stream, enabled, no_tv, light_gate, light_entities: []}]
vision_cameras: List[dict] = field(default_factory=list)

# 频率与门槛
vision_cooldown_s: int = 60              # 同房间两次 VLM 最小间隔
vision_max_per_hour: int = 20            # 每房间每小时硬上限
vision_no_tv_interval_s: int = 300       # 无 TV 房间巡检周期
vision_light_gate: bool = True           # 光线门槛全局总闸
vision_snapshot_retention_days: int = 7
```

同步修改 `config_routes.py`:
- `WRITABLE_FIELDS` 追加全部 `vision_*` / `go2rtc_*` / `vlm_*` 键(**漏加会被静默丢弃**)
- `SECRET_FIELDS` 追加 `vision_device_token, go2rtc_pass, vlm_api_key`
- `get_config_api`/`update_config_api` 增加掩码回显与恢复逻辑(照抄 `ha_db_password` 模式)

---

## 三、光线门槛设计(核心新增,非 spec 原有)

### 3.1 规则
> 摄像头所在房间的**照明灯开着**才允许轮询视频流做多模态识别;房间暗(灯全关)时跳过,不拉帧、不调 VLM。

### 3.2 判定流程(在节流闸门**最前面**,先于 spec §6 的 5 条)

```
巡检 tick / TV 事件触发
  → vision_light_gate 总闸开? 房间 light_gate 开?
  → 取该房间 light_entities(用户在 UI 显式勾选;为空则回退 cfg.rooms[room] 下全部 light.*)
  → 逐个 GET rt.ha.get_state(entity_id)(HA REST,实时;10s 内存缓存防打爆)
  → 任一灯 state == "on" → 放行,进入冷却/去重/上限链
  → 全关 → 拦截,behavior_events 不记,但记录 skip 计数与原因 'light_off'(状态页可见)
```

### 3.3 语义细则
- **先查灯再拉帧**:spec §6 已注明「冷却期内的帧不拉」;光线门槛同样放在拉帧之前,省 go2rtc 4~13s 等待。
- **无灯可判 → 放行(fail-open)**:房间没配任何灯实体时不过度拦截,但状态页亮黄牌提示「未配置照明实体,光线门槛不生效」。理由:门槛目的是省调用 + 提画质,不是安全硬闸。
- **HA 不可达 → 放行 + 标记 degraded**,状态页提示;连续失败超过 N 次改为 fail-closed(可在 UI 切换,默认 fail-open)。
- **手动 `force:true` 绕过光线门槛**(调试用途),但记录 `trigger=manual_bypass_gate`。
- **开灯即检(可选增强,零代码)**:HA 自动化「房间灯 on → 调 `POST /api/vision/analyze`」,把下次巡检提前到开灯瞬间;不改后端,写进 spec 附录即可。
- 默认值:`vision_light_gate=true`,每房间 `light_gate` 继承全局;客厅(TV 房)同样受门槛约束。

---

## 四、后端触点(vision_routes.py 新增)

| 端点 | 用途 |
|---|---|
| `GET/POST /api/vision/config` | 读写视觉配置(掩码/恢复;或直接并入 `/api/config`) |
| `POST /api/vision/cameras/test` | `{stream}` → go2rtc `frame.jpeg` 取帧,返回 base64 预览 + 耗时 + 尺寸(验证流名/鉴权/延迟) |
| `POST /api/vision/test-llm` | 端到端:取帧 → doubao `/v1/images/analyses` 简单提问 → 返回 VLM 文本 + 耗时(验证会话存活,失效时提示扫码重登 `:9090/admin`) |
| `GET /api/vision/status` | 每房间:门槛状态(灯态)、上次识别时间、今日调用数、冷却/退避剩余、skip 计数 |
| `GET /api/vision/lights?room=` | 列出某房间可用 `light.*` 实体(供 UI 勾选) |

spec 里的 `/api/events/face`、`/api/vision/analyze`、`/api/behaviors` 属于 spec P1~P3 范围,本计划只保证**配置与测试端点先行**,不阻塞。

---

## 五、前端「视觉识别」页(vision.js)

布局照 settings.js 风格(卡片 + `cfg.*` x-model + 测试结果面板 resultBox):

### 块 1:摄像头设置
- 表格:房间(下拉,来源 `cfg.rooms` 键)/ go2rtc 流名(文本,如 `客厅`、`小黄人`)/ 启用 switch / 房间类型(有 TV=事件触发;无 TV=定时巡检)
- 每行「测试取帧」按钮 → 弹预览图 + 延迟(调用 `cameras/test`);流名为中文,内部 URL-encode
- 每行「光线门槛」switch + 照明实体多选(`lights?room=` 返回列表;默认全选该房间 `light.*`)
- 增删摄像头行;保存时整体 POST

### 块 2:多模态 LLM 设置
- `vlm_base_url` / `vlm_api_key`(掩码+眼睛)/ `vlm_model` / `vlm_timeout_s` / `vlm_max_retries`
- 「端到端测试」按钮 → `test-llm`:显示 VLM 回答 + 耗时;失败时展示「会话可能失效 → http://192.168.2.200:9090/admin?key=… 扫码重登」提示
- 说明文案:此网关独立于「系统设置-大模型」,仅用于图片识别

### 块 3:识别频率与门槛
- 总开关 `vision_enabled`(红牌文案:关闭立即停,家人反馈不适时用)
- 冷却 `vision_cooldown_s` / 每小时上限 `vision_max_per_hour` / 无 TV 巡检周期 `vision_no_tv_interval_s`
- 光线门槛全局总闸 `vision_light_gate` + HA 不可达策略(fail-open/fail-closed)
- 快照保留天数 `vision_snapshot_retention_days`

### 块 4:运行状态(P4 接入后启用)
- 每房间:灯态(亮/灭图标)、门槛是否放行、今日 VLM 调用数、上次识别摘要、skip(light_off) 次数
- 顶栏 healthChips 可后续加「CAM」状态灯(可选)

### 注册五件套(照 signal_rules)
1. `static/js/api.js` 增 `visionConfig/visionCameraTest/visionTestLlm/visionStatus/visionLights`
2. `static/js/pages/vision.js` 新建页面组件
3. `static/js/store.js` `ROUTES` 追加 `'vision'`
4. `static/js/main.js` import + NAV 项(图标:摄像头)+ `Alpine.data('visionPage', ...)`
5. `static/index.html` `<template x-if="route==='vision'">` 块;`static/sw.js` 预缓存追加

---

## 六、实施阶段

| 阶段 | 内容 | 工时 | 验收 |
|---|---|---|---|
| **P1 配置契约**(先行) | config.py 字段 + config_routes 白名单/掩码 + vision_routes 的 config/lights/status 端点 | 0.5d | `GET /api/vision/config` 返回掩码配置;POST 保存后重启不丢;`lights?room=客厅` 列出灯实体 |
| **P2 设置页** | vision.js 三块 UI + 测试按钮联调 + 五件套注册 | 1d | 摄像头测试返回预览帧;test-llm 返回 VLM 回答或失效提示;全部配置可保存回显 |
| **P3 后端识别链**(spec P1+P3) | go2rtc 客户端、doubao 客户端、behavior_events 表、节流闸门(光线门槛置于首位)、无 TV 巡检定时器 | 1.5d | 手动 analyze 客厅 → behavior_events 落库含 action;连触 5 次实际 VLM ≤2;**灯全关房间 tick 被 skip 且 reason=light_off**;开灯后下一 tick 恢复识别 |
| **P4 状态与收尾** | 前端状态块接入 /api/vision/status;文档更新;部署 NAS + 重启验证 | 0.5d | 状态页实时反映灯态与调用数;生产日志无 ASGI/异常错误 |

依赖关系:P1→P2 可与 P3 并行(契约冻结后);P4 收尾。

---

## 七、验收清单(光线门槛专项)

1. 小黄人房间灯全关 → 巡检 tick 跳过,`status` 显示 `gate=blocked, reason=light_off`,go2rtc 无取帧、doubao 零调用。
2. 打开该房间任一配置灯 → 下一巡检周期正常识别;若配置了 HA 开灯自动化,则开灯 ~10s 内出事件。
3. 客厅(TV 房)有人脸事件但灯全关 → 事件入库但 `status=skipped_light`(或按 spec 记 `vlm_failed` 变体),不调 VLM。
4. 未配置灯实体的房间 → 门槛不拦截,状态页黄牌提示。
5. `force:true` 手动识别绕过门槛,审计记录 `manual_bypass_gate`。
6. HA 停机 → 放行 + degraded 标记,不产生误拦截告警风暴。

---

## 八、风险与对策

| 风险 | 对策 |
|---|---|
| `WRITABLE_FIELDS` 漏加导致前端保存静默丢失 | P1 先行冻结契约;保存接口对未知 vision 键返回警告而非静默 |
| 米家摄像头暗光画面废帧浪费 VLM | 门槛在拉帧前拦截;VLM prompt 含 `snapshot_quality: dim` 自评,低质结果降置信入库 |
| doubao 会话失效(历史 710020702) | test-llm 一键体检;失败退避(60/120/240s…上限 30min);UI 常驻「需扫码重登」指引 |
| go2rtc 流名为中文 | 统一 URL-encode;测试按钮暴露原始错误便于排障 |
| 灯实体被排除采集/离线导致误判 | 门槛读 `cfg.rooms` 全量灯实体(与采集启停解耦);灯态查询失败按 fail-open 处理并计数 |
| 隐私顾虑 | 快照留存天数可配;总开关一键停;设备 Token 与 WebUI/MCP 隔离(沿 spec §10) |

---

## 九、明确不做(边界)

- 不在设置页实现 TV 端对接与行为查询(属 spec P2/P4:`/api/events/face`、`/api/behaviors`)
- 不改 doubao2api 任何代码
- 不做持续视频流/MJPEG(沿 spec:仅单帧)
- 内置 llm_backends 不承载视觉调用,两套配置物理隔离
