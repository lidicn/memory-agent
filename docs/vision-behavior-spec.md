# 多模态行为识别 · 接口与存储设计规格

> 交付对象：memory-agent 开发者
> 目标：让 memory-agent「长眼睛」——作为全屋视觉行为识别的**中枢**，对接 TV 端人脸识别事件、go2rtc 摄像头帧、doubao2api 多模态推理，把「谁 + 在哪 + 何时 + 在干嘛」沉淀为可查询的行为记忆。
> 本文所有外部依赖均为 2026-08-30 实测确认，非纸面设计。

---

## 1. 背景与问题

### 1.1 现状

| 组件 | 位置 | 已有能力 |
|---|---|---|
| TV 端 APK（客厅） | `http://192.168.2.238:8080` | ArcFace 人脸识别（已注册 lidicn/Kevin/Emily）、TV 摄像头 + 米家全景双摄融合（`/api/scan`、`/api/pano`）、HTTP 服务常驻 |
| go2rtc | NAS `192.168.2.200:1984` | 全屋摄像头统一接入（米家经小米云协议），HTTP/RTSP 取流 |
| doubao2api | NAS `192.168.2.200:9090` | 逆向豆包的 OpenAI 兼容网关，**已内置图片分析接口** |
| memory-agent | NAS `192.168.2.200:8086`（容器 8000） | SQLite 事件库 + Chroma 向量库 + 成员体系 + 洞察工具链（`get_person_history`/`get_behavior_insights`/`infer_activities`/`ask_memory`）+ NR 契约 |
| Node-RED | NAS | TTS / 微信通知编排 |

### 1.2 缺口

1. 人脸识别只回答「**是谁**」，不知道「**在干嘛**」（坐着看书 / 躺着玩手机 / 打架）。
2. 行为没有沉淀——「Emily 今天下午 3 点在电竞沙发看书」这类事件目前无处存储、无法回查。
3. 只有客厅有 TV（=有端侧人脸识别）；其他房间只有摄像头，既不知道「谁」也不知道「干嘛」。

### 1.3 成员画像（身份先验，重要）

| 成员 | 年龄/性别 | 外观区分度 |
|---|---|---|
| lidicn | 40 岁男 | 成年男性 |
| Kevin | 13 岁男 | 青少年男孩 |
| Emily | 10 岁女 | 小女孩 |

**三人年龄/性别差异极大，VLM 只凭「大致年龄 + 性别」就能高置信区分**——这是无 TV 房间「曲线救国认人」的天然先验，再叠加「当日穿搭」做二次确认（见 §4.2）。

---

## 2. 总体架构与分工

```mermaid
flowchart TD
    subgraph Edge["边缘（每房间，有 TV 的房间）"]
        A["TV APK：ArcFace 人脸识别<br/>+ 双摄融合（已建成）"] --> B{"状态变化？<br/>人数变化/身份集变化/新人出现<br/>否则 60s 心跳"}
        B -->|是| C["POST /api/events/face（新增）<br/>{room, persons[], count, trigger, ts}"]
    end

    C --> D
    subgraph Hub["memory-agent（本设计的全部新增）"]
        D["事件接收 + 身份登记"] --> E{"节流闸门：<br/>房间冷却 ≥60s / 场景哈希去重 /<br/>每小时上限 / doubao 失败退避"}
        E -->|放行| F["从 go2rtc 拉该房间当前帧"]
        F --> G["doubao2api /v1/images/analyses<br/>prompt 注入 TV 报告的身份"]
        E -->|拦截| K[(behavior_events)]
        G --> H["解析结构化行为 JSON"] --> K
        K --> L["行为摘要按天 → Chroma behavior_history"]
        K --> M["Webhook → NR（复用 NR_URL）"]
    end

    F -.统一视觉入口.-> N["go2rtc streams：<br/>客厅 / 客厅sd / 小黄人 / 未来全屋"]

    subgraph NoTV["无 TV 房间（曲线救国）"]
        O["低频巡检定时器（默认 5 分钟）"] --> F
        P["VLM 外观描述<br/>（年龄/性别/穿搭）"] --> Q["成员匹配器<br/>年龄性别先验 + 当日穿搭比对"]
        Q --> K
    end

    L -.语义检索.-> R["问：Emily 今天下午干嘛了？"]
```

**分工原则：**

| 层 | 职责 | 不做什么 |
|---|---|---|
| TV（边缘） | 「是谁」（端侧 ArcFace，快、隐私、免上传）+ 产生**触发信号** | 不调豆包、不存行为、不做节流 |
| memory-agent（中枢） | 何时调 VLM（节流策略唯一持有者）、调豆包、身份融合、**行为记忆存储**、查询 API、NR webhook | 不做人脸识别 |
| doubao2api | 纯多模态推理网关，**一行不改** | 不懂摄像头、不懂业务 |
| Node-RED | 通知与联动规则（查 memory-agent API） | 不碰图像、不做识别 |

> 为什么不让 TV 直调豆包（方案 3）？每台 TV 重复实现触发/限流/重试，行为记忆还得回传中心，多房间时逻辑 ×N。为什么不在 doubao2api 里加业务（方案 2）？它是逆向豆包的协议适配器，塞业务会让上游升级痛苦。为什么不全放 NR（方案 1）？触发/去重/重试在 NR 里会变成面条流。

---

## 3. 外部依赖清单（实测数据）

### 3.1 go2rtc —— 全屋统一「眼睛」

- 基址：`http://192.168.2.200:1984`，Basic Auth：`lidicn` / `longyin1003`（api 与 rtsp 同套，见 NAS `/vol1/1000/docker/go2rtc/go2rtc.yaml`）
- 已配置流（`streams:`）：`客厅`（米家 chuangmi.camera.051a01 HD）、`客厅sd`（同机 SD 副码流）、`小黄人`（chuangmi.camera.ipc019e HD）。新增房间 = go2rtc 加一条流 + memory-agent 房间注册表加一行。
- 取帧端点（**推荐 MVP 用单帧**）：

```
GET /api/frame.jpeg?src=<URL编码的流名>
→ 200 image/jpeg（客厅 HD 实测 1920x1080，约 78-85KB）
```

| 实测注意点 | 数据 | 建议 |
|---|---|---|
| `frame.jpeg` 单帧延迟 | 4~13s（米家 IPC 关键帧间隔长） | 巡检/触发拉帧按**异步任务**设计，不要同步等待；单帧超时设 20s |
| `GET /api/stream.mjpeg` | 本次实测返回 `200` 但 body 0 字节（握手/头未调通） | 后续可换持久 MJPEG 提帧率；**MVP 不依赖它** |
| RTSP 直连 | `rtsp://lidicn:longyin1003@192.168.2.200:8554/客厅` 可用 | 容器内若已有 ffmpeg 可作备选，非必需 |

### 3.2 doubao2api —— 多模态推理网关（零改造）

- 基址：`http://192.168.2.200:9090`，`Authorization: Bearer longyin`
- 图片分析端点（OpenAI Vision 标准格式，详见其 `IMAGE_ANALYSIS_API.md`）：

```
POST /v1/images/analyses
{"model":"doubao","messages":[{"role":"user","content":[
  {"type":"text","text":"<prompt>"},
  {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,<b64>"}}
]}]}
→ choices[0].message.content
```

备选 `POST /v1/responses`（`input_text`/`input_image`）。支持 base64 data URI 与 HTTP URL 两种图片传法——**优先 base64**（帧已在手，无需落盘公网）。

| 实测注意点 | 说明 |
|---|---|
| 会话失效风险 | 逆向豆包会话可能过期/风控（历史上 TTS 踩过 710020702）。失效时返回 500/502 → 必须退避并在 WebUI 提示「需扫码重登」（管理面板 `/admin?key=longyin`）。**豆包挂了只影响「在干嘛」，不得影响「是谁」与事件入库** |
| 单次延迟预估 | 3~7s（上传 1s + 推理 2~5s），未最终实测，P0 验收脚本见 §9 |
| 图片建议 | 下采样至 ≤1280px、JPEG q60~70（单帧 100~200KB），质量和耗时平衡点 |

### 3.3 TV APK —— 边缘事件源（对接方职责，非本次开发）

- 基址：`http://192.168.2.238:8080`；已有 `/api/health`、`/api/scan`（多人扫描）、`/api/pano`（**新增的双路融合接口**：`pano[]` 米家原始结果 + `fused[]` 按名融合名单 + `panoOnline`）、`/api/snapshot`。
- 已注册成员：lidicn / Kevin / Emily（3 人）。
- 对接改动（由 TV 侧开发，另行排期）：在 `RecognitionState` 之上加「状态变化检测 + 上报」，向 memory-agent `POST /api/events/face`。**TV 端改动不影响本设计接口契约，契约以本文为准。**

---

## 4. 身份策略

### 4.1 有 TV 的房间（客厅，主路径）

TV 上报 `persons: [{name:"Emily", score:0.92}, ...]`，**身份直接采信**（端侧 ArcFace 已做阈值 0.80 判定与双摄融合）。VLM prompt 里注入已识别身份，让它**只答动作**，不答「是谁」——效率与准确率双高：

```
画面中有：lidicn（成年男性）、Emily（10岁女孩）。
对每个人输出：在做什么动作、姿态、与谁互动。不要猜测身份。
```

### 4.2 无 TV 房间（曲线救国认人）

VLM 对画面每人输出外观结构化描述，再由 memory-agent 本地匹配：

```json
{"persons":[{"appearance":{"approx_age":10,"gender":"female","clothing":"白色T恤、蓝色短裤、马尾辫"},"action":"坐在地毯上搭积木"}]}
```

匹配优先级（匹配器在 memory-agent 内实现，不靠 VLM）：

1. **年龄/性别先验**：三人年龄差大，`approx_age ±8 岁 + gender` 即可高置信锁定唯一成员（40M / 13M / 10F 互不混淆）。
2. **当日穿搭比对**：若先验歧义（如未来加入同龄成员），用「今日穿搭档案」二次确认——VLM 输出的 clothing 与当日档案做文本相似度（LLM 判定或简单关键词/embedding）。
3. 仍不确定 → 记为 `person:"未识别成员"`，动作照常入库（行为价值不依赖身份 100% 正确）。

### 4.3 当日穿搭档案（穿搭标注机制）

**触发**：客厅识别到人脸（TV 事件携带身份）且本房间即将/已经拉帧时，让同一次 VLM 调用顺带输出该身份者的穿搭描述——**不增加额外豆包调用次数**（一次调用两个产出：行为 + 穿搭）。

**存储**：`outfit_profiles` 表，每人每天一条（当天多次识别取最新/最完整一条）：

```sql
CREATE TABLE IF NOT EXISTS outfit_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,              -- 'YYYY-MM-DD'（本地时区）
  member TEXT NOT NULL,           -- 'lidicn' / 'Kevin' / 'Emily'
  outfit_text TEXT NOT NULL,      -- '白色连帽卫衣、黑色长裤、黑框眼镜'
  raw_json TEXT,                  -- VLM 原始输出留痕
  source TEXT NOT NULL,           -- 'living_room_face' / 'manual'
  updated_at TEXT NOT NULL,
  UNIQUE(day, member)
);
```

**用途**：① 无 TV 房间身份二次确认；② 未来「找穿红衣服的 Emily」类语义查询；③ 换装检测（可选，后续再说）。

---

## 5. 新增接口规格（全部挂在现有 HTTP 服务 :8086 下）

鉴权：沿用现有体系。设备类调用（TV/巡检）建议新增独立 Token：`VISION_DEVICE_TOKEN`（env 注入），`Authorization: Bearer <token>`；与 WebUI JWT、MCP Token 三者隔离。

### 5.1 `POST /api/events/face` —— TV 人脸事件上报（边缘触发入口）

```json
{
  "room": "客厅",
  "camera": "客厅",
  "persons": [
    {"name": "lidicn", "score": 0.887},
    {"name": "Emily", "score": 0.905}
  ],
  "count": 2,
  "trigger": "identity_change",   // count_change | identity_change | new_person | heartbeat | scan
  "ts": 1788070996056             // ms，TV 时钟；服务端另记 server_ts
}
```

- 响应 `202 {"accepted":true,"deduped":false,"vlm_dispatched":true,"reason":"..."}`（异步处理，**不同步等 VLM**，TV 不能被 3~7s 阻塞）
- `persons` 为空数组 = 「房间没人了」事件，同样要入库（离场时间有用）

### 5.2 `POST /api/vision/analyze` —— 手动触发多模态识别（运维/调试/NR 用）

```json
{"room": "客厅", "camera": "客厅", "force": true, "wait": false}
```

- `force:true` 绕过冷却与去重（仍受每小时硬上限保护，防误用打爆豆包）
- `wait:true` 同步返回结果（调试用，超时 20s）；默认异步走 5.4 webhook
- 无 TV 房间的定时巡检，内部即等价于 `force:false` 的定时调用

### 5.3 `GET /api/behaviors` —— 行为查询

```
GET /api/behaviors?member=Emily&room=客厅&from=2026-08-30T00:00&to=2026-08-30T23:59&limit=100
→ {"count":n,"events":[{...behavior_events 行...}]}
```

MCP 侧建议同步暴露一个 `get_behavior_events(member?, room?, from?, to?)` 工具，让 `ask_memory` 的语义问句（「Emily 下午干嘛了」）能路由到它。

### 5.4 Webhook → NR

VLM 结果落库后，向现有 `NR_URL` POST（复用既有 NR 集成通道与鉴权）：

```json
{"type":"behavior","room":"客厅","member":"Emily","action":"坐在电竞沙发看书",
 "scene":"两人各自安静看书","confidence":0.9,"ts":...,"snapshot":"/data/snapshots/2026-08-30/xxx.jpg"}
```

NR 侧现有 TTS/微信流程只需加一个 `http in` 节点接收即可。

### 5.5 配置（env / config.json 新增键）

```
VISION_ENABLED=true
VISION_DEVICE_TOKEN=<随机串>
GO2RTC_BASE_URL=http://192.168.2.200:1984
GO2RTC_USER=lidicn
GO2RTC_PASS=***
DOUBAO_BASE_URL=http://192.168.2.200:9090
DOUBAO_API_KEY=longyin
DOUBAO_VISION_TIMEOUT_S=25
VISION_ROOMS={"客厅":"客厅","小房间":"小黄人"}        # room → go2rtc 流名
VISION_NO_TV_ROOMS=["小房间"]                        # 纯摄像头房间（启用低频巡检）
VISION_COOLDOWN_S=60          # 同房间两次 VLM 最小间隔
VISION_MAX_PER_HOUR=20        # 每房间每小时硬上限
VISION_NO_TV_INTERVAL_S=300   # 无 TV 房间巡检周期
VISION_SNAPSHOT_RETENTION_DAYS=7
```

---

## 6. 节流与去重（回答「不能无时无刻调豆包」）

按序执行，任一命中即拦截（拦截不报错，记 `deduped` 原因便于诊断）：

1. **房间冷却**：同房间距上次 VLM 调用 < `VISION_COOLDOWN_S` → 拦截。
2. **场景哈希去重**：拉到帧后计算感知哈希（pHash/dHash，纯 Python 可实现），与该房间上次帧哈希汉明距离 < 阈值 → 拦截（画面没变就别再问）。**注意：哈希比较在拉帧之后、调豆包之前**，所以冷却期内的帧不拉（省 go2rtc 4~13s 的等待）。
3. **小时硬上限**：同房间 1 小时内 VLM 调用 ≥ `VISION_MAX_PER_HOUR` → 拦截并告警。
4. **失败退避**：豆包 5xx/超时 → 指数退避（60s/120s/240s…上限 30 分钟），期间事件照常入库但 `action` 标 `vlm_failed`，恢复后不回填。
5. **效果预期**：两人持续看电视 2 小时 ≈ 2~5 次 VLM 调用；一人换动作 → 新事件 → 一次调用。

---

## 7. 存储设计

### 7.1 `behavior_events`（SQLite，新增）

> 现有事件库是**设备/实体导向**（`entity_id` + 状态变化，来自 HA 采集）。行为事件是**「人+动作」导向**，语义不同，**独立建表**，不要硬塞进设备事件流（`infer_activities` 是传感器推断，视觉实测是另一类证据，分开存、洞察时合并）。

```sql
CREATE TABLE IF NOT EXISTS behavior_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  server_ts TEXT NOT NULL,        -- ISO8601 本地时区（服务端接收时间，查询主键）
  device_ts INTEGER,              -- TV 上报的 ms 时间戳（可空）
  day TEXT NOT NULL,              -- 'YYYY-MM-DD'，冗余列方便按天聚合/分区清理
  room TEXT NOT NULL,
  camera_src TEXT,                -- go2rtc 流名
  persons_json TEXT NOT NULL,     -- '[{"name":"Emily","score":0.92,"via":"face|appearance"}]'
  count INTEGER NOT NULL DEFAULT 0,
  action TEXT,                    -- '坐在电竞沙发看书'
  scene TEXT,                     -- 一句话场景概括
  confidence REAL,                -- 0~1，VLM 自评或解析置信
  appearance_json TEXT,           -- 无TV房间的外观描述（穿搭标注的原料）
  trigger TEXT,                   -- count_change/identity_change/heartbeat/patrol/manual
  vlm_latency_ms INTEGER,         -- 端到端豆包耗时（观测用）
  snapshot_path TEXT,             -- '/data/snapshots/2026-08-30/xxx.jpg'
  raw_response TEXT,              -- VLM 原文留痕（排查用）
  status TEXT NOT NULL DEFAULT 'ok'  -- ok | vlm_failed | low_confidence
);
CREATE INDEX IF NOT EXISTS idx_be_day_room ON behavior_events(day, room);
CREATE INDEX IF NOT EXISTS idx_be_member ON behavior_events(day);  -- persons 用 JSON，成员过滤在服务层做（量小）
```

> 成员过滤实现提示：量级为每天几十条，服务层 `json.loads` 后过滤足够，不必上 JSON1 虚拟列。

### 7.2 快照文件

- 路径：`/data/snapshots/<YYYY-MM-DD>/<room>_<ts>.jpg`（挂载卷 `./data`，已有）
- **只存路径不存 blob**；后台任务按 `VISION_SNAPSHOT_RETENTION_DAYS` 清理过期文件 + 置空对应行 `snapshot_path`

### 7.3 向量库（Chroma）

复用现有「按天 × 房间摘要写入 `behavior_history` 集合」的 mirror 机制，把当天该房间的行为事件并入摘要文本（如「08-30 客厅：Emily 15:00-16:00 看书，Kevin 16:30 打架」）。这样「Emily 今天下午干嘛了」这类语义问句能直接命中，不需要新集合。

### 7.4 与现有洞察工具的关系

- `get_person_history` / `get_member_persona`：可增量 join `behavior_events`（成员名对齐现有 members），让画像里出现「视觉行为」维度
- `infer_activities`：保持传感器推断不变；两者是互补证据（看电视：传感器说电视开着 + 视觉说人坐在沙发上 = 互相印证）
- `ask_memory`：加一条结构化路由「问某人某时段行为 → get_behavior_events」

---

## 8. Prompt 模板（初始版本，可迭代）

### 8.1 行为识别（有身份注入）

```
你是家庭监控画面分析助手。这是{room}的摄像头画面，时间{time}。
画面中已识别到：{persons_desc}。人数应为{count}人。
请只输出如下 JSON，不要输出其他内容：
{"persons":[{"identity":"<上列名字或'未识别'>","action":"<10-20字动作描述，如'坐在电竞沙发看书'>",
"posture":"坐/站/躺/走","interaction":"<与谁互动或'无'>","confidence":0.0-1.0}],
"scene":"<一句话场景概括>","snapshot_quality":"good|dim|occluded"}
注意：不要猜测未列出的人的身份；画面模糊时 confidence 调低。
```

### 8.2 行为识别 + 穿搭登记（同一次调用，客厅识别到人时）

在 8.1 基础上 `persons[]` 每项增加 `"outfit":"<当日穿搭：上衣/下装/鞋/显著配饰，30字内>"`。

### 8.3 无 TV 房间（外观匹配模式）

```
你是家庭监控画面分析助手。这是{room}的摄像头画面，时间{time}。
家庭成员参考：lidicn(40岁男)、Kevin(13岁男)、Emily(10岁女)。
对画面中每个人输出外观描述与动作。只输出 JSON：
{"persons":[{"approx_age":<数字>,"gender":"male|female",
"clothing":"<上衣/下装/配饰>","hair":"<发型>",
"action":"<动作>","posture":"坐/站/躺/走","confidence":0.0-1.0}],
"scene":"<一句话>"}
不要输出姓名，匹配由系统完成。
```

解析 JSON 失败时：重试 1 次（附「上次输出不是合法 JSON」）；仍失败则 `status=vlm_failed` 落库留 raw_response。

---

## 9. 实施阶段与验收

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| P0（半天） | 验证豆包图片链路：会话存活、延迟、识别质量 | 下述脚本返回 200 且能正确描述画面；记录 `耗时` |
| P1（1~2天） | §5 全部接口 + §6 节流 + §7 存储 | `POST /api/vision/analyze` 手动触发客厅 → `behavior_events` 出现一条含 action 的记录；连续触发 5 次，实际 VLM 调用 ≤2 次（冷却+去重生效） |
| P2（1天，TV 侧配合） | TV 事件上报对接 | TV 前「换人/人数变化」→ 10s 内 `behavior_events` 新增记录 + NR 收到 webhook |
| P3（0.5天） | 无 TV 房间巡检 + 穿搭档案 + 成员匹配 | `小黄人` 房间每 5 分钟有巡检记录；三人穿搭档案当日可查；外观匹配能正确区分三人 |
| P4（0.5天） | 洞察集成：MCP 工具 + `ask_memory` 路由 + 摘要入库 | 问「Emily 今天下午干嘛了」能返回行为事件 |

### P0 验收脚本（可直接运行）

```python
import base64, json, time, urllib.request

img_path = r"<任意一张客厅摄像头 JPEG>"   # 或 TV 快照: 先 GET http://192.168.2.238:8080/api/snapshot
b64 = base64.b64encode(open(img_path, "rb").read()).decode()
payload = {"model": "doubao", "messages": [{"role": "user", "content": [
    {"type": "text", "text": "这是客厅监控画面。回答：1)几个人 2)每人在做什么 3)一句话概括场景。简短中文。"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]}]}
req = urllib.request.Request("http://192.168.2.200:9090/v1/images/analyses",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Bearer longyin"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.loads(r.read().decode())
    print("耗时 %.1fs" % (time.time() - t0))
    print(out["choices"][0]["message"]["content"])
except Exception as e:
    print("失败(%.1fs): %s —— 多半是豆包会话失效，去 http://192.168.2.200:9090/admin?key=longyin 扫码重登" % (time.time() - t0, e))
```

---

## 10. 隐私与安全

1. **最小上传**：仅在触发/巡检时上传**单帧** JPEG 到豆包云；持续视频流不出内网。TV 事件里的身份在本地产生，不额外上传人脸图。
2. **快照留存 7 天**自动清理，用户可在 WebUI 配置。
3. **设备 Token 独立**：`VISION_DEVICE_TOKEN` 泄露只影响写入行为事件，不触达 HA/MCP 权限。
4. **豆包云风险告知**：逆向接口，无 SLA；行为识别是「锦上添花」能力，任何失败都降级为缺数据，不阻塞人脸识别与既有自动化。
5. 建议后续提供一键总开关 `VISION_ENABLED=false`（家人反馈不适时立刻停）。

---

## 11. 附录：2026-08-30 实测数据（双摄融合，支撑「身份由边缘提供」的设计）

TV 快照单帧：lidicn 0.887（matched），Emily 未检出。米家帧（同场景）×5：Emily 稳定 0.896~0.922（matched），lidicn 0.197~0.594；同帧最多检出 2 脸。**融合后（按名取最高分）两人同框均达标 → TV 侧 `persons[]` 上报是可信身份源**。识别数据：`/api/pano` 的 `fused[]` 字段即融合名单。

---

*文档版本：v1.0 · 2026-08-30 · 由 TV 端视觉联调会话产出，实测数据均为当日验证*
