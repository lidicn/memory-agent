# memory-agent 第五轮审计：启动稳定性与并发功能性

审计日期：2026-10-01
方法论：sota-async-concurrency skill（AUDIT 模式）+ 沙箱实测
测试库：/tmp/ma30.db（30 天规模，103.9 MB / events 30 万行）

## 两个 CRITICAL

### 1. 4 个常驻 task 从不取消
startup 创建 11 个，shutdown 只 cancel 7 个。
泄漏：`_retention_task`（跑 purge_old，持全局锁 5.2 秒）、
`_candidate_promotion_task`、`_causal_scan_task`、`_self_diary_task`（每日调 LLM）。

实测：retention 持锁时 shutdown 的 store.close() 被阻塞 2.7 秒。
→ 重启被 docker stop 强杀，DELETE 可能中断；泄漏的 diary task 可能触发付费 LLM 调用。

### 2. 32 处 async 内同步 DB 调用
实测同一 416ms 查询：

| 写法 | 事件循环调度次数 |
|---|---|
| `await asyncio.to_thread(...)` | **71 次** ✓ |
| async 内直接同步（MA 现状） | **9 次** ✗ |

调度能力下降约 87%，期间 WebUI/MCP/ACP/采集请求基本停滞。

**关键**：项目在视觉/电视路径已正确使用 to_thread
（vision_service.py:1121、tv_routes.py:85，且注释写明"避免阻塞事件循环"），
说明是 DB 路径的遗漏，不是不知该模式。

分布：insight_routes 8、mcp_server 5、behavior_routes 5、researcher 4、
collect_routes 3、face_routes 3、identity_routes 3、events_routes 1

## 已排除的误报
tv_service.py:168 与 vision_service.py:241 的 time.sleep 均在同步函数内，
且调用链已由 to_thread 卸载 —— 不需要改。

## 复现脚本

| 脚本 | 用途 |
|---|---|
| final_block2.py | ★核心：同步 vs to_thread 调度对照（71 vs 9） |
| audit_async_db_calls.py | 32 处同步调用精确清单 |
| task_leak.py | 4 个泄漏 task 验证 |
| shutdown_race.py | shutdown 与 retention 竞态（阻塞 2.7s） |
| ast_scan.py / ast_scan2.py | 并发风险 AST 扫描 |
| mk30d.py | 构造 30 天测试库 |

## 运行

```bash
export PYTHONPATH=<repo>/src
python 复现脚本/mk30d.py             # 先建库（沙箱重建会清空 /tmp）
python 复现脚本/final_block2.py
python 复现脚本/audit_async_db_calls.py
```

环境：
```bash
pip install pydantic starlette uvicorn python-dotenv redis httpx bcrypt \
    "python-jose[cryptography]" itsdangerous pymysql paho-mqtt mcp pytest pytest-asyncio
```
