# memory-agent 第六轮审计：长跑稳定性

审计日期：2026-10-02
方法论：sota-async-concurrency skill（rules/07-audit-bug-catalog，AUDIT 模式）+ 沙箱实测
方向（自主选定）：重试风暴 / 惊群 / 幂等与重复执行 / 资源增长

## 两个 CRITICAL

### 1. 幂等键 TOCTOU —— 并发下写工具重复执行
mcp_server.py:904-930 分发层是 check-then-act，CHECK 与 SAVE 之间横跨 await：

| 工具耗时 | 并发同 key | 实际执行 |
|---|---|---|
| 0ms | 5 | 1 ✓ |
| 50ms | 5 | **5** ✗ |
| 300ms | 5 | **5** ✗ |
| 1s | 5 | **5** ✗ |

工具耗时 >0 即全部穿透；幂等只在串行下成立。
影响 report_bug / save_agent_memory 等写工具。
加剧：store.get_idempotency(2441) 不持锁，save_idempotency(2454) 持锁 —— 读写保护不对称。

### 2. 18 处绕过 _lock 直连共享连接 —— 事务原子性破坏
store.connect() 返回单例连接，但 18 个调用点直接 conn.execute() 不持锁。
实测：线程A 持锁跑多语句事务，线程B 无锁插入并提交 →
最终表 ['A_step1', 'B_rogue', 'A_step2']，**B 的 commit 提交了 A 未完成的事务**。

生产可达：causal_scanner.scan 经 runtime.py:370 的 to_thread 在工作线程跑，
与 API 的持锁 db_query 真并发。

分布：rule_engine 7、candidate_promotion 4、task_record 2、mcp_server 2、
causal_scanner / signal_learning / behavior_routes 各 1

## MEDIUM
- 幂等 TTL 用 %Y-%m-%d 存 → 承诺 24h 实际 24.5~48h；ttl=1/2/12 全塌陷为同一天
- LLM 重试 `for _ in range(2)` 无退避无 jitter → Provider 不健康时负载翻倍
- 视觉取帧退避固定线性无 jitter（仅 2 次，影响小）

## LOW（可忽略，不建议优先修）
_hour_calls 无界增长，实测年增 0.1 MB

## 已排除的误报
tv_service.py:168 / vision_service.py:241 的 time.sleep 均在同步函数内且已 to_thread 卸载；
mqtt_bridge reconnect_delay_set(5,60) 是合格退避。

## 复现脚本
| 脚本 | 用途 |
|---|---|
| idem_toctou2.py | ★CRITICAL-1 并发幂等穿透 |
| lock_bypass.py | ★CRITICAL-2 事务原子性破坏 |
| idem_ttl.py | TTL 日期粒度偏差 |
| leak_growth.py | 运行态 dict 定界 |
| mk30d.py | 构造 30 天测试库 |

另附 sota-async-concurrency.md（skill 正文）与 r_07-audit-bug-catalog.md（bug 目录）。

## 运行
```bash
export PYTHONPATH=<repo>/src
python 复现脚本/mk30d.py          # 沙箱重建会清空 /tmp
python 复现脚本/lock_bypass.py    # 独立，不需建库
python 复现脚本/idem_toctou2.py
```
