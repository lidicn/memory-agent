# memory-agent 第七轮审计报告：共享文件的读-改-写与写入原子性

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.6「解除环境阻塞 + 注释关键词反推新维度」**
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

本轮确认 **1 条 High 缺陷**，且是**全仓唯一一处**符合该形状的站点。

**MA-17（High）：`app.py:503 metrics_ingest_endpoint` —— 共享指标文件的读-改-写，非原子、无锁。**

三个实测数字：

| 场景 | 实测 |
|---|---|
| 20 线程各写不同 key | **只剩 5 条，丢 15 条** |
| 文件损坏后一次**完全正常**的 ingest | 原 3 条 → **落盘 1 条**（永久抹掉） |
| 读写并发 | **空读率 59.1%**，读侧无日志 |

### 为什么它是"唯一一处"

全仓扫"**读同一路径 → 改 → 写回**"的形状：

```
读-改-写共享文件：1 处
既无原子写也无锁：1 处   ← app.py:503 metrics_ingest_endpoint
```

**13 处非原子写 → 加"读同一路径"条件后只剩 1 处，零假阳性**（lesson 102）。

---

## 二、方法：v2.6 的两个迭代点

| 版本 | 做法 |
|---|---|
| v2.4（五轮） | 找自带门禁 → 变异测试找盲区 |
| v2.5（六轮） | 已确认缺陷形状清单 → 逐条全量反查 |
| **v2.6（本轮）** | **① 解除环境阻塞闭合遗留；② 用"注释里反复出现的关键词"反推新维度** |

### 迭代点 1：解除环境阻塞（lesson 100）

`pip install bcrypt` 后，`auth.py:92 _save_users` 遗留项直接闭合——**验证为正确**：

```python
tmp_fd, tmp_path = tempfile.mkstemp(prefix=".users-", suffix=".tmp", dir=directory)
... json.dump / flush / os.fsync ...
os.replace(tmp_path, self.users_file)
except: 清理 tmp 并 raise
```

⇒ 原子写、失败清理、重新抛出，三件事都对。**遗留项里标"因缺依赖未验证"的，优先装依赖。**

### 迭代点 2：注释关键词反推维度（lesson 101）

全仓 `grep 原子` 得 **5 处**，每处都写了为什么：

| 位置 | 注释 |
|---|---|
| `config.py:335` | 「原子写入：先写临时文件再 `os.replace`，避免中断导致配置损坏」 |
| `auth.py:93` | 「原子写，避免中断损坏账号文件」 |
| `home_profile.py:17` | 「原子写 profile.md：临时文件 + `os.replace`，**并发写不读半截**」 |
| `templates.py:323` | 「原子写，避免并发保存写坏文件」 |
| `template_validate.py:74` | 「原子写，与 templates.json 同目录」 |

**一个团队在 5 个文件里反复写同一个理由 = 那是他们踩过的坑。**
⇒ 反查"所有写文件处，哪些没用原子"即得新维度，且天然带 5 个正面对照。

---

## 三、确认缺陷

### 🔴 MA-17　`metrics_ingest_endpoint`：共享 `af_metrics.json` 的 RMW，非原子无锁

**位置**：`app.py:503-527`（写）、`app.py:529-537`（读）

```python
# 写
store: dict = {}
if os.path.exists(_METRICS_PATH):
    try:
        store = json.loads(Path(_METRICS_PATH).read_text(encoding="utf-8"))
    except Exception:
        store = {}                       # ← 解析失败静默变空
store[key] = payload
Path(_METRICS_PATH).write_text(json.dumps(store, ...), encoding="utf-8")

# 读
try:
    store = json.loads(Path(_METRICS_PATH).read_text(encoding="utf-8"))
except Exception:
    return JSONResponse({"ok": True, "metrics": {}})    # ← 无日志
```

**可达性**：`/api/metrics/ingest` 在 `BUTLER_POST_PATHS`（AutoForge 运行指标回灌），`/api/metrics` 在 `BUTLER_GET_PATHS`。契约见 `docs/MA_METRICS_CONTRACT.md`。

#### 实测 1：并发写入丢数据

```
20 线程各写不同 key（k0..k19）
→ 落盘 5 条     ❌ 丢失 15 条
```

#### 实测 2：损坏后一次正常写入 → 历史被永久抹掉

```
步骤1 写入 3 条              → 落盘 3 条
步骤2 文件被截断（模拟中断）→ query() 返回 0 条（静默，与"没有指标"不可区分）
步骤3 一次完全正常的 ingest   → 落盘 1 条
```

⇒ **原 3 条永久丢失；返回体仍是 `ok: True`，全程无 warning。**

这正是 AutoForge 侧 R10-02 / R16-01 的同一形状（RMW-on-silent-empty），在 memory-agent 的第一次确认。

#### 实测 3：读写并发 → 半截读

```
读 1327 次，其中读到空 784 次 → 空读率 59.1%
```

`write_text` 是**截断后写入**，中间存在"文件为空/半截"的窗口。读侧 `except` 静默返回空 ⇒ **"读到半截"与"确实没有指标"返回完全相同**。

#### 定 High 的理由

1. **三条独立失效路径**（并发丢失 / 损坏后抹除 / 半截读），实测均有数字
2. **写入方是外部服务**（AutoForge 回灌），并发是正常运维而非构造攻击
3. **全链路静默**：读写两侧都无日志，`ok: True` 一路返回
4. **与同仓 5 处已做对的写法形成直接对照**（见下）

#### 正面对照（同仓 `home_profile.py:17`）

> 原子写 profile.md：临时文件 + `os.replace`，**并发写不读半截**

⇒ 项目**已经想清楚了这个问题的两面**（中断损坏 + 并发半截读），并在 5 个文件里写对了；`af_metrics.json` 是唯一没覆盖到的。

---

## 四、验证通过（确认无问题）

| 项 | 结论 |
|---|---|
| `auth.py:92 _save_users` | ✅ **原子写正确**（mkstemp + fsync + replace + 失败清理 + raise）—— 遗留项闭合 |
| `config.py:335 save` | ✅ 原子（mkstemp + fsync + replace + 清理） |
| `home_profile.py:22 write_profile_atomic` | ✅ 原子，且注释点明"并发写不读半截" |
| `templates.py:333 _save_custom_templates` | ✅ 原子 |
| `template_validate.py:110 _save` | ✅ 原子 |
| `api/behavior_routes.py:398 behaviors_home_profile` | ✅ **遗留项闭合**：`max_chars` 有 try + `max(100, min(_, 20000))`；写走 `write_profile_atomic` 且卸载到线程 |
| `feedback_pack.py:186/289` | ✅ 写临时文件后 `tar.add` 并 `unlink`，非共享路径 |
| `tv_service.save_snapshot` / `vision_service._save_snapshot` | ✅ **唯一文件名**（时间戳到秒），无 RMW |
| `mcp_server.seed_builtin_skills:678` | ✅ 有 `if os.path.isfile(dst): continue` 幂等保护，且只在启动期 |
| `app.py:800 _dump_traceback_handler` | ✅ 调试用，写失败 `continue` 到下一个路径 |

---

## 五、观察项（不升级为缺陷）

### 观察 1：`mcp_server.save_skill:3121` 无锁并发写同一路径

`save_skill` 用 `open(path, "w")` 直接写 `SKILL.md`，无锁、非原子。版本号 `new_version` 来自**调用方传入内容的 frontmatter**（不是从磁盘读），所以**不构成磁盘 RMW**，丢更新风险低于 MA-17。

并发实测（12 线程 × 200KB）：**本次未观察到内容交叠**（落盘长度 200021 vs 期望 200020，frontmatter 只出现 1 次）。

⇒ **不能报成缺陷（没复现），也不能说安全（无锁是事实，只是时序没撞上）**。标为观察项（lesson 105）。建议补锁 + 原子写，成本很低。

### 观察 2：配置热更新的内存-磁盘分歧

`api/config_routes.py:257-260`：

```python
if current != value:
    setattr(cfg, key, value)     # 先改内存
    ...
if changed:
    cfg.save()                   # 后落盘
    rt.reload_config()
```

⇒ 若 `cfg.save()` 抛异常（磁盘满/权限），**内存已被修改而磁盘仍是旧值**，且 `reload_config()` 不执行 ⇒ 分歧持续到下次重启。返回 500 但不回退内存状态。

**未升级为缺陷**：需要磁盘故障才触发，且 500 是可见的。列出供团队判断是否需要 try 内回滚。

---

## 六、修复建议

### MA-17（一处，对齐同仓 5 处既有范式）

最省事的做法是复用 `home_profile.write_profile_atomic` 的形状，或直接把指标搬进 SQLite（`store.py` 已有 `log_mcp_audit` 等审计表，天然原子 + 事务）。

若保持文件形态，最小改动：

```python
# 1) 加一把模块级锁，覆盖 读→改→写 全程
_METRICS_LOCK = threading.Lock()

with _METRICS_LOCK:
    store = _load_metrics_or_raise()      # 解析失败要 raise，不要静默 {}
    store[key] = payload
    _atomic_write_text(_METRICS_PATH, json.dumps(store, ...))   # mkstemp+fsync+replace

# 2) 读侧同样持锁 + 解析失败留痕
with _METRICS_LOCK:
    try:
        store = json.loads(Path(_METRICS_PATH).read_text(encoding="utf-8"))
    except Exception:
        _LOG.warning("af_metrics.json 解析失败，返回空（非无指标）: %s", exc)
        store = {}
```

**关键点：`except: store = {}` 必须改掉**——它是"损坏 → 下次写入 → 历史抹除"链条的第一环。

### 建议加门禁（治本）

> `check_shared_file_rmw.py`：断言同一函数内出现 `read_text/json.load` + `write_text/json.dump`
> 时，必须同时出现 `mkstemp/os.replace` 或持锁。白名单放现有 5 处已合规的。

这条规则能永久封住 MA-17 这一族，且与项目"基线只准减少"的机制契合。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | 20 并发 ingest 不同 key | 落盘 **20** 条 |
| 2 | 截断 `af_metrics.json` 后 ingest 一次 | 原数据**保留**（或明确报错，不静默清空） |
| 3 | 读写并发 3 秒 | 空读率 **0%**（或读侧有 warning 日志） |
| 4 | 截断后调用 `/api/metrics` | 日志出现解析失败告警 |
| 5 | `save_skill` 并发 12 次 | 落盘文件只有 1 份 frontmatter |

---

## 七、横向观察

### 七轮下来，"正确范式孤岛"第五次出现——这次的对照最完整

| 轮 | 正确范式 | 已做对 | 未铺开 |
|---|---|---|---|
| 三 | `_num(lo,hi)` / LIMIT 钳制 | `behavior_routes`、`list_mcp_audit` | `insight_routes`、`llm_routes`、MCP 2 处 |
| 四 | `clamp_days` | days 族（门禁覆盖） | `hours/minutes/seconds` 同族 |
| 五 | 门禁 `swallow-and-claim-ok` | 抓"空体 except" | 抓不住"体非空降级" |
| **七** | **原子写（mkstemp+fsync+replace）** | **5 处** | **`af_metrics.json` 1 处** |

本轮的特殊之处：**5 处都写了注释说明"为什么"**，其中 `home_profile.py:17` 甚至点明了"并发写不读半截"——MA-17 踩的正是这两件事（半截读 + 中断损坏）。

⇒ **不是不知道，是第 6 个写文件的地方没想起来用。**

### 与 AutoForge 二十轮的对照

MA-17 与 AutoForge 的 R10-02（`_read_tags` 静默空 → `set_tags` 覆盖）、R16-01（`_load_aliases` 静默空 → `set_alias` 抹除）**是同一个形状在两个代码库里的三次出现**。三次的共同点都是：**读侧把"读不出来"当成"没有"，然后写侧把"没有"写回去。**

⇒ 建议两边共用同一条门禁规则（见第六节），这条规则的形状是稳定的，与业务无关。

---

## 八、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 新脚本用 `auditlib` 原语 | ✅ 本轮扫描用 `own_walk` / `full_unparse` / `rel_path` |
| **门 2** | 命中异常时手工复核 | ✅ **决定性**——13 处非原子写 → 加"读同一路径"条件 → 1 处，零假阳性 |
| **门 3** | 每条缺陷有实测 PoC + 同仓对照 | ✅ MA-17 三个场景全部实测；正面对照 `home_profile.py:17` |

### 遗留队列（七轮累积）

| 项 | 状态 |
|---|---|
| `auth.py:92 _save_users` | ✅ **本轮闭合**（装 bcrypt 后验证正确） |
| `api/behavior_routes.py:398 behaviors_home_profile` | ✅ **本轮闭合**（try + 钳制 + 原子写 + 卸载线程） |
| `timedelta` 同族剩余 11 处 | ⚠️ 未逐一核查（抽查 arena `history_days` 安全：走 `resolve_range` → `clamp_days`） |
| MA-03 端到端 | ⚠️ 未复现（`chromadb` 太重，未装） |
| 47 处静默降级中"否定方向"44 处 | ⚠️ 仅抽查 |
| M2 的 34 处未钳制 `LIMIT` 站点 | ⚠️ 抽查，未逐一确认 |
| `save_skill` 并发 | ⚠️ 观察项，未复现交叠 |

### 环境

| 项 | 值 |
|---|---|
| 本轮新增安装 | `bcrypt 5.0.0`（闭合 `auth.py` 遗留项） |
| 仍未装 | `chromadb`（体积过大）、`starlette` |
| 门禁 | `run_gates.py . --no-smoke` → rc=0（208 条基线） |
| 新增 lessons | **100–105**（已并入 `lessons-round2.md`，共 1185 行） |
| 仓库状态 | 探针已还原；源码、门禁、基线均未修改 |
