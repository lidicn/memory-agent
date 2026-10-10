# memory-agent FTS5 与检索正确性专项审计（R5）

> 审计对象：`E:\NAS\memory-agent` @ commit `8f493a4`
> 审计日期：2026-10-10（第 5 轮 · R5）
> 审计范围：`agent_memory.retrieve`（agent_memory.py:563-666）与 `store.search_agent_memories_fts`（store.py:4918-4950）
> 前置：R1 曾因信静态指纹误报（已归档）；R4 已确认 FTS 建表/触发/重建路径闭合。本轮聚焦**检索语义**。

---

## 检索链路（已读代码）

    retrieve():
      第一路 向量: chroma col.query(where={state:live, member_id, source?, trust?}, n_results=k)
                    sim = 1/(1+dist)
      第二路 FTS : store.search_agent_memories_fts(question, limit=k, state="live")
                    结果层再按 source/member/trust 过滤
      融合       : final = 0.6*sim + 0.25*fts + 0.15*((trust+1)/2)
      as_of 过滤 : 逐条 get_agent_memory 查 valid_from/valid_to
      排序截断   : sort(-final)[:top_k]

k = agent_retrieve_k（默认 20，上限 50）；top_k 默认 5。

---

## 结论速览

| 编号 | 发现 | 已读代码 | 严重性 | 触发前提 |
|---|---|---|---|---|
| R5-1 | FTS 查询整体 phrase 包裹，长问句几乎恒不命中 | 是 | 中 | 自然语言问句检索 |
| R5-2 | fts 是二元标志，bm25 rank 查出却未用于打分 | 是 | 中 | FTS 命中多条时 |
| R5-3 | FTS 结果层过滤在 LIMIT k 之后，可致召回不足 | 是 | 中 | member/source/trust 过滤掉前 k 条 |
| R5-4 | as_of 时间过滤逐条 DB 查询（N+1） | 是 | 低 | as_of 非空 |

未发现会导致错误记忆被返回或数据损坏的问题。以上均影响**召回率/排序质量**。

---## R5-1 · FTS 查询整体 phrase 包裹，长问句几乎恒不命中

证据（store.py:4924-4931）：q 取 query.strip()，再用双引号包成 phrase（内部双引号翻倍转义），作为 MATCH 参数。

问题：调用方传入的是整句自然语言问题（agent_memory.py:612），如「客厅的灯昨天开了多久」。整句被包成一个 phrase 查询，FTS5 要求短语内连续相邻匹配；中文分词后整串相邻组合在记忆中几乎不可能连续出现。

结果：FTS 路对自然语言问句几乎恒返空，只有恰好整句原样入库的记忆才命中。注释自述的「专名/设备名/房间名补充召回」基本失效。

建议：长问句改对分词后的 OR 连接；或 phrase 仅对短查询（实体名）启用。phrase 包裹本身是有效的 MATCH 注入防护，问题在粒度。

---

## R5-2 · fts 退化为二元标志，bm25 rank 被丢弃

证据：SQL 层（store.py:4937-4941）算了 bm25 并 ORDER BY rank；但 retrieve 侧（agent_memory.py:636）只置 merged[mid].fts = 1.0，rank 未参与打分。融合公式 final = 0.6*sim + 0.25*fts + 0.15*((trust+1)/2)。

问题：FTS 命中任何一条 fts 一律 =1.0，第 1 名与第 20 名打分完全等价，SQL 已算好的 bm25 被浪费。

建议：bm25 rank 归一化后（如 1/(1+rank)）代入 fts 分量。

---
## R5-3 · FTS 结果层过滤在 LIMIT k 之后，可致召回不足

证据：SQL（store.py:4943-4946）先 LIMIT ?（=k）返回，之后才在 Python 侧（agent_memory.py:614-629）按 source / member_id / trust_min 逐条 continue 过滤。

问题：过滤在截断之后。若前 k 条恰好都属于别的成员/别的来源/低信任，被 continue 掉后，本应能补上的第 k+1…条永远拿不到——FTS 路召回数可能远低于 k，甚至归零。

对比：向量路（第一路）把 member_id/source/trust 都下推到 chroma 的 where（agent_memory.py:576-587），过滤在候选集生成前，无此问题。两路过滤时机不一致。

根因：agent_memory.py:624 注释称 member_id 过滤 SQL 层不支持，退到结果层——但 member_id 是 agent_memories 表的列，完全可在 SQL WHERE a.state=? 里一并加 AND a.member_id=?。真正无法下推的只有 chroma 侧镜像。

建议：把 member_id/source/trust 下推到 search_agent_memories_fts 的 SQL WHERE，LIMIT 前过滤。

---

## R5-4 · as_of 时间过滤逐条 DB 查询（N+1）

证据（agent_memory.py:659-666）：as_of 非空时，对 scored 每条调 get_agent_memory(memory_id) 单独查库，再用 valid_from/valid_to 过滤，之后才 sort。

问题：scored 最多 k(≤50) 条却对每条一次带锁 DB 往返（get_agent_memory 走 self.connect()+锁），多线程下与写事务争 store._lock。

影响：低。k 上限 50，量级不大。

建议：改批量 WHERE memory_id IN (...) 一次取回。

---

## 复核确认正确的点

- phrase 双引号转义：q.replace(双引号, 双引号双写) 是 FTS5 phrase 内的正确转义，确实防住 MATCH 注入。设计意图对，问题在粒度（R5-1）。
- 向量路 where 下推：member_id/source/trust_min/state 全部下推 chroma，用 $and 组合。正确。
- member_id fail-closed：空 member_id 时向量路加 {member_id:""}、FTS 路 != "" 则 continue——只返回公共记忆，不泄漏成员私有。安全设计正确。
- 融合权重 0.6/0.25/0.15 之和=1.0，trust 用 (trust+1)/2 归一到 [0,1]。量纲正确。

---

## 建议汇总

| 优先级 | 动作 | 对应 |
|---|---|---|
| 中 | 长问句 FTS 改 OR 分词（短查询保留 phrase） | R5-1 |
| 中 | bm25 rank 归一化后代入 fts 分量，替代二元标志 | R5-2 |
| 中 | member_id/source/trust 下推 SQL WHERE，LIMIT 前过滤 | R5-3 |
| 低 | as_of 改为批量 IN 查询 | R5-4 |

---

## 诚实声明

- 未执行任何写操作于源码；未连真库、未跑 SQL、未实测检索。
- R5-1「几乎恒不命中」为基于 FTS5 phrase 语义的推断，未在本项目 SQLite 构建上用真实问句实测。
- R5-2/R5-3/R5-4 为读代码确认的事实（rank 被丢弃、过滤在 LIMIT 后、逐条查询），非推断；实际影响幅度未量化。
- 未审计向量路 chroma where 语法细节与 dist 归一化准确性。

*报告生成：2026-10-10 · 第 5 轮 R5 FTS/检索审计 · 生成者：Agent*
