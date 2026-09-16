# 个性化 mytv — memory-agent 对接文档

> 编写时间：2026-09-07
> 最后更新：2026-09-07（确认决策：MQTT 主动推送、默认保留90天UI可设定、数据模型含前台状态/用户/节目）
> 对接方：memory-agent（多模态视觉识别 + 行为记忆）
> 项目：个性化 mytv（基于开源 yaoxieyoulei/mytv-android 二次开发）
> 包名：com.tvcam.mytv
> 电视 IP：192.168.2.238
> mytv HTTP API 端口：10481
> MQTT Broker：tcp://192.168.2.200:1883（账号 butler）

---

## 一、对接概述

个性化 mytv 记录用户观看历史数据，并通过 MQTT 主动推送给 memory-agent，同时提供 HTTP API 用于批量查询和回填。用于：
1. **用户行为画像**：谁在什么时间看了什么频道、看了多久、mytv 是否在前台
2. **个性化推荐**：基于观看历史推荐频道和节目
3. **多模态上下文补充**：结合电视画面截图 + 观看历史，更准确理解用户场景
4. **人脸身份联动**：Arcface 人脸识别到用户后，mytv 切换到该用户的个性化模式

---

## 二、观看历史数据模型

> **注意：** 数据模型为初步设计，细节（如节目粒度、多用户同时观看、前台状态与观看的关系）待后续专门讨论一次后定稿。以下为当前设想。

### 2.1 观看会话（WatchSession）

每次连续观看一个频道为一个会话，切换频道或暂停超过阈值（默认 5 分钟）则结束当前会话。

```json
{
  "session_id": "ws_20260907_201000_a1b2c3",
  "user": "lidicn",
  "channel": {
    "name": "湖南卫视",
    "no": 24,
    "group": "卫视"
  },
  "epg": {
    "title": "快乐大本营",
    "start": "2026-09-07T20:00:00+08:00",
    "end": "2026-09-07T21:30:00+08:00"
  },
  "started_at": "2026-09-07T20:10:00+08:00",
  "ended_at": "2026-09-07T21:00:00+08:00",
  "duration_s": 3000,
  "foreground_duration_s": 2800,
  "background_duration_s": 200,
  "source": "voice_zap",
  "completed": true,
  "ts": 1757246400000
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| session_id | string | 会话唯一 ID |
| user | string | 观看用户（face_id 登录时），未登录为 "guest" |
| channel | object | 频道信息（name/no/group） |
| epg | object | 会话开始时的 EPG 节目（EPG 可用时），可能为 null |
| started_at | string | 开始时间 ISO 8601 |
| ended_at | string | 结束时间，进行中为 null |
| duration_s | int | 总时长（秒），从 started_at 到 ended_at |
| foreground_duration_s | int | mytv 在前台且播放中的时长（秒），排除后台和暂停 |
| background_duration_s | int | mytv 在后台的时长（秒），后台时可能仍在播放音频 |
| source | string | 切换来源：`voice_zap`(语音换台) / `manual`(手动按键) / `face_recommend`(人脸推荐) / `boot_resume`(开机恢复) / `api`(API/MQTT调用) |
| completed | bool | 会话是否已结束 |

**关于前台状态的记录（补充需求）：**
- mytv 需独立记录前台/后台状态变化，不仅是观看会话的一个字段
- 前台状态变化事件（见 2.3）独立记录，可用于分析"mytv 开了但用户切到别的 App 看了多久"
- `foreground_duration_s` 是会话内的汇总，精确到秒；前台事件流提供更细粒度的时间线

### 2.2 频道切换事件（ChannelSwitchEvent）

每次换台产生一条事件记录，粒度比会话更细。

```json
{
  "event_id": "cs_20260907_201000_a1b2c3",
  "user": "lidicn",
  "from_channel": "中央一台",
  "to_channel": "湖南卫视",
  "to_channel_no": 24,
  "source": "voice_zap",
  "timestamp": "2026-09-07T20:10:00+08:00",
  "ts": 1757246400000
}
```

### 2.3 前台状态事件（ForegroundEvent）— 补充需求

记录 mytv 何时在前台/后台，独立于观看会话。

```json
{
  "event_id": "fg_20260907_200000_a1b2c3",
  "user": "lidicn",
  "foreground": true,
  "channel_at_event": "湖南卫视",
  "timestamp": "2026-09-07T20:00:00+08:00",
  "ts": 1757246400000
}
```

| 字段 | 说明 |
|------|------|
| foreground | true=进入前台，false=退到后台 |
| channel_at_event | 事件发生时当前播放的频道（可能为 null，如果未在播放） |

**用途：**
- 分析 mytv 的实际使用时长（前台时长 ≠ 开机时长）
- 结合 Arcface 的 `tv/livingroom/state`（foreground_app）交叉验证
- 判断用户是"在看电视"还是"电视开着但在看别的 App"

### 2.4 节目观看记录（ProgramWatch）— 待讨论

如果需要精确到"看了哪个节目"而非仅"看了哪个频道"，可在 EPG 可用时记录节目级别的观看。

```json
{
  "session_id": "ws_...",
  "user": "lidicn",
  "channel": "湖南卫视",
  "program_title": "快乐大本营",
  "program_start": "2026-09-07T20:00:00+08:00",
  "program_end": "2026-09-07T21:30:00+08:00",
  "watched_from": "2026-09-07T20:10:00+08:00",
  "watched_to": "2026-09-07T21:00:00+08:00",
  "watch_ratio": 0.56
}
```

> **待讨论：** 是否需要节目级别的记录？当前 EPG 覆盖不全（部分频道无节目单），节目级记录可能不完整。第一期可只做频道级（WatchSession），节目级作为第二期增强。

---

## 三、HTTP API 接口规范

基础 URL：`http://192.168.2.238:10481`

### 3.1 查询观看历史

**GET /api/history**

查询观看历史会话列表，支持按用户、时间范围、频道筛选。

**查询参数：**
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| user | string | 空 | 按用户筛选，空返回全部用户 |
| channel | string | 空 | 按频道名筛选，模糊匹配 |
| group | string | 空 | 按频道分组筛选（卫视/央视/广东等） |
| start | string | 空 | 开始时间 ISO 8601 |
| end | string | 空 | 结束时间 ISO 8601 |
| limit | int | 50 | 返回条数，最大 500 |
| offset | int | 0 | 偏移量，用于分页 |
| sort | string | desc | 排序：desc（最新优先）/ asc（最早优先）/ duration（按观看时长） |
| min_duration | int | 0 | 最小观看时长（秒），过滤掉误触的短会话 |

**响应：**
```json
{
  "ok": true,
  "total": 128,
  "returned": 50,
  "offset": 0,
  "limit": 50,
  "sessions": [
    {
      "session_id": "ws_20260907_201000_a1b2c3",
      "user": "lidicn",
      "channel": { "name": "湖南卫视", "no": 24, "group": "卫视" },
      "epg": { "title": "快乐大本营", "start": "...", "end": "..." },
      "started_at": "2026-09-07T20:10:00+08:00",
      "ended_at": "2026-09-07T21:00:00+08:00",
      "duration_s": 3000,
      "foreground_duration_s": 2800,
      "background_duration_s": 200,
      "source": "voice_zap",
      "completed": true
    }
  ],
  "summary": {
    "total_watch_s": 86400,
    "unique_users": 3,
    "unique_channels": 25,
    "top_channels": [
      { "name": "湖南卫视", "count": 12, "total_duration_s": 14400 },
      { "name": "中央一台", "count": 8, "total_duration_s": 7200 }
    ],
    "top_users": [
      { "name": "lidicn", "total_duration_s": 43200, "session_count": 45 }
    ]
  }
}
```

### 3.2 查询当前观看会话

**GET /api/history/current**

返回当前正在进行的观看会话（如果有）。未播放时 `session` 为 null。

### 3.3 查询用户观看统计

**GET /api/history/stats**

按用户或全局统计观看数据。参数：user / start / end / granularity（hour/day/week/month）

### 3.4 查询频道切换事件

**GET /api/history/switches**

查询频道切换事件流（细粒度）。

### 3.5 查询前台状态事件

**GET /api/history/foreground**

查询前台/后台状态变化事件。参数：user / start / end / limit / offset。

### 3.6 导出观看历史

**GET /api/history/export**

导出指定时间范围的观看历史为 JSON 文件。参数：start / end / user / format（json/csv）。

### 3.7 清空观看历史

**DELETE /api/history**

清空观看历史数据（需确认）。请求体：`{"confirm": true, "user": "lidicn"}`，user 为空时清空全部。

---

## 四、人脸登录联动接口

### 4.1 当前登录用户

**GET /api/user/current**

```json
{
  "ok": true,
  "user": {
    "name": "lidicn",
    "login_method": "face_id",
    "login_at": "2026-09-07T19:30:00+08:00",
    "confidence": 0.94
  }
}
```

### 4.2 用户个性化配置

**GET /api/user/profile?user=lidicn**

返回用户的收藏频道、偏好分组、隐藏频道、默认频道等。

---

## 五、数据同步策略

### 5.1 MQTT 主动推送（核心方式，已确认）

mytv 内置 MQTT 客户端（Eclipse Paho），主动向 memory-agent 推送观看事件和状态变化。这是主要的数据同步方式。

**Broker**: `tcp://192.168.2.200:1883`（账号 `butler`）
**Client ID**: `mytv-livingroom`
**Topic 前缀**: `tv/livingroom/mytv`

#### mytv 发布（mytv → memory-agent）

| Topic | QoS | Retained | 触发时机 | 说明 |
|-------|-----|----------|----------|------|
| `tv/livingroom/mytv/history/session_start` | 1 | ❌ | 新观看会话开始时 | 完整 WatchSession 对象（ended_at=null, completed=false） |
| `tv/livingroom/mytv/history/session_end` | 1 | ❌ | 观看会话结束时 | 完整 WatchSession 对象（含 ended_at, duration_s, completed=true） |
| `tv/livingroom/mytv/history/switch` | 0 | ❌ | 每次频道切换时 | ChannelSwitchEvent 对象 |
| `tv/livingroom/mytv/history/foreground` | 1 | ✅ | 前台/后台状态变化时 | ForegroundEvent 对象，retained 便于启动时获取当前状态 |
| `tv/livingroom/mytv/current` | 1 | ✅ | 换台时 + 每 30 秒 | 当前观看状态，retained |
| `tv/livingroom/mytv/status` | 1 | ✅ | 启动/退出/每 60 秒 | mytv 在线状态 |
| `tv/livingroom/mytv/user/login` | 1 | ❌ | 用户登录（face_id）时 | `{"user":"lidicn","confidence":0.94,"login_method":"face_id","timestamp":"..."}` |
| `tv/livingroom/mytv/user/logout` | 1 | ❌ | 用户登出时 | `{"user":"lidicn","reason":"face_lost"\|"manual"\|"app_exit","timestamp":"..."}` |

#### 消息格式示例

**session_start：**
```json
{
  "session_id": "ws_20260907_201000_a1b2c3",
  "user": "lidicn",
  "channel": { "name": "湖南卫视", "no": 24, "group": "卫视" },
  "epg": { "title": "快乐大本营", "start": "...", "end": "..." },
  "started_at": "2026-09-07T20:10:00+08:00",
  "ended_at": null,
  "duration_s": 0,
  "source": "voice_zap",
  "completed": false,
  "ts": 1757246400000
}
```

**session_end：**
```json
{
  "session_id": "ws_20260907_201000_a1b2c3",
  "user": "lidicn",
  "channel": { "name": "湖南卫视", "no": 24, "group": "卫视" },
  "started_at": "2026-09-07T20:10:00+08:00",
  "ended_at": "2026-09-07T21:00:00+08:00",
  "duration_s": 3000,
  "foreground_duration_s": 2800,
  "background_duration_s": 200,
  "source": "voice_zap",
  "completed": true,
  "ts": 1757249400000
}
```

**foreground：**
```json
{
  "foreground": true,
  "user": "lidicn",
  "channel_at_event": "湖南卫视",
  "timestamp": "2026-09-07T20:00:00+08:00",
  "ts": 1757246400000
}
```

### 5.2 HTTP 拉模式（补充查询）

HTTP API 作为补充，用于：
- memory-agent 启动时批量回填历史数据（MQTT retained 只有最新状态，历史会话需拉取）
- 按需查询统计报表
- 导出数据

**建议使用方式：**
1. memory-agent 启动时 → HTTP 拉取最近 N 天历史做初始化
2. 运行时 → MQTT 实时接收事件增量更新
3. 定期（如每日）→ HTTP 拉取统计做汇总校验

---

## 六、与 Arcface 人脸识别的联动

mytv 的 face_id 登录依赖 Arcface App 的人脸识别结果。

### 6.1 mytv 获取识别结果

**方案 A：HTTP 轮询 Arcface（第一期）**
- mytv 每 5 秒调用 `http://192.168.2.238:8080/api/scan/latest`
- 识别到已注册用户且置信度 ≥ 0.85 持续 3 秒 → 自动登录
- 用户离开画面 30 秒 → 自动登出（或切换到 guest 模式）

**方案 B：MQTT 订阅（第二期）**
- mytv 订阅 `tv/livingroom/face` topic，实时获取识别结果

### 6.2 多人同时在时的处理

- mytv 取置信度最高且在画面中持续时间最长的用户作为当前登录用户
- 可配置"主用户"偏好
- 其他在场用户记录到 `present_users`（第二期）

### 6.3 登录后的个性化行为

1. 频道列表重排（收藏+常看置顶）
2. 默认频道自动播放
3. 隐藏频道过滤
4. 观看历史关联到该用户
5. UI 显示当前登录用户名

---

## 七、数据存储与隐私

### 7.1 存储方案

- mytv 本地使用 **Room (SQLite)** 存储观看历史
- 表结构：`watch_sessions`、`channel_switches`、`foreground_events`、`user_profiles`
- 数据保留策略：**默认 90 天，可在 mytv 设置 UI 中调整**（可选 30/90/180/365 天或永久保留）
- 自动清理：每日凌晨检查，清理超过保留时长的数据
- 数据导出：支持 JSON/CSV 格式导出
- 设置项持久化：SharedPreferences，key 为 `history_retention_days`

### 7.2 隐私考虑

- 观看历史仅存储在电视本地，内网传输
- 可配置 `watch_history_enabled` 关闭某用户的历史记录
- 不收集用户人脸图像，只记录 Arcface 返回的用户名和置信度

---

## 八、memory-agent 侧建议的使用场景

### 8.1 行为分析问答
用户问"我昨天晚上看了什么" → MQTT 实时数据或 HTTP 历史查询 → 按时间线回答

### 8.2 个性化推荐
基于 top_channels + 当前 EPG 推荐，可直接调用 mytv 换台

### 8.3 多模态场景理解
结合当前观看（MQTT current）+ 人脸（Arcface）+ 画面截图（Arcface screenshot）

### 8.4 家庭成员行为画像
定期汇总各用户观看数据，生成周报或调整自动化场景

---

## 九、开发分期建议

| 阶段 | 内容 | memory-agent 对接 |
|------|------|-------------------|
| **第一期** | MQTT 客户端 + 观看历史记录（Room SQLite：watch_sessions / channel_switches / foreground_events）+ MQTT 主动推送（session_start/end/switch/foreground/current/status）+ HTTP /api/history 系列查询接口 + 保留时长 UI 设置 | MQTT 订阅实时事件 + HTTP 批量回填，行为分析问答可用 |
| **第二期** | face_id 登录 + 个性化频道列表 + 用户配置接口 + user/login user/logout MQTT 事件 | 用户身份关联，个性化推荐 |
| **第三期** | 节目级观看记录（ProgramWatch，EPG 可用时）+ 多人在场记录 + 数据导出/导入优化 | 更细粒度的行为分析 |

---

## 十、已确认决策（2026-09-07）

| 事项 | 决策 |
|------|------|
| 通信方式 | MQTT 主动推送为核心 + HTTP 拉模式补充查询 |
| 数据保留时长 | 默认 90 天，mytv 设置 UI 可调整（30/90/180/365/永久） |
| 数据模型 | 包含：观看会话（频道级）、频道切换事件、前台状态事件；用户账号通过 face_id 登录关联 |
| 包名 | com.tvcam.mytv |
| 截图 | 第一期复用 Arcface `/api/screenshot`，mytv 不自实现 |

---

## 十一、待讨论事项（数据模型细节，后续专门讨论一次）

1. **节目级记录**：是否需要 ProgramWatch（精确到看了哪个节目）？当前 EPG 覆盖不全，第一期是否只做频道级？
2. **前台状态与观看的关系**：mytv 在后台时音频可能仍在播放，这段时间算"观看"吗？foreground/background 分开记录是否够用？
3. **guest 模式数据**：未识别到用户时的观看记录归到 "guest"，还是不记录？guest 数据是否参与家庭整体统计？
4. **多人同时观看归属**：Arcface 识别到多人时，观看记录算谁的？记录主用户 + present_users 数组？还是标记为"共同观看"？
5. **暂停/缓冲处理**：暂停超过多久算会话结束？缓冲时间算观看时长吗？
6. **与 behavior_events 表的关系**：mytv 的观看历史是否需要写入 memory-agent 的 behavior_events 表？还是独立存储通过 MQTT/API 查询？如果写入，event_type 用什么？
7. **数据回填机制**：memory-agent 重启后，MQTT retained 只有最新状态，历史会话如何回填？HTTP 拉取最近 N 天？还是 mytv 保留"未同步会话"队列？
8. **EPG 数据质量**：当前 EPG 覆盖不全，epg 字段经常为 null，是否可接受？是否需要更换 EPG 源？

---

*文档结束。如有疑问请联系 TV_CAM 开发者。*
