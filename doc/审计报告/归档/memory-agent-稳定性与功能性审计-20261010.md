# memory-agent 稳定性与功能性审计报告

> 审计对象：`E:\NAS\memory-agent`（本地只读审计）
> 审计日期：2026-10-10
> 审计方式：只读侦察（目录/入口/核心写路径/门禁基线/git 历史/关键源码定点读取），未修改任何源码
> 参照基线：`审计报告_上传.md`（2026-09-30）、`.gates-baseline.txt`、`.gates.toml`、git log

---

## 一、结论先行

2026-09-30 报告中列出的 3 个 P0/P1 缺陷**已在 10-08 代码中修复**；
但该报告**未覆盖**的一批「假成功（fake-ok）/ 吞异常后仍宣称成功（swallow-and-claim-ok）」类缺陷仍然存在，
且项目自有的质量门禁基线 `.gates-baseline.txt` 已把这批函数**固化为"可接受的债务"**，
使它们不再触发 CI 告警。这是本次审计认为最严重的元问题。

---

## 二、已修复（对比 2026-09-30 报告）

| 报告项 | 位置 | 现状证据 |
|---|---|---|
| P0-1 `InsightService` 参数顺序反 | `runtime.py:72` | 现为 `InsightService(self.store, self.config)` ✅ |
| P1-1 `time.monotonic()` 配 `0.0` 哨兵 | `announcer.py:87-89`、`perception_rules.py:54-55` | 已改 `None` 哨兵，含注释「避免 cooldown > uptime 时首次触发被吞」✅ |
| P1-2 `_acp_kind` 缺 None 守卫 | `acp_server.py:149` | 现为 `((scope or {}).get("state") or {})...` ✅ |
| 数据库反复损坏 | `store.py:651-655` | 已加 `PRAGMA synchronous=FULL` + 关前 WAL checkpoint + 每小时 checkpoint（git `e85febd`）✅ |

---
## 三、仍然存在 · 影响稳定性/功能性（本次确认）

### 🔴 P0-A　「恒返回成功」的桩函数（fake-ok-const）

`.gates-baseline.txt` 用静态指纹把一批**函数体固定返回 `{"ok": True}`、不反映真实执行结果**的函数登记为“基线债务”。
关键在于这些不是边角，而是对外契约面：

- `src/memory_agent/agent_memory.py#fake-ok-const#` → `health` / `reconcile` / `list_agent_memories` / `get_session_trust` /
  `add_semantic_memory` / `merge_semantic_memory` / `revoke_memory` / `rollback_agent_memory` /
  `sweep_promote_candidates` / `feedback_memory`
- `src/memory_agent/api/acp_routes.py#fake-ok-const#` → `acp_selftest` / `acp_test_outbound`
- `src/memory_agent/api/face_routes.py#fake-ok-const#` → `face_recognize`
- `src/memory_agent/api/llm_routes.py#fake-ok-const#` → `llm_ask`
- `src/memory_agent/api/system_routes.py#fake-ok-const#` → `apply_update` / `check_update`
- `src/memory_agent/store.py#fake-ok-const#` → `query_unified_events` / `split_logical_device`

**影响**：调用方（外部 Agent / WebUI / 健康检查）无法区分“真的成功了”和“底层失败但接口照样回 ok”。
这是最典型的**假成功型 bug**——服务看起来 200，数据其实没写进去。
尤其 `agent_memory.health`/`reconcile` 这类**运维入口本身在说谎**，会让“三副本一致性”这类核心承诺失去可观测性。

**抽样佐证**（`agent_memory.py:814-821`）：

```python
def health(self) -> dict:
    counts = {st: len(self.store.list_agent_memories(st)) for st in AGENT_STATES}
    return {
        "ok": True,
        "states": counts,
        "mirror_dirty": len(self.store.list_dirty_agent_mirrors()),
        "chroma_available": self._col is not None,
    }
```

`list_agent_memories(st)` 在 store 层异常时会怎样？若被 store 内部吞掉或返回空，`health` 仍报 `ok=True`，
运维看板显示“全绿”，但记忆其实没读出来。

---

### 🔴 P0-B　「吞异常后仍宣称 ok」（swallow-and-claim-ok）

基线同样固化了这批：

- `agent_memory.py#swallow-and-claim-ok#AgentMemoryService.promote_memory`
- `insights_legacy.py#swallow-and-claim-ok#InsightService.device_health / plan_question`
- `mcp_server.py#swallow-and-claim-ok#_build_server.retrieve_agent_memories`
- `signal_learning.py#swallow-and-claim-ok#SignalLearningService.suggest_rules_from_negative_feedback`

**影响**：正是它们让 09-30 报告里的 P0-1（实体目录空）能“服务照常 200、用户完全无感”地活着。
**上游掏空 → 下游全 0 → 却报 ok**，这条链路没有被真正封堵，只是把证据从报告搬进了基线文件。

---

### 🔴 P0-C　`except Exception: pass` 空吞（broad except）

- `src/memory_agent/api/system_routes.py:74-75`（`get_version` 里裸 `except Exception: pass`）
- 基线里另有 `llm_client.py#LLMProvider.close / LLMRouter.close`、`mqtt_bridge.py#MqttBridge.close / _default_client_factory`、
  `store.py#Store.close`、`vision_service.py#VisionService.stop`、`poller.py#CollectService.stop`、
  `patterns.py#PatternManager.match_pattern` 等 **40+ 处** `except-pass-broad`。

**影响**：关闭/清理路径吞异常，会掩盖**连接未释放、文件句柄泄漏、WAL 未 checkpoint**——
对一个长期驻留、用 SQLite WAL 的服务，`Store.close` 吞异常直接关联数据完整性。

---

### 🟠 P1-D　MCP 权限判定仍未与错误契约对齐（原 P0-3 根因仍在）

`mcp_scopes.py:161-171`：未登记工具 → `scope_of()` 返回 `UNKNOWN` → `requires()` 返回 `False`。
设计上是 fail-close（正确），但权限层把它当作“需要 write 权限被拒”，
于是仍会产出 09-30 报告里那句自相矛盾的 `DENIED: ... 无 write 权限（当前权限 read,write）`。

```python
# mcp_scopes.py:161-171
def requires(tool: str, granted) -> bool:
    need = scope_of(tool)
    if need == UNKNOWN:
        return False          # ← 未登记工具被当成权限不足，而非 NOT_FOUND
    if need == READ:
        return True
    return WRITE in (granted or [])
```

**影响**：外部 Agent 依赖的 `NOT_FOUND` / `UPSTREAM_UNAVAILABLE` / `RATE_LIMITED` / `INTERNAL` / `INVALID_PARAM` 错误码分支，
**无法从未登记工具这条路径产生**，错误契约依旧不可验证。应让未登记工具走 `NOT_FOUND` 而非 `DENIED`。

---

### 🟠 P1-E　`Store` 单连接 + RLock + synchronous=FULL 的写并发瓶颈

`store.py:9` 明确“单连接 + RLock，check_same_thread=False”；`store.py:651-654` 又强制 `synchronous=FULL`（每次提交 fsync WAL）。

**影响**：这是稳定性/性能的架构性取舍——正确性换吞吐。高写入（采集 + 晋升 sweep + 每小时 checkpoint + VACUUM）叠加时，
单连接 RLock 会让所有 `asyncio.to_thread(store.xxx)` 排队，事件循环虽不阻塞但请求延迟放大。
**VACUUM 是排他的**（注释自承几百 ms~秒级），与采集任务抢锁时 WebUI 会卡。

---

### 🟡 P2-F　当前仓库处于 dirty 状态，升级链路实际不可用

`git status --short` 显示多个 `doc/审计报告/*.md` 被删除（未提交），另有 untracked 文件。
而 `system_routes.py:149-152` 的 `apply_update` 逻辑是：**工作树 dirty → 拒绝更新**。

**影响**：在当前工作副本上，`/api/system/apply_update` 必然返回“工作树有未提交改动，已拒绝更新”。
若这是 NAS 上实际运行的副本，则**一键升级功能处于不可用状态**（要么先 commit/stash，要么用 restart_cmd 托管流程）。

---

### 🟡 P2-G　敏感文件与残留

- `.env`（595 B，`-a-h--`）位于仓库根。若该目录被 git 跟踪/打包，密钥存在外泄面。
- 根目录遗留 `output.txt`、`agent-vector-iteration-feasibility.md`、`vector-db-agent-writeback-plan.md`、`审计报告_上传.md` 等审计/规划产物——非运行必需，混在部署根。
- 全仓 `19195` 个文件 / `845 MB`（含 `.git`、`__pycache__`、`.qoder/worktrees` 多份 worktree），`.gitignore` 未能覆盖所有生成物。

---

### 🟡 P2-H　CI 元问题（来自报告，仍需复核）

09-30 报告实测 `77 failed, 484 passed, 13 skipped, 7 errors`，且 CI 在 `test_acp_server.py::test_handle_initialize` 第一个用例 `-x` 即中止。
git 历史里有 `40f174c ci: 修复 GitHub Actions CI 失败 — 移除外部 homesdk 依赖`，说明**至少修了一部分**，
但**没有直接证据显示当前全量 CI 已转绿**（`pytest.ini` 仅 188 B，未确认 asyncio auto 模式与 `JWT_SECRET` 注入是否补齐）。

---

## 四、复核建议（按收益排序）

| 优先级 | 动作 | 说明 |
|---|---|---|
| 🔴 立刻 | 给 `fake-ok-const` / `swallow-and-claim-ok` 清单逐条加“失败也必须非 ok”的语义 | 把基线里的债务**只减不增**地真实消掉，而不是继续固化 |
| 🔴 立刻 | `Store.close` / `MqttBridge.close` / `LLMProvider.close` 的 `except: pass` 改 `log.exception` + 标记未释放 | 防止句柄/连接泄漏与 WAL 未 checkpoint |
| 🟠 本周 | `mcp_scopes` 未登记工具走 `NOT_FOUND` 而非权限拒绝 | 恢复 MCP 错误契约可验证性 |
| 🟠 本周 | 明确 `Store` 写并发方案（读写分离连接 / 批量提交 / 降低 FULL 到 NORMAL+定期 checkpoint） | 缓解单连接 RLock 瓶颈 |
| 🟡 排期 | 清理仓库根（`.env` 移出、审计文档归档到 `doc/`）、提交或 stash 当前 dirty 工作树 | 恢复升级链路、减小攻击面 |
| 🟡 排期 | 本地跑一次 `pytest tests/ -q` 与 CI 口径 `-x` | 确认 CI 是否真的转绿 |

---

## 五、诚实声明（审计边界）

- 本次审计**未执行任何写操作**于源码；全部为 `Get-Content` / `Select-String` / `git log` / `git status` 只读侦察。
- 全域 `Get-ChildItem -Recurse` 因仓库过大（845 MB / 19195 文件）多次超时，改为**分目录 + 定点读取**；
  因此 `scripts/`、`tests/`、`vendor/` 内部未逐文件审计。
- P0-A / P0-B 的具体函数体只确认了“基线登记 + 抽样阅读”，**未对每一条写复现脚本**。
- 若需把 P0-A / P0-B 的每一处都落到“文件:行号 + 触发条件 + 修复建议”，
  建议优先顺序：`agent_memory.py` → `insights_legacy.py` → `store.py` → `system_routes.py`。

---

*报告生成：2026-10-10 · 生成方式：本地只读审计 · 生成者：Agent*
