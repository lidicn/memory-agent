# memory-agent 审计交付包

审计对象：https://github.com/lidicn/memory-agent（main 分支）
审计方式：沙箱实测（真装依赖、真跑测试、真起服务、真复现），非静态阅读
审计日期：2026-09-30

## 文件说明

| 文件 | 内容 |
|---|---|
| 01-第一轮审计报告.md | 首轮审计：P0 功能性失效、P1 稳定性缺陷、CI 失效等 |
| 02-第二轮审计报告.md | 深度审计：绞杀者模式迁移断裂根因（本轮核心发现） |
| 03-全量测试失败清单.txt | pytest 全量运行失败/错误清单（84 项） |
| 复现脚本/ | 每个缺陷的独立复现脚本，可自行重跑 |

## 复现脚本对照表

| 脚本 | 对应缺陷 |
|---|---|
| rt_verify.py | ★核心：真实运行时验证 10/10 关键方法缺失 |
| contract_scan.py | 生产 vs legacy 方法比对（37 个缺失） |
| scan2.py | 缺失方法的生产调用点（10 处） |
| tmpl_test.py | 模板渲染路径 AttributeError |
| fix_verify.py | _tags_of A/B 对照实验（决定性证据） |
| repro4.py | P0-1 参数顺序对照实验 |
| repro6.py | P1-1 announcer 冷却哨兵 |
| repro7.py | P1-1 规则引擎冷却哨兵 |
| e2e.py | 真实 HTTP 端到端 |
| conc.py | SQLite 并发压测 |

## 运行方式

```bash
python3.11 -m venv venv && source venv/bin/activate
pip install pydantic starlette uvicorn python-dotenv redis httpx bcrypt \
    "python-jose[cryptography]" itsdangerous pymysql paho-mqtt mcp pytest pytest-asyncio
export PYTHONPATH=<repo>/src
python 复现脚本/rt_verify.py
```

## 最高优先级修复建议

把 `src/memory_agent/insights/__init__.py` 的 `InsightService` 切回 legacy 实现
（Phase 3 未完成前）。一行改动，可恢复行为推断 / 模板分析 / 实体名兜底 / 缓存四条功能线。
