# memory-agent 审计报告 · 第一期第一轮（数据库专题）

- **审计对象**：`lidicn/memory-agent`
- **轮次**：`round-001`
- **报告日期**：2026-10-09
- **重点**：数据库相关缺陷（用户反馈「数据库最近经常出错，导致容器无法启动」）

---

## 一句话结论

**找到了容器启不来的直接原因，而且不止一个——是三个缺陷叠在一起：自动恢复分支第一行必崩（M1）、默认部署下根本没有备份可恢复（M5）、恢复失败后没有降级启动路径（M2）。三条同时命中，「数据库出错 → 容器启动失败」就是必然结果，不是偶发。**

M1 已在补丁副本修复并对照实测验证：修复前 `check_and_recover` 直接抛 `DatabaseError` ⇒ 启动失败；修复后 `recovered=True` + `init_schema OK` ⇒ 启动继续。

---

## 一、M1【高危·已修复并验证】恢复分支第一行裸调 connect()

### 现象

`store.py:709 check_and_recover()` 的流程是：检测 → 判红 → 从备份恢复。
**检测阶段**写得很好（`_run()` 有 try/except，`quick_check` 判红还会升级 `integrity_check` 复核，宁可慢 4 秒也不凭快判覆盖数据）。

但**恢复分支**的第一行是：

```python
# store.py:779
conn = self.connect()      # ← 没有 try/except
conn.close()
self._conn = None
```

而 `connect()` 内部（651 行）会执行 `PRAGMA journal_mode=WAL`。

**文件头损坏时，这一句必然抛 `DatabaseError: file is not a database`** ⇒ 异常从 779 行冒泡出去 ⇒ 下面 30 行恢复代码（`copy2` 备份 / 删 `-wal` `-shm` / `integrity_check` 复核）**一行都执行不到**。

### 实测对照

用 Store 自己建的库（schema 一致），文件头写入 4KB 垃圾，备份存在：

| | check_and_recover | init_schema | 容器 |
|---|---|---|---|
| **原版** | 抛 `DatabaseError` | — | **启动失败** |
| **补丁版** | `recovered=True`，用上 `ma-2026-01-01.db` | OK | **启动继续** |

### 为什么这条最值得看

讽刺点在于：**检测阶段有 try/except，30 行后的恢复阶段忘了包。**
有备份也救不回来——不是"恢复失败"，是恢复代码根本没机会跑。

这与 AutoForge F10、doubao-butler D1–D9 是同一族：**防护只做了一半**。
区别只是那两次是"防住了截断、没防住读失败"，这次是"防住了检测、没防住恢复"。

### 修复（一行语义）

```python
try:
    conn = self.connect()
    conn.close()
except Exception as exc:
    logger.warning(f"关闭待恢复的旧连接失败（可忽略，随后整体替换）: {exc}")
self._conn = None
```

这行的目的只是"关掉当前连接"，失败完全无妨（后面整份文件都会被替换）。

---

## 二、M5【高危】默认部署下根本没有备份可恢复

`config.py:328`：

```python
backup_enabled: bool = False
```

而 `.env.example`、`config.example.json`、`docker-compose.yml` **三处都没有 `BACKUP_ENABLED` 字样**。

`runtime.py:230` 只在 `backup_enabled` 为真时才创建备份任务 ⇒
**默认部署下 `backup_dir` 里永远不会有 `ma-*.db`** ⇒
`check_and_recover` 就算代码全对，也找不到任何备份。

**M1 + M5 叠加的效果：整个自动恢复子系统在默认部署下是死代码。**

这条很可能就是"数据库最近经常出错导致容器起不来"的主因——
你看到的可能不是"恢复失败"，而是"从来就没有可恢复的东西"。

---

## 三、M2【高危】恢复不了时没有降级启动路径

六种损坏组合实测（truncate / garbage / zeropage × 有/无备份）：

| 损坏方式 | 有备份 | 无备份 |
|---|---|---|
| truncate | 原版抛异常（M1）| 判红后无备份 ⇒ init_schema 抛异常 |
| garbage | 同上 | 同上 |
| zeropage | 同上 | 同上 |

**无备份时**：`check_and_recover` 只记一条 error 日志就返回，随后 `init_schema` 仍然抛异常 ⇒ 容器启动失败，且必须人工介入。

当前设计**没有**"把损坏库改名保留、建空库降级启动"的分支。

建议：确认无法恢复且无备份时，将损坏库重命名为 `.corrupt-<ts>` 后建空库启动。
**数据仍会丢，但服务可用且现场可查** —— 这比"起不来 + 无痕"强得多。

---

## 四、M6【中】users.json 损坏后注册一次抹掉全部账号

实测：播种 `alice / bob / carol` → `users.json` 损坏 → `_load_users()` 返回 `{}` →
`register("dave")` → **盘上只剩 `['dave']`，原 3 个账号（含管理员）全丢**。

写侧做得很规范（`tmp` + `fsync` + `os.replace` 原子写），
但读侧失败被当成"没有用户"，随后的写入是全量覆盖。

与 D1–D9 / F8–F10 同族，区别是这里**有 `_LOG.warning`**（失败可见），
所以不是"静默"，但**数据照样丢**。

---

## 五、M3【中·待确认】数据目录是否落在网络文件系统

`docker-compose.yml:42` 是 `./data:/data`，而 `deploy_nas.sh` 指向
`/vol1/1000/docker/memory-agent`（群晖 NAS 路径）。

SQLite 官方明确警告：**网络文件系统（NFS/SMB）上的 POSIX advisory lock 不可靠**，
是数据库损坏的经典根因。

**这是假设，需要你确认实际部署路径是否在网络挂载上。** 如果是，
那 M1/M2/M5 只是"损坏后救不回来"，而 M3 才是"为什么会频繁损坏"。

---

## 六、M4【已核实修复】第六轮 CRITICAL-2（锁覆盖）

项目自己的第六轮审计指出过"execute 与 commit 之间没有锁"，并为此引入了
`transaction()`。本轮新增 **W93 锁覆盖分析器**（AST 精确定位 + 负向测试）复核：

- `store.py` 有 105 处 `conn = self.connect()` 在锁外 —— **安全**，因为
  `connect()` 是幂等的单例获取，真正的 `execute/commit` 都在 `with self._lock:` 内
- **执行型调用（execute/commit/fetch…）在锁外的：0 处**

⇒ CRITICAL-2 已彻底修复。

**这里有个过程教训值得记**：W93 首版把"获取连接"误判成"使用连接"，
一次性报出 **281 条**。修正为只判执行型调用后降到 0。
281 条噪声如果进了报告，会把这轮所有真信号都淹没。

---

## 七、工作流迭代（本轮两项）

**W93**：新增 `core/analyzers/sqlite_lock_defects.py`。
用 AST 判定"触碰单例连接的语句是否落在持锁区内"，而不是靠 grep 计数
（`grep -c 'self.connect()'` 给 111，`with self._lock` 给若干——
**粗粒度统计无法回答"某处到底在不在锁内"**）。
含负向测试：锁外样例必须报、锁内样例不得报，两条均通过。

**W94（登记未修）**：DO-01 不区分"静默降级"与"有日志的降级"。
M6 就是后者——有 warning 日志，但数据仍丢。
第十九轮给 AFS-04 做的"失败是否可见"三态改造没有同步到 DO 族，
导致 DO-01 把"有日志但仍丢数据"和"完全静默"判成同一档。

**另修一处扫描范围污染**：工作副本里残留了 `.qoder/`（339 个 .py，历史审计快照），
已补进 `audit-exclude.txt`。这是 doubao-butler W89 的同一教训在新项目上的第一次兑现——
**换项目后第一件事就该确认扫描范围**。

---

## 八、数字

| 项 | 值 |
|---|---|
| 扫描文件（排除 .qoder/attic/benchmarks/tests 后） | 待下轮重跑 |
| 首轮全量命中（含污染） | 2344（high 29） |
| 确证缺陷 | **6**（M1–M6） |
| 已修复并验证 | **1**（M1） |

---

## 九、给用户的三条行动建议（按优先级）

1. **确认 `BACKUP_ENABLED` 是否为 true**（M5）。如果没开，现在就开——
   否则数据库一旦损坏，恢复机制形同虚设。
2. **应用 M1 补丁**（一行 try/except，已在 `repos/ma-patched` 验证有效）。
3. **确认 `./data` 是否落在 NAS 网络挂载上**（M3）。如果是，考虑改到本地卷，
   否则损坏会持续发生，修多少次恢复逻辑都治标不治本。

---

## 十、如实说明

- **M3 是假设**，未验证实际部署路径；需要你确认。
- **M2 / M5 / M6 未进补丁**，本轮只完成 M1。
- **首轮全量命中 2344 条含 `.qoder` 污染**，本轮只人工核验了 db 相关的高危项，
  其余 medium/low 未分诊。
- **M6 未做补丁**，修法与 M1 同族（读失败置位 + 写侧拒绝），可下一轮统一处理。
- **依赖 CVE 面未扫**；`bcrypt` / `python-jose` 为本轮实测临时安装。
- 补丁只在只读副本 `repos/ma-patched`，**原仓库未改动**。
