# memory-agent Schema / 索引 / Trigger 专项审计（R4）

> 审计对象：`E:\NAS\memory-agent` @ commit `8f493a4`
> 审计日期：2026-10-10（第 4 轮 · R4）
> 审计方式：逐条读 DDL 与迁移代码（_SCHEMA_SQL 74-320、init_schema 888-1560、FTS 段 837-935）
> 前置：R1 曾因信静态指纹产生误报（已归档）。本报告每条均标注是否已读代码确认。

---

## 规模基线

```
store.py 5256 行 · 192 函数 · 37 CREATE TABLE · 41 CREATE INDEX
3 CREATE TRIGGER · 21 处 ALTER/迁移 · 57 处 FTS 相关
SCHEMA_VERSION = 1（store.py:31）
```

---

## 结论速览

| 编号 | 发现 | 已读代码 | 严重性 | 触发前提 |
|---|---|---|---|---|
| R4-1 | SCHEMA_VERSION 恒定=1，6+ 代迁移无版本推进 | 是 | 中 | 任何时候 |
| R4-2 | 21 处 ALTER 靠 "duplicate column" 字符串判幂等，他错静默续行 | 是 | 中 | 迁移遇非重复列错误 |
| R4-3 | FTS tokenizer 降级（trigram→unicode61）静默 | 是 | 低-中 | SQLite 无 trigram 编译 |
| R4-4 | detected_activities 索引定义散落在迁移段 | 是 | 低 | — |
| R4-5 | FTS 表初始化路径已确认闭合（非缺陷） | 是 | — | — |

未发现会导致数据损坏的 P0/P1。

---## R4-5（先排除误解）· FTS 虚拟表初始化路径确认闭合 ✅

**疑问**：agent_memories_fts 是 FTS5 虚拟表，但**不在** _SCHEMA_SQL 里，只在 _rebuild_agent_memory_fts（store.py:894）内 CREATE VIRTUAL TABLE。新建库第一次启动，FTS 表从哪来？

**已读代码确认**（store.py:1072-1079，init_schema 内）：

    if self._fts_index_healthy(conn):
        print("[Store] FTS5 关键词索引健康（integrity-check）")
    else:
        _tok = self._rebuild_agent_memory_fts(conn)   # 内部建虚拟表

_fts_index_healthy（store.py:877）对外部内容表执行 FTS5 integrity-check，**表不存在时抛异常返回 False**，触发 _rebuild。重建内 CREATE VIRTUAL TABLE + 三 trigger + VALUES(rebuild) 回填。

**结论：新库首次启动会正确建立 FTS 表与触发器。此路径闭合，非缺陷。**

---

## R4-1 · SCHEMA_VERSION 恒定 = 1，无版本治理

**证据**（store.py:31）：SCHEMA_VERSION = 1
写入点（store.py:955-956）：INSERT OR REPLACE INTO meta(key,value) VALUES(\'schema_version\', ?)

**问题**：迁移段累积了**至少 6 代**结构变更：v0.5 source、v0.8-2 prev_id、v0.8-4 FTS5、v0.9 valid_from/valid_to/observed_at、WO-MA-005 member_id、DCD R1 feedback_question/comment，另有 behavior_events.scene_graph_json、perception_events.event_id、members.avatar_url/appearance_json/face_feature、rule_trigger_history.dry_run/false_positive 等。但 schema_version **永远是 1**。

**后果**：
1. 无法从 meta.schema_version 判断库迁到第几代，运维失去唯一版本锚点。
2. 迁移无法条件分支（if version < N），只能无条件全跑靠吞异常——正是 R4-2 根因。
3. 未来破坏性迁移（改类型/删列/重建表）无版本号无法安全排序。

**建议**：为每次结构变更递增 SCHEMA_VERSION，迁移段改 if current < N: apply_migration_N()。

---
## R4-2 · 迁移靠 "duplicate column" 字符串匹配判幂等，他错静默续行

**证据**（21 处迁移的统一形态，示例 store.py:958-967）：

    try:
        conn.execute("ALTER TABLE agent_memories ADD COLUMN source TEXT NOT NULL DEFAULT \'ma\'")
    except Exception as _exc:
        if "duplicate column" not in str(_exc).lower():
            print(f"[Store] agent_memories.source 列迁移异常: {_exc}")

**问题一：判据脆弱**。用异常**文本**判断"列已存在"。SQLite 该错误稳定为 duplicate column name: xxx，实践可用，但属字符串耦合——依赖 SQLite 版本措辞，非结构化错误码。

**问题二（更实）**：若 ALTER 因**其他原因**失败（磁盘满 disk I/O error、库被锁 database is locked、库损坏 malformed），代码只 print 一行，**不中断、不回滚、继续执行后续迁移**。init_schema 迁移段整体只有 with self._lock，**没有事务包裹**——每句 ALTER 各自隐式提交。后果：库可能停在"部分列已加、部分没加"的中间态，而启动照常继续，直到某处读取缺失列才报错。

**问题三**：21 处里只有 1 处（store.py:1007-1018 perception_events.event_id）注释说明"SQLite 不支持 ADD COLUMN 加 UNIQUE，改两步"，其余无同类防护记录，是否都验证过值得抽查。

**建议**：
1. 迁移段整体包一个事务（或每代迁移一个事务），失败则 rollback + 明确报错，而非 print 续行。
2. 判幂等优先用 PRAGMA table_info(<table>) 查列是否存在（代码已在 1011 行用过此手法），而非捕字符串。

---

## R4-3 · FTS tokenizer 降级静默

**证据**（store.py:900-918）：

    for _tok in ("trigram", "unicode61"):  # trigram 中文子串友好，不支持则退 unicode61
        try:
            ... CREATE VIRTUAL TABLE ... tokenize=\'{_tok}\' ...
            return _tok
        except Exception as exc:
            last_exc = exc
            conn.rollback()
            print(f"[Store] FTS5({_tok}) 重建失败: {exc}")

**问题**：trigram 是 FTS5 对**中文子串检索**唯一友好的内置分词器；unicode61 按 Unicode 类别切词，**中文无空格**，实际会把整段中文当作单 token 或按标点切，中文关键词检索基本失效。

降级是**静默的**：return _tok 把实际生效分词器返回给调用方，但 init_schema（store.py:1075-1079）只 print，**不做告警、不落库、不进健康检查**。生产环境若 SQLite 编译未启用 trigram（部分发行版默认关），中文 FTS 检索会**无声返空**。

**建议**：降级到 unicode61 时写 WARN 级日志，并在 chroma_status/健康端点暴露"当前 FTS tokenizer"。

---

## R4-4 · detected_activities 索引定义分散

**证据**：表在 _SCHEMA_SQL（store.py:134-146）创建，**DDL 内无二级索引**；唯一的 idx_detected_source_rule 在**迁移段**（store.py:1344-1346）才建。

**影响**：低。索引最终会建（IF NOT EXISTS），功能无碍。但 schema 定义与索引定义分处两段，是新维护者排查"这表有哪些索引"时的认知负担。

**建议**：新表的新列与其索引尽量收敛到 _SCHEMA_SQL，迁移段只保留对已发布旧库的补丁。

---

## 复核确认无问题的点 ✅

- events 覆盖索引 idx_events_entity_cover(entity_id, ts, room, domain)：注释记录旧 idx_events_entity_ts 是其严格前缀，已删除避免写放大，实测 12.0s→0.45s。**设计正确**。
- 三个 FTS 触发器（ai/ad/au）形态标准：ad 与 au 的 delete 分支用 INSERT INTO fts(fts, rowid, ...) VALUES(\'delete\', ...)，符合 FTS5 外部内容表规范。**正确**。
- task_records 的 UNIQUE(task_id, period_key)、collect_jobs 的 idx_jobs_created(created_at DESC)：约束与索引均合理。
- _rebuild_agent_memory_fts 重建前 DROP TRIGGER + DROP TABLE，避免残留旧触发器指向新表。**顺序正确**。

---

## 建议汇总

| 优先级 | 动作 | 对应 |
|---|---|---|
| 中 | 迁移段包事务；失败 rollback + 明确报错，不静默续行 | R4-2 |
| 中 | 为每次结构变更递增 SCHEMA_VERSION，迁移改条件式 | R4-1 |
| 低-中 | tokenizer 降级到 unicode61 时 WARN + 健康端点暴露 | R4-3 |
| 低 | 新列/新索引收敛回 _SCHEMA_SQL | R4-4 |

---

## 诚实声明

- 未执行任何写操作于源码；未连真库、未跑 SQL、未实测迁移。
- 未逐条列出全部 21 处迁移，R4-2 为**抽样形态**归纳（示例已注明行号）。
- R4-2 "部分列已加"的中间态为**基于代码路径的推断**，未构造磁盘满/锁库场景复现。
- R4-3 "unicode61 中文失效"为**基于 FTS5 分词器语义的推断**，未在本项目 SQLite 构建上实测中文检索。
- 未审计 37 张表的**全部**列类型与约束（本轮聚焦索引/触发器/迁移，列级约束为抽样）。

*报告生成：2026-10-10 · 第 4 轮 R4 schema/索引/trigger 审计 · 生成者：Agent*
