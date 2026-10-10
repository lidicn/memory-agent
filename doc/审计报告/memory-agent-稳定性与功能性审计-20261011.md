# memory-agent 稳定性与功能性审计报告

> 审计对象：`E:\NAS\memory-agent`（本地只读审计）
> 审计日期：2026-10-11
> 审计方式：只读侦察（124 个 `.py` / 58,124 行，deploy/脚本/CI 配置，全量 pytest）+ 独立复现脚本
> 参照基线：`doc/审计报告/归档/memory-agent-稳定性与功能性审计-20261010.md`、`.gates.toml`、`.gates-baseline.txt`、`README.md`、git log（HEAD `5bb9d30`）
> 审计边界与不可信来源：见 §十一

---

## 一、结论先行

**测试基线：`18 failed, 2163 passed, 27 skipped in 864s`。CI 显示全绿，真实原因是 `ci.yml:41 --ignore=tests/test_quality_gates.py` + `ci.yml:48 GATES_DISABLE: "1"` 把门禁测试跳掉了——跳过不等于通过。**

本次确认 **5 个严重、14 个高、10 个中、14 个低**。与 2026-10-10 报告相比，**无一项历史 P0 被重复报出**（P0-A/B/C 函数清单已按 `.gates.toml` 逐条核对）。

**五条最该先修的事：**

1. **B-05（提权链）** 任一普通登录用户 → `POST /api/mcp/tokens/scopes {"scopes":["read","admin"]}`（`mcp_routes.py:155` 只 `require_user`、无属主校验、`normalize` 明确放行 `admin`）→ 再用 `list_agent_memories(state="all", member_id=<任意已知id>)` 读到**已 revoke 的记忆原文**。守卫只判 `state == "revoked"` 一个字符串，而 docstring 自己声明 `all` 是合法值。两步都是已登录用户可达。
2. **B-01** `vision_service.record_face_event` 从 worker 线程派发 `asyncio.create_task` → 抛 `RuntimeError`，房间槽位永久占用。**已用独立脚本在 CPython 3.13.2 复现**：首次抛异常，之后该房间**每一条**人脸事件都被去重拒绝，**需重启进程才恢复**。README 承诺的「TV/手机 ArcFace → 高置信覆盖外观匹配」整条上报链路是死的。
3. **B-02** README 明列"兼容红线"的 Node-RED 端点 `POST /api/analyze/water_purifier`：服务端返回 `data:[{total_volume_l,...}]`，仓内提交的 flow 读 `data.daily` / `day.total_l` / `data.entity_id`——**全部字段不存在**，`for (const day of data.daily)` 抛 `TypeError`，报告永不渲染。
4. **B-03** 数据库 quarantine 路径在 Windows 上**必然失败**（rename 前未 close），失败后**谎报"已隔离并重建空库"**、真实错误被吞，而它覆盖的场景正是"integrity_check 判红 + 无备份"——即最需要可信反馈的那一刻。测试已红。
5. **B-04** 门禁被 CI 双层绕过：本地跑 `test_quality_gates.py` 报 **5 条新增违规 + 3 条已修未删基线**。防线存在但被摘除，等于没有防线。

**另外两条独立的成组缺陷：**

- **授权粒度不足（C-01~C-05）**：全仓**未发现任何未认证可达的写端点**（Basic Auth / Cookie / `agent-token` 别名 / `/api/nr/*` 四条兼容红线全部在位），但**多个管理面端点用了 `require_user` 而非 `require_admin`**，任一普通登录用户可自签长期令牌、撤销他人令牌、改写他人成员档案。
- **MCP 传输层与资源边界（C-11~C-14）**：非 HTTP scope 完全绕过 token 鉴权、请求体上限只声明未执行、`_no_buffer_send` 会掐死已建立的 SSE 流、ACP 会话淘汰泄漏 `_CONV` 历史。

**隔离红线（MCP token ↔ WebUI JWT 互用）经复核：成立。** `/mcp`、`/acp` 在 `PUBLIC_PREFIXES` 中由 AuthMiddleware 放行后改走独立 Token 中间件；JWT 不进入 `agent_tokens` 库，反向也不成立（`app.py:389-397` 只对 `kind=="debug"` 放行）；token 校验用 `secrets.compare_digest`（`mcp_tokens.py:248,253`），日志只打令牌**名**；kind 隔离双向有效（MCP 侧 `("mcp","debug")`，ACP 侧 `("acp","arena")`）。**但这条红线不保护 MCP 面内部的 scope 语义** —— B-05 就在内部。

---

## 二、严重（阻断性 / 数据恢复能力丧失 / 提权）

### B-01　`record_face_event` 从 worker 线程派发任务，房间槽位永久占用，TV 人脸上报全线失效

- **位置**：`src/memory_agent/vision_service.py:1219-1237`，调用点 `src/memory_agent/api/vision_routes.py:188-191`
- **问题**：`record_face_event` 内部用 `task_registry.create(asyncio.to_thread(...))`（内部是 `asyncio.create_task`），但它唯一的调用点已把它包在 `await asyncio.to_thread(rt.vision.record_face_event, ...)` 里——即**运行在工作线程，那里没有 running event loop**。
- **触发条件**：部署后第一条 `POST /api/events/face`（`persons` 非空）→ ① `1224` `self._vlm_inflight.add(room)` 先执行；② `1225` `task_registry.create` → `asyncio.create_task` 抛 `RuntimeError: no running event loop`；③ done_callback 永不执行，`_vlm_inflight` **永久保留该房间**；④ 之后该房间每条事件命中 `1219` 去重分支被拒。
- **证据**：
```python
# vision_service.py:1224-1237
self._vlm_inflight.add(room)                    # ← 先占槽
task = task_registry.create(                    # ← 后派发（在工作线程里会抛）
    asyncio.to_thread(
        self.analyze_room, room,
        trigger=trigger or "identity_change", ...),
    name=f"vision.analyze_room.{room}",
)
def _clear_inflight(_task, _room=room):         # ← 永不执行
    self._vlm_inflight.discard(_room)
task.add_done_callback(_clear_inflight)
```
- **独立复现**（`/tmp/v01repro/repro2.py`，CPython 3.13.2，与容器同版本）：
```
第1次: ('RAISED', 'RuntimeError: no running event loop')
第2次: ('OK', {'deduped': True, 'vlm_dispatched': False, 'reason': 'living_room 上一次识别仍在执行'})
第3次: ('OK', {'deduped': True, ...})
_vlm_inflight: {'living_room'}
```
- **建议修法**：槽位占用与派发都收进事件循环侧——路由内先占位再 `task_registry.create(asyncio.to_thread(...))`，`record_face_event` 只做数据准备不派发；或用 `asyncio.run_coroutine_threadsafe` 投递回主 loop。
- **违背 README 承诺**：是。README:50「接入 TV / 手机 ArcFace 节点后，高置信生物识别覆盖外观匹配（节点池可插拔、失败自动降级 VLM）」；README:149「节点 = 一台运行 ArcFace 识别的 TV / 手机」——TV 这条上报主链路是死的，且是**永久**故障。
- **为何未被测试发现**：`tests/test_vma_r5_event_loop_offload.py` 直接同步调用 `svc.record_face_event(...)`，测试进程里恰好有 running loop，掩盖了真实调用方式。

---

### B-02　Node-RED 契约端点与仓内 flow 字段全不匹配，报告永不渲染

- **位置**：`src/memory_agent/api/nr_routes.py:189-207` ↔ `nodered/water_purifier_flow.json:84`
- **问题**：服务端返回 `{"ok":true,"message":"分析完成","data":[{"date","count","total_volume_l","avg_tds_in","avg_tds_out","tds_reduction_pct"}]}`；flow 的 `parse_result` 读 `data.daily` / `data.entity_id` / `data.start_time` / `data.total_records` / `data.days` / `day.total_l`——**一个都不存在**。
- **触发条件**：NR 触发"查询最近7天" → `data.daily` 为 `undefined` → `for (const day of data.daily)` 抛 `TypeError`。
- **证据**：
```js
// water_purifier_flow.json:84
result += '记录: ' + data.total_records + '条 / ' + data.days + '天\n\n';
for (const day of data.daily) {      // data.daily === undefined → TypeError
    totalWater += day.total_l;       // 服务端字段是 total_volume_l
```
```python
# nr_routes.py:207
return ok({"message": "分析完成", "data": result})   # result 是 list，不是 {"daily": [...]}
```
`nr_routes.py:134` 自述"响应结构被 Node-RED 流直接消费，不可改"，而 `nr_routes.py:5-11` 自述这份 JSON 就是"线上运行流"——两者自相矛盾，当前提交版本二者都对不上。
- **建议修法**：以服务端返回结构为准重写 `parse_result`（读 `data.data`、字段 `total_volume_l`）；加一条"用仓内 flow 字段表回放真实响应"的契约测试，把这个唯一的外发契约端点锁住。

---

### B-03　`check_and_recover` quarantine 前未关连接，Windows 上必败；失败后谎报"已隔离"

- **位置**：`src/memory_agent/store.py:738, 824-830`
- **问题**：`_quarantine_corrupt_db` 用 `self._conn = None` 只丢 Python 引用、**不 close**，SQLite 的 OS 文件句柄仍持有 → `os.rename` 在 Windows 抛 `[WinError 32] 另一个程序正在使用此文件`；而 `check_and_recover` **不检查**返回值，无条件把 `result["error"]` 改写成"已隔离并重建空库"。
- **触发条件**：`integrity_check` 判红 + 无备份 → rename 失败 → 返回 `{"recovered": false, "quarantined": None, "error": "No backup found; corrupted db quarantined, empty db created"}`。运维看到"已隔离"，实际损坏库原地未动，真实错误被丢。
- **证据**：
```python
# store.py:738  —— 只丢引用，不 close
self._conn = None  # 断开现有连接
if os.path.exists(self.db_path):
    os.rename(self.db_path, quarantined)      # ← Windows: WinError 32
...
# store.py:824-830  —— 不检查返回值，无条件改写 error
result["quarantined"] = self._quarantine_corrupt_db()
result["recovered"] = False
result["error"] = "No backup found; corrupted db quarantined, empty db created"
```
**同一函数里的正确写法就是对照**：恢复路径 855-857 行先 `conn = self.connect(); conn.close(); self._conn = None` 再 `shutil.copy2`。
- **测试已直接暴露**（18 个失败之一）：
```
FAILED tests/test_db_integrity_check_mode.py::test_both_red_without_backup_leaves_db_alone
E       assert 'No backup fo...ty db created' == 'integrity_ch...: still wrong'
ERROR    memory_agent.store:store.py:826 No backup found, quarantining corrupted db and recreating empty
ERROR    memory_agent.store:store.py:753 quarantine failed: [WinError 32] 另一个程序正在使用此文件
```
- **建议修法**：`_quarantine_corrupt_db` 里先 `close()` 再置 `None`；`check_and_recover` 按返回值分流——返回 `None` 时保留原始 `error` 并加 `"quarantine_failed": true`，绝不宣称已隔离。

---

### B-04　门禁被 CI 双层绕过：本地跑门禁测试是**失败**的，CI 却绿

- **位置**：`.github/workflows/ci.yml:39-42,48`；`gates.sh:8`；`Dockerfile:9-16`
- **问题**：AST 门禁在 CI 里根本不跑，而它对应的配置文件和基线是孤儿文件，看上去权威、实际无人消费。
- **证据**：
```yaml
# ci.yml:41  显式跳过门禁测试
        --ignore=tests/test_quality_gates.py \
# ci.yml:48  就算去掉 --ignore，这一行也会 skip 掉它
      GATES_DISABLE: "1"
```
```bash
# gates.sh:8  用法：docker exec memory-agent bash /app/gates.sh
```
`Dockerfile` 只 `COPY src/ pyproject.toml vendor/`——`gates.sh` 从未进镜像；`./src:/app/src:ro` 挂载也不含它 → 该命令必然 `No such file`。`gates-baseline` 在正式脚本里**零命中**，`.gates-baseline.txt`（12.5 KB / 169 条指纹）只被 `.qoder/tmp-c*.sh` 临时脚本引用。
- **本地实测**（18 个失败中的 2 个）：
```
FAILED tests/test_quality_gates.py::test_no_new_gate_violations
  新增 5 条门禁违规（error 3 / warn 2）：
  WARN  except-pass-broad       __init__.py:23                _resolve_version  咽下 Exception 且不做处理
  ERROR fake-ok-const           agent_memory.py:775            match_recipe     字面量 ok=True，不来自任何实际校验
  ERROR except-pass-broad       api/system_routes.py:110      apply_update     关键模块咽下 Exception
  ERROR swallow-and-claim-ok    api/system_routes.py:159      apply_update     既咽下异常又返回 ok=True —— 谎报成功
  WARN  except-pass-broad       history.py:464               _close_chroma_client 咽下 Exception 且不做处理

FAILED tests/test_quality_gates.py::test_baseline_shrinks_when_you_fix_things
  3 条基线条目已不再命中，请删掉：
  api/system_routes.py#fake-ok-const#apply_update
  app.py#fake-ok-const#liveness
  store.py#except-pass-broad#Store.close
```
`GATES_REQUIRE` 在全仓从未置 1，`test_quality_gates.py` 里"必须真跑"的护栏是关的。存量基线 169 条，全量统计 `fake-ok-const 165 / except-pass-broad 31 / swallow-and-claim-ok 8`。
- **建议修法**：`ci.yml` 去掉 `--ignore` 与 `GATES_DISABLE` 二者之一，把 `.gates.toml` 的 `critical_globs` 落成 CI 可执行规则；或删掉根目录那两个基线文件以免制造"已有门禁"的假象。先删 3 条已失效基线、消掉 5 条新增违规。

---

### B-05　提权链：任一普通登录用户可自我授权 `admin` scope 并读到已 revoke 的记忆

**两条独立缺陷合成一条完整提权链，两步都只需 `require_user`。**

#### 第 1 步：`POST /api/mcp/tokens/scopes` 无属主校验，可自我授权 `admin`

- **位置**：`src/memory_agent/api/mcp_routes.py:153-168`；`src/memory_agent/mcp_tokens.py:171-182`；`src/memory_agent/mcp_scopes.py:22,174-185`
- **证据**：
```python
# mcp_routes.py:153-168
async def update_token_scopes(request: Request):
    """调整令牌权限（v0.7.5：read / read+write）。"""
    _, err = require_user(request)          # ← 非 require_admin，且 docstring 只提 read/read+write
    ...
    if not runtime(request).tokens.update_scopes(name, scopes):
        return error("Token 不存在", 404)   # ← 只查存在性，不查属主
```
```python
# mcp_tokens.py:171-182  直接落盘，无二次校验
rec = {**rec, "scopes": normalize(scopes)}
```
```python
# mcp_scopes.py:22, 183  normalize 明确放行 admin
ALL_SCOPES = (READ, WRITE, ADMIN)
if s in ALL_SCOPES and s not in out:
    out.append(s)
```
- **链条已核实完整**：`create_token`（`mcp_routes.py:105-109`）与 `list_tokens`（`:98-102`）**同样只需 `require_user`**，所以普通用户能自己建令牌、自己改 scope。令牌记录里也没有 `owner_username` 字段，无从校验属主。
- **前置条件**：登录 WebUI（普通用户）→ `POST /api/mcp/tokens {"name":"x"}` → `POST /api/mcp/tokens/scopes {"name":"x","scopes":["read","admin"]}` → 中间件 `mcp_auth.py:113-123` 把 `["read","admin"]` 写进 `_caller_context`。

#### 第 2 步：`list_agent_memories(state="all")` 绕过 revoked 守卫

- **位置**：`src/memory_agent/mcp_server.py:2159-2201`；下游 `src/memory_agent/store.py:4936-4955`、`src/memory_agent/agent_memory.py:513-518`
- **问题**：守卫只判 `state == "revoked"` **这一个字符串**，而 docstring 自己声明 `state: staging|live|revoked|pending_review|all`——`all` 是文档合法值，直穿守卫。
- **证据**：
```python
# mcp_server.py:2159-2170  docstring 声明 all 合法
async def list_agent_memories(state: str = "live", member_id: str = "") -> dict:
    """列出 agent 记忆（WO-MA-005 隐私面收窄）。
    state: staging|live|revoked|pending_review|all，默认 live（revoked 永不经 MCP 返回）。
```
```python
# mcp_server.py:2194-2201  守卫只拦精确值 "revoked"
        # revoked 永不经 MCP 面返回（即使 admin 也只能经专门审计通道）
        if state == "revoked":                      # ← 只拦精确值
            _tok, _scopes, _origin = _caller_context()
            if "admin" not in (_scopes or []):
                return {"ok": False, "error": "revoked 记忆仅 admin 审计通道可访问", "code": 403}
        return await asyncio.to_thread(
            rt.agent_memory.list_agent_memories, state, "", member_id   # ← state="all" 直穿
```
```python
# store.py:4943-4945  state="all" 时不加 state 过滤
if state != "all":
    where_parts.append("state=?")
    params.append(state)
```
- **`agent_memory.list_agent_memories`（`:513-518`）把 `state` 直传 store、不二次过滤**，且返回行带 `state` 字段（`:534`）——revoked 行会被原样返回。
- **对比：`retrieve_agent_memories` 不受影响**，下游 FTS 路硬编码 `state="live"`（`store.py:616`），Chroma where 也是 `{"state":"live"}`（`agent_memory.py`）。**所以两条读路径对 revoked 的口径不一致**，只有 `list` 这一条漏了。
- **触发条件**：`tools/call list_agent_memories {"state":"all","member_id":"<任意已知id>"}`（`member_id` 非空时普通 read 令牌即可，无需 admin；`list_members` 就能拿到 id）。
- **合成效果**：非管理员 → read+admin → **读任何成员名下已被 revoke（删除）的记忆原文**。
- **是否破坏隔离红线**：否（未跨越 JWT/MCP 边界），但**废掉 `mcp_scopes.ADMIN` 的设计前提**（"admin 才能读含 revoked 的全量"），注释 `revoked 永不经 MCP 面返回` 直接变成假的。
- **建议修法**：守卫改 `if state in ("revoked", "all")`；更稳的是改白名单 `allowed = ("live","staging","pending_review")`，admin 另加 `revoked`；同时把 `update_token_scopes` 收紧到 `require_admin`（或令牌记录加 `owner_username` 并校验属主）。

---

## 三、高

### C-01　`GET /api/users` 只需 `require_user`，任一登录用户可枚举全部账号（含管理员）

- **位置**：`src/memory_agent/api/auth_routes.py:155-159`
- **问题**：`list_users` 只校验登录，但返回**所有账号**的 `username / is_admin / created_at`。同文件 `delete_user`（162 行）正确使用了 `require_admin`，说明作者知道这个面该收紧。管理员用户名的存在与创建时间属管理元数据，泄漏后可用于定向爆破与社工。
- **证据**：
```python
async def list_users(request: Request):
    _, err = require_user(request)          # ← 应为 require_admin
    if err:
        return err
    return ok({"users": runtime(request).auth.list_users()})
```
- **建议修法**：改为 `require_admin`。

---

### C-02　`POST /api/config/app-tokens` 只需 `require_user`，普通用户可自签长期令牌

- **位置**：`src/memory_agent/api/config_routes.py:471-476`
- **问题**：app-tokens 是 App 生态的签发面，任何非 admin JWT 都能自签令牌长期外传，拿到 `/api/insights/query`、`/api/agent/memories`（`APP_ENDPOINTS` 白名单）的持久化访问。
- **证据**：
```python
async def create_app_token(request: Request):
    _, err = require_user(request)          # ← 同文件 create_service_token(507) 用的是 require_admin
    if err:
        return err
    body = await json_body(request)
    name = (body.get("name") or "").strip()
    ...
    res = get_app_token_store().generate(name, source)
```
- **建议修法**：改为 `require_admin`。

---

### C-03　App 令牌撤销也只需 `require_user`，可吊销他人/外部集成令牌

- **位置**：`src/memory_agent/api/config_routes.py:484-491`
- **证据**：
```python
async def revoke_app_token(request: Request):
    _, err = require_user(request)          # ← 破坏性操作
    ...
    name = request.path_params.get("name", "")
    if not get_app_token_store().revoke(name):
```
- **建议修法**：改为 `require_admin`；或按令牌 `source` 做归属校验。

---

### C-04　MCP/ACP/Debug 令牌撤销无所有权校验，任一登录用户可吊销他人令牌（含 admin 与 `dbg_`）

- **位置**：`src/memory_agent/api/mcp_routes.py:139-149`；store 层 `src/memory_agent/mcp_tokens.py:184-193`
- **问题**：`revoke_token` 只需 `require_user`，按 `name` 全局查找删除，**不校验调用者与令牌的归属关系**，也不校验 `kind`——可吊销 admin 自己的 MCP/ACP 令牌，甚至调试令牌。旧别名 `POST/DELETE /api/config/agent-token`（`mcp_routes.py:322-323`，README 兼容红线端点）委托同一实现，同样暴露。
- **证据**：
```python
# mcp_routes.py:139-149
async def revoke_token(request: Request):
    _, err = require_user(request)
    ...
    name = body.get("name") or request.query_params.get("name") or ""
    if not runtime(request).tokens.revoke(name):
        return error("Token 不存在", 404)
```
```python
# mcp_tokens.py:184-193  仅检查存在即删，无归属/无 kind 校验
def revoke(self, name: str) -> bool:
    with self._lock:
        tokens = dict(self.config.agent_tokens or {})
        if name not in tokens:
            return False
        del tokens[name]
        self.config.agent_tokens = tokens
        self.config.save()
```
- **建议修法**：`revoke_token` 收紧到 `require_admin`（与 `create_token` 对 `kind=debug` 的收紧思路一致，见 `mcp_tokens.py:116-122`）。

---

### C-05　成员档案无归属校验：改写/删除端点只 `require_user`

- **位置**：`PUT /api/members/{id}` `member_routes.py:74-89`；`DELETE /api/members/{id}` `:121-127`；`DELETE /api/members/{id}/tags/{tag}` `:205-212`
- **问题**：`store.update_member` / `delete_member` / `delete_member_tag` 均不做归属校验，路由层也只 `require_user`。任一普通登录用户可改写/清空**其他成员**（含管理员）的 `profile_json`、`name`、`appearance_json`，或直接删除成员实体。同一模块 `GET /api/members/{id}` 走中间件 + handler 无校验，butler 令牌也可读到完整 `profile_json`（仅 `face_feature` 被 strip）。
- **证据**：
```python
# member_routes.py:121-127
async def member_delete(request: Request):
    _, err = require_user(request)          # ← 破坏性操作，应 require_admin
    if err:
        return err
    member_id = request.path_params.get("member_id", "")
    await asyncio.to_thread(runtime(request).store.delete_member, member_id)  # 返回值被丢弃
    return ok({"message": "成员已删除"})     # 不存在也返回 200
```
- **建议修法**：删除类改 `require_admin`；改写类至少拒绝"修改自己的 admin 位"，或统一收紧到 admin。

---

### C-06　`change_password` / `register` 在事件循环里跑同步 `bcrypt.hashpw`（登录已卸载，改密/注册没有）

- **位置**：`src/memory_agent/api/auth_routes.py:149`（change_password）、`:54`（register）、对照 `:96,101,124`
- **问题**：`rt.auth.change_password(...)` 内部走 `bcrypt.hashpw`（百毫秒级）外加 `_load_users`/`_save_users` 两次磁盘 I/O，但 handler **没有 `asyncio.to_thread`**；同文件的 `login`（96）、`get_user`（101）、`has_users`（124）**都已** `to_thread`。`/api/auth/register` 在 `PUBLIC_PREFIXES`（未认证可达），每个请求都在事件循环上钉住 ~300ms。
- **证据**：
```python
# auth_routes.py:149  change_password —— 同步，未卸载
result = rt.auth.change_password(user["username"], old_password, new_password)
# auth_routes.py:96    login —— 已卸载
result = await asyncio.to_thread(rt.auth.login, username, password)
```
- **建议修法**：`await asyncio.to_thread(rt.auth.change_password, ...)`；register 同理（若需保持 `has_users + register` 原子性，封成一个原子方法整体进线程），并给 register 补速率限制（login 有爆破防护，register 没有）。

---

### C-07　`_init_background` 无异常处理，中途失败 = 服务已就绪但 7 个常驻任务永不注册

- **位置**：`src/memory_agent/runtime.py:177-317`、`:218-220`；`task_registry.py:45-51`
- **问题**：`_init_background` **完全没有 `try/except`**（全段扫描零命中），而 `_started = True` 与 `set_state("ready")` 在 218-220 行，其后 221-316 行注册 12 个周期任务。220 行之后任意一步抛异常（`researcher.start()`、`get_promoter`、`CausalScanner`/`Announcer` 构造、`LivingRoomAIIngest` 初始化等），整个协程退出，而 `task_registry._on_done` 只 `logger.warning` **不 re-raise**——服务已标记 ready 且 Uvicorn 正在接流量，但 device_feed / livingroom_ai / candidate_promotion / causal_scan / researcher / self_diary / wal_checkpoint 这 7 个任务从未注册，且无重试、无告警。
- **证据**：
```python
# runtime.py:218-220  状态先置 ready
self._started = True
set_state("ready")
# runtime.py:221-316  之后 12 个任务注册，无任何保护
self._device_feed_task = task_registry.create(
    self._periodic_device_feed(), name="runtime.device_feed")   # :265
...
self._wal_checkpoint_task = task_registry.create(               # :314
    self._periodic_wal_checkpoint(), name="runtime.wal_checkpoint")
```
```python
# task_registry.py:49-51  异常只留 warning，不传播
exc = task.exception()
if exc is not None:
    logger.warning("[Tasks] 后台任务异常退出: %s: %r", label, exc)
```
- **建议修法**：`_init_background` 包 `try/except`，失败写入区别于 `ready` 的状态让 `/health` 与关键路由可识别"半就绪"；或把置位挪到所有注册之后。

---

### C-08　无 readiness 门控：DB 检查在后台跑时 `/api/*` 已对外接单

- **位置**：`runtime.py:169-175`、`app.py:502-503`、`api/deps.py`
- **问题**：`startup()` 置 `set_state("db_check")` 后立即返回 lifespan，DB 检查/恢复/schema 初始化全在后台。`startup_health.health_dict()` **只被 `/health` 引用**，`/api/*` 与 `/mcp/*` 没有任何一层前置门控。
- **触发条件**：Uvicorn 接流量到 `_init_background` 完成的 100ms~5000ms 窗口内，`store.*` 调用与 `shutil.copy2(latest_bak, db_path)`（`store.py:859`）或 `os.rename(db_path, quarantined)`（`store.py:740`）撞车 → SQLite 文件被替换，最坏返回 `database disk image is malformed` 或读到空库数据。`Store.connect()` 的 `_closed` 检查也救不了（恢复期间未置位）。
- **证据**：
```python
# runtime.py:169-175
async def startup(self) -> None:
    if self._started:
        return
    from .startup_health import set_state
    set_state("db_check")
    print("[Runtime] 启动中（DB 检查后台进行，health 已就绪）…")
    # DCD 20261010 Q3: DB 检查挪后台，lifespan 立即返回，Uvicorn 先接请求
    self._init_task = task_registry.create(self._init_background(), name="runtime.init")
```
```python
# app.py:502-503  唯一的消费点，且只有 /health
from .startup_health import health_dict
return JSONResponse(health_dict())
```
`deps.py` 里 `state|ready|health` 只命中 `request.state.user`，无 readiness 检查。
- **建议修法**：`deps.runtime()` 或中间件加 `if get_state() != "ready": return 503`；或 lifespan `await _init_task` 再返回。

---

### C-09　备份先删成品再 `VACUUM INTO`，失败即永久丢失当日恢复点；chroma 失败不置 `ok=False`

- **位置**：`src/memory_agent/backup.py:57-80`（SQLite）、`:84-102`（chroma）
- **问题 1**：`VACUUM INTO` 不允许目标已存在，代码在导出**之前**先 `os.remove(dest)`。`VACUUM INTO` 失败（磁盘满 / 长事务 busy timeout / 路径非法字符被守卫抛 `ValueError`）时当日备份已删；`ok=False` 使旧备份不轮转，但当日那份已经没了。
- **问题 2**：chroma 分支的 `except` 只写 `results["chroma_error"]`，**没有**像 db 分支那样置 `ok=False` → `_rotate()` 照常删旧 chroma 快照（磁盘清理 + 无新备份 = 恢复点丢失）。且 `shutil.copytree` 非原子，中途失败留下半拷贝。
- **证据**：
```python
# backup.py:57-80
if os.path.exists(dest):
    os.remove(dest)                    # ← 先删成品
conn = sqlite3.connect(db_path, timeout=30.0)
conn.execute("VACUUM INTO ?", (dest,)) # ← 失败则 dest 已消失
...
# backup.py:84-102
except Exception as exc:
    results["chroma_error"] = f"{type(exc).__name__}: {exc}"   # ← ok 未置 False
if results.get("ok"):                                     # ok 仍是 True
    results["retained"] = self._rotate(backup_dir, retention)   # ← 轮换继续
```
- **建议修法**：一律"写 tmp + `os.replace`"原子覆盖；chroma `except` 加 `results["ok"] = False`；`copytree` 到 `dest.tmp` 成功后 `os.replace`。

---

### C-10　在线更新机制三重不一致：`git reset --hard` ≠ 声明的 ff-only；cron 未安装；标记文件无 TTL

- **位置**：`scripts/host_update.sh:13,65,75,94,103-108`；`README.md:171-175`；`install.sh:60`；`api/system_routes.py:130-134`
- **问题 1（`reset --hard` vs ff-only）**：`host_update.sh:75` 用 `git reset --hard "origin/$REF"`，`README.md:173` 明写"仅 fast-forward 拉取（绝不 force / merge）"，架构图也写 `git pull --ff-only`。若远端历史被改写或本地有提交，本地分支被静默销毁且不报错。
- **问题 2（cron 未安装）**：`host_update.sh:3` 的 cron 条目只写在脚本注释里，`grep -n cron README.md install.sh` = **0 命中**；install.sh:60 却提示用户去 WebUI 点更新。新装机器上点"检查更新"永远返回"宿主预检脚本尚未运行"，且两条 cron 路径硬编码 `/vol1/1000/docker/memory-agent`，与 install.sh 克隆到的 `./memory-agent` 不是同一位置。
- **问题 3（标记无 TTL）**：`system_routes.py:130-134` 标记存在即 409，无 TTL、无超时清理——宿主执行器不跑时，用户点一次更新后**永久锁死**，只能手工删文件。
- **问题 4（成功状态写在重启之前 + 缺 `set -e`）**：`host_update.sh:5` `set -uo pipefail`（缺 `-e`）；`:103` `write_result True` → `:104` `rm -f $MARKER` → `:108` `docker restart`。重启失败时 UI 显示成功、标记已删无法重试，容器仍是旧代码。
- **问题 5（`py_compile` 只扫顶层）**：`:94` `python3 -m py_compile src/memory_agent/*.py` 只编译顶层 82 个文件，`api/`、`insights/` 等子目录全部漏检——子模块语法错误直接烧进容器。
- **建议修法**：改 `git merge --ff-only`；install.sh 增加 cron 安装（含幂等检测）与路径参数化；`MARKER_PATH` 按 `requested_at` 年龄自动清理；先 restart 再 `write_result`、补 `-e`；`py_compile` 改 `find ... -exec`。

---

### C-11　非 HTTP scope 完全绕过 Token 鉴权（WebSocket 提权后门）

- **位置**：`src/memory_agent/mcp_auth.py:49-52`、`src/memory_agent/acp_auth.py:25-28`、`src/memory_agent/app.py:673-675`
- **问题**：两个 Token 中间件对 `scope["type"] != "http"` 一律直通下游；`_mcp_dispatcher` 又对非 http scope 直接透传给 `mcp_app`。`ws` / `lifespan` 类型请求**不做任何 token 校验**。
- **触发条件**：`websocket.connect("/mcp")`（无 `Authorization` 头）→ 中间件直通 → 分发器直通 → 子应用 ws 路由（若存在）。当前官方 SDK 未注册 websocket 路由（404），**暂不可利用**；但 SDK 树里已有 `mcp/server/websocket.py`，后续版本一旦启用，`/mcp` 就是无鉴权 WebSocket 入口。
- **证据**：
```python
# mcp_auth.py:49-52
async def __call__(self, scope, receive, send):
    if scope["type"] != "http":
        await self.app(scope, receive, send)   # ← 无 token 校验
        return
```
```python
# app.py:673-675  分发器同样直通
if scope.get("type") != "http":
    await mcp_server.mcp_app(scope, receive, send)
    return
```
- **是否破坏隔离红线**：否（未涉及 JWT），但让 ACP「与 MCP 同构」的鉴权承诺在传输层失效。
- **建议修法**：非 `http` 一律回 403 / 关闭连接，不要交给子应用；或显式白名单 `lifespan`。

---

### C-12　请求体大小上限只声明未执行

- **位置**：`src/memory_agent/mcp_auth.py:32`（`MAX_BODY_BYTES = 1024 * 1024`）
- **问题**：`MAX_BODY_BYTES` **全仓只有 1 处引用**（定义本身），是死常量。中间件注释「不要在中间件读 body（只能消费一次）」而把权限判定下沉，代价是**请求体大小从不校验**。已确认安装的 `mcp` SDK 的 `streamable_http` / `sse` 都没有 body size 限制；`_build_routes()` 也未挂任何 body-limit 中间件。
- **触发条件**：`POST /mcp` 带数百 MB JSON-RPC 体 → 服务端整段读入内存 → OOM / 进程被 OOM-killer 打死（`app.py` 单进程 uvicorn）。
- **建议修法**：ASGI 层基于 `content-length` 头预检（不消费 body，不破坏 Mount 转发），超限直接 413。

---

### C-13　`_no_buffer_send` 会把客户端已收到的 SSE 流掐死

- **位置**：`src/memory_agent/app.py:627-652`
- **问题**：`suppress` 是**单调翻转的布尔**，一旦置 `True` 就**丢弃此后所有 `http.response.body`**（包括合法的 SSE 事件帧），不只是丢掉那一条错误回写 body。SSE/流式响应的正确性完全依赖后续 body 消息全部透传。
- **触发条件**：SSE 流已发 `http.response.start` → 上游因任何异常发了一次重复 `start` → `suppress=True` → 之后**所有** message 事件被吞 → 客户端挂死直到连接超时。日志只有一条 warning，无恢复手段。
- **证据**：
```python
        if mtype == "http.response.start":
            if sent_start:
                # 响应头已发送，重复 start 是框架的错误回写，丢弃以免击垮 worker。
                _log.warning("MCP: 丢弃重复的 http.response.start…")
                suppress = True
                return
            ...
        elif mtype == "http.response.body" and suppress:
            # 错误回写 body，随上面的重复 start 一起丢弃。
            return              # ← 永久丢弃后续所有 body
```
- **建议修法**：改为只吞「那一次重复 start 之后紧随的一条 error body」：记录待丢弃计数（1 条），用完后恢复透传。

---

### C-14　ACP 会话存储淘汰时泄漏 `_CONV` 对话历史

- **位置**：`src/memory_agent/acp_server.py:124-131`（`SessionStore._trim`）
- **问题**：`_trim` 只 `_sessions.pop()`，不像 `delete()`（`:96-98`）那样清理对话历史。`_CONV` 的 FIFO 淘汰走 `debug_routes` 自己的 `_CONV_ORDER`，而 ACP 创建的 session 从不进入 `_CONV_ORDER`（只有 `_execute_run` 写入时才 append）→ 被淘汰的 ACP session 的完整 messages 上下文永久滞留内存。
- **触发条件**：任意 ACP 令牌反复 `prompt` 带自定义新 `sessionId` 超过 200 个 → `_trim` 驱逐旧 session → `_sessions` 保持 200 条，但 `_CONV` 无限增长（每条含多轮 LLM 上下文）→ OOM。
- **证据**：
```python
# acp_server.py:124-131
def _trim(self) -> None:
    if len(self._sessions) <= self._max:
        return
    oldest = sorted(...)
    for s, _ in oldest[: len(self._sessions) - self._max]:
        self._sessions.pop(s, None)      # ← 无 _CONV.pop(s, None)
```
```python
# acp_server.py:96-98  delete() 是正确的对照
def delete(self, sid: str) -> None:
    self._sessions.pop(sid, None)
    _CONV.pop(sid, None)
```
- **建议修法**：`_trim` 里补 `_CONV.pop(s, None)`。

---

## 四、中

### D-01　`outbound_guard` 未接入 admin 可编辑的外发 URL 字段（DNS rebinding）

- **位置**：`src/memory_agent/outbound_guard.py:91-108`、`api/config_routes.py`（`WRITABLE_FIELDS`）
- **问题**：guard 只被 `acp_routes.py:203`、`face_routes.py:71`、`vision_routes.py:122` 三处调用；`llm_api_url` / `vlm_base_url` / `autoflow_acp_url` / `go2rtc_base_url` / `llm_backends[i].api_url` 走 `update_config_api` 白名单但**不经 guard**。guard 对域名**不做 DNS 解析**（`:104` 注释自承"域名：不做 DNS 解析（避免绕过），直接放行"）。
- **触发条件**：admin 把 `llm_api_url` 设为解析到 `127.0.0.1:8123` 或 `169.254.169.254` 的域名 → 放行 → SSRF 打本机 HA / redis / 元数据服务。
- **建议修法**：`update_config_api` 对 `WRITABLE_FIELDS` 中所有 `*_url` / `*_host` 统一过 `validate_outbound_url`；域名增加解析后二次校验（rebinding 天然难挡，至少挡住字面量与解析结果）。

---

### D-02　`paho-mqtt` 导入失败被静默吞，运维误判"未启用"

- **位置**：`src/memory_agent/mqtt_bridge.py:34-42`
- **问题**：导入失败只写进模块常量 `MQTT_IMPORT_ERROR`，**从未被任何日志或健康出口读取**；`MQTT_AVAILABLE=False` 后不可逆（factory 永远返回 `None`，无 `reset_*_probe` 接缝）。
- **触发条件**：容器缺 `paho-mqtt` / 版本不符 → `publish_*` 全返回 `False` → `/api/health` 只显示 `ma_mqtt_enabled=False`，运维以为"配置没开"，真相是"依赖装不上"。
- **证据**：
```python
try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
    MQTT_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover
    mqtt = None
    MQTT_AVAILABLE = False
    MQTT_IMPORT_ERROR = str(exc)      # ← 存了变量，无人消费
```
- **建议修法**：`except` 分支立即 `print(f"[MQTT] paho-mqtt 不可用，推送全部降级为空转: {exc}")`；把 `MQTT_IMPORT_ERROR` 加进 `MqttBridge.status()`；补 `reset_mqtt_probe()`。

---

### D-03　`llm.chat()` 对 429 不重试，与模块 docstring 承诺不符

- **位置**：`src/memory_agent/llm_client.py:291-296`
- **问题**：`_RetryLater` 只对 5xx 触发，429 落进 `raise LLMError` → 立即抛给 `LLMRouter.chat()` 换下一个 provider，当前 provider 的 `Retry-After` 重试完全失效。
- **证据**：
```python
if 500 <= resp.status_code < 600:
    raise _RetryLater(f"...", retry_after=resp.headers.get("Retry-After"))
raise LLMError(f"[{self.name}] HTTP {resp.status_code}: {resp.text[:300]}")   # ← 429 落在这里
```
- **建议修法**：`if 500 <= resp.status_code < 600 or resp.status_code == 429:`，`Retry-After` 解析路径已存在。

---

### D-04　`_persist_progress` 裸 await 在主循环内，进度写失败把已落库的采集标成 error

- **位置**：`src/memory_agent/poller.py:374`（外层 `416-420`）
- **问题**：`_flush` 已把事件落库后，紧接着 `await self._persist_progress(job_id)` 无 try/except；一次 `update_job` 抛异常（`database is locked`、busy timeout）会被外层 catch 成整个 job `error`。进度条与状态失真，下一轮从 `last_poll_time` 之前的窗口重跑（HA 侧压力翻倍，靠 `make_event_id` 幂等才不至于重复入库）。
- **建议修法**：给 `_persist_progress` 包 `try/except` 只写 warning，不上升为 job 失败。

---

### D-05　`merge_members` 多步非原子，中途失败留下部分合并且不可重入

- **位置**：`src/memory_agent/store.py:3895-3963`
- **问题**：`set_member_rooms` → `set_member_devices` → N × `add_member_tag` → `update_member` → `delete_member(source_id)`，每步独立加锁独立 commit。中间任意一步失败后：target 已收到部分修改、source 仍存在、`tags` 只复制了一半。下次重试会再次复制已复制的 tag（`INSERT OR REPLACE` 覆盖 confidence）。
- **触发条件**：合并 20+ 条 tags 时中途 sqlite busy timeout；进程被 SIGKILL。
- **建议修法**：整个 `merge_members` 用同一连接显式 `BEGIN IMMEDIATE ... COMMIT` 包裹，`delete_member(source_id)` 作为最后一条语句。

---

### D-06　`config.json` 已达 1.3 MB，无轮转/截断/大小上限

- **位置**：`src/memory_agent/config.py`（`save`）、`data/config.json`
- **实测**：`data/config.json` 1,318,650 字节（≈1.3 MB）、`config.json.bak` 409,188 字节。对一份配置而言异常体量。`grep "backup|rotate|truncat"` 在 `config.py` 内**零命中**——只有 `backup_enabled`/`backup_dir`/`backup_retention` 三个无关字段（那是数据库备份）。配置写路径无轮转、无截断保护，且 `.env`/`config.json` 含 `ha_db_password`、`jwt_secret`、`llm_api_key`，随 `./data:/data` 挂进容器，任何拿到 docker 权限的宿主进程可读。
- **建议修法**：查明体积来源（疑似累积的历史版本字段或 token 记录），加写入前的体积检查与旧键清理；密钥字段考虑迁出配置主体。

---

### D-07　`POST /api/events` 无批量大小上限，单条失败用 `print` 吞掉

- **位置**：`src/memory_agent/api/events_routes.py:26-89`
- **问题**：① 无批量上限——`events` 数组可任意长，一次 POST 可带上万条，`_insert_batch` 在 `to_thread` 里同步写 SQLite，长事务钉住线程池；② 单条失败只 `print`，不进日志体系，非 stdout 路径拿不到；③ 返回体只有 `inserted`/`total`，未区分成功/失败条数。
- **证据**：
```python
def _insert_batch() -> int:
    inserted = 0
    for it in items:
        try:
            rt.store.insert_behavior_event(it)
            inserted += 1
        except Exception as exc:  # 单条失败不中断整批
            print(f"[Events] 写入失败 {it.get('room')}/{it.get('action')}: {exc}")
    return inserted
```
- **建议修法**：加批量上限（如 500）；`print` → `logging.warning`；返回体报出失败条数与首条错误摘要。

---

### D-08　MCP 响应体上限只量 `TextContent`，非文本内容不受限且被静默丢弃

- **位置**：`src/memory_agent/mcp_server.py:594-618`（`_apply_response_cap`）、`:398-405`（`_result_text`）
- **问题**：上限只统计**第一个** `type=="text"` 的 content 的 UTF-8 字节数。`ImageContent` / `EmbeddedResource` 完全不计入配额；且两个截断分支都重建为「单 TextContent」结果（`_build_tool_result`），**非文本内容块被无声丢弃**，调用方拿不到任何提示。
- **证据**：
```python
# mcp_server.py:603-606  只量第一个 text 块
text = _result_text(result)
raw = text.encode("utf-8")
if len(raw) <= cap:
    return result               # ← 其他 content 块不参与配额
```
```python
# mcp_server.py:398-405  只取第一个 text
def _result_text(result) -> str:
    try:
        for c in result.content:
            if getattr(c, "type", "") == "text":
                return str(c.text)
```
- **当前实害**：无工具返回 `ImageContent`（`include_preview` 是放进 JSON 文本的 data URL，`vision_service.py:1519-1520`），故目前不触发；但 `SERVER_INSTRUCTIONS` 声明 `include_preview=True`「返回 base64 缩略图」，**契约与实现已漂移**——若哪天改成真 `ImageContent`，配额立即失效。
- **建议修法**：配额改为累计全部 content 块字节；非文本块超限时显式替换为带说明的文本块，而非丢弃。

---

### D-09　工具异常原文回传给任意 MCP 令牌（信息泄露）

- **位置**：`src/memory_agent/mcp_server.py:654-737`（`_tracked_call_tool`）
- **问题**：`_tracked_call_tool` 捕获异常记日志后**原样 `raise`**（`:732-734`），由 SDK 包装成 `CallToolResult(is_error=True, content=[TextContent(str(exc))])` 返回给客户端。异常里常含 HA `entity_id`、Chroma/上游端点、SQL 语句、文件路径。
- **证据**：
```python
    except Exception as exc:
        is_err = True
        err_text = f"{type(exc).__name__}: {exc}"   # ← 内部细节进 err_text
        raise                                        # ← 原文上抛，SDK 会回传给客户端
    finally:
        ...
        await _record_mcp_call(name, dt, is_err, err_text)
```
- **触发条件**：令牌调 `ask_memory` 期间 chroma 不可达 → 返回文本含内部连接串/路径。
- **建议修法**：上抛前脱敏/截断消息，内部细节只进日志（日志已有）。

---

### D-10　ACP `initialize` 不做协议版本协商

- **位置**：`src/memory_agent/acp_server.py:342-372`
- **问题**：ACP 规范里 `initialize` 响应必须回显协商后的 `protocolVersion`。本实现忽略 `params.protocolVersion`，只回 `agent{name,version}` + `capabilities`（`:364`）。全文件 `grep protocolVersion` **零命中**。
- **触发条件**：官方 ACP client → `initialize {"protocolVersion":"2024-11-05"}` → 响应无 `protocolVersion` → client 判握手失败退出。
- **建议修法**：解析请求 `protocolVersion`，支持则回显，不支持返回版本不匹配错误。

---

### D-11　ACP `cancel` 无法终止卡住的 LLM 调用

- **位置**：`src/memory_agent/acp_server.py:411-426`（`M_CANCEL`）、`src/memory_agent/api/debug_routes.py:158-160`（`abort()`）
- **问题**：`abort()` 只翻转布尔，轮次间才检查。若一次 `rt.llm.chat()` 卡住（网络半开、后端不返回），后台任务不结束，SSE 生成器 `await own.get()` 永久挂起。僵尸标记（`_ZOMBIE_RUN_TTL_S=600`）只在 `_register` 时对超阈值 run 调 `finish()`——`finish()` 投 `_TERMINAL` 让流收尾，但**不取消任务**，LLM 调用继续跑。
- **证据**：
```python
# debug_routes.py:158-160
def abort(self) -> None:
    self.cancelled = True          # ← 只置标志，无人读
```
- **触发条件**：客户端 `cancel` 后立即断开 SSE → `run.abort()` → 但 `_execute_run` 正卡在 `rt.llm.chat()`，标志无人读 → 任务空转最多 10 分钟，占用连接与 LLM 并发额度。
- **建议修法**：`run.abort()` 记录 task 引用，cancel 时 `task.cancel()`；或对 LLM 调用套 `asyncio.wait_for`。

---

## 五、低

| ID | 位置 | 问题 |
|---|---|---|
| E-01 | `api/*_routes.py` 15 处 | 裸 `int()` 转换，`?limit=abc` 直接 500（无 400 语义）。例：`behavior_routes.py:31,48,226,296,302,386,897,942,1094`、`agent_memory_routes.py:173,200`、`arena_routes.py:28`、`vision_routes.py:248`。同仓 `_num()`（`deps.py`）已是标准做法 |
| E-02 | `arena_routes.py:28,60` | `int(...) or 30` / `int(version) if version else None` 对非法值**静默换默认**，与 `_num()` "不替调用方悄悄改值"口径相反；`arena_id` 无长度上限 |
| E-03 | `member_routes.py:126,211` | `delete_member` / `delete_member_tag` 返回值被丢弃，`DELETE /api/members/nonexistent` 返回 200 + "成员已删除"。同仓 `auth_routes.delete_user`、`debug_revoke_token` 都返回 404 |
| E-04 | `arena_routes.py:52-66` / `store.py:2125` | 竞技场令牌可访问 `ARENA_ENDPOINTS` 全部端点，`get_snapshot` 按 URL `arena_id` 直取、`list_arena_snapshots` 返回**全部分区**——`arena_id` 无归属绑定。快照号称"脱敏"，但脱敏是否覆盖跨分区可见面未验证 |
| E-05 | `mcp_server.py:1414-1430` | `get_member_persona` 的 `archived_tags` 直接 `member.get("tags", [])` 原样返回，含 `evidence`（自由文本证据链）、`source`、`confirmed_at`、`id`；而 `list_members`（`:1316-1323`）明确裁剪 tags 到 `label/category/confidence`，注释写「去掉 evidence_json/evidence 长文本」。**两条读路径脱敏口径不一致**，read 令牌即可拿到完整证据链 |
| E-06 | `mcp_server.py:2193-2201` | revoked 分支无独立留痕——`_record_mcp_call` 只记 `scope=scope_of(name)="read"`，事后追责时审计表无法区分「读 live」与「读 revoked」（B-05 修好后的必要配套） |
| E-07 | `llm_client.py:509-531` | 所有后端失败时 `br.record_failure` 双重计数（循环内每 provider 一次 + 总结后一次），一次 `chat()` 可把失败计数打到 4，下一次直接进 60s 冷却 |
| E-08 | `llm_client.py:474-497` / `api/config_routes.py:341` | `ping()` 顺序调用所有后端，4 个后端 1 个黑洞 = "测试连接"最多挂 4×120s |
| E-09 | `llm_client.py:128-131` | `_num()` 对空串 `""` 静默回落 120s，UI 显示"已清空"但后端按 120s 跑 |
| E-10 | `ha_client.py:39-44` | LRU 池淘汰 `dead.close()` 无超时（工作线程上阻塞）；`reload_config` 换对象后旧池条目不显式关闭 |
| E-11 | `app_tokens.py:132-157` | `_touch()` 内存已写但节流跳过落盘，磁盘计数永久落后内存 15s 窗口 |
| E-12 | `mqtt_bridge.py:528-548` | `_reconnect_interval()` 未夹下限，配置为 0 时退避失效变紧密循环；该字段未入 `WRITABLE_FIELDS`/`NUMERIC_BOUNDS` |
| E-13 | `config.py:353-373` | `Config.save()` 原子替换但**无跨进程互斥**，两个 `save()` 并发时后写覆盖前写全部字段 |
| E-14 | `poller.py:262-279` | `_run_backfill` 窗口构造 `cursor += timedelta(days=1)`，第二个窗口 `end` 为次日 `23:59:59`，会拉到用户未声明的时段（当前调用路径 `strptime` 得 `00:00:00`，暂不触发，属 API 层隐患） |

---

## 六、测试基线与失败归类

### 全量基线

```
python -m pytest tests/ -q
18 failed, 2163 passed, 27 skipped, 98 subtests passed in 864.44s
```

> `pytest.ini` 仅 6 行，无 timeout 配置；`pytest-timeout` 未安装，带 `--timeout` 参数会直接报 `unrecognized arguments`、一个用例都不跑。
> CI 侧 `ci.yml:39-42` 额外 `--ignore` 了 `test_mqtt_bridge.py` / `test_ha_assist.py` / `test_vlm_gate.py` / `test_quality_gates.py`。

### 18 个失败按性质归类

**① 真缺陷（对应报告条目）**

| 测试 | 对应 | 失败要点 |
|---|---|---|
| `test_db_integrity_check_mode.py::test_both_red_without_backup_leaves_db_alone` | B-03 | quarantine 未关连接 → WinError 32，`error` 被谎报为"已隔离" |
| `test_quality_gates.py::test_no_new_gate_violations` | B-04 | 新增 5 条门禁违规（error 3 / warn 2） |
| `test_quality_gates.py::test_baseline_shrinks_when_you_fix_things` | B-04 | 3 条基线条目已修未删 |

**② 测试断言过时（任务已挪到 `_init_background`，测试仍断言 `AppRuntime.startup` 源码）**

| 测试 | 断言 | 实际位置 |
|---|---|---|
| `test_device_event_feed.py::test_startup_registers_the_device_feed_task` | `'name="runtime.device_feed"' in startup` | `runtime.py:265-266` |
| `test_rounds11_19_ma25_35_fixes.py::test_retention_task_is_registered_at_startup` | 同上 | `runtime.py:212-214` |
| `test_startup_identity_nonblocking.py::test_startup_no_longer_awaits_the_first_reconcile` | 同上 | `runtime.py:228-233` |
| `test_vma_step1_adm_presence.py::test_startup_advertises_before_the_first_interval` | `"ensure_advertised(self.adm_caps())" in startup` | `runtime.py:241` |

这 4 个失败说明"启动挪后台"的架构变更**只改了实现没改测试**，而它们恰好是 C-07/C-08 的直接后果：任务注册位置变了，测试没跟着变成"在 `_init_background` 里验证"，导致 C-07 的失败模式（任务没注册）没有任何测试能捕获。

**③ 静态扫描器与实现口径不一致**

```
FAILED tests/test_vma_r6_db_lock_discipline.py::test_shared_connection_is_never_used_outside_the_store_lock
  store.py::checkpoint 未加锁的共享连接操作 @ [675]
```
`checkpoint`（`store.py:662-690`）实际用了手动 `self._lock.acquire(timeout=5.0)` / `finally: self._lock.release()`，**在锁内**——扫描器只认 `with self._db()` / `with self.transaction()`，不识别手动 acquire/release。属扫描器误报，但这条纪律测试长期红灯会被当作"已知失败"忽略，建议修扫描器或统一改用 `with`。

**④ 声明一致性检查失败（文档/工具 schema 与运行时漂移）**

- `test_tool_prose_promised_keys.py::test_every_spec_still_has_a_landing_site`
- `test_tool_prose_promised_keys.py::test_intent_value_list_in_prose_matches_the_enum`
- `test_vma_promised_keys_runtime_contract.py::test_plan_question_payload_has_no_invented_recommended_tool`
- `test_vma_dcd_20261002_payload.py::test_adm_caps_version_is_the_plan_number_not_the_package_version`
- `test_vma_phase2_claims_gauge.py::test_claim_gate_two_ledgers_cover_every_declaration`
- `test_vma_step1_adm_presence.py` 其余 4 个（LWT/retain/广告时序）

最后一组 4 个是同一处契约漂移：
```
assert status["payload"] == "offline" and status["retain"] is True
  ('{"state":"offline","ts":1791652827,"degraded":false,"reasons":[],"version":"1.4"}' == 'offline')
```
MQTT 广告消息的 `payload` 由纯 `"offline"` 字符串改成了 JSON，测试断言未同步。

**⑤ 环境污染（非代码缺陷）**

```
FAILED tests/test_webui_payload_keys.py::test_gauge_control_legs_pass
E   FileExistsError: [WinError 183] ... 'E:\NAS\memory-agent\.qoder\tmp-c77-ctl_run\src\memory_agent'
```
`scripts/scan_webui_payload_keys.py:619` 的 `copytree` 不幂等，重跑撞上残留目录。

### 门禁存量基线规模

`.gates-baseline.txt` 12.5 KB / 169 条指纹；全量统计 `fake-ok-const 165 / except-pass-broad 31 / swallow-and-claim-ok 8`。

---

## 七、声明不一致（README / 文档 vs 实际）

| 声明 | 实际 |
|---|---|
| README「Caddy 另占 80/443 用于 iOS PWA 的 HTTPS」 | `docker-compose.yml:63-64` 映射 `9080:80` / `9443:443` |
| README「一键安装…自动克隆、生成 .env、构建启动（含 redis/chroma/caddy）」 | `install.sh:37` `cp .env.example .env` 后 `JWT_SECRET` 为空 → compose 的 `${JWT_SECRET:?}` 中止；`certs/` 只有 README.md（实测），Caddy 起不来。名义一键，实际卡第二步 |
| README「WebUI 在线更新…仅 fast-forward，不触碰数据」；架构图「git pull --ff-only + 重启」 | `host_update.sh:75` 是 `git reset --hard`（非 ff）；cron 只写在脚本注释里从未安装（见 C-10） |
| README:175/283/343「容器需把宿主机仓库根挂载到 `/repo`（docker-compose 已配置 `.:/repo:rw`）」 | `compose:45` 明写"在线更新已停用…（WO-MA-001 / 审计 P0-10）"，无 `/repo` 挂载——按文档配置反而重新打开已修复的安全红线 |
| `PWA-iOS指南.md` §一「Caddy 反代方案为可选/已过时…当前部署可跳过」 | compose 仍在编排并暴露 caddy，README 快速开始仍写"含 caddy" |
| `PWA-iOS指南.md` §六 `https://<主机名>.ts.net/health` | compose 暴露 9443 而非 443 |
| `app.py:127-131` `APP_ENDPOINTS` 列了 `/api/agent/memories/recall`「POST 记忆召回（只读）」，`service_tokens.py:74` 也登记了 `POST:/api/agent/memories/recall` | `agent_memory_routes.py:214-223` 的 `ROUTES` 里**没有** `/recall`，只有 `/retrieve`。承诺的召回入口在路由层不存在 |
| `mcp_scopes.py:20` 注释「`ADMIN`：可列全量记忆（含 revoked）」 | 实现里 `"admin" in` 只有 3 处（`mcp_server.py:2175/2197/2245`），全在 agent_memory 三个工具；且 B-05 后 revoked 连 admin 专属都不是。文档与实现双向漂移 |
| `mcp_server.py:2163` docstring「`revoked 永不经 MCP 返回`」 | B-05：`state="all"` 直穿守卫 |
| `SERVER_INSTRUCTIONS`「`include_preview=True` 返回 base64 缩略图」 | 实现把 data URL 放进 JSON 文本，未用 `ImageContent`（D-08） |
| `pyproject.toml:7,11` `pydantic>=2.0` / `chromadb>=0.4.0` | `Dockerfile:19` 固定 `chromadb==0.5.23` 且**未显式装 pydantic**（靠 chromadb/mcp 传递）。CI 与镜像不是同一组依赖版本 |
| `Dockerfile:18` 注释「paho-mqtt：v0.6 起在场推送依赖」 | paho-mqtt 没有 0.6 这个版本（现存为 1.6.x）。另 `pyproject` 写 `paho-mqtt>=1.6` 浮动跨大版本，`mqtt_bridge.py:152-157` 用 `try/except AttributeError` 同时兼容 1.x/2.x |
| `compose:57,76,86` | `caddy:2-alpine`（只钉 major）、`redis:alpine`（完全浮动）、`docker.m.daocloud.io/chromadb/chroma:0.5.23`（走第三方镜像代理）；与 `Dockerfile:15-16` 对 vendored wheel 严格登记 sha256 的做法反差明显 |
| `compose:46-48,69-70` | 四个服务**全部没有 `healthcheck`**，`depends_on` 未用 `condition: service_healthy`，编排对就绪状态无感知（应用层 `history.py` 的 30s 重试有部分缓解，但 `docker compose restart chroma` 后无信号促使 MA 重试） |
| `.env.example:38-44` + README 快速开始 | 默认 `MA_TRUST_PROXY=0` 时全家共享一个源 IP 桶，5 次登录失败锁 30 分钟；README 的 Caddy 段落从不告诉用户打开代理信任开关 |
| `Caddyfile:13-15` | `X-Accel-Buffering no` 是 nginx 专用头，Caddy 转发给浏览器无作用（真正生效的是同块 `flush_interval -1`） |
| `compose:17` | `NR_USER` 默认值 `lidicn` 明文写进编排文件，会泄漏到任何 `docker inspect` |
| `pytest.ini:8-9` | `filterwarnings = ignore::DeprecationWarning` 全局抑制，依赖破坏性变更不会让测试变红 |

---

## 八、已确认的误报

以下经我逐条核实为误报，本次不列为缺陷：

1. **`poller.py:446-458` MariaDB 回退 REST 重复写入 `series`** —— 不成立。`get_history` 抛异常时 `sub` 未赋值，`for k, v in (sub or {}).items()` 从未执行，`series` 仍为空。
2. **`ha_client.py` 池化 `httpx.Client()` 无默认超时** —— 不构成缺陷。全部 9 个调用点均显式带 `timeout=`（30/10/10/5/60/30/60/30/60）。全仓 `httpx` / `requests` 调用点**均已带超时**。
3. **`store._db()` 24 处调用均无 `commit`** —— 经逐块核查全部是只读 `SELECT`/`PRAGMA`，无写路径漏 commit。
4. **`vision_service.py:469` 用 `choices` 取值** —— 正确，那是解析 VLM 原始 `httpx.post` 响应，非 `llm.chat()` 结果。
5. **`app_tokens.verify()` 用 `==` 明文比较 token** —— 不成立。两处比较（`:119,128`）均为 `secrets.compare_digest`，且先哈希再比。
6. **`face_routes` 的 `auth == f"Bearer …"`** —— 已修为 `secrets.compare_digest`（`:33-34`）。
7. **`/api/vision/analyze` 在事件循环里跑 25~60s 阻塞 I/O** —— **已修**：`vision_routes.py:156-161` 已包 `asyncio.to_thread`，仅 `wait=true` 路径会等到 VLM 返回，属设计内。
8. **`acp_server._CONV` 无限增长（无上限）** —— 已有 `_MAX_CONV = 200`（`debug_routes.py:54`）上限；C-14 是**淘汰时不回收**的另一条路径，不矛盾。
9. **arena 令牌可"跨 kind 逃逸"到 MCP/builtin 工具面** —— 不成立。arena 令牌只能看见 3 个 arena 工具（`build_acp_tools(kind="arena")` 只返回这三条），无法借 builtin 面绕过 MCP 的 write scope。残留风险是**跨 arena 分区**读快照（已列 E-04）。
10. **MCP 工具注册集与 `REGISTERED_TOOLS ∪ WRITE_TOOLS` 声明漂移** —— 经脚本双向比对，两个方向差集均为空；`expose=("builtin",)` 的 20 个工具全部只读；`assert_write_tools_complete()` 启动期 fail-close 生效。
11. **SDK SSE 会话跨凭据混用** —— 经核实不成立：SDK `sse.py` 有 `requestor != self._session_owners.get(session_id)` 校验；streamable 侧用 `stateless_http=True` 无 session 跟踪。
12. **MCP SDK 与 README 声明不一致（`MCPServer` 不存在）** —— 不成立：`pyproject.toml` 与 `Dockerfile` 均写 `mcp>=2.0.0`，该版本下 `MCPServer`、`sse_app()`、`streamable_http_app()` 全部存在；本地装的是 1.28.0（无 `MCPServer`），此时走 `MCP_AVAILABLE=False` 降级路径，服务不崩、`/api/mcp/info` 报不可用，符合注释预期。

**历史报告（2026-10-10）已列出、本次未重复：**P0-A `fake-ok-const` 165 处清单、P0-B `swallow-and-claim-ok` 8 处、P0-C `except-pass-broad` 31 处、P1-D `mcp_scopes` 未登记工具走 `DENIED`、P1-E `Store` 单连接 + RLock + `synchronous=FULL` 写并发瓶颈、P2-F 工作树 dirty 导致升级不可用、P2-G `.env` 与审计产物混在部署根、P2-H CI 元问题。
> 注：B-04 是 P2-H 的**定案**——历史报告说"没有直接证据显示当前全量 CI 已转绿"，本次证据是：本地全量 18 失败，且 CI 通过 `--ignore` + `GATES_DISABLE=1` 跳过门禁测试。

---

## 九、未确认但值得关注

1. **`SessionStore.new()` 令牌名碰撞即可接管会话**：`owner_token` 存的是用户自取的令牌名（`generate()` 只校验非空/≤64）。若 B 的令牌名恰好等于 A 记录的 `owner_token`，B 用自己的令牌 + A 的 `sessionId` 可绕过 `SessionOwnerConflict`（`acp_server.py:85-87`）。建议存稳定标识（令牌 hash 前缀）而非自取名。
2. **`MCP_ALLOW_URL_TOKEN=1` 时 token 进访问日志**：`_extract_token` 从 `query_string` 取 token，访问日志会记录完整 query。默认关闭；建议命中该分支时打一次告警。
3. **`get_session_trust(session_id)` 无属主校验**：任意令牌可传任意 `session_id` 查其 trust 聚合统计（非消息体），泄露面小。
4. **`_CONV_LOCKS` 无界增长**：`debug_routes.py:62-69` 按 conversation_id 懒建锁，永不清理。
5. **`update_token_scopes` 无法真正锁死令牌**：`normalize([])` 返回 `DEFAULT_SCOPES=["read"]`，收敛手段只剩 revoke。
6. **`_mcp_dispatcher` 非 http scope 直通在 `mcp_app is None` 时会 `AttributeError`**（`app.py:674`）——SDK 未装时 `/mcp` 已被 `mcp_unavailable` 兜住，只在极端时序下触达。
7. **`ask_memory` 内部走 `retrieve(q, top_k=5)` 不传 `member_id`**：fail-closed 只返回公共记忆，与成员收窄层口径一致，但未复用 MCP 面的 `member_id` 参数——功能缺口而非泄露。
8. **`POST /api/events` 缺 `confidence`/`server_ts` 类型校验**：非数字会被 SQLite 拒 → `print` 到 stdout 消失。
9. **`butler_token` 打 `GET /api/behaviors`**：中间件已把 user 写进 state，handler 的 `require_user` 会通过，等价于 butler 可访问 vision 行为明细。语义与 `vision_presence` 一致但未显式声明。
10. **`system_routes.apply_update` 无 `os.makedirs` 兜底**：`DATA_DIR` 不存在时 `open(..., "w")` 抛 `FileNotFoundError` → 全局异常处理器返回非语义化 500。
11. **`debug_run` 的 `max_tool_rounds`**：`body.get("max_tool_rounds") or 3` 对 `0`/`"false"` 静默换成 3，与 `_num()` 口径不一致。
12. **`identity_fusion.py` 整模块疑似未接线**：`grep` 全仓仅测试文件引用，若属实则 README:43 描述的「F → 高置信覆盖 → C」融合链路在生产中不存在。**本次未定案**（见 §十一）。
13. **`identity.Reconciler` 首次部署无健康记录时全量放行**：`_health_allows` 注释说无健康记录时放行，`_mark_stale` 只在 `seen` 之外才标记 → 首次部署（HA 从未对账成功）时所有实体都无健康记录 → `resolve()` 对所有 ref 放行。未做时序验证。
14. **`Arena` 快照"脱敏"内容是否真覆盖跨分区可见面**未验证（E-04）。
15. **`mcp_scopes.ADMIN` 覆盖面与文档不符**（已列 §七），全仓 `"admin" in` 只有 3 处。

---

## 十、复核建议（按收益排序）

| 优先级 | 动作 | 依据 |
|---|---|---|
| 🔴 立刻 | 修 **B-05**：守卫改 `if state in ("revoked","all")`（或改白名单），同时 `update_token_scopes` 收紧到 `require_admin` | 完整提权链，两步都只需登录 |
| 🔴 立刻 | 修 **B-01**：派发与槽位占用收进事件循环侧 | TV 上报主链路阻断，重启才恢复 |
| 🔴 立刻 | 修 **B-02**：以服务端返回结构重写 Node-RED `parse_result` + 加契约回放测试 | README 明列的兼容红线端点从未工作 |
| 🔴 立刻 | 修 **B-03**：quarantine 先 close 再置空，`check_and_recover` 检查返回值 | 恢复能力丧失 + 谎报状态，测试已红 |
| 🔴 立刻 | 修 **B-04**：CI 去掉 `--ignore test_quality_gates.py` 与 `GATES_DISABLE`，先清 5 条新增违规 + 3 条失效基线 | 门禁是唯一自动化防线，现已摘除 |
| 🔴 立刻 | 修 **C-07**：`_init_background` 包 try/except，失败写入区别于 `ready` 的状态 | 静默丢失 7 个常驻任务，无告警 |
| 🟠 本周 | 修 **C-01~C-05**：管理类端点收紧到 `require_admin`，令牌 revoke 补归属校验 | 任一登录用户可自签令牌/吊销他人令牌/改写他人档案 |
| 🟠 本周 | 修 **C-11~C-14**：非 http scope 一律拒绝；ASGI 层 `content-length` 预检 413；`_no_buffer_send` 改成丢弃计数 1 条；`_trim` 补 `_CONV.pop` | 传输层鉴权缺口 + OOM 面 |
| 🟠 本周 | 修 **C-09**：备份一律"写 tmp + `os.replace`"，chroma 失败置 `ok=False` | 恢复点会因自身操作被删 |
| 🟠 本周 | 修 **C-08**：`/api/*` 加 readiness 门控（`get_state() != "ready" → 503`） | 启动窗口内 DB 文件可能被替换 |
| 🟠 本周 | 修 **C-06**：`change_password`/`register` 加 `asyncio.to_thread`，register 补限速 | 事件循环钉死，注册面未认证可达 |
| 🟠 本周 | 修 **C-10**：`reset --hard` 改 `--ff-only`，cron 装进 install.sh，标记加 TTL，`py_compile` 覆盖子目录 | 更新机制声明与实现三重不一致 |
| 🟡 排期 | 修 **D-01~D-11**、**E-01~E-14** | 见各条建议 |
| 🟡 排期 | 把 §六② 的 4 个过时断言改为在 `_init_background` 里验证；§六③ 修扫描器识别手动锁 | 让测试能真正捕获 C-07 的失败模式 |
| 🟡 排期 | 补 `/api/agent/memories/recall` 路由或从 `APP_ENDPOINTS` 移除；清理 §七 其余声明不一致 | 按文档操作必然失败 |
| 🟡 排期 | 核实 §九 第 12 项：`identity_fusion.py` 是否真的未接线 | 若属实则 README:43 描述的身份融合链路不存在 |

---

## 十一、诚实声明（审计边界）

- 本次审计**未修改任何源码或配置**；全部为只读侦察 + 只写 `/tmp/v01repro/` 的独立复现脚本。
- 覆盖：`src/` 下 124 个 `.py` / 58,124 行；`Dockerfile`、`docker-compose.yml`、`Caddyfile`、`.dockerignore`、`pyproject.toml`、`pytest.ini`、`.gates.toml`、`.gates-baseline.txt`、`gates.sh`、`install.sh`、`deploy_nas.sh`、`scripts/host_update.sh`、`scripts/host_check_update.sh`、`.github/workflows/*.yml`、`nodered/water_purifier_flow.json`；全量 `pytest tests/`（864s）。
- **已确认的复现**：B-01 用独立脚本在 CPython 3.13.2 严格复现（第 1 次抛 `RuntimeError: no running event loop`，第 2/3 次被去重拒绝，`_vlm_inflight == {'living_room'}`）；B-03 由测试运行直接输出 `[WinError 32]`。
- **静态确认、未构造端到端时序脚本**：B-05、C-06~C-14、D-01~D-11、E-01~E-14 全部为代码路径静态确认（B-05 的每一步都已逐个读到源码核实，含 `create_token` 权限、`normalize` 放行 `admin`、store 的 `state="all"` 分支、`agent_memory` 不二次过滤）。
- **未逐行审计的文件（本次有效覆盖缺口）**：`identity_fusion.py`、`insights_legacy.py`(3431 行)、`insights/` 包、`candidate_promotion.py`、`algo_kernel.py`、`algo_eval.py`、`activity_hmm.py`、`activity_inference.py`、`behavior_predictor.py`、`entity_resolution.py`、`daily_profile.py`、`patterns.py`、`presence_fusion.py`、`rule_engine.py`(1149 行)。这些文件派出的专项审计**未产出可验证结果**（输出为空），故本次对这些文件没有有效结论——这是一处需要补的洞，不要把它们当作"已审计无问题"。
- **已核实的排除**：§八 的 12 项误报全部经我逐条读源码排除，附可核验依据。
- `certs/` 内容（只有 README.md）、`grep -n cron README.md install.sh` = 0 命中、`data/config.json` 1,318,650 字节、`grep protocolVersion acp_server.py` = 0 命中、`MAX_BODY_BYTES` 全仓仅 1 处引用均为本机实测。
- 报告内所有行号基于工作树当前状态（HEAD `5bb9d30`，含未提交改动），可能随后续提交漂移。

---

*报告生成：2026-10-11 · 生成方式：本地只读审计 + 独立复现脚本 + 全量 pytest + 并行专项审计 · 生成者：Agent*
