# memory-agent 第四轮审计报告：启动性能与数据隐私

> 审计日期：2026-10-01
> 审计触发：用户反馈「MA 启动总要卡很久」，并担心数据库是否会被推上 GitHub
> 审计方式：**沙箱实测** —— 构造 30 天真实规模库（104 MB / 30 万行 events），逐阶段计时

---

## 一、先直接回答你的两个问题

### Q1：启动慢，是数据库太大的锅吗？

**数据库有贡献，但不是主因。**

在你描述的 30 天数据规模下（实测 104 MB / 30 万行），启动耗时账单如下：

| 启动阶段 | 实测耗时 | 是否与数据库大小相关 |
|---|---|---|
| `import mcp`（第三方 MCP SDK） | **≈ 10.7 s** | ❌ 无关 |
| `import mcp_server`（本模块自身） | ≈ 2.5 s | ❌ 无关 |
| `identity_reconciler.reconcile` | ≈ 5.1 s | ❌ 无关（取决于 HA 可达性） |
| `check_and_recover` → `PRAGMA integrity_check` | **≈ 2.1 s** | ✅ **线性相关** |
| `AppRuntime.__init__` 其余装配 | 0.07 s | ❌ 无关 |
| **合计** | **≈ 20.5 s** | |

**结论：数据库只贡献 2.1 秒（约 10%），真正的大头是启动期强制导入 MCP SDK 的 13 秒。**

也就是说——**就算你把数据库清空，启动也要卡十几秒**。

### Q2：GitHub 上不会有我的数据库吧？

**不会。实测确认是安全的。**

| 检查项 | 结果 |
|---|---|
| 仓库内 `.db` / `.sqlite` 文件 | **0 个**（全仓库扫描） |
| `git check-ignore data/memory_agent.db` | **被忽略 ✓** |
| `git check-ignore data/config.json` | **被忽略 ✓**（含明文密钥） |
| `git check-ignore .env` | **被忽略 ✓** |
| `git check-ignore certs/x.pem` | **被忽略 ✓** |

- 数据库默认路径 `/data/memory_agent.db`（`config.py:280`），`docker-compose.yml` 挂载 `./data:/data` → 实际落在宿主机 `<repo>/data/`，**整个 `data/` 目录已被 `.gitignore` 排除**
- `deploy_nas.sh` 用 `git checkout -f origin/main -- .`，只覆盖**被 git 跟踪的文件**，不碰 `data/`、`.env`
- 仓库里唯一的配置文件 `config.example.json` 是脱敏模板，密钥位是 `<HA_LONG_LIVED_TOKEN>`、`<GENERATE_A_32_BYTE_RANDOM_SECRET>` 等占位符，无真实值
- `install.sh` 结尾也明确写着：「`.env` 与 `data/` 均落在 `$INSTALL_DIR` 内，更新代码不会触碰你的配置与数据」

---

## 二、🔴 P0-10　启动期强制导入 MCP SDK，固定付出 13 秒

**位置**：`app.py:43`

```python
from . import api, mcp_server      # ← 模块级导入，服务启动时必付
```

**实测分解**：

```
import mcp                    → 10213 ms（新拉起 544 个模块）
import mcp_server（自身）      →  2500 ms
────────────────────────────────────────
合计                          ≈ 13.2 s
```

`mcp` 包导入链上的重型依赖：`opentelemetry`（23 个模块）、`cryptography`（22 个）、`pydantic`（47 个）、`httpx`、`starlette`。

**为什么无法避免**：`mcp_server.py` 里其实设计了优雅降级——

```python
try:
    from mcp.server import MCPServer
    ...
except Exception as exc:
    MCP_AVAILABLE = False       # 环境没装 mcp 时置 None，WebUI 提示重建镜像
```

**但 `app.py` 是模块级强制导入，这个降级分支在启动路径上永远用不上。** 即：不管你用不用 MCP 功能，这 13 秒都得付。

**已排除的干扰因素**：我做了本地盘 vs 网络盘对照实验（`/data/workspace` 14033 ms vs `/tmp` 13943 ms，几乎相同），确认**瓶颈不是文件系统 IO**，是纯模块导入开销。重复导入也不缓存（两次均约 10.7 s）。

**建议**：
1. 若大多数场景不用 MCP：把 `mcp_server` 改为**延迟导入**（首次访问 `/mcp` 路由时再 import），启动可省约 13 秒
2. 若必须保留：在 Dockerfile 里加 `python -m compileall` 预编译 bytecode，可小幅改善；或接受该成本
3. 最低成本验证：在容器内执行 `python -c "import mcp"` 计时，确认你的机器上是否也是这个量级

---

## 三、🔴 P0-11　`integrity_check` 每次启动全文件校验，随数据线性增长

**位置**：`store.py:535` `check_and_recover()`，被 `startup()` 在**第一步**调用

```python
r = conn.execute("PRAGMA integrity_check").fetchone()   # 无条件执行，无开关
```

`integrity_check` 会**逐页校验整个数据库文件**，耗时与文件体积近似线性：

| events 行数 | DB 体积 | 耗时 |
|---|---|---|
| 0 | 0.4 MB | 1 ms |
| 5 万 | 11.9 MB | 188 ms |
| 15 万 | 35.5 MB | 716 ms |
| 30 万 | 70.8 MB | 1481 ms |
| **30 万（含其余表）** | **104 MB** | **2108 ms** |

**外推**：数据库涨到 500 MB 时，单次启动仅完整性校验就约 **10 秒量级**，且持续增长。

**建议**：
- 改用 `PRAGMA quick_check`（快一个数量级，覆盖绝大多数实际损坏场景），或
- 加配置项（如 `DB_INTEGRITY_CHECK=quick|full|off`），把 full 模式留给手动运维，或
- 仅在异常关闭后（存在 `-wal` 残留）才跑 full check

---

## 四、🟠 P1-12　`identity_reconciler.reconcile` 在启动期 `await` 阻塞

**位置**：`runtime.py` `startup()` 中，`await` 阻塞而非后台任务

**实测**：真实 `AppRuntime` 启动中该步耗时 **5139 ms**。

**关键发现——它与数据库大小无关**：

| 库规模 | events | reconcile 耗时 |
|---|---|---|
| 空库 | 0 | 0 ms |
| 5 万 | 50,000 | 0 ms |
| 15 万 | 150,000 | 0 ms |
| 30 万 | 300,000 | 0 ms |
| 30 天真实库 | 300,000 | 0 ms |

各规模下均为 0 ms。这说明 5.1 秒来自 **`ha_getter` 对 Home Assistant 的网络调用**——我的测试环境 HA 不可达（`127.0.0.1:18123`），走的是连接失败/超时路径。

**对你的实际影响**：如果 HA 地址不通、响应慢或令牌失效，这一步会**卡在网络超时上**，且卡的是启动流程。这解释了为什么"有时快有时慢"。

**建议**：
- 移入后台任务（启动不阻塞），或
- 给 HA 调用加**短超时 + 失败跳过**（当前 HA 客户端单请求 timeout 已配 5~60 s，启动路径应更短）

---

## 五、⚠️ 数据安全：一处值得注意的设计缺陷

数据库不会被推上 GitHub，但部署脚本的**备份逻辑有隐患**：

```bash
# deploy_nas.sh
tar czf "$BACKUP_DIR/$BACKUP_NAME.tar.gz" --exclude='./data' --exclude='./.git' .
```

**名为「部署前备份」，却把 `data/`（也就是你的全部行为数据 + `config.json` 密钥）排除在外。**

后果：一旦在线更新后新版本有问题，这份备份**只能回滚代码，恢复不了数据**。

**建议**：备份改为分别处理——代码备份排除 `data/`，但对数据库单独做 `sqlite3 .backup`（在线安全备份，不需要停服务），并纳入保留 5 份的轮转。

另外两处小瑕疵：
- `.env.example` 的 `DB_PATH=/data/memory_worker.db` 与 `config.py:280` 的 `/data/memory_agent.db` **不一致**（都在 `/data/` 下，不影响安全，但排查问题时容易混淆）
- 若你手动改过 `DB_PATH` 指向 `data/` 之外，或执行过 `git add -f data/`，则上述 gitignore 保护失效——建议 `git status --ignored` 确认一次

---

## 六、修复优先级

| 优先级 | 缺陷 | 动作 | 预估收益 |
|---|---|---|---|
| 🔴 P0-10 | 强制导入 MCP SDK | 改延迟导入 | **启动省 ≈13 s** |
| 🔴 P0-11 | `integrity_check` 全文件校验 | 改 `quick_check` 或加开关 | 省 2.1 s，且随数据增长收益扩大 |
| 🟠 P1-12 | `reconcile` 启动期阻塞 | 后台化 + HA 短超时 | 消除 HA 不通时的启动卡顿 |
| 🟡 | 备份排除 `data/` | 数据库单独 `sqlite3 .backup` | 更新可回滚数据 |

**给你的一句话**：先把 P0-10 和 P0-11 做了，启动时间大约能从 20 秒降到 5 秒以内，而且数据库越大，收益越明显。数据库不是启动慢的主因，但它确实是唯一会**随着时间推移越来越慢**的那一项——`integrity_check` 现在 2.1 秒，明年可能就是 10 秒。

---

## 附：本轮实测脚本（沙箱内保留）

| 脚本 | 用途 |
|---|---|
| `rec_scale2.py` | reconcile 随数据量变化（证明与 DB 无关） |
| `startup_breakdown.py` | 真实 `AppRuntime` 启动逐阶段计时 |
| `scale_curve.py` | integrity_check 随 DB 体积的耗时曲线 |

测试库：`/tmp/ma30.db`（104 MB / 30 天真实规模）、`/tmp/sc_*.db`（规模曲线对照）

> 说明：本轮耗时数据均在沙箱 CPU 上测得，你的机器绝对值会有差异，但**各阶段比例关系与"是否与数据库相关"的结论成立**。建议用 `python -c "import mcp"` 在你的容器内实测一次以确认。
