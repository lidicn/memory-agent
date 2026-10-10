# memory-agent 审计报告 · 第一期第二轮

- **审计对象**：`lidicn/memory-agent`
- **轮次**：`round-002`
- **报告日期**：2026-10-09
- **承接**：第一轮数据库专题（M1–M6）

---

## 一句话结论

**容器启不来的两条路径都补上了：有备份能恢复（M1）、没备份能隔离重建（M2）。补丁副本实测：有备份 → `recovered=True`；无备份 → `quarantined=True` + 服务可继续启动，且损坏文件改名留证。另外确证 M7（自定义模板会因一次文件损坏全丢），并修掉了一个让 ASM-01 报出 327 条假阳性的布局假设缺陷（W95）。**

---

## 一、M2【高危·本轮修复并验证】恢复不了时没有降级路径

### 原实现

`check_and_recover` 找不到备份时只记一条 error 日志就返回：

```python
if not backups:
    logger.error(f"No backup found for recovery ...")
    return result          # ← 然后 init_schema 在同一份坏文件上必然抛异常
```

六种损坏组合（truncate / garbage / zeropage × 有/无备份）实测：
**无备份时 `init_schema` 必定抛异常 ⇒ 容器启动失败且必须人工介入。**

### 修复

新增 `_quarantine_corrupt()`：把损坏库改名成 `.corrupt-<时间戳>`
（**连同 `-wal` / `-shm` 一起处理**，否则新库会读到上一份日志），
让 `init_schema` 在原路径重建空库启动。

**改名而不是删除** —— 损坏的文件是唯一现场，删了就再也查不出损坏原因。

### 对照实测

| | check_and_recover | init_schema | 现场保留 |
|---|---|---|---|
| **原版** | 只记日志 | CRASH `DatabaseError` ⇒ 起不来 | ✗ |
| **补丁版** | `quarantined=True` | OK ⇒ 服务可继续启动 | ✓ |

### M1 + M2 组合验证（补丁副本同时含两条）

| 场景 | recovered | quarantined | init_schema |
|---|---|---|---|
| **有备份** | True | — | OK |
| **无备份** | False | True | OK |

两条路径互不干扰：有备份走恢复，没备份走隔离重建。

---

## 二、M7【中·确证】templates.json 损坏 → 保存一次抹掉全部自定义模板

实测：播种 2 个自定义模板 → `templates.json` 损坏 →
`_load_templates` 只剩内置 4 个 → `_save_custom_templates` 写 `[]` →
**盘上自定义模板全丢**。

与 doubao-butler D6（`fast_routes`）**同一个形状**：
护栏装在 `_load`，写发生在 `_save`。

### 附带发现

`from_dict` 不做**逐条跳过** —— 实测一个条目缺 `description` 字段，
`BehaviorInsight.from_dict` 抛 `KeyError`，导致**整个文件加载失败**，
另外 2 个完好的模板跟着一起丢。

也就是说：**一个坏条目会带走全部好条目。** 这比"损坏才丢"更容易触发。

---

## 三、W95【工作流】ASM-01 的 327 条 high 里几乎全是假阳性

这是本轮最有价值的工作流修复，三个独立缺陷叠在一起：

| 子项 | 根因 | 后果 |
|---|---|---|
| **W95a** | `src-layout`：包在 `src/memory_agent/`，而规则在 `<root>/memory_agent/` 找 | 64 个内部模块被判"不存在" |
| **W95b** | `pyproject.toml` 在仓库根，但以 `src` 为 root 时只在 `src` 下找 | 依赖清单读不到 |
| **W95c** | **`tomllib` 在 Python 3.10 不存在**，首版只写 `import tomllib`，ImportError 被 except 吞掉 | 依赖清单根本没读 |

三点修复后：**high 327 → 3**。

W95c 尤其值得记：它和第十六轮 external_tool 缺失是同一形态——
**依赖不可用 ⇒ 表现为"命中变多变少"而不是报错**。
那次是变少（工具没了），这次是变多（依赖清单读不到，于是全都"未声明"）。

剩下 3 条里 2 条仍是假阳性（`jose` 缺包名映射、`task_registry` 绝对导入回退），
1 条转为 M8 登记。

---

## 四、M8【低】绝对导入回退不可能成功

```python
# llm_client.py:27-29
try:
    from .task_registry import task_registry
except ImportError:
    from task_registry import task_registry      # ← 这一句不可能成功
```

`task_registry.py` 在包内（`src/memory_agent/`），不在 `src` 根 ⇒
相对导入失败的场景下，绝对导入**必然也失败**，而且抛出的
`ModuleNotFoundError` 会**掩盖真正的原始 ImportError**。

当前相对导入正常，所以不活跃；属**可诊断性**缺陷——真出问题时会误导排查方向。

---

## 五、M3 仍然是假设（需要你确认）

`docker-compose.yml:42` 是 `./data:/data`，`deploy_nas.sh` 指向
`/vol1/1000/docker/memory-agent`（群晖 NAS）。

SQLite 官方警告：**网络文件系统上的 POSIX advisory lock 不可靠**，
是数据库损坏的经典根因。

**这是"为什么会频繁损坏"，M1/M2/M5 只是"损坏后救不回来"。**
如果 `./data` 确实在网络挂载上，修多少恢复逻辑都治标不治本。

---

## 六、当前台账

| ID | 严重度 | 状态 | 一句话 |
|---|---|---|---|
| M1 | high | **已修复验证** | 恢复分支第一行裸调 connect()，文件头损坏时整段恢复不可达 |
| M2 | high | **已修复验证** | 无备份时无降级路径，改为隔离损坏库 + 重建空库启动 |
| M5 | high | still_open | `backup_enabled` 默认 False，三处示例配置均未提及 |
| M6 | medium | still_open | users.json 损坏 → 注册一次抹掉全部账号（含管理员） |
| M7 | medium | still_open | templates.json 损坏 → 保存一次抹掉全部自定义模板 |
| M3 | medium | **待确认** | ./data 是否在 NAS 网络挂载上 |
| M8 | low | still_open | 绝对导入回退不可能成功，掩盖原始错误 |
| M4 | info | 已核实修复 | 第六轮 CRITICAL-2（锁覆盖）确认已修 |

---

## 七、给用户的建议（按优先级）

1. **确认 `BACKUP_ENABLED`**（M5）——没开就开，否则恢复机制形同虚设
2. **确认 `./data` 是否在 NAS 网络挂载上**（M3）——这是根因层面的，其余都是补救
3. **应用 M1 + M2 补丁**——已在 `repos/ma-patched` 验证，两条路径都通
4. **M6 / M7 一并修**——与 M1/M2 同族（读失败被当成没有），建议统一成一条约定后一次过

---

## 八、如实说明

- **M3 仍未验证**，需你确认部署路径。
- **M5 / M6 / M7 / M8 未进补丁**，本轮只完成 M2（M1 上一轮完成）。
- **M7 的"逐条跳过"子问题**也建议一并修：一个坏条目不应带走全部好条目。
- 本轮全量命中 1273 条中，ASM-01 占 327 high（修复后降到 3）；
  其余 medium/low **未逐条分诊**。
- 补丁只在只读副本 `repos/ma-patched`，**原仓库未改动**。
- 依赖 CVE 面未扫。

---

## 九、本轮最该记住的一条

**W95 的三个子缺陷里，没有一个是"逻辑写错了"。**

- src-layout 探测：前两个项目都是 flat-layout，所以从没暴露
- pyproject 查找位置：前两个项目的 manifest 恰好和 root 同级
- `tomllib`：只在 Python 3.10 上才缺失

三个都是**"环境/布局假设"**，全靠换项目才撞出来。这和第一轮 W93 首版
把"获取连接"误判成"使用连接"是同一类：**规则内嵌了源项目的隐含前提。**

推论还是那条：换项目后第一件事不是跑规则，是**先确认它的前提还成不成立**。
