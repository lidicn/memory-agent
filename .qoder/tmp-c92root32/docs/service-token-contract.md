# 服务令牌（service_token）对接契约 · vMA-1.2.3 3.3.3

依据：`E:\NAS\关键决策部\decisions\20261001-MA-service_token与设备事件feed-裁定.md`（Q1-Q5）。
实现：`src/memory_agent/service_tokens.py` + `src/memory_agent/app.py`（`AuthMiddleware`）。

## 1. 这一版解决了什么

| 裁定 | 落地形态 |
|------|---------|
| Q1=A MA 先做单侧 additive | MA 上签发/校验新令牌，**旧 `BUTLER_TOKEN` / `APP_TOKEN` 照常并行接受**，不设 expiry、不下线旧凭据；DB 侧真正切换只需在它自己的 v2.6 窗口换 env 值 |
| Q2=A 令牌自带路径+方法 | 令牌记录携带 `scopes: ["GET:/api/members", …]`；鉴权按「方法:路径」逐条匹配，**清单为空一律拒绝** |
| Q3=A 一实例一令牌 | 每个外部实例单独签发、单独计数、单独吊销；env 单密钥被**只读导入**成一条可审计记录 |
| Q4=A 只做可吊销+使用计数 | 记录里**没有**过期字段；TTL 与双轨到期日耦合，留到 DB 切换后另议 |
| Q5 保留 source 派生 | 写记忆时来源仍由令牌派生，调用方自报无效（`_resolve_source`）；签发侧另加一道：`source` 必须在 `agent_memory_sources` 白名单内 |

线格式没变（不透明字符串 + `secrets.compare_digest` + sha256 落盘），`homesdk` 侧不需要改任何消费逻辑——它只是把一个字符串放进 `Authorization: Bearer`。

## 2. 线格式

```
svc_<urlsafe-32B>          # 前缀 svc_ 与 app_/mcp_/btl_ 互斥，明文只在签发响应里出现一次
```

## 3. 作用域语法

```
<方法>:<路径>[/*]
GET:/api/members            # 精确匹配
GET:/api/members/*          # 子树（匹配 /api/members/abc123，不匹配 /api/members）
POST:/api/events
```

* 方法只接受 `GET/POST/PATCH/PUT/DELETE`，大小写不敏感；**没有通配方法**。
* 签发时禁止落在特权前缀：`/api/config`、`/api/auth`、`/api/users`、`/api/system`、`/api/debug`。
* 路径含 `..` 一律拒。

## 4. 管理端点（都要求管理员 JWT）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/config/service-tokens` | GET | 列出（含 env 导入记录，字段 `persisted` 区分） |
| `/api/config/service-tokens` | POST | `{name, scopes[], source?}` → 返回一次性明文 |
| `/api/config/service-tokens/{name}` | DELETE | 吊销；env 导入记录返回 400 并说明原因 |

## 5. 遗留凭据的语义（必须读）

`BUTLER_TOKEN` / `APP_TOKEN` 里已有密钥会被**只读导入**成名为 `butler-env` / `app-env` 的记录：

* 它们带该通道现有的方法+路径清单，因此走的就是新校验分支（能被统计、在列表里可见）；
* **不落盘**：鉴权热路径不写 `config.json`；
* **不能在这里吊销**：`app.py` 的遗留分支仍独立接受 env 值。下线旧凭据 = 清 env 并重启，那由 DCD/SP 定到期日；
* 导入记录保留 `butler` / `app` 身份位——`member_routes.py:33` 按 `butler` 判权限，去掉这个映射会在旧凭据下线前就把豆包管家打断。

## 6. 下游迁移（DB / AutoForge）

1. 在 MA Web 管理面（或 `POST /api/config/service-tokens`）为该实例签发一枚，作用域按实际调用逐条列；
2. 把 env 值换成新明文（homesdk 的 `LEGACY_TOKEN_KEYS` 已同时接受 `MEMORY_AGENT_TOKEN` / `MEMORY_AGENT_APP_TOKEN`，不需要改代码）；
3. 观察 `use_count` 涨、旧 `*-env` 记录不再涨，即完成切流；
4. 到期日到了再清 env 并重启 MA。

## 7. 回归锁

`tests/test_service_tokens.py`（22 条）逐条钉住上面各项，其中三条是判据来源：
方法不匹配必须 403、导入记录不得扩权（与 `BUTLER_GET/POST_PATHS` 集合相等）、导入过程不得调用 `Config.save`。
