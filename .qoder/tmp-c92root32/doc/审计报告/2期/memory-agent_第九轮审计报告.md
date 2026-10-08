# memory-agent 第九轮审计报告：无界状态增长与限流绕过

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件 + 部署拓扑（`docker-compose.yml` / `Caddyfile`）
> 本轮工作流：**v2.8「跨项目缺陷族迁移」**
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

本轮的新维度来自**另一个仓**。AutoForge 二十轮沉淀的缺陷族里有"**无界状态增长 / 软上限**"一族（软上限只清理不阻止、模块级容器无清理）。拿它对照 memory-agent，得到 **3 条确认缺陷**：

| 编号 | 级别 | 位置 | 一句话 |
|---|---|---|---|
| **MA-20** | 🔴 High | `api/debug_routes.py:58` `_CONV_LOCKS` | 键为请求体提供的 `conversation_id`，**懒创建后永不清除**，同模块另两个容器都有上限 |
| **MA-21** | 🟠 Medium | `auth.py:30-31` `_login_fails` / `_login_locked` | 键永不移除、无全局 prune；实测 **50000 次 → 50250 条**（约 228 B/条） |
| **MA-22** | 🔴 High | `api/auth_routes.py:66-79` `_client_ip` | **无条件信任 XFF**，而服务**直曝 8086 不经代理** ⇒ IP 限流可完全绕过 |

**MA-22 是本轮最需要说的一条**：它单看代码"合理"（注释还写了理由），只有读到 `docker-compose.yml` 才发现前提不成立。

---

## 二、方法：v2.8 的迭代点

| 版本 | 做法 |
|---|---|
| v2.6（七轮） | 解除环境阻塞 + 注释关键词反推 |
| v2.7（八轮） | 门禁自己声明的折算口径 → 新维度 |
| **v2.8（本轮）** | **跨项目缺陷族迁移**：拿 AutoForge 已确认的缺陷族清单当 checklist |

**lesson 111**：本仓新维度枯竭时，**换仓的已确认缺陷族**是最便宜的新维度来源——族的形状与业务无关，迁移成本低。

### 扫描结果（全仓模块级可变容器）

模块级可变容器 **15 处**（`self._lock` 15 处另计）。其中**无任何清理动作**的字典 3 个：

| 容器 | 键空间 | 有上限？ |
|---|---|---|
| `circuit_breaker.py:77` `_BREAKERS` | **固定 2 个**（`embedding`、`llm`） | ✅ 构造上有界 |
| `template_validate.py:169` `_STORES` | **单一配置值** `data_dir` | ✅ 构造上有界 |
| **`api/debug_routes.py:58` `_CONV_LOCKS`** | **请求体提供的 `conversation_id`** | ❌ **无** |

⇒ 3 个里 2 个键空间天然有界，只有 `_CONV_LOCKS` 的键来自外部输入且无清理。

---

## 三、确认缺陷

### 🔴 MA-20　`_CONV_LOCKS`：同模块三个容器，唯独它没有上限

**位置**：`api/debug_routes.py:58`；写入点 `_get_conv_lock`；键来源 `:381` `body.get("conversation_id")`

同一模块里三个容器：

```python
_MAX_RUNS = 200
_MAX_CONV = 200
_RUNS       # _MAX_RUNS 上限 ✅
_CONV       # _MAX_CONV 上限 ✅
_CONV_ORDER # 与 _CONV 同步淘汰 ✅
_CONV_LOCKS # ← 无上限、无清理 ❌
```

`_register` 的淘汰路径（180-186 行）级联清理了 `_CONV` 与 `_CONV_ORDER`，**没有动 `_CONV_LOCKS`**。

**实测**（复刻原逻辑，各 N 个不同 `conversation_id`）：

| N | `_RUNS` | `_CONV` | **`_CONV_LOCKS`** |
|---|---|---|---|
| 500 | 200 ✅ | 200 ✅ | **500** ❌ |
| 2000 | 200 ✅ | 200 ✅ | **2000** ❌ |
| 10000 | 200 ✅ | 200 ✅ | **10000** ❌ |

⇒ **两个有上限的容器精确停在 200，没上限的那个线性同增。**

**可达性**：`POST /api/debug/llm/run`，需 `debug_mode=True` + loopback/内网来源（`app.py:368`）+ dbg_ 令牌。**属调试通道**，故危害限于调试部署，但一旦开启即无回收。

**lesson 112**：扫"无界增长"不能只看"这个模块有没有上限"，要看"**每个**容器是否都被同一条淘汰路径覆盖"。

---

### 🟠 MA-21　`_login_fails` / `_login_locked`：键永不移除，无全局 prune

**位置**：`auth.py:30-31`，`_login_guard` 保护；写入 `note_login_failure`

**对照同文件**：`_revoked_jtis` 有 `_prune_revoked` 全局清理；`_login_fails`/`_login_locked` **只在当前键上清理过期时间戳，键本身永不移除**（`pop` 0 次、`clear()` 0 次）。

**实测（真实代码）**：

| 失败次数 | `_login_fails` 条数 |
|---|---|
| 1000 | 1250 |
| 10000 | 10250 |
| 50000 | **50250** |

**内存量级**：单条约 **228 B**（键 + 时间戳列表），100000 条 ≈ **21.8 MB** 常驻。

**缓解因素（必须如实标注）**：`login_allowed` 先于 `note_login_failure` 检查，单 IP 5 次失败即锁 30 分钟（`_LOGIN_MAX_FAILS=5`、`_LOGIN_LOCK_SECONDS=1800`），故**单 IP 场景下增速受限**。

⇒ 定 Medium。但与 MA-22 叠加后严重性上升（见下）。

---

### 🔴 MA-22　`_client_ip` 无条件信任 XFF，而服务直曝不经代理

**位置**：`api/auth_routes.py:66-79`

```python
"""取客户端 IP（WO-MA-004 ⑤b：取 X-Forwarded-For 最后一个元素）。"""
xff = request.headers.get("x-forwarded-for", "")
if xff:
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if parts:
        return parts[-1]          # ← 假设末尾是可信代理追加的真实 IP
return request.client.host
```

**代码本身没错**——"取最后一个元素"正是标准做法，**前提**是请求必经可信代理。

**但这个前提在默认部署下不成立**。`docker-compose.yml`：

```yaml
memory-agent:
  ports:
    - "192.168.2.200:8086:8000"      # ← 直曝局域网，不经过 caddy
caddy:
  image: caddy:2-alpine
  ports:
    - "192.168.2.200:9080:80"
    - "192.168.2.200:9443:443"
```

Caddy 是**并列的另一个服务**，不是 MA 的唯一入口。`Caddyfile` 的 `reverse_proxy memory-agent:8000` 只覆盖走 9080/9443 的路径。**直连 8086 时没有任何代理**，XFF 末元素 = 攻击者完全可控。

#### 实测对比（真实 `auth` 模块）

```
══ 场景 A：经 Caddy（有可信代理追加真实 IP）══
   第 6 次 → 429 锁定（剩余 1800s）✅ 防护生效

══ 场景 B：直连 8086（无代理）══
   XFF=fake-0.evil → 解析IP=fake-0.evil  allowed=True
   ... （8 次全部 allowed=True）
   8 次失败后 _login_locked 条目: 0  → ❌ 从未锁定

══ 场景 C：场景 B + 轮换用户名（真实代码）══
   20000 次失败 → _login_fails 20007 条
```

⇒ **同一份代码，经代理时第 6 次就锁，直连时 8 次（可无限次）全放行。**

#### 后果（两条叠加）

1. **爆破防护失效**：`POST /api/auth/login` 在 `PUBLIC_PREFIXES`，无需任何鉴权 ⇒ 可无限次尝试密码
2. **MA-21 从 Medium 升为实际可利用**：MA-21 唯一的缓解（单 IP 5 次即锁）被 MA-22 绕过 ⇒ 无界增长变为可持续触发

#### 同仓正面对照（lesson 114）

`app.py:177` 判 dbg_ 令牌可信来源时用的是：

```python
client_ip = scope.get("client", (None, None))[0] if scope.get("client") else None
```

⇒ **TCP 对端，正确且 fail-closed**（`_is_trusted_source` 无法解析 IP 时返回 False）。

**同一个仓里两个 IP 来源，一个对一个错**——这就是最好的证据，不需要外部论据。

#### 修复方向（两选一）

```python
# 方案 A（推荐）：与 app.py 对齐，默认取 TCP 对端
return request.client.host

# 方案 B：仅在显式配置信任代理时才解析 XFF（对齐 AUTOFORGE_TRUST_PROXY 的做法）
if os.getenv("MA_TRUST_PROXY", "").lower() in ("1","true"):
    ...解析 XFF...
return request.client.host
```

**lesson 113**：涉及"信任代理头"的判断，必须读 `docker-compose` / `Caddyfile` / nginx conf，确认服务是否**强制**经过代理。

---

## 四、验证通过（确认无问题）

| 项 | 结论 |
|---|---|
| `circuit_breaker._BREAKERS` | ✅ 键为固定 2 个（`get_breaker` 仅 `embedding`/`llm`） |
| `template_validate._STORES` | ✅ 键为单一 `data_dir` |
| `auth._revoked_jtis` | ✅ 有 `_prune_revoked` 全局清理 —— **同文件内的正确范式** |
| `app.py:177` 的 IP 来源 | ✅ TCP 对端，fail-closed |
| `_is_trusted_source` | ✅ 无法解析 IP 时返回 False |
| `login` 路由顺序 | ✅ 先 `login_allowed`（锁定则 429）再做 `bcrypt.checkpw`（且已卸载到线程，防事件循环冻结） |
| **第六轮 CRITICAL-2 修法铺开度** | ✅ 直接抓 `store._conn` 的反模式**已全部修复**（仅剩注释）；修法注释分布在 12 个文件 |
| `ha_db.py` 的 `_conn` | ✅ 自带 `_lock`（注释标注"稳定性审计缺陷3"） |

**最后一条值得单独说**：第六轮 CRITICAL-2（共享 SQLite 连接须持 Store 锁）的修法**铺开得很彻底**——205 处共享连接访问点，反模式残留为 0。这与本轮 MA-20（同模块三个容器漏一个）形成鲜明对比：**同一个项目，有的族修得很干净，有的族漏一个。**

---

## 五、修复建议

### MA-20（一行）

在 `_register` 的淘汰路径（180-186 行）里，与 `_CONV` / `_CONV_ORDER` 同级联清理：

```python
for old in evicted:
    _CONV.pop(old, None)
    _CONV_ORDER.pop(old, None)
    _CONV_LOCKS.pop(old, None)      # ← 补这一行
```

### MA-21（对齐同文件 `_revoked_jtis`）

在 `note_login_failure` 里加一次全局 prune（与 `_prune_revoked` 同形状），移除所有过期键。阈值常量已齐（`_LOGIN_WINDOW_SECONDS=300`、`_LOGIN_LOCK_SECONDS=1800`）。

### MA-22（对齐同仓 `app.py`）

见上节方案 A/B。**这一条优先级最高**——它是唯一能让另外两条变得可持续触发的。

### 建议加门禁

> `check_unbounded_module_state.py`：断言模块级可变容器的键必须来自**固定集合或配置值**；
> 若键来自请求参数/请求体，则同模块必须存在该容器的清理或上限。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | 10000 个不同 `conversation_id` | `_CONV_LOCKS` ≤ `_MAX_CONV`（200） |
| 2 | 50000 次轮换用户名失败登录 | `_login_fails` 有界（不线性同增） |
| 3 | **不带 XFF** 连续失败登录 | 第 6 次起 429 |
| 4 | **带伪造 XFF** 连续失败登录 | 同样第 6 次起 429（IP 取 TCP 对端） |
| 5 | `X-Forwarded-For: 9.9.9.9` 经 Caddy | 仍解析到真实 IP（若保留方案 B 则配 `MA_TRUST_PROXY`） |

---

## 六、横向观察

### "防护做对了，但只做在到达的那个地方"——第九次

| 轮 | 族 | 做对的地方 | 漏的地方 |
|---|---|---|---|
| 三 | LIMIT 钳制 | API 侧 | MCP 侧 |
| 四 | `clamp_days` | days 族 | hours/minutes 族 |
| 七 | 原子写 | 5 处 | `af_metrics.json` |
| 八 | 配置边界 | `/api/collect/config` | `/api/config` |
| **九** | **IP 来源** | **`app.py:177`（TCP）** | **`auth_routes.py:73`（XFF）** |
| **九** | **无界清理** | **`_revoked_jtis`（有 prune）** | **`_login_fails`（无 prune）** |

本轮一次性贡献了两个实例，且两个都**自带同仓正面对照**。

### 但也要说：第六轮 CRITICAL-2 修得很干净

205 处共享连接访问点，反模式残留 **0**。说明**项目有能力把一个族修透**，问题在于"哪些族被选中修透"——CRITICAL-2 因为是 CRITICAL 而得到了彻底铺开，而 MA-20/MA-21 这类无界增长从未被单独立项。

⇒ 建议把"无界状态增长"提升为与 CRITICAL-2 同级的族，做一次全仓普查，而不是等它变成 CRITICAL。

### 与 AutoForge 的对照

AutoForge 第四轮 R4-02（熔断可并发绕过，上限 5 实测落盘 18-25）、第七轮 `_MAX_KEYS` 软上限（只清理不阻止）——与本轮 MA-20/MA-21 **是同一族**：**容器有名义上限，但清理路径没覆盖到所有写入点，或只清理不阻止**。

差别：AutoForge 那边是"清理跟不上新增"（软上限），memory-agent 这边是"**清理路径压根没覆盖这个容器**"（MA-20）和"**键永不移除**"（MA-21）。后者更彻底一点。

---

## 七、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 用 `auditlib` 原语 | ✅ 本轮扫描用 `all_walk` / `rel_path` |
| **门 2** | 命中多时手工分诊 | ✅ 15 处模块级字典 → 按"键空间是否有界"降到 1 处真无界 |
| **门 3** | 每条缺陷有实测 PoC + 同仓对照 | ✅ MA-20 有 `_RUNS`/`_CONV` 对照；MA-21 有 `_revoked_jtis` 对照 + 内存量级；MA-22 有**经代理/直连**双场景对照 + `app.py:177` 正面对照 |

### 遗留队列（九轮累积）

| 项 | 状态 |
|---|---|
| MA-03 端到端 | ⚠️ 未复现（`chromadb` 太重） |
| 47 处静默降级中"否定方向"44 处 | ⚠️ 仅抽查 |
| M2 的 34 处未钳制 `LIMIT` 站点 | ⚠️ 抽查，未逐一确认 |
| `save_skill` 并发 | ⚠️ 观察项，未复现交叠 |
| 24 个数值配置键中其余 16 个 | ⚠️ 未逐一找消费点 |
| `self._lock` 15 处 | ⚠️ 未逐一核查锁覆盖范围（本轮只看模块级容器） |

### 环境

| 项 | 值 |
|---|---|
| 本轮新增安装 | `python-jose`（闭合 `auth.py` 遗留项，使真实代码可实测） |
| 已装 | `bcrypt 5.0.0`、`tomli` |
| 仍未装 | `chromadb`（体积过大）、`starlette` |
| 门禁 | `run_gates.py . --no-smoke` → rc=0（208 条基线） |
| 新增 lessons | **111–115**（已并入 `lessons-round2.md`，共 1275 行） |
| 仓库状态 | 探针已还原；源码、门禁、基线均未修改 |
