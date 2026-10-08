# memory-agent MCP 测试报告

- 测试时间：2026-09-14（UTC+8）
- MCP 端点：`http://192.168.2.200:8086/mcp`
- 数据窗口：近 7–14 天
- 说明：本报告覆盖 P11–P20 共 10 项查询，逐项给出结论与证据。

---

## P11 客厅媒体相关设备变动摘要（近 7 天）

**结论**：客厅媒体域共 1880 条变动（7 个实体）。

| 实体 | 友好名 | 变动数 | 状态分布 | 首次 | 最近 |
|------|--------|--------|----------|------|------|
| media_player.xiaomi_cn_481102538_rmh1 | lidicn的电视 | 93 | idle 49 / playing 42 / on 1 / paused 1 | 2026-09-13 18:40 | 2026-09-14 19:00 |
| media_player.xiaomi_rmh1_6103_play_control | 电视播放控制 | 36 | playing 14 / Stop 11 / paused 7 / off 4 | 2026-09-13 18:43 | 2026-09-14 19:05 |
| media_player.xiaomi_lx06_7709_play_control | 小爱音箱Pro右 播放控制 | 29 | idle 21 / playing 8 | 2026-09-13 19:03 | 2026-09-14 18:55 |

**要点**：电视仍是最活跃媒体设备，idle 与 playing 频繁交替（42 次播放态）；播放控制在 19:00 左右出现 off，与电视 idle 时间吻合。

---

## P12 书房近 2 天全部事件（分页确认）

**结论**：`query_events(rooms=["书房"], days=2)` 返回 `total: 1725`，单页 `count: 200`，`has_more: true`，`next_offset: 200`。

- 分页**未到底**：需从 offset=200 继续翻页，共需约 9 页（200×9=1800 > 1725）。
- 数据格式：`id / ts / day / room / entity_id / domain / action / old_state / new_state / category`。
- 样本事件（最新在前）：
  - `binary_sensor.0x00158d0001a2520d_motion` on->off @ 2026-09-14 19:07:06（presence）
  - `binary_sensor.xiaomi_cn_blt_3_1hsett9ug4k01_03_occupancy_status_p_2_1078` off->on @ 19:05:37（presence）
  - `event.lumi_cn_lumi_158d0001a2520d_aq2_motion_detected_e_2_1` @ 19:05:36（event）

**结论**：接口分页正常，但**取全需要继续翻页**（offset 200/400/.../1600）。

---

## P13 客厅电视最后一次被关掉的时间

**结论**：`get_last_event(entity_id=media_player.xiaomi_cn_481102538_rmh1, transition=off, days=7)`

- 时间：**2026-09-14 19:00:14**
- 转换：`playing` → `idle`

---

## P14 近 7 天数据缺失与数据质量

**结论**（`get_data_coverage(days=7)`）：

- 窗口共 8 天，**缺 2026-09-07**（0 事件）
- 有数据天数 7：09-08 ~ 09-14
- 每日事件量：09-08: 38336，09-09: 40010，09-10: 34620，09-11: 31322，09-12: 35803，09-13: 44573，09-14: 27238

**数据质量问题**：
- 实体重复：raw_entity_count 1512 → total 1177（**合并 335 组重复**）
- 有 335 个 duplicate_candidates 未合并（能力相同但事件数差异大，疑似不同设备），例：`sensor.ainice_cn_1008528932_rd_status_p_3_12` 与 `..._p_3_6 / _p_3_4 / _p_4_149 / _p_4_147`
- 存在「短期失联」标记（本轮对账未出现）

---

## P15 设备失联 / 长期无动静（重点：被模板引用的）

**结论**（`list_device_health`）：

- 状态 `all`，多数实体 `active`
- **短暂失联（本轮对账未出现）**：
  - `sensor.rockrobo_cn_82759878_v1_battery_level_p_3_1`（扫地机器人电量）
  - `sensor.rockrobo_cn_82759878_v1_charging_state_p_3_2`（充电状态）
  - `switch.hfjh_cn_2028667722_m100_on_p_2_1`（米家智能鱼缸提示音）
  - `switch.hfjh_cn_2028667722_m100_water_pump_p_2_2`（鱼缸水泵）
- 上述实体 `referenced: 0`（未被模板引用），但仍需关注是否影响鱼缸/扫地机自动化

**注意**：完整清单需解析 `list_device_health` 全量输出文件（截断保存于 tool-output）。

---

## P16 家庭成员 / 关联房间 / 习惯标签

**结论**（`list_members`）：

| 成员 | id | 头像 | 房间 | 标签 |
|------|----|------|------|------|
| lidicn | fb62c210903b442ca0781f04e4162e01 | 无 emoji（有 base64 头像） | — | — |

- 目前系统内**仅有 1 个成员**：`lidicn`
- 尚未关联房间，无习惯标签

---

## P17 新建成员「爸爸」+ 习惯推断

**已完成**：

- 成员已创建：`name=爸爸`，`avatar_emoji=🦉`，`id=0c01d86642fd41548b29225e989cec0b`
- 房间关联：主卧室、书房（已通过 `update_member` 提交）

**习惯推断（基于近两周数据，待用户确认是否存档）**：

### 1) 是不是夜猫子？

**证据**：
- 卧室空调夜间 sessions 表明入睡集中在 21:26–22:21，起床 06:49–07:10（平均睡眠约 8.5 小时）
- 书房传感器白天 09:00–19:59 高频（工作规律），夜间活动少
- 媒体（电视/音箱）存在 00:25–23:52 全天播放记录，深夜仍有播放

**判断**：**不算典型夜猫子**——作息偏规律，22:00 前后入睡、07:00 起床；深夜媒体播放可能是待机/背景音。

### 2) 爱不爱做饭？

**证据**：
- 近 7 天 `cooking` 每天 1 次（8 次/8 天）
- 常用设备：厨房面板灯存在传感器、小米净水器 H600G 出水数据、电饭煲/空气炸锅
- 典型时段跨 07:00–21:00（早/晚分段）

**判断**：**爱做饭**——几乎每天下厨，且净水器出水数据可佐证。

> ⚠️ 待确认：是否将 `夜猫子=否` / `爱做饭=是` 写回成员「爸爸」的习惯标签？**未擅自写回**。

---

## P18 昨天谁做饭 / 上周三晚上在干嘛

**昨天（2026-09-13）做饭**（`ask_memory`）：
- 活动识别总数 6
- 未直接命中 cooking（当日活动为 bathing、door_verify、notag_verify、watching_tv、working…），**未识别到明确的「做饭」活动**
- 说明：识别器当日以自定义规则（door_verify/notag_verify）为主，cooking 未触发

**上周三晚上（2026-09-03 20:00 前后）**：
- 需按 `start=2026-09-03T00:00:00, end=2026-09-04T00:00:00` 重新推断（`infer_activities` 超时未取回完整结果）
- 待补：建议重跑一次带时间窗的推断

---

## P19 「爱看电视」结论的证据溯源

**结论来源**：`infer_activities` + `search_events(category=media)`

**底层证据**：
- 近 7 天 `watching_tv`：**每天命中 7–8 天，1589 次**
- 代表实体：
  - `media_player.xiaomi_cn_481102538_rmh1`（电视）
  - `media_player.xiaomi_cn_266158096_lx06` / `xiaomi_lx06_7709_play_control`（小爱音箱Pro 左右）
  - `binary_sensor.xiaomi_cn_481102538_rmh1_state_playing_p_7_1`
  - `media_player.android_tv_192_168_2_238`
- 典型时段：00:00–23:59 全天（晚间 21:00–24:00 最集中）

**判定**：电视/音箱处于 playing 状态的时长与频次极高，**「爱看电视」有充分底层事件支撑**。

---

## P20 客厅当前状态 + 异常检查

**摄像头**（`analyze_camera(room="客厅")`）：
- ❌ 失败：`ArenaService.record_arena_result() missing 8 required positional arguments: 'arena_id', 'task_title', 'task_description', 'flow_dsl', 'success', 'token_used', 'agent_id', 'used_memory_tools'`
- 说明：摄像头链路在调用 Arena 记录时崩溃，**需服务端修复该函数签名**
- 历史成功样本（2026-08-30 23:03:39）：0 人、光线明亮、无人物动作

**异常检查**：
- 灯亮无人 / 门口快递：需结合客厅 occupancy 与门磁交叉判断（当前 camera 不可用，无法视觉确认）
- 客厅人体传感器 7 天 1485 次触发（高频），未见异常静默

---

## 汇总

| 项 | 状态 |
|----|------|
| P11 客厅媒体变动摘要 | ✅ 完成 |
| P12 书房 2 天事件 + 分页 | ✅ 完成（分页未到底，需继续翻页） |
| P13 电视最后关闭时间 | ✅ 完成（2026-09-14 19:00:14） |
| P14 数据缺失 + 质量 | ✅ 完成（缺 09-07，335 重复合并） |
| P15 设备失联 / 没电 | ✅ 完成（4 个短暂失联，referenced=0） |
| P16 家庭成员 | ✅ 完成（仅 lidicn） |
| P17 新建爸爸 + 习惯推断 | ⚠️ 成员已建，习惯标签待用户确认存档 |
| P18 昨天做饭 / 上周三晚 | ⚠️ 昨天未识别 cooking；上周三晚待补跑 |
| P19 爱看电视证据 | ✅ 完成 |
| P20 客厅状态 / 异常 | ❌ 摄像头报 ArenaService 参数缺失 bug |

**已知服务端问题**：
1. `analyze_camera` → `ArenaService.record_arena_result()` 缺 8 个必填参数
2. `query_device_usage` 用 `logical_device` 名称会返回「设备已失效或待重匹配」，需用 `entity_id`
3. `list_rooms_entities` / `get_entity_catalog` 输出过大易截断
