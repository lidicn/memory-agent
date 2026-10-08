# 交接单：MA ↔ 豆包管家 对接「成员档案 + 在场查询」

> 提出方：豆包管家（doubao-butler, 192.168.2.200:8095）
> 接收方：memory-agent（MA, 192.168.2.200:8086）
> 日期：2026-09-03 · 优先级：高

## 背景

豆包管家要做「拟人化主动打招呼」：早/中/晚按成员作息个性化问候（含课程提醒、"今天怎么这么早"等）。
按现有分工：**眼睛与记忆归 MA，耳朵与表达归豆包管家**。管家不建感知、不建记忆库，
需要 MA 补齐 **3 个窄接口**（均为对既有能力的薄封装，改动很小）。

## 需求 1：成员档案扩展（核心）

### 现状

`members` 表已有 name / avatar / note / appearance_json / face_feature，
但**没有作息、兴趣、课程等档案字段**。

### 请求

`members` 表新增一列 `profile_json`（TEXT，自由 JSON，schema 由管家定义，MA 不解释内容），
并暴露读写 API：

```
GET  /api/members                     # 已有，响应中带上 profile_json
GET  /api/members/{id}                # 已有，响应中带上 profile_json
PATCH /api/members/{id}               # 新增：支持更新 note / profile_json
```

### profile_json 约定 schema（v1）

```json
{
  "nickname": "爱美丽",
  "school": "黄麻布学校五年级",
  "routine": {
    "weekday": {
      "wake": "07:00", "leave_morning": "07:40",
      "return_lunch": "12:10", "leave_afternoon": "13:30",
      "return_evening": ["19:00", "22:00"]
    },
    "weekend": { "wake": "08:30", "note": "上午可能有兴趣班" }
  },
  "courses": {
    "mon": ["语文", "体育", "英语", "语文", "数学", "音乐", "道法", "延时英语"],
    "tue": ["信息", "英语", "数学", "英语", "体育"],
    "wed": ["语文阅读", "数学", "数学", "语文", "体育"],
    "thu": ["数学", "数学", "数学", "体育", "语文"],
    "fri": ["数学", "英语", "劳动", "语文", "体育"]
  },
  "interests": ["画画"],
  " reminders": ["早上出门提醒带学习用品", "作业未完成时温和提醒"]
}
```

> 字段解释权归管家；MA 只做透明存取。courses 等内容由管家侧 LLM
> 从自由文本/课表文档整理生成后 PATCH 过来，MA 无需理解。

## 需求 2：在场查询 API（触发源）

### 现状

`behavior_events` 已记录每次识别结果，但管家轮询需要按人员过滤的轻量查询。

### 请求

```
GET /api/vision/presence?room=客厅&minutes=10
→ { "ok": true,
    "items": [ { "name": "Emily", "via": "arcface", "confidence": 0.9,
                 "last_seen": "2026-09-03T18:32:10", "room": "客厅" } ] }
```

- 语义：最近 N 分钟内各成员最后被识别到的时间（含 via=arcface / appearance_matched）。
  实现可直接查 `behavior_events`（persons_json）+ TV 人脸事件，**无需新表**。
- 用途：管家每 30~60s 轮询一次，发现「某成员新出现且该时段配额未用完」→ 触发问候。
  （如后续 MA 愿意在 `record_face_event` 后加 HTTP 回调/MQTT 转发可替代轮询，非必需，另行沟通。）

## 需求 3（可选，低优先）：作息规律的自动佐证

MA 已有 `detected_activities` / insights 能力。若能按成员输出「最近 N 天回家时间分布」类摘要
（`GET /api/insights/member-schedule?name=Kevin&days=14`），管家可用实测数据校准作息档
（比如发现 Kevin 实际常 19:30 回家，就把问候基准调早）。非必需，可后续迭代。

## ## 需求 4：butler→MA 富化事件推送（感知层边界，v0.8 §〇）

### 场景

MA 保留 HA 直连被动日志（保底、全量、不依赖 butler 存活）；butler 额外做「主动/上下文富化」感知。
凡 butler 主动感知到、MA 被动日志会漏掉的高价值事件（在场/动作/上下文），经本接口推给 MA，
合并进同一张 `behavior_events`，供在场查询与作息洞察消费。

> **边界（铁律，见 v0.8 §〇）**：本接口**只收富化事件**，不收设备状态原始事件——
> MA 已有 HA 直连兜底，避免双写、双轮询、状态分叉。MA 记忆**不硬依赖 butler 存活**。

### 接口

```
POST /api/events
Authorization: Bearer <butler_token>
Content-Type: application/json
```

请求体（推荐数组批量；单条对象或裸数组均可）：

```json
{
  "events": [
    { "room": "客厅", "action": "出现", "persons": ["爸爸"],
      "confidence": 0.92, "scene": "沙发", "server_ts": "2026-09-13T21:05:00",
      "camera_src": "butler", "trigger": "butler", "status": "ok" }
  ]
}
```

字段约定：

| 字段 | 必填 | 说明 |
|---|---|---|
| `room` | 否 | 房间/区域；空则不过滤，presence 仍按 persons 分桶 |
| `action` | 否 | 动作语义（出现/离开/坐下…），自由字符串，MA 不解释 |
| `persons` | 否 | 已识别成员名数组；**未识别不填**（presence 查询会跳过未识别）。缺省 `[]` |
| `confidence` | 否 | 0~1 置信度，原样入库 |
| `scene` | 否 | 上下文场景（沙发/书桌…），自由字符串 |
| `server_ts` | 否 | 事件时间（ISO 本地时区）。缺省取 MA 接收时刻；`day` 由 MA 自动推导 |
| `camera_src` | 否 | 来源标记，缺省 `butler`（落库后可用此字段区分 butler 推送 vs MA 被动日志） |
| `trigger` | 否 | 触发源，缺省 `butler` |
| `status` | 否 | 事件状态，缺省 `ok` |

响应：`{ "ok": true, "inserted": 1, "total": 1 }`

- 鉴权：复用 butler_token 白名单（见「一、鉴权」）。路径命中 → 200；令牌无效/为空 → 401；令牌正确但路径越权 → 403。
- WebUI 管理员（JWT）亦可调用本接口，用于调试/手动补录；设备令牌、APP 令牌被各自通道拦截，无法触达。
- 单条写入失败不影响整批（失败项跳过，仍返回 `inserted` 计数）。

非目标（明确不做）

- MA 不做：TTS、问候配额、对话状态机、时段窗口判断 —— 全部归管家
- 管家不做：取帧、VLM、人脸识别、语义记忆库 —— 全部走 MA 现有能力
- 不合并两个项目：契约隔离，各自迭代

## 验收

1. PATCH members/{id} 写入 profile_json 后 GET 能原样读回
2. presence 接口在 TV 前站人 10s 内能查到该成员（含 ArcFace via）
3. 现有功能（视觉巡检、人脸事件、语义记忆）回归无异常
4. butler 经 `POST /api/events` 推送的富化事件落库 `behavior_events`（`camera_src=butler`）；匿名/错误令牌 → 401，令牌正确但路径越权 → 403（见需求 4）

---

# MA 侧交付回执（2026-09-03，已实现并上线）

## 一、鉴权：外部服务专用令牌（新增，需管家配合）

MA 的 WebUI 接口此前只有 JWT（WebUI 登录）与设备令牌（TV/节点上报）两套凭据，
都不适合给管家复用（一个要登录态，一个是设备上报通道）。因此新增第三种：

| 项 | 值 |
|---|---|
| 配置项 | `butler_token`（WebUI 设置页可写，或 compose 环境变量 `BUTLER_TOKEN`） |
| 用法 | `Authorization: Bearer <butler_token>` |
| 放行路径（白名单） | `GET /api/members`、`GET/PATCH /api/members/{id}`、`GET /api/vision/presence`、`GET /api/insights/member-schedule`、`POST /api/events` |
| 白名单外 | 一律 **403**（即使令牌正确） |
| 未配置 | 通道关闭，任何 Bearer 都走原有 401 |

令牌正确但路径越权 → 403；令牌错误/为空 → 401（与「未登录」一致）。

> 令牌值由 MA 侧生成后交给管家（建议 32 字节随机串）。

## 二、需求 1：成员档案 profile_json —— 已完成

- `members` 表新增 `profile_json TEXT`（含旧库自动 `ALTER TABLE` 迁移，无需手工操作）
- `PATCH /api/members/{id}` 只更新 body 里出现的字段（带 `profile_json` 来不会覆盖 note/头像）
- `GET /api/members`、`GET /api/members/{id}` 响应同时给出：
  - `profile_json`：入库原文（字符串），保证「原样读回」
  - `profile`：同名对象视图，省去调用方 `json.loads`
- MA **不解释** profile 内容，只做一次「必须是合法 JSON」的校验：
  非法 JSON 直接 400，不会静默清空已有档案
- `appearance_json`（外观档案）与 `face_feature`（ArcSoft 特征）走各自既有通道，不在 PATCH 开放

**管家侧调用示例**

```bash
curl -X PATCH http://192.168.2.200:8086/api/members/<id> \
  -H "Authorization: Bearer $BUTLER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"profile_json":{"nickname":"爱美丽","school":"黄麻布学校五年级","routine":{...},"courses":{...}}}'
```

## 三、需求 2：在场查询 —— 已完成

```
GET /api/vision/presence?room=客厅&minutes=10
```

响应：

```json
{
  "ok": true, "room": "客厅", "window_minutes": 10,
  "since": "2026-09-03T18:22:10", "now": "2026-09-03T18:32:10",
  "count": 1,
  "items": [
    { "name": "Emily", "member_id": "3f9a...", "via": "arcface",
      "via_raw": "face", "confidence": 0.9,
      "last_seen": "2026-09-03T18:32:10", "room": "客厅",
      "trigger": "identity_change" }
  ],
  "fusion": {
    "enabled": true,
    "method": "elimination",
    "home_count": 3,
    "occupancy": { "客厅": {"members":["lidicn","Emily"],"unknown":0,"count":2},
                   "书房": {"members":[],"unknown":1,"count":1} },
    "inferred": [ { "member": "Kevin", "room": "书房", "confidence": 0.9,
                    "method": "elimination",
                    "reason": "消除法：其余成员已确认在 客厅；名册余 Kevin；书房未识别 1 人 = 缺席人数 → 推断在此" } ],
    "unresolved_unknown": 0,
    "known_present": ["Emily","lidicn"],
    "absent": ["Kevin"]
  }
}
```

实现说明（与交接单一致，未建新表）：

- 数据源 `behavior_events.persons_json`，TV 人脸事件与视觉巡检都写这里
- `via` 归一化：TV 端上报的 `face` 与服务端补认的 `arcface` 统一为 `arcface`；
  外观匹配为 `appearance_matched`；原始值保留在 `via_raw`
- **未识别身份不返回**（`未识别` / `未识别成员` / `陌生人` / `unknown`）——
  管家只问候「认出是谁」的人，未识别的不该触发称呼式问候
- 同名成员只保留窗口内**最近一次**；`items` 按 `last_seen` 倒序
- 窗口左闭（`server_ts >= since`），`minutes` 取值 0.5 ~ 1440，默认 10

### `fusion`：在场融合 + 名册消除法（v0.7.5 新增，butler 可直接消费）

`items` 只给「已识别的人」。若某房间只有「人数」没有「名字」（如书房 1 人未识别），
单看 `items` 无法回答「谁在书房」。故在场查询**额外返回 `fusion` 字段**（纯确定性规则、
零 LLM，不影响 `items` 既有契约）：

- 取各房间窗口内**最新一条**行为事件作为占用快照 `occupancy`（含 `members` 已识别名 +
  `unknown` 未识别人数 = `count - len(members)`）。**注意 `fusion` 始终基于全房子视图，
  不受 `?room=` 过滤影响**——消除法需要「客厅有谁」才能推「书房是谁」。
- `known_present` = 所有房间已识别成员并集；`absent` = 名册全集 − known_present。
- **消除法（确定性）**：当 `Σ unknown == len(absent)` 时，把每个未识别占位归给一名缺席成员，
  按成员关联房间 `members.rooms` / 外观档案 `typical_location` 加权（命中先验 → 置信 0.9，
  无先验 → 0.7），结果进 `inferred`。
- **等式不成立时保守**：`method:"inconclusive"`，只报 `unresolved_unknown` 人数，**不编造身份**
  （名册缺录访客 / 多人同室未识别时即落此分支）。
- `home_count` = 确定在场人数（known_present + inferred 去重）。

依赖：名册完整性（`members` 表）。若有未录入的访客，消除法会把「访客」误判成「缺席成员」——
此时 `method` 仍为 `elimination` 但结果含访客，管家侧宜结合 `unresolved_unknown` 与已知访客名单判断。

## 四、需求 3（可选）：作息实测摘要 —— 已完成

```
GET /api/insights/member-schedule?name=Kevin&days=14
```

```json
{ "ok": true, "name": "Kevin", "days": 14,
  "samples": [ { "day": "2026-09-03", "first_seen": "2026-09-03T08:05:00",
                 "last_seen": "2026-09-03T21:00:00", "appearances": 3,
                 "rooms": ["客厅"] } ],
  "summary": { "days_with_data": 7, "days_requested": 14,
               "median_first_seen": "08:05", "median_last_seen": "21:00" } }
```

⚠️ `appearances` 是当天命中该成员的事件条数，受巡检频率与冷却限制，
**不等于真实进出次数**，只作趋势参考；校准作息建议看 `median_*`。
无数据时返回空 `samples` 并附 `hint`。

## 交付回执：需求 4（butler→MA 富化事件推送，2026-09-13 已实现并上线）

- 新增 `src/memory_agent/api/events_routes.py`：`POST /api/events`，复用 `store.insert_behavior_event`
  （自动填 `server_ts`/`day`、persons 序列化、`camera_src`/`trigger` 默认 `butler`）；
  兼容 `{events:[...]}` 与裸数组 body；单条失败不中断整批。
- `BUTLER_ENDPOINTS` 白名单加入 `/api/events`，越权沿用 403。
- 边界（铁律，见 v0.8 §〇）：**只收富化事件（在场/动作/上下文），不收设备状态原始事件**；MA 记忆不硬依赖 butler 存活。
- 端到端验证：butler_token → 200、`inserted:1`、落库 `(房间, action, persons_json, camera_src=butler, trigger=butler, status=ok)`；
  匿名/错误令牌 → 401。
- 管家侧调用示例：

  ```bash
  curl -X POST http://192.168.2.200:8086/api/events \
    -H "Authorization: Bearer $BUTLER_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"events":[{"room":"客厅","action":"出现","persons":["爸爸"],"confidence":0.92,"scene":"沙发","server_ts":"2026-09-13T21:05:00"}]}'
  ```

## 五、改动清单

| 文件 | 改动 |
|---|---|
| `src/memory_agent/store.py` | `members.profile_json` 建表 + 旧库迁移；`_normalize_profile` / `_parse_json_field`；`update_member` 支持 profile_json（非法 JSON 抛 `ValueError`）；新增 `recent_presence()`、`member_schedule()`、`recent_occupancy()`（v0.7.5 在场融合用，取各房间窗口内最新占用快照）；`get_member`/`list_members` 附带 `profile` 视图 |
| `src/memory_agent/config.py` | 新增 `butler_token` 字段 + `BUTLER_TOKEN` 环境变量 |
| `src/memory_agent/app.py` | `AuthMiddleware` 新增管家令牌分支（`secrets.compare_digest`）与 `BUTLER_ENDPOINTS` 白名单（本次新增 `POST /api/events`）；`_reject` 支持自定义状态码/文案 |
| `src/memory_agent/api/member_routes.py` | 新增 `PATCH /api/members/{id}`；管家视角剔除 `face_feature`；PUT 同步捕获 `ValueError` |
| `src/memory_agent/api/vision_routes.py` | 新增 `GET /api/vision/presence`；v0.7.5 起响应追加 `fusion` 在场融合字段（`recent_occupancy` + `presence_fusion.fuse_presence`） |
| `src/memory_agent/presence_fusion.py` | **新增**（v0.7.5）：`fuse_presence(roster, occupancy)` 纯确定性在岗融合 + 名册消除法身份推断，零 LLM |
| `src/memory_agent/api/insight_routes.py` | 新增 `GET /api/insights/member-schedule` |
| `src/memory_agent/api/events_routes.py` | 新增 `POST /api/events`（butler→MA 富化事件推送，复用 `store.insert_behavior_event`，仅 butler 令牌白名单可触达） |
| `src/memory_agent/api/config_routes.py` | `butler_token` 进可写字段 / 密钥掩码 / 配置读取 |
| `docker-compose.yml` | 注入 `BUTLER_TOKEN: "${BUTLER_TOKEN:-}"` |
| `tests/test_butler_api.py` | 新增：档案读写/校验、在场查询（去重/过滤/归一化/排序）、作息摘要 共 14 例 |
| `tests/test_butler_auth.py` | 新增：管家令牌白名单与越权/无效/未配置/匿名 共 5 例 |
| `tests/test_presence_fusion.py` | **新增**（v0.7.5）：`fuse_presence` 消除法推断 / 无未知不推断 / 人数不匹配保守不编造 / 未识别占位不计入；`recent_occupancy` 取各房间最新一条 + 端到端融合 共 5 例 |

## 六、如何验证

```bash
# 1. 档案写入后原样读回
curl -s http://192.168.2.200:8086/api/members -H "Authorization: Bearer $BUTLER_TOKEN" | jq '.members[0].profile'
# 2. 在场查询（TV 前站人后）
curl -s "http://192.168.2.200:8086/api/vision/presence?minutes=10" -H "Authorization: Bearer $BUTLER_TOKEN" | jq
# 3. 越权应当 403
curl -s -o /dev/null -w "%{http_code}" http://192.168.2.200:8086/api/config -H "Authorization: Bearer $BUTLER_TOKEN"
# 期望 403
```

本地 `python -m pytest tests/ -q` 39 项通过（排除 4 项与本次改动无关的既有失败：
本地 mcp SDK 是 1.x、项目要求 mcp>=2.0，容器内为 2.0，故 ACP/MCP 相关用例如此）。

## 七、已知风险 / 遗留

1. **在场是「识别到」而非「人在」**：受巡检间隔与冷却限制，窗口内没有新事件不代表人已离开，
   建议管家用自己的配额/去抖逻辑兜底（轮询推荐 30~60s）。
2. **推送替代轮询未做**：交接单里提到 `record_face_event` 后加 HTTP 回调/MQTT 可替代轮询，
   本次未实现（保持最小改动）。若轮询压力或延迟成为问题，下一轮再议。
3. **`profile_json` 无 schema 校验**：按约定 MA 不解释内容，只保证是合法 JSON；
   字段写错只能由管家侧自检。
4. **管家视角已剔除 `face_feature`**：生物特征 blob 只留在节点同步通道，不随成员列表外发。
5. 令牌为长期静态凭据，目前无过期/轮换机制；泄露后需在 MA 设置页重新生成并同步给管家。
6. 事件推送边界：`POST /api/events` 只收富化事件（在场/动作/上下文），**不收设备状态原始事件**
   （HA 直连兜底在 MA 侧，不在本接口）；butler 离线不影响 MA 记忆自洽与洞察生成。
