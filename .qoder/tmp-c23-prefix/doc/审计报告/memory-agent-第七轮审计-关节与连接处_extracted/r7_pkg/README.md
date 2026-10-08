# memory-agent 第七轮审计：广度探测 —— 关节与连接处

审计日期：2026-10-02
方法论：sota-async-concurrency skill（AUDIT 模式）+ 沙箱实测
视角：不看单个模块内部，看**模块之间的接缝**

## 六个发现，全部落在接缝上

### 🔴 CRITICAL-1 配置热更新漏 5 个组件
reload_config() 更新了 17 处，但 insights/ha_assist/semantic_dedup/backup/
activity/researcher 从未更新。实测 5/7 组件仍持旧配置对象。
根因：activity_inference.py 与 researcher.py 用 `self.config = runtime.config` 快照引用。
后果：用户改 HA 地址/令牌后，部分功能仍打向旧地址。

### 🔴 CRITICAL-2 Chroma 首次失败永久缓存
history.py 的 _chroma_tried/_chroma_error：首次连接失败后所有调用直接返回 None，
不再重试。唯一重置是 reload 时 chroma 配置变化，或重启进程。
docker compose 中 MA 与 chroma 并列启动，顺序不保证 → 首次竞争即永久降级。

### 🔴 HIGH 时区 × 热更新 交叉错位（两个缺陷叠加）
容器 UTC 16:00-24:00 窗口：

| 容器时钟 | store/identity | activity/researcher |
|---|---|---|
| 16:30 | 2026-10-02 | **2026-10-03** |
| 20:30 | 2026-10-02 | **2026-10-03** |
| 23:30 | 2026-10-02 | **2026-10-03** |

恰是家庭行为采集高峰（北京凌晨 0-8 点）。
reload_config() 特意同步了 store/identity/identity_reconciler 的 tz，却漏了这两个。

### 🟠 HIGH HA 连接不复用
每次 with httpx.Client() 新建：5.10 ms/次 vs 复用 0.54 ms/次，差 10×，
建连固定成本 3.78 ms 占 74%。跨主机/弱网更差，且产生 TIME_WAIT。

### 🟡 MEDIUM 14 处裸 json.loads
89 处中 66 处有保护（74%），遗漏 14 处。
实测：一条脏记录 → 3 条规则只加载 1 条，外层 except pass 静默吞掉。
分布：rule_engine 4、llm_routes 3、learning_store 2、patterns 2、
acp_server/arena/insights_legacy 各 1

### ⚪ LOW 假警报（但掩盖真问题）
runtime.py:664 `await self.llm.close()`，但 close() 是同步 def 返回 None，
await None 抛 TypeError 走进 except → 打印误导性的「关闭 LLM 客户端异常」。
真实隐患是 llm_client.py:124 的 create_task 发后不管，被这行日志盖住。

## ✅ 已验证健康
- 超时链完整：ha_client 每调用显式传 timeout(10/30)，history embedding 用 Client(timeout=30)
- store.close() 经 to_thread 正确调用
- json 保护覆盖率 74% —— 是遗漏而非不知

## 复现脚本
| 脚本 | 用途 |
|---|---|
| tz_cross3.py | ★时区×热更新交叉（三种 UTC 时刻对照） |
| reload_verify.py | CRITICAL-1 七组件对比 |
| chroma_stuck.py | CRITICAL-2 失败缓存卡死 |
| tz_boundary.py | 时区 day 错位边界 |
| http_conn2.py | HA 连接复用开销对照 |
| json_crash.py | 脏 JSON 打断整批 |
| await_sync.py | 同步 close 被 await |
| reload_live.py | 热更新实测 |
| mk30d.py | 构造 30 天测试库 |

## 运行
```bash
export PYTHONPATH=<repo>/src
python 复现脚本/mk30d.py        # 沙箱重建会清空 /tmp
python 复现脚本/tz_cross3.py
python 复现脚本/reload_verify.py
```
