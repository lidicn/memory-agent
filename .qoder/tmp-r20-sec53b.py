import io

p = "doc/审计报告/修复与核实/审计核实与修复_20261001.md"
t = io.open(p, encoding="utf-8", newline="").read()
anchor = "\n## 五十四、第二十轮批次吸收"
assert t.count(anchor) == 1, t.count(anchor)

SEC = """### 六、run26 容器权威门读数（快照树 = commit `fdad983`，容器 3.11.16，2026-10-07 18:19→19:04 UTC）

**门本身是绿的**：`TOOLCHAIN_RC=0`（`PYTEST=9.1.1` / `PYFLAKES=4.0.2`）、
`GATE_RC=0`（pyflakes 当前 0 / 基线 0 / 新增 0 / 已修 0）、`SUITE_RC=0`（**2002 passed, 10 skipped in 549.90s**）、
定向三格 `AUTH_A_RC=0`（157 passed）/ `ANN_B_RC=0`（84 passed, 1 skipped）/ `PROSE_C_RC=0`（39 passed）。

**快照同一性**（本机与容器跑同一份 `_hashes.py`，逐字对账）：
`HASH_LISTED=406 HASH_FILES=406 HASH_MISSING=0`、
`HASH_AGGREGATE=998d4aa8cbd06041c086a1598d290b11fda2e9377d2cfdd78d3f4fa664afd34a`，
**POST 二次哈希逐字同**（`POST_HASHES_RC=0`，同一枚聚合摘要）⇒ 36 条变异腿没碰到被测树；
POST 原样复跑 `POST_ANN_RC=0`（39 passed）、`POST_AUTH_RC=0`（22 passed）。

**两档变异腿在容器 3.11 的判定与本机 3.13 同口径**（原样档已落盘：
`.qoder/tmp-c82-m81-container26.out`、`.qoder/tmp-c82-m82-container26.out`）：

- mut81（#81 登录限速）：控制腿 `[M-0] rc=0 '207 passed in 86.47s'`，M01~M22 **全部 KILLED**，
  `totals: killed=22 survived=0 invalid=0 legs=22`，`WT_UNTOUCHED=True`；
- mut82（#82 vendored 0.3.2 + 播报两条路）：控制腿 `[M-0] rc=0 '70 passed in 7.03s'`，N01~N14 **全部 KILLED**，
  `totals: killed=14 survived=0 invalid=0 legs=14`，`WT_UNTOUCHED=True`
  （N09 用的是换过写法的那条**不等价**腿，等价变异已按第二节登记为量具边界）。

**两处量具自身的缺陷（都在本轮改掉，读数因此分两档取）**：

1. **RUNTIME 探针取不到根**：探针脚本被放到 `/tmp/<SNAP>_probe.py`，它用 `__file__` 往上两级算仓库根，
   在容器里算到 `/src` ⇒ `PROBE_RC=1 / ModuleNotFoundError: No module named 'memory_agent'`。
   改成根走环境变量（`PROBE_ROOT`）+ 显式 `PYTHONPATH`，本机重跑四格 `PROBE_BAD=0`；
   **容器那一格由 run27 出**。这是「量具的取数根必须可指认」的第二次发作（第一次是 day-bounds 量具）。
2. **主机侧读不到容器内的档**：`MUT_OUT` 是 python 在**容器内**写的，
   而 remote26 里那三条 `grep`/`sed` 没包在 `ex` 里、跑在 NAS 主机上 ⇒
   `M81_RC=0` 后面跟着三条 `No such file or directory`，读数当场没打出来。
   事后用 `docker exec … cat` 原样取回并落成本机档（上面两份），**结论没受影响**（`M81_RC=0` 与两份 totals 行都是实测）。
   remote27 起读数一律走 `ex`，并把这条写成注释钉在脚本里。

### 七、之后仍开着的（本节口径）

- 本节的四件（#79~#82）生产半边全部出网，运行面一件没动：**重烤 / recreate 不在自主范围**，
  合并窗五件按计划 §七 登记排队；两处字面偏离 + ACP 批次的 sessionId 口径已随
  `inbox/20261008-MA-四件落码回执与两处字面偏离与ACP批次三问-决策申请.md` 呈请追认。
- #81 的回落条件宽一格、caddy 单 IP 只落机制未落值 —— 这两条**在追认之前不许当成已裁**。
- 8086 收口挂 DB token 收敛同一批；`MA_TRUST_PROXY` 进不进 `WRITABLE_FIELDS` 随 #68 第二批一并定。
"""

io.open(p, "w", encoding="utf-8", newline="").write(t.replace(anchor, SEC + anchor))
out = io.open(p, encoding="utf-8", newline="").read()
print("INSERTED lines=%d CR=%d" % (out.count(chr(10)), out.count(chr(13))))
