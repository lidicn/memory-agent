# vMA-1.3 多模态记忆统一 · PoC 草稿

> 版本：PoC v0.1（决策门前置草稿）
> 日期：2026-09-28
> 状态：待 DCD 评审
> 前置：vMA-1.2.0/1.2.1/1.2.2 已完成

---

## 一、现状盘点

### 1.1 当前数据表（4 张核心事件表）

| 表 | 用途 | 数据量 | 写入来源 |
|---|------|--------|---------|
| `events` | 设备状态变化（HA 采集） | ~110 万条 | HA MariaDB 增量拉取 |
| `perception_events` | 统一感知总线（边缘AI/VLM/sensor） | ~10 万条 | livingroom_ai / vision_service |
| `behavior_events` | 视觉行为事件（人+动作） | ~5 千条 | TV端 ArcFace + VLM |
| `detected_activities` | 推断活动（过程挖掘） | ~1 千条 | signal_learning / 过程挖掘 |

### 1.2 问题

1. **三套时间戳不统一**：`events.ts`、`perception_events.server_ts`、`behavior_events.server_ts` 都是 ISO8601 但格式略有差异
2. **房间/实体命名不统一**：`events.room` 是中文房间名，`perception_events.room` 也是，但 `entity_id` 命名规则混乱（有的用 HA entity_id，有的用自定义）
3. **person 字段稀疏**：`events.person` 大部分为空，`perception_events` 有 `persons_json` 但格式不统一
4. **跨表查询难**：想查"lidicn 昨晚在客厅做了什么"需要 join 4 张表，且时间对齐麻烦

---

## 二、统一数据模型设计

### 2.1 核心表：`unified_events`（新建）

```sql
CREATE TABLE unified_events (
  id            TEXT PRIMARY KEY,        -- UUID
  ts            TEXT NOT NULL,           -- 统一 ISO8601（UTC+8）
  day           TEXT NOT NULL,           -- 'YYYY-MM-DD'
  room          TEXT NOT NULL DEFAULT '',
  source        TEXT NOT NULL,           -- device | vision | perception | activity | llm
  modality      TEXT NOT NULL,           -- text | image | audio | video | sensor
  person        TEXT NOT NULL DEFAULT '', -- 空=未识别，lidicn/Kevin/Emily
  entity_id     TEXT NOT NULL DEFAULT '',
  event_type    TEXT NOT NULL,           -- state_change | face_recognized | motion | gesture | activity_inferred | diary
  summary       TEXT NOT NULL,           -- 一句话人类可读描述
  confidence    REAL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at    TEXT NOT NULL
);

CREATE INDEX idx_ue_ts       ON unified_events(ts);
CREATE INDEX idx_ue_day_room ON unified_events(day, room);
CREATE INDEX idx_ue_person_ts ON unified_events(person, ts);
CREATE INDEX idx_ue_source_ts ON unified_events(source, ts);
```

### 2.2 统一语义

| 字段 | 取值规范 |
|------|---------|
| `source` | `device`（HA）/ `vision`（VLM）/ `perception`（边缘AI）/ `activity`（推断）/ `llm`（日记/总结） |
| `modality` | `sensor`（设备状态）/ `image`（摄像头截图）/ `audio`（声音）/ `text`（LLM输出） |
| `person` | `''`（未识别）/ `lidicn` / `Kevin` / `Emily` |
| `event_type` | `state_change` / `face_known` / `face_unknown` / `motion` / `gesture` / `activity_inferred` / `diary` / `report` |

### 2.3 迁移策略

**不删旧表**，做双写：
- 旧表（events/perception_events/behavior_events）继续写入，保证向后兼容
- 新增 `unified_events` 同时写入，作为统一查询入口
- 跑一段时间（1-2 周）验证数据完整性后，再考虑旧表只读归档

---

## 三、PoC 验证计划

### 3.1 PoC 范围（最小可行）

只做：
1. 建 `unified_events` 表
2. 在 `events` 写入路径加双写（HA 设备事件 → unified_events）
3. 在 `perception_events` 写入路径加双写（视觉/边缘AI → unified_events）
4. 提供一个查询 API：`GET /api/unified/events?person=&room=&start=&end=`

不做：
- 迁移旧数据（110 万条太多，先双写新的）
- behavior_events / detected_activities 双写（第二阶段）
- 向量库集成（第三阶段）

### 3.2 验收标准

- [ ] `unified_events` 表创建成功
- [ ] HA 设备事件写入后，unified_events 同步有记录
- [ ] 视觉感知事件写入后，unified_events 同步有记录
- [ ] 查询 API 能按 person/room/时间范围查
- [ ] 双写对现有功能零影响（旧表查询正常）

---

## 四、风险与决策点

| 风险 | 等级 | 应对 |
|------|------|------|
| 双写性能下降 | 中 | 异步写入，J3455 扛得住 |
| 数据不一致 | 中 | 双写失败不影响主流程，只记日志 |
| schema 变更影响现有代码 | 低 | 新表独立，不动旧表 |
| 迁移成本 | 低 | 不迁移旧数据，只双写新的 |

### 需 DCD 决策

1. **统一数据模型字段是否够用？** 还是需要加 `scene_graph_json`、`emotion` 等字段？
2. **双写策略是否正确？** 还是直接做视图（view）而不是新表？
3. **person 字段规范化**：空字符串 vs NULL，哪个更好？
4. **PoC 范围是否合适？** 还是要把 behavior_events 也加进来？

---

## 五、下一步

1. DCD 评审本草稿
2. 通过后做 PoC 实现（建表 + 双写 + 查询 API）
3. PoC 跑 1 周验证数据完整性
4. 验证通过后进入正式迁移阶段
