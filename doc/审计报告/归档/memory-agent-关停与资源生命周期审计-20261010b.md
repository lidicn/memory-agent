# memory-agent 关停与资源生命周期审计

> 审计对象：`E:\NAS\memory-agent` @ commit `8f493a4`
> 审计日期：2026-10-10（第 2 轮）
> 审计方式：**逐函数阅读**（本报告每条发现均标注是否已读函数体确认）
> 前置教训：上一轮把 `.gates-baseline.txt` 静态指纹当真结论，产生 6 项误报。本轮**不接受静态扫描结论**，只报已读代码确认者。

---

## 审计范围与置信度声明

本轮聚焦「进程关停 / 配置热更新 时的资源释放」。

| 发现 | 是否已读函数体 | 置信度 |
|---|---|---|
| F1 chromadb 客户端从不被关闭 | 是 | 高 |
| F2 `reset_chroma()` 只丢引用不关闭 | 是 | 高 |
| F3 `shutdown()` 未释放 `self.history` | 是 | 高 |
| F4 `store.close` 是唯一未独立 guard 的关闭调用 | 是 | 高（但严重性低） |

---

## F1 · `HistoryManager` 没有 `close()`，chromadb 客户端永不显式关闭

**证据**：`src/memory_agent/history.py` 全部方法名（`Select-String -Pattern "def "` 实测）：

```
58  EmbeddingFunction.__init__      137 HistoryManager.__init__
63  EmbeddingFunction.__call__      149 _embedding_function
97  resolve_embedding_function      158 collection / 202 agent_collection
243 chroma_status / 260 embedding_status / 288 chroma_selftest
445 reset_chroma
455 add_events / 464 add_event / 470 _normalize_legacy_event
496 mirror_days / 531 semantic_search / 572 _range / 578 _to_legacy
600 get_person_history / 633 get_all_persons / 636 get_behavior_summary
672 export_history / 690 get_stats / 695 query_range
```

**没有 `close()`，也没有 `__del__`。** 而 `history.py:180` 建立了 `chromadb.HttpClient(host=..., port=...)`。

**影响**：`chromadb.HttpClient` 内部持有 httpx 连接池。该对象在整个进程生命周期内不会被显式释放，只能等 GC。

---

## F2 · `reset_chroma()` 只把引用置 None，不关闭底层客户端

**证据**（`history.py:445-451`）：

```python
def reset_chroma(self) -> None:
    self._client = None
    self._collection = None
    self._chroma_error = ""
    self._chroma_retry_after = 0.0
    self._embed_fn_cache = None
    self._embed_resolved = False
```

全部是赋值重置，**没有任何 `self._client.close()` / `clear_system_cache()`**。

**影响**：旧 `chromadb.HttpClient` 的 httpx 连接池失去唯一强引用后**不会立即关闭**，靠 GC 回收；回收时机不确定，期间 socket 保持占用。

---

## F3 · `shutdown()` 完全未释放 `self.history`（chroma）

**证据**（`runtime.py:877-910`，已完整读取）：

```python
async def shutdown(self) -> None:
    await task_registry.cancel_all()      # 后台任务
    self.mqtt.close()                     # MQTT
    await self.vision.stop()              # 视觉
    await self.collector.stop()           # 采集
    self.llm.close()                      # LLM
    ha_db.close()                         # HA MariaDB
    await asyncio.to_thread(self.store.close)   # SQLite
```

关闭清单里**没有 `self.history`**。

**交叉验证**：全仓搜索 `reset_chroma|history.close|.history.close`，结果只有两行——

```
history.py:445   def reset_chroma(self) -> None:
runtime.py:963   self.history.reset_chroma()
```

即：`history` 相关的释放动作**全仓仅此一处**，且它是 `reset_chroma`（F2 的不完整版本），不是关闭。

**严重性评估（务必如实）**：进程正常退出时，OS 会回收全部 socket，**实际运行影响很小**——这正是它长期未被暴露的原因。它真正构成问题的是「**进程内热更新**」场景（见 F3-b）。

**F3-b（热更新路径）**：`system_routes.py` 有两条重启路径——`_reexec()`（`os.execv`，真重启，旧进程整体消失）与 `reload_config()`（**进程内**重建下游客户端）。`runtime.py:963` 的 `reset_chroma()` 位于 `reload_config()` 内，即**不重启进程**。因此每次「用户修改 chroma 地址或 embedding 端点并热更新」都会让一个旧 httpx 连接池失去管理、等待 GC。

---

## F4 · `store.close` 是 `shutdown()` 中唯一未独立 guard 的关闭调用（低危）

**证据**：`shutdown()` 中 5 个关闭调用各有一个独立 `try/except`（mqtt / vision / collector / llm / ha_db），唯独 `await asyncio.to_thread(self.store.close)` 裸露。

**为什么严重性低**：`store.py:1969-1987` 的 `close()` **内部已自带 try/except**（commit `8f493a4` 已把 checkpoint 失败改为 `logging.warning`），实际不会向上抛。因此这仅是**结构不对称**，不是活跃缺陷。

---

## 建议（按性价比）

| 优先级 | 动作 | 理由 |
|---|---|---|
| 🟡 中 | 给 `HistoryManager` 加 `close()`：`self._client` 非空时尝试关闭并置 None | 补 F1/F2，让 chroma 连接有明确释放路径 |
| 🟡 中 | `reset_chroma()` 在置 None **之前**先尝试关闭旧 client | 消除热更新场景的连接泄漏 |
| 🟢 低 | `shutdown()` 末尾追加 `self.history.close()`（带 try/except） | 对称性；进程退出场景收益有限 |
| 🟢 低 | 给 `store.close` 调用补 try/except | 纯结构对称，非活跃 bug |

---

## 与上一轮报告的关系

- 上一轮 6 项（P0-A/P0-B/P1-D/P1-E/P2-G/P0-C）经核实为**误报或过时**，已在 `doc/审计报告/归档/`。
- 本轮**未使用**静态扫描作为结论依据；F1–F3 均为「读 `history.py` 全方法表 + 读 `runtime.shutdown()` 全文 + 全仓 grep 调用点」三重确认。
- 本轮**未发现 P0/P1 级缺陷**。F1–F3 属**资源生命周期设计不完整**，真实影响集中在「进程内热更新」这一低频路径。

---

## 诚实声明

- 未执行任何写操作于源码；全部为只读阅读与 grep。
- 未实际触发 `reload_config()` 验证连接泄漏量；F3-b 的「每次热更新泄漏一个连接池」为**基于代码路径的推断**，未做实测计数。
- 未评估 `chromadb.HttpClient` 内部是否自带 `__del__`（若第三方库自带，F1 影响会进一步降低）。此点**未验证**，不作为结论。

*报告生成：2026-10-10 · 第 2 轮关停与资源生命周期审计 · 生成者：Agent*