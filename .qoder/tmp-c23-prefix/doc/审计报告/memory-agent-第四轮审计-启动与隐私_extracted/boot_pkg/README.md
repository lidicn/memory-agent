# memory-agent 第四轮审计：启动性能与数据隐私

审计日期：2026-10-01
触发问题：①MA 启动总要卡很久，是不是数据库太大？②GitHub 上不会有我的数据库吧？

## 结论速览

### 启动慢：数据库只占约 10%

| 阶段 | 耗时 | 与 DB 相关 |
|---|---|---|
| import mcp（第三方 SDK） | ≈10.7 s | ❌ |
| import mcp_server 自身 | ≈2.5 s | ❌ |
| identity_reconciler.reconcile | ≈5.1 s | ❌（取决于 HA 可达性） |
| check_and_recover (integrity_check) | ≈2.1 s | ✅ 线性相关 |
| AppRuntime.__init__ 其余 | 0.07 s | ❌ |
| **合计** | **≈20.5 s** | |

**清空数据库也要卡十几秒**——大头是启动期强制导入 MCP SDK。

### 数据库不会上 GitHub：已实测确认

- 仓库内 .db/.sqlite 文件：**0 个**
- `git check-ignore data/memory_agent.db` → 被忽略 ✓
- `git check-ignore data/config.json` → 被忽略 ✓（含明文密钥）
- `git check-ignore .env` / `certs/x.pem` → 被忽略 ✓
- deploy_nas.sh 用 `git checkout -f origin/main -- .` 只动被跟踪文件

⚠️ 但 deploy_nas.sh 的备份 `--exclude='./data'` **排除了数据库**——
名为备份，实际只能回滚代码，恢复不了数据。

## 三个待修项

1. **P0-10** `app.py:43` 模块级强制 `from . import mcp_server` → 改延迟导入，省 ≈13 s
2. **P0-11** `store.py:535` 每次启动 `PRAGMA integrity_check` 全文件校验 → 改 quick_check 或加开关
3. **P1-12** reconcile 启动期 await 阻塞 → 后台化 + HA 短超时

## 复现脚本

| 脚本 | 用途 |
|---|---|
| startup_breakdown.py | 真实 AppRuntime 启动逐阶段计时 |
| scale_curve.py | integrity_check 随 DB 体积的耗时曲线 |
| rec_scale2.py | reconcile 随数据量变化（证明与 DB 无关） |
| mk30d.py | 构造 30 天真实规模测试库 |

## 运行前置

```bash
export PYTHONPATH=<repo>/src
python 复现脚本/mk30d.py            # 生成 /tmp/ma30.db（104MB / 30万行）
python 复现脚本/startup_breakdown.py
python 复现脚本/scale_curve.py
```

注意：大数据测试库必须在 /tmp 生成，/data/workspace 只有 500M。

## 环境

```bash
python3.11 -m venv venv && source venv/bin/activate
pip install pydantic starlette uvicorn python-dotenv redis httpx bcrypt \
    "python-jose[cryptography]" itsdangerous pymysql paho-mqtt mcp pytest pytest-asyncio
```

> 耗时为沙箱 CPU 实测，你的机器绝对值会有差异，
> 但各阶段比例关系与「是否与数据库相关」的结论成立。
> 建议先在你的容器内跑 `python -c "import mcp"` 计时确认。
