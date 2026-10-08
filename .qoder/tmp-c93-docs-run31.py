"""run31 读数落册：三份文档各自插/追一节，读数全部来自当场现读的日志与产物。

锚点唯一性先判（命中 != 1 ⇒ 整份不动并退出 2），写入一律 `newline=''`（本机 python 文本模式
会把 LF 改成 CRLF，三份文档的 CR 必须仍为 0）。
"""
import io
import sys

PROG = "doc/审计报告/进度与交接/Qoder接手进度_20261004.md"
FIX = "doc/审计报告/修复与核实/审计核实与修复_20261001.md"
PLAN = "doc/路线图与规划/ADM联动执行计划-MA.md"

# --- 1) 台账：插在最后一条页脚之前 -------------------------------------------
PROG_ANCHOR = "\n---\n\n—— MA 侧执行台账 · 2026-10-04（基准 HEAD `f02eefd`）\n"
PROG_SECTION = """## 二十四、2026-10-08 追加（run31 容器权威档：`UNVERIFIED` 23 → 18 的"有断言"由 13+22+11+14 = 60 腿变异自证；`_is_trusted_source` 按 docstring 收紧；闭合 §二十三「下一只手」第 1 条）

主档（快照树 = `b150000`，414 文件——比 run30 多的那一个是新增用例文件 `tests/test_vma_phase3_claims_direct.py`；
容器 `memory-agent` / Python 3.11.16 / pytest 9.1.1 / pyflakes 4.0.2，起算 06:53:54+08:00 → `REMOTE_DONE`
07:42:43+08:00，整跑 48 分 49 秒）：

| 格 | 读数（当场从日志取，不是"应该已通过"） | RC |
| --- | --- | --- |
| `-1 TOOLCHAIN` | `Python 3.11.16`、`PYTEST=9.1.1`、`PYFLAKES=4.0.2`、**`MCP_IMPORT_OK`** | 0 |
| `0 SNAP` | `HASH_LISTED=414 HASH_FILES=414 HASH_MISSING=0`，聚合 `f66629b7d18aabf6ae6675925ddc58b66cb24a775c052bd7de3504842604a1b2` = 本机串**逐字符同** | 0 |
| `0M MUTFILE` | 414 件清单 + 四份 harness 字节数（10649 / 10686 / 9843 / 9592）+ 五件被测源文件字节数（app 40759 / auth 20547 / mcp_tokens 11930 / task_record 3090 / store 255182）两侧同 | 0 |
| `0A ANCHOR`（**本轮新增的一格**） | 四份 harness 共 60 条腿锚点在**被测树上当场数**：`ANCHOR_OK=60 ANCHOR_BAD=0 ANCHOR_MISSING=0`（13/22/11/14 每份 `anchor_ok == legs`） | 0 |
| `0B AST311` | 全件在容器 3.11 下可解析 `AST311_ALL_OK` | 0 |
| `1 PH3` | **41 passed in 5.83s**，`PH3_SKIPPED=0`（本机 3.13 同 41 条 / 2.79s） | 0 |
| `2 LOCK` | **7 passed in 16.54s**（本机同 7 条 / 6.23s） | 0 |
| `2N NEWTWO` | **2 passed in 3.98s**，`NEWTWO_SKIPPED=0` ⇒ `retrieve_agent_memories` 工具本体那两条在容器里**真跑** | 0 |
| `3 MUT31` | 对照腿 `Q-0` 绿 ⇒ **13 腿全杀** `killed=13 survived=0 invalid=0`、`WT_UNTOUCHED=True`（本机 3.13 同 13/13） | 0 |
| `4 MUT81` | 旧档重跑：**22 腿全杀**（`survived=0 invalid=0`、`WT_UNTOUCHED=True`） | 0 |
| `5 MUT85` | 旧档重跑：**11 腿全杀**（同上） | 0 |
| `6 MUT82` | 旧档重跑：**14 腿全杀**（同上） | 0 |
| `7 G1..G4` | 四把尺 `SELFTEST_RC=0`×4 + `SCAN_RC=0`×4，`PROBLEM=0`×4；G4 现读 `DECLARED=41 REGISTERED=23 CASES=36 UNVERIFIED=18 PROBLEM=0`（本机 ledger 同 `23 18 41 36`） | 0 |
| `8 GATE` | `pyflakes: 当前 0 条，基线 0 条，新增 0，已修 0` | 0 |
| `9 SUITE` | **2096 passed, 10 skipped in 681.70s**（= 2106 收集，比 run30 的 2065 多 41 = 本批新用例文件里的 41 条；自查用来防"加了用例却说不清去了哪"） | 0 |
| `10 POST` | 二次哈希聚合仍是 `f66629b7…a1b2`（与 `SNAP` 同串，`HASH_MISSING=0`）、`G4` 复扫同读数、`MUT_DST_CLEANED_RC=0`（只删自命名副本树） | 0 |

10 条 skip 逐条有名（`hmmlearn`×2 / `pm4py`×2 / `river`×5 都是模块级可选依赖 import skip，另 1 条是精简检出
下 wheel / vendor README / Dockerfile 未同时在场的 provenance 用例）⇒ **零用例级 skip**，名单里没有
`test_rounds11_19`。

### 本轮量到的三件事

1. **`ip_address().is_private` 比 docstring 承诺的"内网段"宽**（补断言时量出来的，不是读出来的）：
   `203.0.113.7`（TEST-NET）、`169.254.1.1`（链路本地）、`2001:db8::1`（IPv6 文档段）在 `is_private` 下**全为 True**。
   `app._is_trusted_source` 是 `dbg_` 令牌的来源闸，按旧写法这些段会被当内网放行 ⇒ 改按 RFC1918 + `fc00::/7`
   实名段判，方向 fail-closed。旧行为零用例依赖，登录/鉴权相关 207 条本机全绿。
   **T03 腿**就是"退回 `is_private`"，它必须让新用例判红——实测杀（3 条红）。这条腿证明"收紧"不是空话。
2. **ANCHOR 预检格是本轮才立的**，理由不是形式主义：run31 改过 `src/memory_agent/app.py`，旧三档（c81/c85/c82）
   的腿不再满足"源码未变 ⇒ 整档跳过"的条件；而腿一旦锚点漂移，harness 只报 `INVALID`（既不算杀也不算活），
   整档读数就废在出网之后。这一格在发容器前把 60 条锚点逐条数一遍（文件键取自 harness 自己的 `FILES` 表，不猜文件名）。
   本轮读数 60/60 ⇒ 四条 `MUT*` 档的 `invalid=0` 是被**预检**过的，不是被**运气**过的。
3. **DCD 20261008 §五.5 升格的那条新红线（权威门在飞期间，工作树只允许 harness 一个人动）本轮按字面执行**：
   run32 的全部准备（草稿用例、gauge 补丁、9 条变异腿）都在 `.qoder/` + `git archive HEAD` 副本树里完成，
   `src/tests/scripts` 一个字节没动；`POST` 与 `SNAP` 同串聚合哈希就是这条的机械证据。

### 两处量具自错（都写进了 harness 注释，不是口头教训）

- `scan_claimed_semantics` 头部期望读数写成 `CASES=35`、现读 36：减格同时新增用例，计数没跟着走。
  修法是让读数行来自**同一棵树两次现读**并当场比对，而不是我记忆里的数。
- 副本树对照腿 `Q-0` 首跑 `1 failed 40 passed`：台账里"登记自身的形状"那条用例用
  `../scripts/scan_claimed_semantics.py` 现读台账，而快照清单不含 `.qoder/`、`build_copy` 只拷了 `src`
  ⇒ **副本树缺 `scripts/`**。修法：`build_copy` 连 `scripts` 一起拷（注释留在 `tmp-c91-mut31.py`）。
  这一条值得单独记：对照腿的意义是"什么都不改也必须绿"，它一红就把整档读数作废——本轮真的作废过一次。

### DCD `decisions/20261008-AF两件与MA四回执-裁定.md` 吸收（本仓侧六条，逐条状态）

| 裁定 | 落点 | 状态 |
| --- | --- | --- |
| sessionId **乙**（保留自报） | `check_owner` 四入口已统一（含本轮补的 `M_PROMPT`） | 已落；"下轮可再议甲"登记在案 |
| MA-29 五动作 **乙**（如实降级） | `rule_engine._action_alert/webhook/tts/light/camera` ⇒ `ok:false` + `not_implemented` + 触发历史 `dry_run=1` | **待落码**（run33） |
| MA-32 死开关 **甲**（删） | `config.py:209` + `api/config_routes.py:36/:192` + `static/js/pages/settings.js:274` | **待落码**（run33） |
| MA-34 `persist` **甲**（默认翻 False） | `activity_inference.py:548/:756/:887` 服务默认 + `behavior_routes.py:209/:282/:340` HTTP 默认 + `runtime.py` 三条每日任务显式 `persist=True` | **待落码**（run33） |
| run30 基线只减 | 登记为**轻量 DCD 口径**（"语义覆盖声明只准增不准减；减了必须逐条给直接断言证明"），不做硬门 | 已按此口径执行 |
| run28 命名 / run29 补档 | `scan_*` 保持仓规不改；run29 要求的"五件新文件与全量回归共处一树"由本轮 `ANCHOR`/`AST311`/`SUITE` 三格补上 | 已闭合 |
| **判例 2**（入口对等性要门禁化） | 新量具：AST 入口对等门——读敏感会话 id 的分支集合必须 == 登记集合 | **待落码**（run33） |

### 下一只手

1. **run32**：`UNVERIFIED` 18 → 12（六格）。九腿本机已**全杀**（`killed=9 survived=0 invalid=0`、
   对照腿 `143 passed` skipped=0、`WT_UNTOUCHED=True`，副本树 `.qoder/tmp-c92root32`）⇒ 出网前须容器实跑，
   并按本轮新立的 ANCHOR 格先数锚点。
2. **run33**：DCD 20261008 三件落码（MA-29 乙 / MA-32 甲 / MA-34 甲）+ 判例 2 的入口对等门 + 各自变异腿。
3. 仍不能自证的：MA-22 部署面那半条（compose 8086 直曝，裁乙+丙留给部署面/DB 侧）、计划 §七 六件里的合并窗与重烤。

"""

# --- 2) 修复报告：追加一节 ---------------------------------------------------
FIX_SECTION = """## 五十七、run31 容器权威档：`UNVERIFIED` 23 → 18 的"有断言"由 60 腿变异自证，`dbg_` 令牌来源闸按 docstring 收紧（2026-10-08，任务表 #85 收尾第三档）

本档只记"结论 ↔ 现读"的对应；逐格读数与两处尺子自错见台账 `doc/审计报告/进度与交接/Qoder接手进度_20261004.md` §二十四（活台账唯一正本）。

| §五十六 那侧的悬空结论 | 现在的口径 | 现读判据（不是"应该已经通过"） |
| --- | --- | --- |
| "`UNVERIFIED` 还剩 23 条，继续补一条删一行 + CAP 一起下调" | **已减到 18**（commit `b150000`，run31 自证） | 容器与本机同串 `DECLARED=41 REGISTERED=23 CASES=36 UNVERIFIED=18 PROBLEM=0`；`BASELINE_CAP` 从文件现读 = `len(UNVERIFIED)` = 18；`REDUCED_20261008_B` 五格锁（在 REGISTRY / 不在 UNVERIFIED / `len(cases) >= 2`） |
| "五格的新用例算不算锁" | **算，且由变异腿证明** | 新档 13 腿（T01..T13，各把那一格**退回它在 docstring 里承诺之前的写法**）全杀：`killed=13 survived=0 invalid=0`、对照腿 `Q-0` 绿、`WT_UNTOUCHED=True` |
| "旧三档（11/22/14 腿）能不能跳过" | **不能**——本批改过 `src/memory_agent/app.py` | 三条旧档在容器重跑：`22/0/0`、`11/0/0`、`14/0/0`，四份合计 **60 腿全杀**；出网前 ANCHOR 预检 `ANCHOR_OK=60 ANCHOR_BAD=0 ANCHOR_MISSING=0` |
| "`app._is_trusted_source` 的"内网段"就是实现的意思" | **实现比 docstring 宽，按 docstring 收紧** | 现读 `ip_address(...).is_private` 对 `203.0.113.7` / `169.254.1.1` / `2001:db8::1` 全 True；改判为显式段表（10/8、172.16/12、192.168/16、`fc00::/7`）+ IPv4-mapped 拆封 + 无来源/解析失败一律 False；T03 腿（退回 `is_private`）判红 3 条 ⇒ 收紧有牙 |
| "全量回归还是 2055 条" | **2096 passed / 10 skipped** | `SUITE_RC=0`、`2096 passed, 10 skipped in 681.70s`；2106 − 2065 = 41 = 本批 `test_vma_phase3_claims_direct.py` 用例数（`PH3` 格同 41 条）⇒ 新增用例去向可核 |
| "跑完的工作树还是跑前那棵" | **是，机械证据** | `SNAP` 与 `POST` 聚合哈希同为 `f66629b7…a1b2`（414/414/MISSING=0）；`MUT_DST_CLEANED_RC=0` 只删自命名副本树 |

- DCD `20261008-AF两件与MA四回执-裁定.md` 本仓侧六条已吸收：sessionId 乙（已落）、MA-29 乙 / MA-32 甲 / MA-34 甲
  **转为待落码**（三份回执里这三件原本"不能自裁"，裁定已到 ⇒ 不再挂在待裁决列）、run30 基线口径降为轻量登记、
  run28 命名按仓规保持 `scan_*`、run29 补档由本轮 ANCHOR/AST311/SUITE 三格完成、**判例 2 立项为入口对等门**。
- 本轮新增一条运行纪律（同裁定 §五.5 升格的红线）：**权威门在飞期间工作树只允许 harness 一个人动**。
  下一批（run32）的准备工作全部在 `.qoder/` 与 `git archive HEAD` 副本树内完成，实测 `WT_UNTOUCHED=True`×4 档。

"""

# --- 3) 路线图：在 §七 之前插 6.27 -------------------------------------------
PLAN_ANCHOR = "\n\n---\n\n## 七、下一阶段：更紧密联动（DCD 2026-10-06）\n"
PLAN_SECTION = """

### 6.27 容器权威门 run31（任务表 #85：`UNVERIFIED` 23 → 18 由 60 腿变异自证 + `_is_trusted_source` 收紧，2026-10-08）

快照树 = `b150000`（414 文件），容器 `memory-agent` / Python 3.11.16 / pytest 9.1.1 / pyflakes 4.0.2，
起算 06:53:54+08:00 → `REMOTE_DONE` 07:42:43+08:00（48 分 49 秒）。全档格 RC 实测：
`TOOLCHAIN_RC=0`（含 `MCP_IMPORT_OK`）、`SNAP_RC=0`（414/414/0，聚合
`f66629b7d18aabf6ae6675925ddc58b66cb24a775c052bd7de3504842604a1b2` = 本机串逐字符同）、
`MUTFILE_RC=0`（四份 harness 与五件被测源文件字节数两侧同）、**`ANCHOR_RC=0`（本轮新增格：
`ANCHOR_OK=60 ANCHOR_BAD=0 ANCHOR_MISSING=0`）**、`AST311_RC=0`、`PH3_RC=0` **41 passed / PH3_SKIPPED=0**、
`LOCK_RC=0` **7 passed**、`NEWTWO_RC=0` **2 passed / SKIPPED=0**、
`MUT31_RC=0` **13 腿全杀**、`MUT81_RC=0` **22 腿全杀**、`MUT85_RC=0` **11 腿全杀**、`MUT82_RC=0` **14 腿全杀**
（四档各自 `survived=0 invalid=0` + `WT_UNTOUCHED=True`）、四把门 `SELFTEST/SCAN_RC=0`×4（G4 两侧同串
`DECLARED=41 REGISTERED=23 CASES=36 UNVERIFIED=18 PROBLEM=0`）、`GATE_RC=0`（pyflakes 0/0/0/0）、
**`SUITE_RC=0` = 2096 passed, 10 skipped in 681.70s**（= 2106 收集，比 run30 多 41 = 本批新用例数）、
`POST_HASHES_RC=0` 复算同串、`MUT_DST_CLEANED_RC=0`。十条 skip 全是模块级可选依赖
（hmmlearn×2 / pm4py×2 / river×5）+ 1 条精简检出 provenance ⇒ 零用例级 skip。

**这一档存在理由**（run30 回答不了）：`UNVERIFIED` 从 23 减到 18 的前提是"这五格现在有直接断言"，
而这句话只能由变异腿证明。13 条新腿各把那一格**退回它在 docstring 里承诺之前的写法**：
缺 client_ip 放行 / 坏 IP 放行 / **退回 `is_private`** / 不拆 IPv4-mapped / 网表少 10-8 / ULA 换成文档段 /
坏 CIDR 换来 `0.0.0.0/0` / 缓存短路关掉 / 判重拆掉 / prefix 存整串明文 / upsert 插第二行 /
payload 去时间戳 / id 掺 `monotonic_ns`。其中 **T03 是本批最有价值的一条**：补断言时量出 Python 的
`is_private` 把 TEST-NET / 保留 / 链路本地段都算私有，docstring 写的"内网段"比实现窄 ⇒ 代码按 fail-closed 收紧，
而这条腿证明"收紧"不是空话（退回旧写法判红 3 条）。

**ANCHOR 格为什么进常驻**：本批改过 `src/memory_agent/app.py` ⇒ 旧三档不再满足"源码未变就整档跳过"的条件；
锚点漂移时 harness 只报 `INVALID`（既不算杀也不算活），读数会废在出网之后。发容器前先把 60 条锚点在被测树上
逐条数一遍（文件键取自 harness 自己的 `FILES` 表），`ANCHOR_BAD=0 且 ANCHOR_MISSING=0` 才允许发。

**DCD `20261008-AF两件与MA四回执-裁定.md` 落到本计划的部分**：MA-29 乙 / MA-32 甲 / MA-34 甲 三件从"等裁定"转为
待落码（落点 file:line 已在台账 §二十四 表内登记），sessionId 走乙并把"下轮可再议甲"留档，run29 要求的补档由
本轮三格完成，`scan_*` 命名保持仓规，**判例 2（入口对等性要门禁化）立项为 AST 入口对等门**，
§五.5 升格的红线（权威门在飞期间工作树只允许 harness 一个人动）已按字面执行。

"""

def apply(path, anchor, replacement):
    text = io.open(path, encoding="utf-8", newline="").read()
    if anchor is None:                      # 追加到文件末尾
        new = text.rstrip("\n") + "\n\n" + replacement
    else:
        n = text.count(anchor)
        if n != 1:
            print(f"PATCH_ABORT {path} 锚点命中 {n} 次（要求恰好 1 次）")
            return None
        new = text.replace(anchor, replacement, 1)
    return new


def main():
    planned = [(PROG, PROG_ANCHOR, PROG_SECTION.rstrip("\n") + "\n" + PROG_ANCHOR.lstrip("\n")),
               (FIX, None, FIX_SECTION.rstrip("\n") + "\n"),
               (PLAN, PLAN_ANCHOR, PLAN_SECTION.strip("\n") + "\n" + PLAN_ANCHOR.lstrip("\n"))]
    news = []
    for path, anchor, replacement in planned:
        new = apply(path, anchor, replacement)
        if new is None:
            return 2
        news.append((path, new))
    for path, new in news:                  # 全部锚点核对通过后才落盘
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
        cr = new.count("\r")
        print(f"WROTE {path} bytes={len(new.encode('utf-8'))} CR={cr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
