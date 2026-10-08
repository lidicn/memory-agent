# memory-agent 第三轮审计：数据库速度与稳定性

审计日期：2026-10-01
代码基线：src/memory_agent/store.py（4338 行）

## 核心结论

存储层设计本身正确（WAL + RLock + timeout=30.0），并发压测 0 异常。
真正的问题是：**几个 600ms 级的慢查询被放在全局锁内执行**，
把"查询慢"放大成"整个系统停摆"。

| 操作 | 基线 | 有慢查询时 | 退化 |
|---|---|---|---|
| 写入 insert_events | 0.1 ms | P50 657 ms | ≈6570× |
| 快查询 query_events | 0.4 ms | P50 658 ms | ≈1645× |
| 10 秒内写入次数 | ~1000 | 14 | ≈71× |

## 三个 P0

1. **P0-6 慢查询阻塞全系统** — entity_catalog 621ms、query_unified_events 689ms，持全局锁
2. **P0-7 purge_old 冻结 5.2 秒** — 删 50 万行单事务，期间其他查询阻塞 5181ms
3. **P0-8 130.6 MB 空间永不释放** — 删后 198MB 不变，VACUUM 后 67.4MB

## 复现脚本

| 脚本 | 对应结论 |
|---|---|
| perf6.py | ★P0-6 核心证据：同实例锁竞争 |
| perf7.py | P0-7 + P0-8：purge_old 持锁与空间 |
| perf9.py | P1-9 RLock 无超时 + VACUUM 对比 |
| perf4.py | 各真实 Store 方法耗时 |
| perf5.py | 跨实例并发（验证 WAL 正常） |
| perf8.py | FTS / 写入吞吐 / WAL 增长 |
| conc.py | 8 秒高并发压测 |

## 运行前置

脚本依赖测试库 `/tmp/big.db`（197.96 MB / 82 万行）。
若不存在，需先构造：

```bash
export PYTHONPATH=<repo>/src
python - <<'PY'
import sys; sys.path.insert(0,"<repo>/src")
from memory_agent.store import Store
s=Store("/tmp/big.db", tz_offset_hours=8.0); s.init_schema()
# 批量写入 events 50 万 / behavior_events 20 万 / perception_events 10 万
# 然后: sqlite3 /tmp/big.db "ANALYZE"
PY
```

注意：不要用 /data/workspace（仅 500M），大数据测试须在 /tmp 进行。

## 环境

```bash
python3.11 -m venv venv && source venv/bin/activate
pip install pydantic starlette uvicorn python-dotenv redis httpx bcrypt \
    "python-jose[cryptography]" itsdangerous pymysql paho-mqtt mcp pytest pytest-asyncio
```
