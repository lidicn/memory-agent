### 6.20 A2/A7 静态结论核销第三批（任务表 #61，2026-10-06，容器 run11）——P4-3 改码、P2-4/P2-5/P2-8 补锁、P4×3 首次处置

审计锚点：第二轮 :121（P2-4）、第四轮 :40（P2-5）、第九轮 :30（P2-8）、A5 :23（P4-1）、
第十三轮 :54（P4-2）与 :108（P4-3）。引用纪律按 WebUI 件 :58（报告 + 行号）。
完整过程与门读数在 `doc/审计报告/修复与核实/审计核实与修复_20261001.md` §三十九。

**一句话结论**：三处"审计只给静态结论"的项现在都有运行时/AST 读数，其中 **P4-3 的评级被实测推翻**
（审计判 P4 观察项，实测是"任何一条非空坏 ts 就把窗口基准换成容器墙钟"⇒ 静默空结果 + 500 双通道），
本批按真缺陷改码；P4-1（pm4py AGPLv3）与内置词表口径呈 DCD，P4-2 核销为"前提已过时"，
**零新增出境键**。

| 件 | 处置 | 锁 | 变异自咬 |
|---|---|---|---|
| P4-3 | `intent_inference.py` 新增 `_event_dt:112`，撤掉 `datetime.now()` 兜底（该兜底在可达输入集上是死代码） | 3（含 `_NoClock` 时钟无关锁） | N1 / N2 |
| P2-4 | 现读已修（按天 `dict` 分组 + 排序），本批补顺序不变性 | 2（升/降/交错三档同读数，按频次字典比） | N3 |
| P2-5 | 现读 `^KEYWORD_DOMAINS`=1（:1104），但**修复只有注释在守** | 6（AST 数定义含 `AnnAssign` / 英文键 / 值 / ASCII 人口 / 中文不回归 / 同名表内容相等） | N4 / N8 |
| P2-8 | src 0 个 / attic 8 个，命名空间不可导入 | 6（含 8 参数量化 + `build_router` 调用点 = 0） | N5 / N6 |
| BOM | 三个在册 .py 去 U+FEFF（各 −3 字节，CR 不碰） | 3 | N7 |

**run11 权威门**（快照 `/tmp/c37snap20261006a`，371 文件，基线 HEAD `c3f2719`，Python 3.11.16，2026-10-05T18:12→18:25Z）：
GATE_RC=0（pyflakes 0/0/0/0）、SUITE **1449 passed / 10 skipped**（307.81s）、TARGETED 61、
PREVBATCH 69（与 run10 同数）、TOUCHED 338/3（= 277 + 61）、DAYBATCH 与 ROUTEBATCH 两份台账与 run10 **一字不动**
（src 97 = 44 外部 + 43 字面 + 2 config + 6 date_math + 2 local，bounded 42 / unguarded 55 / marked 2；
handler_shaped=197 mounted=196 unmounted=0 problems=0）。
MUT11 N1..N8 与 MUT10 M1..M10 全部咬住，`restored_identical=OK`、两套 `MUTATION_BAD=0`，
M1..M10 的条数 **5/6/5/8/3/2/4/1/1/24** 与 run10 逐格相同 ⇒ 本批没渗到 insights 读路径，
故不重跑 PROBE，正式基准沿用 §6.18.1（0.67× 改前单条 LIMIT / 3.28× 前缀量具）。

**呈 DCD**（`关键决策部/inbox/20261006-MA-pm4py许可档位与内置词表三处口径-决策申请.md`）：
Q1 pm4py 商用档位（甲 维持 + 发版前法务门 / 乙 从 extras 删 / 丙 采购许可；MA 建议甲，
现读 `find_spec("pm4py")` 在 `/app` 与 `/app + /tmp/pylibs` 下均为 None ⇒ 在役运行时不可达）；
Q2 两份 `KEYWORD_DOMAINS`（utils :1104 = 26 键 8 ASCII，parser/entity :35 = 33 键 5 ASCII，
ASCII 交集只有 `ac/light/tv`）合不合（MA 建议"保持两版 + 差异锁住"，改内置词表不在自主范围，任务表 #38 明令）；
Q3 追认 `CATEGORY_DOMAINS` 在 utils.py 顶层的两份（:271 与 :1091）逐键逐值相同 ⇒ 删前一份是否算"改内置词表"。

**一条必须在役面登记的读数**：`ls /app/src/memory_agent/ | grep -c learning_` = **8**、`/app` 无 `attic`
⇒ **attic 搬迁（任务表 #56）目前只在仓里，线上镜像仍是旧布局**。这与 #57 的网络隔离、端口 9080/9443 同一格：
出网的是仓，在役的是旧镜像，等**镜像重烤 + recreate 窗口**（未获授权）。凡"线上行为"的判定必须以容器 `/app` 那格为准。

