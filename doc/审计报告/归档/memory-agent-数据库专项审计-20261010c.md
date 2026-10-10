# memory-agent 数据库专项审计

> 审计对象：`E:\NAS\memory-agent` @ commit `8f493a4`
> 审计日期：2026-10-10（第 3 轮，主题＝数据库）
> 审计方式：逐函数阅读 + 全仓 grep 计数
> 前置约束：第 1 轮因信静态指纹产生 6 项误报（已归档）。本轮只报**已读函数体确认**者，并显式标注严重性与触发前提。

---

## 范围与置信度

| 发现 | 已读函数体 | 严重性 | 触发前提 |
|---|---|---|---|
| D1 `_conn` vs `connect()` 双轨制 | 是 | 低 | close() 后仍有调用 |
| D2 `transaction()` 封装几乎未采纳 | 是 | 低 | 写方法抛异常时无 rollback |
| D3 `connect()` 无 `busy_timeout` 之外的忙等退避 | 是 | 低 | 多进程/多连接并发 |
| D4（已确认无问题）WAL + synchronous=FULL + 锁 | 是 | — | — |

**本轮未发现 P0/P1 级数据库缺陷。** 历史反复损坏的根因（synchronous=NORMAL）已在 `e85febd` 修复，本轮复核确认修复到位。

---

## D4（先说不问题的）· 数据库损坏相关的既有修复复核 ✅

`store.py:640-657` `connect()`：

```python
conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=FULL")   # ← 崩溃不丢事务
conn.execute("PRAGMA foreign_keys=ON")
```

`store.py:659-668` `checkpoint()` 与 `store.py:1969-1987` `close()` 均在 `self._lock` 内执行 `PRAGMA wal_checkpoint(TRUNCATE)`，且 `close()` 内部已把失败改为 `logging.warning`（commit `8f493a4`）。

`store.py:4612-4658` `purge_old()` 采用「分批删除 + 每批提交」，注释明确记录 P0-7（原单条 DELETE 持锁 143 秒）已修复；VACUUM 仅在删除量 ≥ 50000 时触发，且单独 `with self._lock`（**未包在事务内**——正确，SQLite 要求 VACUUM 不能在事务中执行）。

**结论：损坏相关的三处根因修复（FULL / 关前 checkpoint / 定期 checkpoint）经复核到位，无新发现。**

---

## D1 · 连接访问双轨制：`self._conn` 直用 24 处 vs `self.connect()`

**计数证据**（全仓 grep）：

```
self._conn.(execute|executemany|commit)   出现 24 处
with self.transaction()                    出现  2 处（仅 4970 / 5390 行）
```

`self._conn` 在 `__init__`（`store.py:636`）初始化为 `None`，只有 `connect()` 才真正建立连接。而 `close()`（`store.py:1979-1987`）在收尾时执行 `self._conn = None`。

直接使用 `self._conn` 的方法（示例，均已读函数体）：

```python
# list_logical_devices, store.py:1778
with self._lock:
    rows = self._conn.execute("SELECT * FROM logical_devices ORDER BY stable_id").fetchall()
```

`get_logical_device` / `upsert_logical_device` / `delete_logical_device` / `upsert_device_health` / `get_device_health` / `get_arena_snapshot` / `list_arena_snapshots` / `add_arena_result` / `get_arena_result` / `get_arena_analytics` 等同类。

对比使用安全封装者（`store.py:699`）：

```python
def db_query(self, sql, params=()):
    with self._db() as conn:   # _db() 内部 = with self._lock: yield self.connect()
        ...
```

**实际影响（如实）**：`connect()` 是懒加载 + 幂等；生产启动时 `runtime.startup()` 会先调 `check_and_recover()` → `init_schema()`，两者都走 `self._db()`/`connect()`，因此**运行期 `_conn` 必然非 None**，24 处直用不会崩。

**真正的暴露点**：`close()` 之后。若 shutdown 完成后仍有代码路径调用上述任一方法（例如进程退出竞态、或未来某处 `reload_config` 之后误用），将抛 `AttributeError: NoneType object has no attribute execute`，而不是像 `connect()` 路径那样自愈重连。

**为何定级为低**：正常关停顺序下不会再调用；这是**一致性/健壮性隐患**，非当前活跃 bug。

**建议**：统一为 `with self._db() as conn:` 或 `with self.transaction() as conn:`，消除双轨。

---

## D2 · `transaction()` 统一入口几乎未被采纳

`store.py:676-697` 定义了一个设计良好的上下文管理器：

```python
@contextmanager
def transaction(self):
    conn = self.connect()
    with self._lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception as rb_exc:
                logging.getLogger(__name__).warning("[Store] 事务回滚失败: %s", rb_exc)
            raise
```

注释称其为「第六轮审计 CRITICAL-2 的统一入口」，但全仓**仅 2 处**采纳（4970 / 5390），其余写方法仍手写：

```python
# upsert_device_health, store.py:1886
with self._lock:
    cur = self._conn.execute("SELECT * FROM device_health WHERE entity_id=?", ...)
    ...
    self._conn.execute("INSERT INTO device_health ... ON CONFLICT ...")
    self._conn.commit()      # ← 若上面的 INSERT 抛异常，无 rollback
```

**实际影响（如实）**：`with self._lock` 保证了串行化（这是关键正确性，已具备）；缺的只是**异常时的 rollback**。在 Python sqlite3 默认 `isolation_level=""`（隐式 BEGIN）下，语句失败后事务可能保持打开，下一次同连接 `commit()` 可能提交一个「半截」事务。

**为何定级为低**：这些方法多为单条 INSERT/UPDATE + 紧随 commit，失败点少；且 `ON CONFLICT` 幂等。真正的多语句事务方法（4970/5390）已经用了 `transaction()`。属**封装采纳不彻底**，非活跃数据损坏源。

**建议**：把多语句写方法逐步迁到 `with self.transaction() as conn:`，或至少在 `except` 里补 `conn.rollback()`。

---

## D3 · 忙等退避仅靠 `timeout=30.0`

`connect()` 设了 `timeout=30.0`（SQLite busy timeout）。项目为单进程单连接 + RLock 设计，理论无 SQLITE_BUSY。但：

- `reload_config()` 会重建部分客户端但**不重连 store**；
- 备份任务用 `VACUUM INTO` 到独立文件（不经本连接）；
- 若外部工具（备份脚本、运维 sqlite3 CLI）持锁，本连接会忙等 30 秒后抛 `OperationalError`。

**影响**：低。仅在外部分进程访问同一 db 文件时出现。**未实测**。

---

## 建议汇总

| 优先级 | 动作 | 对应 |
|---|---|---|
| 🟡 中 | 统一连接访问：24 处 `self._conn.` → `with self._db() as conn` | D1 |
| 🟡 中 | 多语句写方法迁到 `with self.transaction() as conn` | D2 |
| 🟢 低 | 运维侧避免外部进程直接打开 db（改用 `VACUUM INTO` 快照） | D3 |

---

## 诚实声明

- 未执行任何写操作于源码；未连接真实数据库；未跑 SQL。
- 计数（24 / 2）来自 grep，**方法名清单为抽样**，未逐一列出全部 24 处。
- D1 的「close() 后崩溃」为**基于代码路径的推断**，未构造复现；生产正常关停顺序下不触发。
- D2 的「半截事务被下次 commit 提交」依赖 Python sqlite3 隐式 BEGIN 行为，**未实测**。
- D4 确认的三处修复为**只读复核**，未重放损坏场景验证。

*报告生成：2026-10-10 · 第 3 轮数据库专项审计 · 生成者：Agent*