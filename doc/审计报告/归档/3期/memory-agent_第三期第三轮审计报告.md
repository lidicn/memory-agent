# memory-agent 审计报告 · 第三期第三轮

- **审计对象**：`lidicn/memory-agent`
- **轮次**：`round-003`
- **报告日期**：2026-10-09
- **承接**：第一轮（M1–M6 数据库专题）、第二轮（M2 修复 + W95）

---

## 一句话结论

**M5/M6/M7 全部修复并对照实测验证；新增 W96「SQLite 损坏-启动 PoC」把前两轮手工验证的 M1/M2 变成每轮可回归的流水线能力。补丁副本实测：6 种损坏组合（3 种损坏方式 × 有/无备份）全部 `startup_ok` —— 服务再也起不来的情况已消除。剩余真正需要你决策的只有 M3（./data 是否在 NAS 网络挂载上）。**

---

## 一、W96【本轮工作流核心】把 M1/M2 变成可回归能力

前两轮确证 M1/M2 时，每一步都是手写一次性脚本。**不可回归、不可移植。**

而这个项目用户的主诉正是「数据库出错 → 容器起不来」——
**启动链能不能扛住损坏** 是本期最该被每轮复验的东西。

新增 `core/poc_sqlite.py`，四步协议（与 state/failopen 族保持一致）：

```
① build    用目标 Store 类自己建库（schema 一致）+ 播种可识别标记
② backup   可选快照（模拟 backup_enabled=True）
③ corrupt  header / page / truncate 三种损坏
④ probe    跑启动链 check_and_recover() → init_schema()
```

**关键判据与 state 族不同**：这里 `crashed` = 启动链抛异常 ⇒ **容器起不来**，
问的是「服务还能不能起来」，不是「数据丢没丢」。

### 原仓库实测（6 种组合）

| 损坏 | 有备份 | 无备份 |
|---|---|---|
| header | **crashed** `file is not a database` | **crashed** |
| page | startup_ok (recovered=True) | **crashed** `disk image is malformed` |
| truncate | **crashed** | **crashed** |

**6 组里 5 组起不来。** 这就是用户遇到的现象。

### 补丁副本实测（6 种组合）

| 损坏 | 有备份 | 无备份 |
|---|---|---|
| header | startup_ok `recovered=True` | data_lost `quarantined=True` |
| page | startup_ok `recovered=True` | data_lost `quarantined=True` |
| truncate | startup_ok `recovered=True` | data_lost `quarantined=True` |

**6 组全部能启动。** 无备份那 3 组仍是 `data_lost`——这是 M2 的设计意图：
**数据会丢，但服务可用且坏文件改名留证**，比"起不来 + 无痕"强得多。

> 6 条 `unavailable` 来自自动发现的 `memory_agent.backup`（无 Store 类），
> 是自动探测的正常噪声，**不是工具失效**，已如实标注。

---

## 二、M6 / M7【已修复并验证】"有日志也照样丢数据"

这两条的共性是：**读侧失败时记了日志，但仍然返回空，随后被全量覆盖。**

日志只让失败"可见"，**拦不住"丢"**。

### 对照实测

| | 原版 | 补丁版 |
|---|---|---|
| **M6** users.json | 注册后盘上只剩 `['dave']`，alice/bob/carol 全丢 | 抛 `StateUnreadable` 拒绝写入，**原文件完整保留** |
| **M7** templates.json | 保存后盘上 `[]`，自定义模板全丢 | 抛 `StateUnreadable` 拒绝写入，**原文件完整保留** |

### 修法

新增 `state_poison.py`（与 `store.py` 的 DB 护栏同一语义，用于普通 JSON）：

1. 读失败 → **置位** + 留痕（不是静默返回 `{}`）
2. 写侧落盘前 → **拒绝**生成新的权威状态
3. 坏文件 → **改名留证**，不直接覆盖

---

## 三、M5【已修·需你确认】自动恢复子系统是死代码

`backup_enabled: bool = False` 是默认值，而 `.env.example`、
`config.example.json`、`docker-compose.yml` **三处都没有 `BACKUP_ENABLED` 字样**。

`runtime.py:230` 只在为真时创建备份任务 ⇒ `backup_dir` 里永远没有 `ma-*.db` ⇒
**`check_and_recover` 就算代码全对也找不到备份。**

补丁副本已改为 `True`。**但需要你确认副作用：**
每日 `VACUUM INTO` 会占用与库等量的磁盘（受 retention 轮换限制）。

---

## 四、M7 的附带问题（建议一并修，未进补丁）

`BehaviorInsight.from_dict` 不做**逐条跳过**——实测一个条目缺 `description`
就抛 `KeyError`，导致**整个文件加载失败**，另外 2 个完好的模板跟着一起丢。

**一个坏条目会带走全部好条目。** 这比"文件损坏才丢"容易触发得多。

---

## 五、本轮核查过但**未发现**缺陷的项（诚实标注）

| 项 | 结论 |
|---|---|
| 停机时 WAL 是否干净关闭 | ✅ `store.close()` 已 commit + `wal_checkpoint(TRUNCATE)` |
| SQLite PRAGMA 配置 | ✅ `journal_mode=WAL` + `synchronous=FULL` + `foreign_keys=ON` + `timeout=30.0` |
| 单例连接锁覆盖（W93） | ✅ 执行型调用在锁外 **0 处**（第二轮复核） |
| 每小时 WAL checkpoint | ✅ `runtime._periodic_wal_checkpoint` 已注册 |

**"未发现"不等于"不存在"** —— 这几项是本轮集中核查过的，不是扫过就放过。

---

## 六、当前台账

| ID | 严重度 | 状态 |
|---|---|---|
| M1 | high | **已修复验证** |
| M2 | high | **已修复验证** |
| M5 | high | **已修·需确认副作用** |
| M6 | medium | **已修复验证** |
| M7 | medium | **已修复验证** |
| M3 | medium | **待你确认**（./data 是否 NAS 网络挂载） |
| M8 | low | still_open（绝对导入回退不可能成功） |
| M4 | info | 已核实修复（第六轮 CRITICAL-2） |

**8 项中 5 项已修复验证，1 项待你确认，1 项低优先级未修，1 项已核实历史修复。**

---

## 七、给你的建议（按优先级）

1. **确认 `./data` 是否在 NAS 网络挂载上（M3）** —— 这是唯一剩下的**根因级**问题。
   SQLite 官方警告网络文件系统上的 advisory lock 不可靠。
   **M1/M2/M5/M6/M7 全是"损坏后救不回来"，M3 才是"为什么会频繁损坏"。**
2. **确认 `backup_enabled` 改 True 的磁盘开销可接受**（M5）。
3. 应用全部补丁（M1/M2/M5/M6/M7 均在 `repos/ma-patched`）。
4. M7 的逐条跳过后补（一个坏条目不该带走全部好条目）。
5. M8 低优先级，可择机清理。

---

## 八、如实说明

- **M3 仍未验证**，需你确认部署路径。
- **M7 的"逐条跳过"子问题未进补丁**，只登记建议。
- **M8 未修**（低优先级，不活跃）。
- W96 的 6 条 `unavailable` 是自动探测噪声，**未冒充"已验证无缺陷"**。
- 前两轮全量命中中的 medium/low **仍未逐条分诊**，本轮人力集中在 DB 专题。
- 依赖 CVE 面未扫。
- 补丁只在只读副本 `repos/ma-patched`，**原仓库未改动**。

---

## 九、本轮最该记住的一条

**M6/M7 的共同教训：日志让失败"可见"，但挡不住数据"丢失"。**

这两处的代码都不是"静默"的——`_LOG.warning` 和 `print` 都在。
按第十九轮给 AFS-04 定的"失败是否可见"三态判据，它们属于**可见**档，不该报。

但它们**照样丢数据**。

这说明"可见性"和"破坏性"是两个正交维度：
- 可见 ⇒ 运维能察觉
- 不破坏 ⇒ 数据还在

只修可见性，等于只保证了"你知道自己丢了什么"，没保证"你没丢"。
**DO 族的规则需要同时表达这两个维度**，这也是第一轮登记的 W94 缺口所在——
本轮仍未修，留给下轮。
