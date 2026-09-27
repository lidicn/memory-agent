# 交接卡：审计 P0 安全加固（Phase 0）

- 工单：PROJECT-20260914-001（FFL 全面审计）· Phase 0 实施
- 日期：2026-09-14
- 范围：安全类 P0（CRITICAL），只读审计后首批修复
- 部署：已 scp 至 NAS `/vol1/1000/docker/memory-agent/src/memory_agent/` 并 `docker restart memory-agent`，实测通过

---

## 一、改动清单（7 文件）

| 问题 | 文件 | 改动 | 行为变化 |
|---|---|---|---|
| **C1** JWT 默认密钥可预测 | `config.py` | 新增 `DEFAULT_JWT_SECRET` 常量；`get_config()` 末尾检测密钥为空或等于默认值时，`secrets.token_urlsafe(48)` 生成强随机密钥并 `config.save()` 持久化 | 仅在密钥为默认/空时触发（一次性）。**本部署密钥非默认，行为完全不变** |
| **A1** 首注册即 admin | `auth.py` + `api/auth_routes.py` | 新增 `_seed_initial_admin()`（读 `INIT_ADMIN_USER`/`INIT_ADMIN_PASS` 种子化）；`register` 路由：已有账号时要求**管理员令牌**方可新增用户，否则 403；新增 `_bearer_or_cookie_token()`（因 register 属 PUBLIC_PREFIXES，中间件不写 `state.user`，故路由内自行解析 JWT） | 系统初始化后**公开注册关闭**；引导期（无账号）首注册仍为 admin；新注册用户 `is_admin:false` |
| **O2** 命令注入 | `api/system_routes.py` | 移除 `shell=True`，改 `shlex.split()` + `Popen(argv)` | `restart_cmd` **不再支持 shell 语法**（`&&`/`|`/重定向）；复合命令须写成脚本。本部署 `restart_cmd=''`，无影响 |
| **O1** SQL 注入 | `backup.py` | `VACUUM INTO '{dest}'` → `VACUUM INTO ?` 参数绑定 + 路径含引号/NUL 校验 | 无（备份结果一致） |
| **AP1** traceback 泄露 | `app.py` | 全局 500 响应体移除 `traceback` 字段（完整栈仍写日志与 `/data/last_traceback.txt`） | 客户端不再收到内部栈；排查走日志/落盘 |
| **A3** 裸 `except:` | `auth.py` | `_load_users` 裸 except → `except Exception as exc` + `_LOG.warning` | 无（异常不再吞 `KeyboardInterrupt`） |
| **O3** 裸 `except:` | `ha_client.py` | 2 处裸 except → `except Exception:` | 无 |

> 顺带补注释：`auth.py register` 处说明"引导期首注册=admin + 路由层已收紧"的安全语义。

---

## 二、验证记录（NAS 容器内实测）

| 项 | 命令/方法 | 结果 |
|---|---|---|
| 语法 | `python -m py_compile`（7 文件） | ✅ PY_COMPILE_OK；lint 0 |
| 密钥未重生 | `get_config().jwt_secret` | ✅ len=34，`is_default=False`（Guard 为 no-op） |
| 未认证注册 | `POST /api/auth/register`（无 token） | ✅ `HTTP 403` `系统已初始化，注册已关闭…` |
| 管理员注册 | 同上 + `Authorization: Bearer <admin JWT>` | ✅ `HTTP 200 ok:true is_admin:false` |
| 清理临时用户 | `POST /api/users/delete` + 复查 | ✅ 已删除，`zz_tmp_exists: False` |
| 备份参数绑定 | `BackupManager(...).run_once()` | ✅ `ok:true`，写 `/data/backups/ma-20260914.db`（容器 sqlite 支持 `VACUUM INTO ?`） |
| 命令注入分支 | `shlex.split('docker compose restart')` | ✅ `['docker','compose','restart']` |
| traceback 脱敏 | 远端 `app.py:617` | ✅ 响应体仅 `{"ok":false,"error":"internal_server_error"}` |
| 裸 except 清除 | `grep 'except:' ha_client.py` | ✅ 无 |
| 链路未破坏 | 容器重启后 Identity 对账（626 设备/合并 277）、采集（1074 条/17.9s）正常 | ✅ |

**未受影响**：Node-RED Basic Auth、butler/app/arena/device 令牌白名单链路均未改动；JWT 密钥未变 → 现有登录态不失效。

---

## 三、已知风险 / 注意事项

1. **`restart_cmd` 语义收窄**：若某部署原用它写复合 shell 命令（`docker compose restart && ...`），升级后不再生效——需改为「脚本路径 + 参数」。本部署为空，无影响。
2. **`INIT_ADMIN_*` 未配置**：种子化逻辑静默跳过，无副作用。
3. **密钥自动生成**：仅当配置为空/默认时触发，会令既有 token 失效（强制重登录）——属预期；本部署不触发。
4. 备份路径校验为"双保险"，主防护是参数绑定。

---

## 四、后续（本次未做，建议逐项排期）

- **Phase 1**：A2 JWT 撤销（jti + 黑名单）、A4 登录暴力破解防护、M6/AC1 URL token 移除、AP1 之外的 O8（debug 端点 traceback）
- **Phase 2**：I1 `get_behavior_insights` 64s 冷启动（缓存/并发）、S1 N+1 查询、M2 `MCP_SLOW_MS` 阈值分级
- **Phase 3**：质量债——宽泛 `except Exception`（insights/store/runtime 约 70 处）、`print`→`logging`、`insights.py`/`store.py`/`mcp_server.py` 拆分、AR1 config 更新鉴权收紧、文档补全

---

## 五、合并影响

- 7 个文件、纯加固；**无 API 签名变更、无 DB schema 变更**。
- 唯一对外可见变化：`/api/auth/register` 在已初始化系统中对未认证/非管理员返回 403（前端引导逻辑基于 `/api/auth/status` 的 `initialized`，二者一致，无需前端改动）。
