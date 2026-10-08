#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""#78 落册：台账 §五十二 + 进度文档 §十九。

纪律：先把整份新文本构造好并当场核对锚点，全部断言过了才写盘；按 bytes 读写，
行尾一律沿用文件原有形状（不许把 LF 文档重写成 CRLF）。
"""
import io
import re

LEDGER = "doc/审计报告/修复与核实/审计核实与修复_20261001.md"
PROGRESS = "doc/审计报告/进度与交接/Qoder接手进度_20261004.md"

SEC52 = """

## 五十二、任务表 #78 落码：死目录镜像 439 行删除 + 三把形状锁 + 一条读者锁（DCD 20261007 §一 裁甲，容器 run25）

### 这一刀切掉了什么（现读核准，不是引用裁定书的行数）

`src/memory_agent/mcp_server.py` 里 `:169` 那份手抄 `TOOL_CATALOG: list[dict]`，
**AST 实测 48 条**（裁定书说"48 条"，对上了；我第一版脚本按"行首 `\"name\":` 计数"量出 49，
因为有一条把 `\"name\"` 写在别的排版位置——**条目数一律按 AST 的 `List.elts` 量，不按数引号量**），
在文件末尾被 `TOOL_CATALOG = build_catalog()`（真源 **90 条**）覆盖。四个读者
（`:1213` / `:1218` / `:1223` / `:1245` 的 help/describe/selftest 与 `:3373` 的 `describe()`）
**全在函数体内**，取到的都是覆盖后的值；`globals()` 里那份字面量从生到死没人读过。

删除区间 `:167-605` 共 **439 行**（两行 `#:` 说明 + 434 行字面量 + 空行 + 派生行
`TOOL_NAMES = [t["name"] for t in TOOL_CATALOG]` + 空行），`:3347-3349` 三行说明注释改口为
「目录唯一真源 = `tool_schema.build_catalog()`」。文件 3379 → 2939 行、158722 → 137704 字节。

### 立了什么锁（`tests/test_mcp_surface_parity.py`，AST 口径、本机不需要 SDK）

| 锁 | 判据 | 为什么这一把而不是"盯住名字" |
| --- | --- | --- |
| `test_tool_catalog_is_bound_exactly_once_from_the_spec_builder` | 模块级 `TOOL_CATALOG` 绑定**恰好一处**，值必须是 `build_catalog()` **零参调用**；`TOOL_NAMES` 恰好一处且取自 `TOOL_NAMES_FROM_SPEC` | 钉的是"覆盖"这个动作本身：谁再手写一份、或把真源换成就地拼装/缓存常量，当场判红 |
| `test_no_hand_written_tool_dict_table_is_bound_at_module_level` | 模块级不许再有 **≥10 条 dict 的列表字面量**（现树实测 0 处） | 只盯 `TOOL_CATALOG` 防得住同名回来，防不住换个名字再来一份"待接线的真源" |
| `test_catalog_is_never_read_at_import_time` | 目录的两个名字在 **import 期一处都不许被读**（只数 `Load`，绑定自身是 `Store`） | 被删那份镜像当初"能活着"正是因为 `:604` 在 import 期消费它。装饰器/默认值/模块级语句三种回法都在这把罩下 |
| `test_describe_page_serves_the_whole_spec_catalog` | `describe()` 给前端的目录条数 == 真源条数、`tools` 与 `catalog` 同源同序、每条带 `summary` | 这条是本轮补出来的**覆盖盲区**：改之前没有任何一条锁跑在这条读路径上，"退回 48 条旧表"不会有任何测试响 |

### run25 的门读数（口径：容器 `memory-agent` 3.11.16，终树快照 `/tmp/c78snap20261007`，**404 文件**，基线 HEAD `97b7062`，区间 2026-10-07T11:31:08+08:00 → 11:40:58+08:00）

- **容器树 == 本机树**：`HASH_LISTED=404 / HASH_FILES=404 / HASH_MISSING=0`，
  `HASH_AGGREGATE=8ad3e70b97992842bbada8a2d2f2ca8f2ca3e59ff0cb0cbb7e3bc5130cf2c9c2`，
  变异跑完后二次哈希逐字相同（`HASHES_IDENTICAL=1`）⇒ 六条腿确实只在一次性副本树里动过手。
- `TOOLCHAIN_RC=0`（PYTEST 9.1.1 / PYFLAKES 4.0.2 / 3.11.16）；`GATE_RC=0`——pyflakes
  「当前 0 / 基线 0 / 新增 0 / 已修 0」，`.gates-baseline.txt` 一字未改（删码不留未用导入，`build_catalog` 仍在用）。
- **`SUITE_RC=0` 1951 passed / 10 skipped（467.73s）**；run24 是 1904 passed，本批 +4 把锁 + 若干新用例 ⇒ 基数往上走，无一条转红。
- **RUNTIME 格（这一档真正要的读数）**：`CATALOG_LEN=90 NAMES_LEN=90 SAME_AS_SOURCE=True
  DESCRIBE_CATALOG=90 DESCRIBE_TOOLS=90 NO_SUMMARY=0 MCP_AVAILABLE=True`。
  AST 锁只证明"形状对"，这一行证明**删完 439 行之后带真 SDK 的读者路径一条不缺**。
- `TARGETED_RC=0` 25 passed（`test_mcp_surface_parity.py` + `test_tool_schema.py`）/
  `FACES_RC=0` 84 passed（ACP / presence 的 `caps.tools` / 文案承诺键 / 调用点绑定 / WebUI 读键尺）。
- **`MUT78_RC=0` 六条腿**：M-0 对照 17 passed，L1 镜像放回 **3 failed**、L2 真源换成就地拼装 **2**、
  L3 名字退回派生行 **2**、L4 import 期读者 **1**、L5 接入页截断 48 条 **1**，
  `MUT78_COUNT=6 MUTATION_BAD=0 SRC_UNCHANGED=True mcp_se_SHA=5f5541e8120d`——
  **与本机 3.13 档 `.qoder/tmp-c78-mut78-local.out` 的每条腿读数逐字相同**（本机 15 passed 是 SDK 缺席少跑 2 条，腿判定一致）。
- `POST_RC=0` 17 passed：变异跑完原样再跑同一份锁，还原自证。

### 本轮自己踩的坑（照旧登记，别让它第二次踩）

1. **删除腿两次自我中止**，都没写盘：第一次 `assert body.count('TOOL_CATALOG')==5` 实为 6；
   第二次按"行首 `\"name\":`"数条目数出 49≠48。**根因同一件事：条目数要用 AST 数，不用文本数。**
   第三次先把 `ast.AnnAssign` 的 `List.elts` 量出来（48）再动刀，一次过。
2. **护栏自己判红，不是被测物判红**：run25 首跑被 `MCP_CR 期望=0 实际=2939` 拦下未出网。
   Git Bash 把**整枚只含 CR 的实参**（`grep -c $'\\r' file`）吞成空模式 ⇒ grep 匹配所有行、报出总行数。
   同一棵树 `tr -dc '\\r' < F | wc -c` 给 0。**判 CR 一律按字节量**；本批 7 个文件（含 4 份 .qoder 脚本）
   全部按这个量法复量，CR=0。
3. **对照腿的第一版期望值写错了**：M1（镜像 + 派生行一起放回）我预期三把门全红，实跑只红两把——
   因为 `import 期读者` 那把我按"最早绑定之前"划界，字面量自己就是最早绑定时派生行落到了界外。
   改成**全局口径**（import 期一处都不许读，只数 `Load`）后 M1 才按预期咬住三条。
   这条正好是"新判红先找反例、也找漏咬"的反面教材：**量具漏咬比误咬更难发现**。

### 这一批之后仍开着的

#79（温控环比乙案）、#80（`route_question` 承诺键门 + `window` 回溯两格）、#81（登录限速乙+丙，
MA-22 的 XFF 半边；8086 收口挂 DB token 同批）、#82（vendored 0.3.2 + speak 调用点 B，运行面随变更窗）、
#83（状态回填）。仍等外部输入：裁6 Q6-1 / #38（SP 本居活动清单）、计划 §七 六件的运行面半边。
"""

SEC19 = """## 十九、2026-10-07 追加（任务表 #78 落码：删 439 行死目录 + 形状锁，容器 run25）

§十八 交接里那句"下批必须重开变异档"这一批就兑现了。三行版：

- **#78（DCD 20261007 §一 裁甲）**：`mcp_server.py` 那份 48 条手抄 `TOOL_CATALOG` 被末尾
  `build_catalog()`（90 条）覆盖、四个读者全在函数体运行期取覆盖后的值——镜像无人消费、
  却比真源少 42 条。删 `:167-605` 共 **439 行**（3379 → 2939 行），说明注释改口"目录唯一真源 =
  `tool_schema.build_catalog()`"。**四把新锁**（绑定恰好一处且必须是 `build_catalog()` /
  模块级不许 ≥10 条 dict 大表 / import 期一处都不许读 / `describe()` 必须给整份目录且每条带 summary），
  最后那把是本轮补出来的覆盖盲区——改之前**没有任何锁跑在这条读路径上**。
- **run25 的门读数**（容器 3.11.16，快照 `/tmp/c78snap20261007` 404 文件，HEAD `97b7062`）：
  `TOOLCHAIN_RC=0`；`GATE_RC=0`（pyflakes 当前 0 / 基线 0 / 新增 0）；
  **`SUITE_RC=0` 1951 passed / 10 skipped（467.73s）**；`TARGETED_RC=0` 25 passed、`FACES_RC=0` 84 passed；
  **RUNTIME 格 `CATALOG_LEN=90 / NAMES_LEN=90 / SAME_AS_SOURCE=True / DESCRIBE_CATALOG=90 /
  DESCRIBE_TOOLS=90 / NO_SUMMARY=0 / MCP_AVAILABLE=True`**（删完之后带真 SDK 的读者路径一条不缺）；
  **`MUT78_RC=0` 六腿**（M-0 17 passed；L1 3 / L2 2 / L3 2 / L4 1 / L5 1，`MUTATION_BAD=0`，
  `SRC_UNCHANGED=True`，与本机 3.13 档逐字同）；首末两次哈希
  `8ad3e70b97992842bbada8a2d2f2ca8f2ca3e59ff0cb0cbb7e3bc5130cf2c9c2` 一致 ⇒ 变异腿没碰被测树。
- **两处自捉**：① 条目数改按 AST 的 `List.elts` 量（文本数引号数出 49，实为 48），删除腿据此
  第三次才写盘，前两次中止时**一个字节都没动**；② 护栏 `grep -c $'\\r'` 在 Git Bash 里把 CR 实参
  吞成空模式、把总行数当 CR 数报，首跑被自己拦下未出网——**判 CR 改用 `tr -dc '\\r' | wc -c`**。

下一位的交接点：#79（温控环比乙案：门面注入 `climate_provider` 回调 + 顶层 `climate_comparison`
+ **同批键集合锁**，这次根因就是"实现有、锁没有"，别再犯）、#80（承诺键门 + `window` 回溯两格）、
#81（`MA_TRUST_PROXY` + 全局 60 次/分）、#82（0.3.2 vendoring + speak 调用点 B）。
生产码改动照旧要带变异档，别再沿用 run25 的结论。

"""

FOOTER = "—— MA 侧执行台账 · 2026-10-04（基准 HEAD `f02eefd`）"

plan = []
with io.open(LEDGER, "rb") as fh:
    led = fh.read()
assert led.count(b"\r") == 0, "台账含 CR，本档不接管这种形状"
lt = led.decode("utf-8")
assert "## 五十二、" not in lt, "§五十二 已存在（别重复落册）"
assert lt.rstrip().endswith("`route_question` 甲案（硬映射）由 DCD 明确驳回、不再自办。"), lt[-200:]
plan.append((LEDGER, lt.rstrip("\n") + "\n" + SEC52))

with io.open(PROGRESS, "rb") as fh:
    pro = fh.read()
assert pro.count(b"\r") == 0, "进度文档含 CR"
pt = pro.decode("utf-8")
assert "## 十九、" not in pt, "§十九 已存在"
n = pt.count(FOOTER)
assert n == 1, "页脚锚点不唯一：%d" % n
assert re.search(r"## 十八、", pt), "§十八 不见了，插入点无从对锚"
idx = pt.index(FOOTER)
plan.append((PROGRESS, pt[:idx] + SEC19 + "\n---\n\n" + pt[idx:]))

for path, text in plan:
    assert "\r" not in text
    data = text.encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(data)
    print("WRITTEN %s bytes=%d" % (path, len(data)))
print("DOCS_RC=0")
