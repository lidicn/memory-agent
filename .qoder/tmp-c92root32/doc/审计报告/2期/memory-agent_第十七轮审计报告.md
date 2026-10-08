# memory-agent 第十七轮审计报告：跨进程契约（真源与同步）

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.16「跨进程契约」** —— 找"唯一真源 / 最新版本 / 同步 / 拉取"类声明，验证同步动作是否真能达成
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

前十六轮审的多是**单进程内的行为**。本轮换到**跨进程契约**：当代码声明"A 是 B 的真源""Agent 通过 X 拉取最新版"时，那个同步动作是否真能达到声明的效果（lesson 152）。

### 确认缺陷

| 编号 | 级别 | 位置 | 一句话 |
|---|---|---|---|
| **MA-33** | 🔴 High | `mcp_server.py:658` `seed_builtin_skills` | 声明"内置技能即网关作为**真源**对外提供的**最新版本**"，但同步判据只有 `os.path.isfile(dst)` —— **完全不比较版本** ⇒ 旧版部署升级后永久停在旧版，且无任何日志 |

### 三个判据的核查结果

| 判据 | 结果 |
|---|---|
| **A** 演进式契约（版本/类型字段的未知值处理） | 47 处读取信封字段的函数 → 分诊：多为内部枚举（有分支处理），**无新增缺陷** |
| **B** 跨仓出站投递的 `trace_id` 契约 | ✅ **0 处违规**：4 处调用点在方法内部统一生成 `trace_id`（`publish_health_change` 272 行、`_maybe_publish_alert` 1132 行），契约成立 |
| **C** 真源同步判据 | ❌ 1 处缺陷（MA-33） |

---

## 二、方法：v2.16 的迭代点

| 版本 | 做法 |
|---|---|
| v2.14（十五轮） | 常驻周期任务 |
| v2.15（十六轮） | 三方一致性（UI/可写/消费） |
| **v2.16（本轮）** | **跨进程契约：真源 / 同步 / 版本比较 / 升级场景** |

**lesson 153（本轮最实用）**：docstring 里**两条声明互相打架**时，实现一定只满足了一条。`seed_builtin_skills` 同段注释里既说"不覆盖 Agent 已迭代出的**更高**版本"，又说"内置技能即真源、对外提供**最新版本**"——但实现只有 `if os.path.isfile(dst): continue`，**既不比较版本，也就无法区分两者**。读两段话就能定位，不用跑代码。

---

## 三、确认缺陷

### 🔴 MA-33　内置技能是"真源"，但升级后永远同步不进来

**位置**：`mcp_server.py:658-680` `seed_builtin_skills`

**两条互相打架的声明**（原文，同一段 docstring）：

```
启动种子化：把包内内置技能（skills_bundle）首次写入网关 skills_dir。

仅当目标技能不存在时才写入（不覆盖 Agent 已迭代出的更高版本），返回新写入的数量。
内置技能即网关作为真源对外提供的最新版本，Agent 通过 get_skill 拉取。
```

**实现**（675 行）：

```python
dst = os.path.join(dst_dir, "SKILL.md")
if os.path.isfile(dst):
    continue          # ← 唯一判据：文件存在即跳过，**不比较版本**
```

#### 版本矩阵实测（bundled = v5）

| 磁盘既有版本 | 写入数 | seed 后版本 | 声明①「不覆盖**更高**」 | 声明②「真源 = **最新**」 |
|---|---|---|---|---|
| （不存在） | 1 | 5 | ✅ 符合 | ✅ |
| **v1 旧版** | 0 | **1** | ❌ **违反** | ❌ **违反** |
| **v3 旧版** | 0 | **3** | ❌ **违反** | ❌ **违反** |
| v5 同版 | 0 | 5 | ✅ 符合 | ✅ |
| v7 更高 | 0 | 7 | ✅ 符合（Agent 迭代应保留） | — |

⇒ **v1 / v3（低于 bundled 的 5）也被跳过**。声明①只承诺保护"更高版本"，实现却保护了"任何已存在的版本"。

#### 可达性：技能目录是持久化的

```
config.py:327        skills_dir: str = "/data/skills"
docker-compose.yml   - ./data:/data          ← 镜像升级后目录保留
runtime.py:173       seeded = await asyncio.to_thread(self._seed_builtin_skills)
```

⇒ **升级镜像 ⇒ 旧技能文件仍在 ⇒ seed 每次都跳过 ⇒ v1 部署永远停在 v1**（lesson 155）。

#### 静默：跳过时无任何日志

`runtime.py:173-175` 只在 `seeded` 非零时打印：

```python
seeded = await asyncio.to_thread(self._seed_builtin_skills)
if seeded:
    print(f"[Runtime] 种子化 {seeded} 个内置技能到 {self.config.skills_dir}")
```

⇒ **跳过时不打印任何东西**，运维看不到"有 N 个内置技能被旧版本挡住"。

#### 为什么定 High

- 该技能（`insight`）的正文明确是**教 Agent 怎么用 MCP 工具**的："通过 memory-agent 的 MCP 工具获取服务端已算好的洞察，不要自己拉原始事件硬算" —— 这是 Agent 行为的**唯一真源**
- 后果是**永久**的（不是偶发），且**完全静默**
- 触发条件（升级镜像）是**正常运维**

#### 修复方向

把存在性判据换成版本比较：

```python
if os.path.isfile(dst):
    cur = _read_skill_meta(dst).get("version", 0)
    bund = _read_skill_meta(src).get("version", 0)
    if isinstance(cur, int) and isinstance(bund, int) and cur >= bund:
        continue          # 仅当磁盘版本 ≥ 包内版本才跳过（兑现声明①）
    # 否则用包内版本覆盖，并留痕说明是升级
```

同时补日志：

```python
if skipped_outdated:
    print(f"[Runtime] {len(skipped_outdated)} 个内置技能因本地版本较低应升级，"
          f"当前实现跳过（旧版仍在生效）")
```

---

## 四、验证通过（确认无问题）

| 项 | 结论 |
|---|---|
| **判据 B：`trace_id` 跨仓契约** | ✅ 5 个出站调用点中，`publish_notify` 显式传 `trace_id`；其余 4 处（`publish_health_change` / `_maybe_publish_alert`）在**方法内部**用 `_new_trace_id()` / `uuid4` 统一生成 ⇒ **契约成立，0 违规** |
| **`mqtt_bridge` 的 fail-closed** | ✅ `publish_notify` 缺 `trace_id` 即 `return False` + 打印（330-345 行），注释写明"MA 是旁路能力，拒绝的表现是返回 False 而不是抛进主链路" ⇒ **设计正确** |
| **`get_skill` 路径遍历防护（P2-7）** | ✅ 双重校验：字符黑名单 + `realpath` 前缀判定；`res_skill` 资源同口径 ⇒ **无绕过** |
| **`save_skill` 参数校验** | ✅ `name` 正则白名单、`content` 非空校验 |
| **`publish_health_change` 别名兼容** | ✅ `entity_id` / `device_id` 双写，注释写明 DCD 裁定理由（`from`/`to` 是 DB 判"失联 vs 恢复"的依据） |
| **判据 A：47 处信封字段读取** | ⚠️ 分诊后多为内部枚举且带分支处理（`rule_engine` 的 kind/type、`insights` 的 type），**无新增缺陷** |

---

## 五、修复建议

### MA-33（一处）

见上节代码。要点三条：
1. **版本比较取代存在性判断**（兑现"只保护更高版本"）
2. **升级时留痕**（打印被跳过的、以及被升级的）
3. 若希望"Agent 迭代过就永不覆盖"，应改为显式标记（如 frontmatter 加 `agent_modified: true`），而不是靠版本号猜

### 建议加门禁

> `check_source_of_truth_sync.py`：断言声明"真源/最新版本"的同步函数，
> 其跳过条件中**必须出现版本比较**（或显式豁免登记）。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | 磁盘 v1、bundled v5 → 启动 | 磁盘变为 **v5**，并打印"已升级" |
| 2 | 磁盘 v7、bundled v5 → 启动 | 磁盘仍为 **v7**（保护 Agent 迭代） |
| 3 | 磁盘 v5、bundled v5 → 启动 | 无写入、无报错 |
| 4 | 不存在 → 启动 | 写入 v5（现有行为不变） |
| 5 | 跳过时 | **有日志**说明被跳过的技能与原因 |

---

## 六、横向观察

### "真源"这个词在本项目里出现两次，一次做对了一次没有

| 位置 | 声明 | 是否达成 |
|---|---|---|
| `runtime.py:435` `adm_caps` | 「工具名取 `tool_schema.TOOL_NAMES`——那份就是"MCP 面暴露了哪些工具"的唯一真源」 | ✅ **十三轮已实测验证**：双向一致，[B] 方向 0 处 |
| `mcp_server.py:658` `seed_builtin_skills` | 「内置技能即网关作为真源对外提供的最新版本」 | ❌ 本轮 MA-33 |

⇒ 差别在于：**前者把真源做成了"取值来源"（不可能不一致），后者把真源做成了"一次性拷贝"（必然漂移）**。这是架构层面的分野，值得在后续设计中注意——**真源应当被读取，而不是被复制**。

### 第十七次"正确范式孤岛"

| 正确 | 错误 |
|---|---|
| `adm_caps` 真源 = 取值来源 | `seed_builtin_skills` 真源 = 一次性拷贝 |
| `publish_notify` trace_id 缺即拒 + 打印 | seed 跳过静默 |
| `get_skill` 双重路径校验 | — |

### 声明对照依然是最高效的入口

本轮 MA-33 是**读两段 docstring 定位的**，代码跑都没跑就基本确定。十七轮下来，"把代码自称的东西拿去实测"这条方法论贡献了 MA-26/27/28/29/30/32/33 共 7 条——**全部是扫描器一条都报不出来的**。

---

## 七、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 用 `auditlib` 原语 | ✅ `all_walk` / `own_walk` / `full_unparse` / `rel_path` |
| **门 2** | 命中多时手工分诊 | ✅ **决定性**：判据 A 47 处 → 逐查均为内部枚举；判据 B 5 处 → 追到方法内部确认内部生成 trace_id ⇒ 全部排除 |
| **门 3** | 每条缺陷有实测 + 同仓对照 | ✅ MA-33 五档版本矩阵实测 + 持久化确认（compose volume）+ 静默确认；正面对照 `adm_caps` |

### 遗留队列（十七轮累积）

| 项 | 状态 |
|---|---|
| **跨仓出站 trace_id 契约** | ✅ 本轮闭合 |
| **配置面三方一致性** | ✅ 十六轮闭合 |
| **Store 共享连接 / 分页 / 重试** | ✅ 十五轮闭合 |
| **MCP 登记表 / 启动期断言** | ✅ 十三轮闭合 |
| **锁覆盖** | ✅ 十二轮闭合 |
| **logging 格式串** | ✅ 十四轮闭合 |
| 常驻周期任务 | ⚠️ 已报 MA-31；`device_feed` 观察项 |
| `candidate_promotion` NaN | ⚠️ 观察项（实测不可达） |
| `redis_host` / `redis_port` | ⚠️ 预留字段，UI 不暴露 |
| `recent_audit` 死代码 | ⚠️ 无调用方 |
| MA-03 端到端 | ⚠️ 未复现（`chromadb` 太重） |
| 47 处静默降级中"否定方向"44 处 | ⚠️ 仅抽查 |
| M2 的 30 处无钳制 `LIMIT` 站点 | ⚠️ 抽查 |
| `fail-closed` 39 处声明 | ⚠️ 已核两支 |
| **判据 A 的 47 处信封字段** | ⚠️ 分诊为内部枚举，未逐一实测 |

### 环境

| 项 | 值 |
|---|---|
| **本轮新增安装** | `bcrypt`、`python-jose`、`pymysql`（lesson 156：应一次装齐） |
| 已装 | 上表 + `starlette`、`httpx`、`tomli` |
| 仍未装 | `chromadb`（体积过大）、`mcp` SDK（模块可导入，仅打印警告） |
| 门禁 | `run_gates.py . --no-smoke` → rc=0（208 条基线） |
| 新增 lessons | **152–156**（已并入 `lessons-round2.md`，共 1652 行） |
| 仓库状态 | 探针已还原；源码、量具、门禁、基线均未修改 |
