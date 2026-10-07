# memory-agent 第十一轮审计报告：资源生命周期（重建后的旧实例回收）

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.10「重建后旧实例是否回收」**
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

新维度与第九轮（无界状态增长）相邻但不同：**那个是容器不清空，这个是资源对象不释放**。

**判据**：找出所有 `self.X = NewObj(...)` 且 `NewObj` 所属类定义了 `close/aclose/shutdown/__exit__` 的赋值点 ⇒ 检查旧实例是否被关闭。

扫描得 **4 处重建**，其中 **`runtime.reload_config` 是唯一关了旧实例的一支**，而它内部的 `llm.reconfigure` 恰恰是漏的那一支。

### 确认缺陷

**MA-25（High）：`llm_client.reconfigure` 整表重建 `providers`，旧实例从不 `close()`。**

实测（`POST /api/config` 每保存一次 → 3 个新 `AsyncClient`）：

| 配置保存次数 | 存活未关闭的 AsyncClient |
|---|---|
| 10 | **30** |
| 30 | **90** |
| 50 | **150** |
| 50（应用修法后） | **3**（恒定） |

---

## 二、方法：v2.10 的迭代点

| 版本 | 做法 |
|---|---|
| v2.8（九轮） | 跨项目缺陷族迁移 |
| v2.9（十轮） | 读项目自带量具声明的"看不见"部分 |
| **v2.10（本轮）** | **资源生命周期：重建后的旧实例回收** |

**lesson 121**：与"无界状态增长"要分开扫——判据分别是"**键是否清理**"（容器）和"**旧实例是否 close**"（资源对象）。

### 扫描结果

定义 `close/aclose/shutdown/__exit__` 的类 **6 个**。重建赋值点 4 处：

| 文件:行 | 函数 | 被重建的属性 | 旧实例回收 |
|---|---|---|---|
| `runtime.py:856` | `reload_config` | `config` / `ha` / `ha_db` | ✅ **3 处回收** |
| `runtime.py:856` → `llm_client.py:427` | `reconfigure` | `self.providers` | ❌ **无** |
| `llm_client.py:427` | `reconfigure` | `self.providers` | ❌ **无** |
| `insights/api.py:238` | `reload_config` | `repo` / `core` / `nl` / `legacy` | ⚠️ 均无 close 方法（非资源类，见第四节） |

---

## 三、确认缺陷

### 🔴 MA-25　`llm_client.reconfigure`：整表重建 providers，旧 `AsyncClient` 从不关闭

**位置**：`llm_client.py:427-431`

```python
def reconfigure(self, config: Optional["Config"] = None) -> None:
    self.config = config
    backs = self._resolve_backends(config)
    enabled = [b for b in backs if b.get("enabled", True)]
    # 若所有后端被禁用，仍保留第一条作为兜底，避免彻底瘫痪
    self.providers = [LLMProvider(b, config) for b in (enabled or backs[:1])]
    #                 ↑ 旧列表被整体丢弃，从未调用 old.close()
```

**旧实例持有什么**：`LLMProvider._ensure_client()` 懒创建 `httpx.AsyncClient`（`llm_client.py:155`），每个 client 持有独立 `AsyncHTTPTransport` 连接池。

**`LLMProvider.close()` 写得很完整**（判 `is_closed`、区分 loop 是否 running、用 TaskRegistry 持强引用）——**但 grep 全部调用点只有两处**：`LLMRouter.close()`（435 行）与 `runtime.shutdown()`（826 行）。**`reconfigure` 这条重建路径上不走 close**（lesson 124）。

#### 实测：泄漏量级

```
══ 复刻 llm_client.reconfigure（3 后端/次）══

配置保存  10 次 → 存活未关闭的 AsyncClient  30 个
配置保存  30 次 → 存活未关闭的 AsyncClient  90 个
配置保存  50 次 → 存活未关闭的 AsyncClient 150 个

══ 修法对照（新建前先关旧的）══
配置保存  50 次 → 存活未关闭 3 个（恒定）
```

#### 可达性（三条路径都会触发）

| 路径 | 触发 |
|---|---|
| `POST /api/config` | `update_config_api` → `rt.reload_config()` → `self.llm.reconfigure(...)` |
| `POST /api/collect/config` · `/api/poller/config` | `collect_config` → 同上 |
| `POST /api/ha/rooms` | `ha_save_rooms` → 同上 |

⇒ **每一次配置保存都泄漏一批连接池**。这是正常运维操作，不需要构造攻击；且配置保存低频但**长期运行不重启**正是本项目的部署形态（Docker 常驻）。

#### 同仓正面对照（lesson 122）

`runtime.reload_config` 的 **ha_db 一支做对了**：

```python
_old_ha_db = getattr(self, "ha_db", None)
if _old_ha_db is not None:
    try:
        _old_ha_db.close()
    except Exception as _exc:
        print(f"[Runtime] 关闭旧 HA MariaDB 客户端异常: {_exc}")
self.ha_db = self._build_ha_db(self.config)
```

注释还标着"稳定性审计缺陷5：覆盖 ha_db 前关闭旧连接，避免僵尸 pymysql 连接"。

⇒ **同一个调用点、相邻两行、一支对一支错**——这是最强的证据，不需要外部论据。

#### 注意一个容易混淆的点

`LLMRouter.close()` 存在且正确：

```python
def close(self) -> None:
    for p in self.providers:
        try: p.close()
        except Exception: pass
```

`runtime.shutdown()` 也确实调用了它。**但 `shutdown` 只在进程退出时执行一次**（lesson 125）——`reconfigure` 是运行期反复执行的。

⇒ **全局 shutdown 正确 ≠ 热更新路径正确。**

---

## 四、验证通过（确认无问题）

| 项 | 结论 |
|---|---|
| `runtime.shutdown()` | ✅ **很完整**：mqtt / vision / collector / llm / ha_db / store 逐个关闭，每个都有 try + 日志 |
| `runtime.reload_config` 的 ha_db | ✅ 关旧连接后再建新（"稳定性审计缺陷5"） |
| `runtime.reload_config` 的 ha | ✅ 覆盖前关闭旧连接（第九轮已确认） |
| `insights/api.py:238 reload_config` | ✅ **非资源类**：`repo` / `core` / `nl` / `legacy` 均未定义 close，重建只是丢弃对象由 GC 回收，**不构成资源泄漏** |
| `algo_kernel.py:182 _build_vocab` | ✅ 纯内存 dict，无资源 |
| `runtime.__init__` 的 store/llm/mqtt | ✅ 构造期赋值（非重建），且 `shutdown` 已覆盖 |
| `TaskRegistry` | ✅ 路线图 3.2，后台任务统一收口，`llm.close` 用它持强引用防 GC |

---

## 五、修复建议

### MA-25（一处，约 4 行，照抄同函数 ha_db 的写法）

```python
def reconfigure(self, config: Optional["Config"] = None) -> None:
    self.config = config
    backs = self._resolve_backends(config)
    enabled = [b for b in backs if b.get("enabled", True)]
    old = getattr(self, "providers", []) or []
    self.providers = [LLMProvider(b, config) for b in (enabled or backs[:1])]
    for p in old:                       # ← 新增
        try:                            #   与 ha_db 一支同形状
            p.close()
        except Exception:
            pass
```

**注意顺序**：先建新的再关旧的（与上面对照写法一致），避免中途失败导致无 provider 可用。

### 建议加门禁（治本）

> `check_rebuild_without_release.py`：断言对**定义了 `close/aclose/shutdown` 的类**做
> `self.X = 新实例` 赋值时，同一函数内必须出现旧实例的关闭调用。

这条规则能一次性覆盖"热更新路径遗漏资源回收"整族，且与项目"基线只准减少"的机制契合。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | 连续 `POST /api/config` 50 次 | 存活 `AsyncClient` **恒定**（= 后端数），不随次数增长 |
| 2 | 同上后查看进程 fd / 连接数 | 不随保存次数增长 |
| 3 | `reconfigure` 后旧后端仍可访问一段时间？ | 无残留连接（旧池已关） |
| 4 | 全部后端禁用时 | 仍保留兜底第一条，且旧的全关 |
| 5 | `runtime.shutdown()` | 仍正常（不重复关闭、不抛异常） |

---

## 六、横向观察

### "修了一半"第十一次，且这次的形态最微妙

| 轮 | 族 | 已修 | 未修 |
|---|---|---|---|
| 八 | 配置边界 | collect 端点 | config 端点 |
| 九 | IP 来源 | `app.py` | `auth_routes` |
| 十 | 同步 I/O 卸载 | 96 / 172 | 76 个 handler |
| **十一** | **旧实例回收** | **`reload_config` 的 ha_db** | **`reload_config` 的 llm** |

前几次是"**不同文件**之间漏一个"，这次是**同一个函数、相邻两行**之间漏一个——距离更近，也更能说明问题：**修复动作是逐点发生的，没有形成一个"凡是重建必先关闭"的约定。**

### 有意思的是：这次的对照就躺在同一个函数里

`_old_ha_db.close()` 紧接着就是 `self.llm.reconfigure(self.config)`。写 ha_db 那段的人想清楚了"覆盖前要关旧连接"，但**这个想法没有传播到下一行**。

这与前几轮完全一致：**正确范式孤岛**——范式写对了（还带了注释和审计编号），但停在它被写下的那一行。

### 与 AutoForge 的对照

AutoForge 第二轮报过 `_CLIENT_POOL` 有界 LRU(8) 且**锁外关闭并留痕**（memory-agent 第二轮验证为"确认已铺开"）。本轮 MA-25 是它的反面：**同一类资源（HTTP client），一处有池化+关闭，一处连关闭都没有。**

⇒ 两边共同的模式：**连接池/客户端的生命周期管理，往往只在"写它的那个人想到的地方"做对了。**

---

## 七、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 用 `auditlib` 原语 | ✅ 本轮用 `all_walk` / `full_unparse` / `rel_path` |
| **门 2** | 命中多时手工分诊 | ✅ **决定性**——4 处重建点按"被重建类是否定义 close"降到 1 处真缺陷；`insights/api.py` 的 4 个属性经查无 close 方法，排除 |
| **门 3** | 每条缺陷有实测 PoC + 同仓对照 | ✅ MA-25 有"N 次保存 → M 个存活"表 + 修法对照；同函数 ha_db 一支为正面对照 |

### 遗留队列（十一轮累积）

| 项 | 状态 |
|---|---|
| MA-03 端到端 | ⚠️ 未复现（`chromadb` 太重） |
| 47 处静默降级中"否定方向"44 处 | ⚠️ 仅抽查 |
| M2 的 34 处未钳制 `LIMIT` 站点 | ⚠️ 抽查，未逐一确认 |
| `save_skill` 并发 | ⚠️ 观察项，未复现交叠 |
| 24 个数值配置键中其余 16 个 | ⚠️ 未逐一找消费点 |
| `self._lock` 15 处 | ⚠️ 未逐一核查锁覆盖范围 |
| 29 处传递性阻塞中未单报的 | ⚠️ 归入 MA-24 的 P99 范畴 |
| **其他类的 `close` 调用点普查** | ⚠️ 本轮只查了 `LLMProvider`；`MqttBridge` / `Store` 的重建路径未发现（构造期赋值），但未逐类核实 |

### 环境

| 项 | 值 |
|---|---|
| 已装 | `bcrypt`、`python-jose`、`httpx`、`tomli` |
| 仍未装 | `chromadb`（体积过大）、`starlette`、`pymysql` |
| 门禁 | `run_gates.py . --no-smoke` → rc=0（208 条基线） |
| 新增 lessons | **121–125**（已并入 `lessons-round2.md`，共 1372 行） |
| 仓库状态 | 探针已还原；源码、量具、门禁、基线均未修改 |
