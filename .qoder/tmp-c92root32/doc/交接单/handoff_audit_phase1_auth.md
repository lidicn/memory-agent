# 交接卡：审计 Phase 1（认证 / 令牌安全）

- 工单：PROJECT-20260914-001 · Phase 1 实施
- 日期：2026-09-14
- 范围：JWT 撤销、登录爆破防护、URL Token 移除、debug traceback 脱敏
- 部署：已 scp 至 NAS `/vol1/1000/docker/memory-agent/src/memory_agent/` 并 `docker restart memory-agent`，实测通过

---

## 一、改动清单（5 文件）

| 问题 | 文件 | 改动 |
|---|---|---|
| **A2** JWT 无撤销（登出后 7 天仍有效） | `auth.py` + `api/auth_routes.py` | `_create_token` 写入 `jti`（+`iat`）；新增**模块级** `_revoked_jtis` 黑名单 + `revoke_token()`；`verify_token` 命中黑名单即拒绝；`logout` 路由提取令牌并拉黑 |
| **A4** 登录无爆破防护 | `auth.py` + `api/auth_routes.py` | 模块级 `_login_fails/_login_locked`；`AuthManager.login_allowed/note_login_failure/note_login_success`；login 路由按 **IP + 用户名双维度**限流（5 次/5 分钟 → 锁定 30 分钟，返回 429）；新增 `_client_ip()`（优先 `X-Forwarded-For`） |
| **M6** MCP Token 可经 URL 传递 | `mcp_auth.py` | URL 参数取 Token **默认关闭**；需显式 `MCP_ALLOW_URL_TOKEN=1` 才开启 |
| **AC1** ACP Token 可经 URL 传递 | `acp_auth.py` | 同上，开关 `ACP_ALLOW_URL_TOKEN` |
| **O8** debug 端点回完整 traceback | `api/debug_routes.py` | 新增 `_tb()`：仅 `MA_DEBUG_TRACEBACK=1` 时附栈，生产默认只回异常摘要（3 处） |

> **设计要点**：`app.AuthMiddleware._authenticate` 每次请求都 `new AuthManager`，因此撤销/限流状态**必须模块级**才能跨请求共享（挂在实例上会失效）。均为进程内状态，重启清零。

---

## 二、验证记录（NAS 实测）

| 项 | 方法 | 结果 |
|---|---|---|
| 语法 | `python -m py_compile`（5 文件） | ✅ PY_COMPILE_OK |
| Token 含 jti/iat | 解码实测 | ✅ `token_has_jti:True has_iat:True` |
| 撤销生效（进程内） | `verify_token` 前后 | ✅ True → False |
| 限流序列（进程内） | 假 IP/用户名 | ✅ `[T,T,T,T,T,F]` |
| URL Token 默认关 | `_extract_token(query: token=…)` | ✅ MCP/ACP 均返回 `''` |
| **登出撤销（HTTP E2E）** | `me`→`logout`→`me` | ✅ `200 → 200 → 401` |
| **登录限流（HTTP E2E）** | 假 IP/用户名 ×6 | ✅ `401×5 → 429` |

**未受影响**：正常 Bearer/Cookie 登录不受影响（仅追加 jti/iat，旧 Token 无 jti 仍有效）；`/mcp`、`/acp` 的 Bearer 头鉴权逻辑不变，仅去掉 URL 兜底；Node-RED Basic Auth、butler/app/arena/device 令牌链路未动。

---

## 三、已知风险 / 注意事项

1. **撤销/限流为进程内状态**：容器重启清零（Token 最长 7 天）。如需强一致需迁移 DB/Redis。
2. **`X-Forwarded-For` 可被伪造**：直连时攻击者可伪造 XFF 绕过 **IP 维度**限流，但**用户名维度锁定**仍生效（锁定该账号 30 分钟）。若部署在可信反代（Caddy）之后则无此问题。
3. **`MA_DEBUG_TRACEBACK` 未设**：debug 端点默认只回异常摘要；排障时可在 `.env` 临时置 1。
4. **部署观察**：`docker restart` 后应用有一段时间未就绪（lifespan 启动做 HA 采集/对账），期间端口短暂 000，属正常，非本次改动引起。

---

## 四、后续（本次未做）

- **Phase 2**：I1 `get_behavior_insights` 64s 冷启动（窗口缓存 / 并发 climate_sessions）、S1 `list_members` N+1、M2 `MCP_SLOW_MS` 阈值分级
- **Phase 3**：宽泛 `except Exception`（~70 处）、`print→logging`、大文件拆分（insights 3733 / store 2565 / mcp_server 1904）、AR1 config 更新鉴权收紧、文档补全

---

## 五、合并影响

- 5 个文件、纯加固；**无 API 签名变更、无 DB schema 变更**。
- 对外可见变化：登出后旧 Token 立即失效（预期）；登录失败 5 次返回 429；`/mcp`、`/acp` 不再接受 `?token=`（默认）。
