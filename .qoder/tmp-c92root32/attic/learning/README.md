# attic/learning —— 下架存档，不参与门禁

依据：DCD 裁定 `20261005-AF用户WebUI与MA四件与CVE-裁定.md` §二.2 **Q2 走乙**
（申请件：`关键决策部/inbox/20261005-MA-主动规则CRUD挂载与死代码六处处置-决策申请.md` §三）。

## 为什么在档案里，不在 `src/` 里

- **外部引用为 0**：八个模块在 `src`/`tests`/`scripts`/`benchmarks` 里没有任何 import 方；
  真正接线的学习模块是 `signal_learning`。
- **按包加载根本进不来**：族内互引用用的是裸绝对导入（`learning_api.py` 里
  `from learning_analyzer import …`），所以 `import memory_agent.learning_api` 直接
  `ModuleNotFoundError`；只有把它们当顶层脚本各自运行时才自洽。
  这一条已由静态结论跑到运行时确认（127 模块导入烟测，整族走不通）。
- **留在 `src/` 的代价是豁免膨胀**：每个静态量具都要为它写一条豁免，而豁免越多，真缺陷越容易藏在豁免里
  ——本仓库已经为此踩过一次（`config` 档的一跳假豁免，见审计台账 §三十六 §九）。

## 门禁口径（这块不参与，别指望门会替你看它）

- `.gates.toml` 的 `source_roots = ["src"]` ⇒ AST 门禁与 import 冒烟不扫本目录；
- `scripts/pyflakes_gate.sh` 的目标目录是 `src/memory_agent` ⇒ pyflakes 基线不含本目录；
- `pytest.ini` 的 `testpaths = tests` ⇒ 无测试收集；
- `Dockerfile` 只 `COPY src/` ⇒ 不进镜像；
- `scripts/scan_day_bounds.py` 的 `ROOT=src` ⇒ **本目录里的 `timedelta(days=)` 站点不在 99 站点册里**，
  移出来之后 `src` 侧站点数会变，两个口径不许混着引用（见审计台账 §三十六 §九的改前后读数）。

## 要接回去时先回答这三问

1. 学习闭环的产出**写哪张表**、**谁消费**（这是"接线"，属新需求，不是清理）；
2. 导入口径统一到哪一种（族内相对导入 + 作为 `memory_agent` 包成员），并给一条能真的 import 的运行时锁；
3. 窗口类参数（`window_days` / `eval_window_days`）走哪套口径——存档前两处已就地收敛到
   `[1, 3650]`（查询窗口口径），接回去不需要重新夹紧；`eval_after_hours` 属 `hours=` 形状，**不在天数册里**。
