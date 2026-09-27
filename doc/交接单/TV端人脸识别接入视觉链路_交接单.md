# Memory Agent 视觉链路 — TV 端人脸识别接入交接单

> 版本：v1.0
> 日期：2026-09-02
> 状态：待 memory-agent 开发者确认
> 目标：将 TV 端 ArcFace 人脸识别接入 memory-agent 视觉链路，实现"人脸优先 + VLM 场景分析"的融合识别

---

## 1. 背景与目标

### 1.1 现状

memory-agent 已实现完整的 VLM 多模态视觉分析链路（`vision_service.py`），包括：
- `patrol_loop`：定时巡逻各房间，拉取摄像头帧
- `vlm_analyze`：调用豆包多模态 API 分析画面（动作/姿态/场景/互动）
- `analyze_room`：综合分析房间（人脸 + VLM）
- `record_face_event`：接收人脸事件
- `resolve_appearance_to_member`：外观匹配成员（无 TV 人脸时的降级方案）

**但当前视觉链路未启用**（`vision_enabled=False`），且未接入 TV 端人脸识别。

### 1.2 TV 端现状

TV 端（`192.168.2.238`）已运行 ArcFace 人脸识别服务：
- 实时识别客厅人员（姓名、相似度、年龄、性别）
- HTTP API：`http://192.168.2.238:8080/api/scan/latest`
- 跨路融合：TV 路 USB 摄像头 + 米家摄像头（go2rtc）
- 状态机：候选→确认→稳定→离开
- 热点 ROI：动态调整检测区域

### 1.3 目标

将 TV 端人脸识别接入 memory-agent 视觉链路：
1. TV 端识别到人脸时，主动上报到 memory-agent（`/api/events/face`）
2. memory-agent 收到人脸事件后，触发 VLM 分析（拉取摄像头帧 + 已知身份）
3. VLM 分析时，身份由 TV 端 ArcFace 提供（高置信），VLM 只分析动作/场景
4. 分析结果存入行为记录，供 Node-RED / 豆包管家调用

---

## 2. 架构设计

### 2.1 数据流

```
┌──────────────┐                         ┌──────────────────────┐
│  TV 端 APK    │                         │   Memory Agent        │
│              │                         │                      │
│  ArcFace     │   1. 人脸事件上报       │  /api/events/face     │
│  实时识别     │ ─────────────────────► │  (设备令牌鉴权)       │
│              │   POST /api/events/face │                      │
│  姓名/相似度  │                         │  2. 触发 VLM 分析     │
│  年龄/性别    │                         │  analyze_room()       │
│  位置/状态    │                         │                      │
└──────────────┘                         │  3. 拉取摄像头帧      │
                                         │  fetch_frame(go2rtc)  │
                                         │                      │
                                         │  4. VLM 分析          │
                                         │  vlm_analyze()        │
                                         │  (已知身份，只分析动作) │
                                         │                      │
                                         │  5. 存储行为记录       │
                                         │  store.list_behavior_events
                                         │                      │
                                         │  6. 供外部查询         │
                                         │  GET /api/behaviors   │
                                         └──────────┬───────────┘
                                                    │
                                                    ▼
                                         ┌──────────────────────┐
                                         │  Node-RED / 豆包管家  │
                                         │  调取行为记录触发自动化 │
                                         └──────────────────────┘
```

### 2.2 两种触发模式

| 模式 | 触发条件 | 说明 |
|------|---------|------|
| **事件驱动** | TV 端上报人脸事件 | 有人脸时即时分析，延迟低 |
| **定时巡逻** | 无 TV 人脸的房间，每 5 分钟 | 降级方案，纯 VLM + 外观匹配 |

---

## 3. TV 端需要完成的工作

### 3.1 配置 memory-agent 连接

在 TV 端 `AppConfig` 中增加/确认以下配置：

| 配置项 | 当前值 | 说明 |
|--------|--------|------|
| `memory_agent_base` | `""`（空） | **需要设置**：`http://192.168.2.200:8086` |
| `vision_device_token` | - | **需要 memory-agent 生成**：设备令牌 |
| `node_id` | `tv-livingroom` | 节点 ID |
| `node_type` | `tv` | 节点类型 |
| `node_endpoint` | `""`（空） | **需要设置**：`http://192.168.2.238:8080` |

### 3.2 人脸事件上报

TV 端在识别到人脸时（状态机进入 `confirmed` 或 `stable`），调用 memory-agent：

```
POST http://192.168.2.200:8086/api/events/face
Headers: Authorization: Bearer <vision_device_token>
Body:
{
  "room": "客厅",
  "trigger": "face_detected",
  "ts": 1725264000000,
  "persons": [
    {
      "name": "Kevin",
      "score": 0.923,
      "age": 13,
      "gender": 0,
      "rect": {"left": 1236, "top": 608, "right": 1380, "bottom": 752},
      "state": "stable"
    }
  ],
  "camera": {
    "source": "tv_usb",
    "resolution": "1920x1080"
  }
}
```

**上报策略**：
- 状态机进入 `confirmed` 时：立即上报一次
- 状态为 `stable` 时：每 60 秒上报一次（心跳，确认人还在）
- 状态变为 `leaving` 时：上报一次（触发离开场景分析）
- 无人脸时：不上报（memory-agent 定时巡逻兜底）

### 3.3 Face-Node 注册（可选，中长期）

TV 端可注册为 memory-agent 的 Face-Node-Pool 节点，使 memory-agent 可主动调用 TV 端人脸识别：

```
POST http://192.168.2.200:8086/api/face/node/register
Body:
{
  "node_id": "tv-livingroom",
  "type": "tv",
  "capability": "arcface",
  "endpoint": "http://192.168.2.238:8080"
}
```

注册后，memory-agent 可调用 `http://192.168.2.238:8080/api/scan/latest` 获取实时识别结果。

---

## 4. Memory Agent 需要完成的工作

### 4.1 启用视觉链路

在 `.env` 或配置中设置：
```
VISION_ENABLED=true
VISION_DEVICE_TOKEN=<生成一个随机令牌>
VLM_BASE_URL=http://192.168.2.200:9090
VLM_MODEL=doubao
```

### 4.2 配置客厅摄像头

在 `vision_cameras` 配置中添加客厅摄像头：

```python
vision_cameras = [
    {
        "room": "客厅",
        "stream": "http://192.168.2.200:1984/api/stream.mjpeg?src=livingroom_mjpeg",
        "enabled": True,
        "light_gate": True,
        "light_entity": "light.living_room_main"
    }
]
```

**摄像头源选择**：
- 优先：米家摄像头 go2rtc MJPEG 流（已有，`livingroom_mjpeg`）
- 备选：TV 端 USB 摄像头（需 TV 端提供 MJPEG 流）

### 4.3 生成设备令牌

为 TV 端生成 `vision_device_token`，配置到：
- memory-agent 端：`VISION_DEVICE_TOKEN` 环境变量
- TV 端：`AppConfig.vision_device_token`（通过 `/api/config` 设置）

### 4.4 完善 `events_face` 端点

当前 `events_face` 端点已存在（`vision_routes.py:155`），需要确认：
1. 设备令牌鉴权是否正常工作
2. 收到人脸事件后是否正确触发 `analyze_room`
3. `persons` 数据格式是否与 `record_face_event` 期望的一致

### 4.5 VLM Prompt 优化（人脸优先模式）

当有人脸事件时，VLM prompt 应包含已知身份（当前 `_vlm_prompt` 已支持 `persons` 参数）：

```
你是家庭监控画面分析助手。这是客厅的摄像头画面，时间 2026-09-02 20:30。
画面中已识别到：Kevin（0.923）。人数应为 1 人。
请只输出如下 JSON，不要输出其他内容：
{"persons":[{"identity":"Kevin","action":"<10-20字动作描述>",
"posture":"坐/站/躺/走","interaction":"<与谁互动或'无'>","confidence":0.0-1.0}],
"scene":"<一句话场景概括>","snapshot_quality":"good|dim|occluded"}
注意：不要猜测未列出的人的身份；画面模糊时 confidence 调低。
```

### 4.6 行为记录查询 API

确认 `GET /api/behaviors` 端点正常工作，支持以下查询参数：
- `room`：房间名
- `member`：成员名
- `from` / `to`：日期范围
- `limit`：返回条数

---

## 5. 联调测试清单

### 5.1 基础连通性

- [ ] TV 端能访问 `http://192.168.2.200:8086/health`
- [ ] memory-agent 能访问 `http://192.168.2.238:8080/api/health`
- [ ] memory-agent 能访问 go2rtc 流 `http://192.168.2.200:1984/api/stream.mjpeg?src=livingroom_mjpeg`

### 5.2 人脸事件上报

- [ ] TV 端识别到人脸时，成功调用 `/api/events/face`
- [ ] memory-agent 收到事件，返回 202
- [ ] memory-agent 日志记录收到的人脸事件

### 5.3 VLM 分析触发

- [ ] 收到人脸事件后，memory-agent 触发 `analyze_room`
- [ ] 成功拉取摄像头帧
- [ ] 成功调用豆包 VLM API
- [ ] VLM 返回包含已知身份的分析结果

### 5.4 行为记录存储与查询

- [ ] 分析结果存入行为记录表
- [ ] `GET /api/behaviors?room=客厅` 能查到最新记录
- [ ] 记录包含：人员、动作、姿态、场景、时间戳

### 5.5 权限与安全

- [ ] 设备令牌鉴权正常（无效令牌返回 401）
- [ ] TV 端配置不包含敏感信息明文
- [ ] 日志不记录完整令牌

---

## 6. 已知问题与待确认

### 6.1 待 memory-agent 开发者确认

1. **`vision_device_token` 生成方式**：是固定配置还是动态生成？TV 端如何获取？
2. **`events_face` 端点的 `persons` 数据格式**：当前期望的字段是什么？是否需要 `age`/`gender`/`state`？
3. **人脸事件触发 VLM 分析的逻辑**：是收到事件立即分析，还是有冷却/去重？
4. **go2rtc 拉帧稳定性**：`fetch_frame` 对 MJPEG 流的支持是否稳定？超时设置？
5. **VLM API 成本控制**：每小时上限 20 次是否合理？人脸事件频繁时如何限流？

### 6.2 TV 端待开发

1. **人脸事件上报逻辑**：在 `RecognitionState` 状态机变化时触发上报
2. **memory-agent 配置**：通过 WebUI 设置 `memory_agent_base` 和 `vision_device_token`
3. **上报失败重试**：memory-agent 不可达时的降级策略（本地缓存 + 补传）
4. **Face-Node 注册**：启动时自动注册到 memory-agent（可选）

### 6.3 米家摄像头角度

当前米家摄像头位置可能无法覆盖电竞沙发，建议：
- 调整米家摄像头角度，覆盖电竞沙发区域
- 或在 TV 端 USB 摄像头基础上，增加第二个摄像头角度

---

## 7. 实施优先级

| 优先级 | 任务 | 负责方 | 工作量 |
|--------|------|--------|--------|
| P0 | memory-agent 启用视觉链路 + 配置摄像头 + 生成令牌 | memory-agent 开发者 | 0.5 天 |
| P0 | TV 端配置 memory-agent 连接 + 人脸事件上报 | TV 端开发者 | 1 天 |
| P1 | 联调测试 + VLM Prompt 优化 | 双方 | 1 天 |
| P2 | Face-Node 注册 + memory-agent 主动调用人脸识别 | 双方 | 1 天 |
| P2 | 行为记录查询 API 完善 + Node-RED 集成 | memory-agent 开发者 | 0.5 天 |

---

## 8. 参考资料

- TV 端 API：`http://192.168.2.238:8080/api/scan/latest`
- memory-agent 视觉服务：`/app/src/memory_agent/vision_service.py`
- memory-agent 视觉路由：`/app/src/memory_agent/api/vision_routes.py`
- memory-agent 人脸路由：`/app/src/memory_agent/api/face_routes.py`
- go2rtc 流：`http://192.168.2.200:1984/api/stream.mjpeg?src=livingroom_mjpeg`
- 豆包 VLM API：`http://192.168.2.200:9090`（doubao2api）

---

## 9. 联系方式

- TV 端开发者：当前 AI 助手
- memory-agent 开发者：待确认
- 测试环境：客厅（TV 192.168.2.238 + NAS 192.168.2.200）
