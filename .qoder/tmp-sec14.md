## 十四、2026-10-06 追加（任务表 #61：A2/A7 静态结论核销第三批，容器 run11）

### 这一批干成了什么（三行版）

1. **P4-3 是真缺陷，不是观察项**：`intent_inference.py` 那句 `max(...)` 的 `now` 兜底被一条**非空但解析不出**的
   `server_ts` 触发 ⇒ 窗口基准变成容器墙钟 ⇒ `/api/behaviors/intents` 静默交 `{"intents": []}` 且 `ok:true`；
   非字符串 ts（如 `1700000000`）走第二条通道：过滤循环里再抛 `TypeError`，而两个调用点没有 try/except ⇒ 500。
   已改码（新增 `_event_dt:112`、撤掉墙钟兜底）+ 3 把锁 + N1/N2 变异自咬。
   第十三轮 :108 定级依据的那句"只在所有 ts 都失败时兜底"是**错的**，实测五行成对读数在 §三十九 §二。
2. **三处静态结论变成有读数**：P2-4（顺序不变性，升/降/交错三档同读数）、P2-5（AST 数定义，含 `AnnAssign`；
   旧修复只有注释在守）、P2-8（命名空间不可导入的运行时读数）。
3. **两件呈 DCD、一件核销**：pm4py 商用档位 + 内置词表口径 → `inbox/20261006-MA-pm4py许可档位与内置词表三处口径-决策申请.md`；
   P4-2（`define_activity` 的 `ok`）核销为"前提已过时"——审计引的那句 message 在 HEAD 上已不存在（grep=0），
   规则现在真被套用，既有锁 `tests/test_vma_activity_semantic.py:162` 看守，**不新增出境键**。

### run11 的门读数（口径：容器 `memory-agent` 3.11.16，快照 `/tmp/c37snap20261006a`，基线 HEAD `c3f2719`）

`GATE_RC=0`（pyflakes 0/0/0/0）、`SUITE_RC=0`（**1449 passed / 10 skipped**，307.81s）、
`TARGETED_RC=0`（61）、`PREVBATCH_RC=0`（69，与 run10 同数）、`TOUCHED_RC=0`（338/3 = 277+61）、
`DAYBATCH_RC=0`（src 97 项台账与 run10 一字不动）、`ROUTEBATCH_RC=0`（197/196/0/0）、
`MUT11_RC=0`（N1..N8 全咬，`MUTATION_BAD=0`，`restored_identical=OK`）、
`MUT10_RC=0`（M1..M10 全咬，条数 5/6/5/8/3/2/4/1/1/24 与 run10 逐格相同）。
`TOOLCHAIN_RC=0` 先量到 pytest 9.1.1 / pyflakes 4.0.2 —— run8 那次因为 recreate 清了 `/tmp/pylibs`，
三条门格读成 `No module named` 而被误判"咬住了"，这一格以后每次都排第一。
**本批无 PROBE 格**：不碰 insights 读路径，正式基准沿用 run10（0.67× / 3.28×）。

### 下一个人要知道的三件事

- **在役 ≠ 仓**：`/app/src/memory_agent/` 里 8 个 `learning_*.py` 都还在、`/app` 没有 `attic`。
  attic 搬迁（任务表 #56）与网络隔离（#57）、caddy 端口 9080/9443 都卡在**镜像重烤未获授权**这一格。
  所以任何"线上行为"的判断必须现读容器 `/app`，不能拿仓里的锁顶。
- **量具的形状问题会被读成代码结论**：这批撞的是 UTF-8 BOM——importer 嗅 `utf-8-sig` 所以 pytest 一直绿，
  但 `open(p, encoding="utf-8") + ast.parse` 的门读到 BOM 就 `SyntaxError`，带 `except SyntaxError: continue`
  的扫描器会把该文件从审计覆盖面里**静默摘掉**。和上一批的 CRLF→`PATCH_NOT_FOUND` 同族。
  顺手一件事：全仓 CR 形状在 blob 与工作区之间不一致（`* text=auto eol=lf`，约 40 个在册文件工作区带 CR），
  本批刻意**没有**为此加锁（会带进一批既红），只按交付纪律 §六.9 保证自己写的文件字节级 CR=0。
- **测试会冻住缺陷**：P2-4 那把顺序锁第一版按"活动列表顺序"断言，而标签来自每窗口一个 `set`，
  同频并列时顺序本就不稳定 ⇒ 改成频次字典。凡是断言"集合类返回值的顺序"之前，先确认实现有没有承诺顺序。

### 还开着的格（与本批无关，别混进 #61）

任务表 #40 Q-B 的⑤⑥（13/17 个工具缺同名实现 + Q3-6 对照读数）、#58 Q-D 参数级核对（等 DB/AF 各出一句读键清单）、
#9 token 合并窗、#10 R2 写窗、#38 本居活动清单、#5 裁决跟踪、Q5 区间数字、FTS 短词下限、
active-rules 路径的字面偏离、端口 9080/9443 追认、A7 §5.1 三项覆盖缺口
（`llm_ask(CC=45)` 重端点压测、72h 长跑、`voice_util` 35.1% / `behavior_predictor` 27.8%）。
另：`scripts/hc_check.py` 是未跟踪的临时件（`import json` 未使用），不属于任何台账，请它的主人收编或删掉。

