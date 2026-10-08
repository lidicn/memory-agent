#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""落册腿：台账 §五十/§五十一 + 进度 §十八。整串先建好、断言过，再一次性写盘。"""
import io

LEDGER = 'doc/审计报告/修复与核实/审计核实与修复_20261001.md'
PROG = 'doc/审计报告/进度与交接/Qoder接手进度_20261004.md'

sec50 = """
## 五十、#77 把尺挪到「给人看的界面」那一面：WebUI 读的键 ↔ HTTP 路由实际给的键（run24 容器权威档，2026-10-07，任务表 #77）

### 一、这一格接的是 §四十九 末尾没罩住的那一面

§四十四~§四十八 收的是**给模型读的文案**（handler docstring / ToolSpec / SKILL.md / 用户手册）：
文案承诺了载荷里没有的键，模型照文案读就拿到空。同一族缺陷还有另一面，消费方是**浏览器**——
`static/js/pages/*.js` 里 `res.foo` 读了载荷里没有的键，JS **不抛异常**，只是 `undefined`，
于是那块面板静默变空白或 `—`，日志里一个字都没有。用户看到的是「功能没了」，
后端看到的是「一切正常」。这一族里最难自查的正是这种**两侧都不报错**的形状。

### 二、量具口径（`scripts/scan_webui_payload_keys.py`，只读，不改任何文件）

分格是刻意的——**不能把「宽松宇宙」当成「精确证明」**，也不能让一个解析机制悄悄把嫌疑洗成绿灯：

- **RESOLVED**＝按真信封语义把 handler 每个 `return` 表达式解到底得到的顶层键：
  `ok(helper(x))` 按 `api/deps.py:47-52` 的 `{\"ok\": True, **data}` 展开、`{**result}` 同理、
  `JSONResponse({...})` 取第一个实参（`system_routes.py:110-116` 走这条）、
  `return data` 回追该名字在 return 之前的最后一次赋值、`asyncio.to_thread(validate_all, …)`
  按第一个实参解析、`return err`（鉴权早退）按 `deps.py:38-44` 的 `{ok, error, **extra}` 记；最深 3 跳。
- **LOOSE**＝handler 函数体任意深度 dict 字面量键的并集（宇宙，宽松兜底）。
- **HARD**＝两格都没有 ⇒ 首要嫌疑；**SHAPE**＝宇宙里有、顶层解不到 ⇒ 嵌套/分支形状交人判；
  **GREEN_BY_HELPER**＝只有走通 helper 边才判绿的键 ⇒ 逐条按 `file:line` 抽查那条边；
  **UNKNOWN**＝表达式解不动（动态派发、跨模块同名歧义、非 dict 载荷），尺承认看不清、单独报数。
- 每条 helper 边带 `helper=file:line#Class.name` 证据；`同名多义`、`已到深度`、`(嵌套)` 都明写在证据里。
- **面数打进读数头**（`FACES`/`FACES2` 两行）——否则又是一次「扫了个空却写了假读数」（§四十九 六）。

### 三、读数：本机 3.13 与容器 3.11 逐字相同，今天没有出货缺陷

    FACES  api_methods=100 unparsed=0 route_entries=208 handler_defs=292 js_callsites=67
    FACES2 defs_indexed=1650 handlers_with_helper_edge=80
    CALLSITES_ORPHAN=0 PATH_UNMATCHED=0 CHAIN_UNKNOWN=5
    READKEY_HARD=0 SHAPE=0 NESTED_MISS=0 GREEN_BY_HELPER=12
    PAIR_CALLSITES=67 PAIR_MULTI_ROUTE=0

四格归零 ⇒ **100 个 api.js 方法 / 208 条路由 / 67 个读点里没有「前端读了载荷里没有的键」**。
12 条只靠 helper 边判绿的读键，逐条按 `file:line` 抽查过生产方（存在性结论登记前抽查）：

| api.js 方法 | 读点 | 键 | 生产方（实测 file:line） |
| --- | --- | --- | --- |
| `agentMemorySweep` | `agent_memory.js:321` | `sweep` | `agent_memory.py:702`（`sweep_and_reconcile` def 在 :701） |
| `createAppToken` | `settings.js:499` | `token` | `app_tokens.py:43`（`AppTokenStore.generate`，`config_routes.py:461` 经 `get_app_token_store()`） |
| `health` | `main.js:131` | `collecting` | `runtime.py:945-996`（`AppRuntime.health`，顶层 `collecting`、`store.total_events` 在第二层） |
| `memberInsightFeedback` | `members.js:833` | `up`/`down`/`memories` | `store.py:4996-5036`（`member_routes.py:259` 的闭包 `_fetch` 经 `asyncio.to_thread`） |
| `signalRules` | `signal_rules.js:385` | `hard`/`soft`/`counts` | `signal_learning.py:182-191`（`SignalLearningService.list_rules`） |
| `validateTemplates` | `insights.js:272` | `summary` | `template_validate.py:414-438`（`validate_all`；:359 `validate_template` 是同链另一条边） |
| `visionAnalyze` | `vision.js:444` | `scene` | `vision_service.py:788`（`analyze_room`，成功出口 `:1095` 带 `scene`） |
| `visionTestLlm` | `vision.js:426` | `answer` | `vision_service.py:1379`（`test_llm`） |

一条**条件形状**登记为注记、不当缺陷改码：`analyze_room` 的降级分支（`vision_service.py:898`）返回
`{ok, persons, action, vlm_failed, reason}` **不带 `scene`**，`vision.js:444` 那格在 VLM 挂掉时是空的；
`vision_routes.py:155-164` 的 202 排队分支同理不带（设计上还没结果）。这两处前端读的是「有结果时」的载荷，
不构成缺陷，但如果哪天把降级分支也渲染进同一块面板，`scene` 就得由前端显式判空。

### 四、判据锁 `tests/test_webui_payload_keys.py`（17 条 = 5 条常规 + 12 条参数化）

1. **面数地板**＋`unparsed==0`：防止「取数根坏了 ⇒ 什么都读不到 ⇒ 报成一切正常」；
2. **缺陷门**：`hard`/`shape`/`nested_miss`/`orphan_callsites` 四格归零、路由配对无落空；
3. **helper 边钉住**：12 条按 `文件#类.方法` 钉（**故意不钉行号**——行号会在无关改动里漂，
   而「键名/符号改名」才是这条锁要抓的事）；
4. **尺自己的五条控制腿**（`scan.selftest()` 在 pytest 里真跑）；
5. **信封口径锁**：AST 读 `deps.py` 断言 `ok()` 仍有 `{\"ok\": True, **data}` 那枚 `**` 展开、
   `error()` 仍 `payload.update(extra)`。信封一改，本尺的解包规则同时作废 ⇒ 先在这里红一次，
   逼着重新校准，而不是拿旧口径给新形状判绿；
6. **盲区点名**：`CHAIN_UNKNOWN` 的 5 张脸（`arenaAnalytics`/`createAppToken`/`health`/`listAppTokens`/
   `memberInsightFeedback`）按名字钉住，新增盲区要显式登记，不许默默变多。

五条控制腿（副本树 `.qoder/tmp-c77-ctl_run`，跑完自删；`CTL_RUN_LEFT=0` 实测）：
L1 注入已知假键 ⇒ 必须落 HARD；L2 真顶层键 `trend` ⇒ 三格都不许出现；L3 helper 键 `sweep` ⇒ 必须落
GREEN_BY_HELPER 而不是 HARD；L4 把 helper 的家（`agent_memory.py`）整个删掉 ⇒ `sweep` 必须改判 HARD；
**L5 只把生产方的键名 `\"sweep\"` 改成 `\"sweep_moved\"` ⇒ 也必须改判 HARD**——这一条才对齐真实缺陷族，
也排除了「绿是因为宇宙里别处也巧同名」。本机 3.13：`17 passed in 26.09s`；容器 3.11：`22 passed in 22.12s`
（新锁 17 + 门禁量具读法约定 5）。反向自证两条都真响过：L5 在副本树打补丁后 `green 行=0、hard 含 sweep`；
把 `deps.py:51` 的 `**data` 摘掉后 `spread==[]` ⇒ 第 5 条锁红。

### 五、本轮自己踩的三个坑（都写进了码注释，免得下次再踩）

- **控制腿的键选错**：L2 首版注入 `r.total_events`，但那键在载荷**第二层**
  （`collect_stats`→`{stats,trend,rooms}`，`total_events` 在 `stats` 里），于是「真键」落进嫌疑格，
  看着像尺误报、其实是腿的口径错。改注顶层 `trend` 才算控制腿。
- **证据串在 `analyse()` 里就 `[:3]` 截断**：那会让「某条边是否被钉住」取决于同路由上别名的边有几条，
  日后新增一条边就可能把被钉的那条挤出窗口、锁为**非缺陷**原因变红。截断挪到打印，锁读全量元组。
- 另两处口径债（`ROOT` 由 `__file__` 取、副本树不开在 `%TEMP%`——本机仓库在 E: 而 `%TEMP%` 在 C:，
  跨盘 `os.path.relpath` 直接 `ValueError`）连同随之无用的 `import tempfile` 一起清掉，新档 pyflakes 0 条。

### 六、还留着的盲区（量具能力边界，不是缺陷）

- Alpine **状态别名**：`const s = this.stats` 之后 `s.total_events` 这类跨变量读法不在本轮面表里
  （尺只追 `api.<方法>()` 返回值的直接属性）。要做成语义级证明得引入别名图，属另一档活。
- v-for 模板项的键（`x.item.foo`）只按「该键在 handler 宇宙里出现过吗」宽松判（第二层口径）。
- `HITS=0` 那类老边界照旧：`bcrypt`/`jwt`/账号文件读写、`paho`/`httpx`、动态派发不参与。
- 全局 `handlers` 按**裸函数名**建索引（同名跨模块会并宇宙）——本轮以 `(嵌套)`/`同名多义` 标注而非藏起来，
  `defs_indexed=1650` 的碰撞面已读数在册。

### 七、run24 的门读数（口径：容器 `memory-agent` 3.11.16，终树快照 `/tmp/c77snap20261007`，**404 文件**，基线 HEAD `f6773ea`，区间 2026-10-07T10:05:02+08:00→10:14:41+08:00）

| 格 | 容器（Python 3.11.16） | 本机（3.13） | 判 |
| --- | --- | --- | --- |
| -1 TOOLCHAIN | `PYTEST=9.1.1 PYFLAKES=4.0.2`、`TOOLCHAIN_RC=0` | — | `/tmp/pylibs` 在可写层，永远排第一 |
| 0 SNAP | `HASH_LISTED=404 HASH_FILES=404 HASH_MISSING=0`<br>`8f19ebc56b3a9c013f1b6ca0af1589ee84ec57d17f27ac242f5acdc09a8a593b` | 聚合摘要逐字相同 | 容器里跑的就是这棵树 |
| 0C/0C2 形状锚点 | `SCAN_ANALYSE_DEF=1 SCAN_MAIN_DEF=1 SCAN_LEG_NAMES=1 SCAN_ENVELOPE_CALL=1 SCAN_TEMPFILE_IMPORT=0`<br>`LOCK_TEST_DEFS=6 LOCK_PIN_ROWS=12 ENVELOPE_OK_SPREAD=1 ENVELOPE_ERROR_UPDATE=1` | 同 | 锚点取**整行代码**形状，不取散文 |
| 1 pyflakes | `GATE_RC=0`：当前 0 条 / 基线 0 条 / 新增 0 / 已修 0 | — | 基线只准减，仍在 0 |
| 2 全量 SUITE | `SUITE_RC=0`：**1947 passed, 10 skipped in 502.81s**，`SUITE_FAILNAMES_RC=1` | — | 10 skip 与 run23 同一张名单（`river` 缺包 4 + 精简检出 6） |
| 定向 1 | `TARGETED_RC=0`：22 passed（新锁 17 + 门禁量具约定 5） | `17 passed` | 两解释器同一读数 |
| 定向 2 尺自证 | `SELFTEST_RC=0`：L1..L5 五条腿全 `True`、`SELFTEST_BAD=0`、`CTL_RUN_LEFT=0` | 同一张表 | 控制腿在**权威环境**里响的 |
| 定向 3 全表 | `FACES_RC=0`：四格 0、`GREEN_BY_HELPER=12`、`CHAIN_UNKNOWN=5`、`PAIR_CALLSITES=67` | 逐字相同 | 读数不依赖解释器 |
| POST 还原自证 | `POST_RC=0`：17 passed；`POST_HASH_RC=0` 且聚合摘要与格 0 **逐字复现** | — | 副本腿没动被测树 |

本批**没动生产码**（只加量具与锁），所以这一档没有变异档：尺自己的五条控制腿就是「响过」的证据，
而且这次要求在容器 3.11 里响——首跑就在那边响了。`REMOTE_BATCH_RC=0 REMOTE_DRIVER_RC=0 CONTAINER_BATCH_RC=0`。
"""

sec51 = """
## 五十一、裁定吸收：`20261007-MA五件与AF一件-裁定`（2026-10-07，任务表 #76 收口 → 拆出 #78~#83）

`decisions/20261007-MA五件与AF一件-裁定.md` 现读到十二条裁定，MA 侧七条全部有落点。逐条登记，
「谁做什么、什么随窗」写清楚，避免下一轮把它当新议题重问：

| 件 | DCD 裁定 | MA 侧动作 | 落点 |
| --- | --- | --- | --- |
| `TOOL_CATALOG` 死字面量（#76） | **甲** | 删 `mcp_server.py:169-602` + `:604`（含两行说明注释，共 439 行）；`:3347-3349` 注释改「目录唯一真源 = `tool_schema.build_catalog()`」；立**形状锁**：模块级 `TOOL_CATALOG` 绑定**恰好一处**且必须是 `build_catalog()` 调用 | 任务表 **#78** |
| 温控环比丢失 | **乙** | 门面 `_compare_insights` 经**注入的 `climate_provider` 回调**取两窗口 sessions（**不**直接读 `self.legacy`），调已迁好的 `insights/utils.py:889 aggregate_climate_sessions` + `:907 compare_climate`，挂顶层 `climate_comparison`（与 legacy 逐字同名）；**同批键集合锁**进 `tests/test_insights_facade_contract.py` | 任务表 **#79** |
| `route_question` Q1 | 乙追认（声明侧已改）＋**甲不做**（硬映射）＋建门 | 新门：ToolSpec 的 `summary`/`pitfall`/`description` 里点名的**返回键**逐个对运行时探针真实返回键校验（形状与 #72 三腿锁同源，可变异自证） | 任务表 **#80** |
| `route_question` Q2 | **甲回溯** | `get_behavior_insights`（`core.compare_insights`）与 `plan_question` 各加一行 `_with_window` + 判据锁；`window`＝统一回显、`time_range`＝`plan_question` 特有语义窗口对象，分工写进契约面 | 任务表 **#80** |
| `butler/inbox/speak` 调用点 | **B** | HA 直发优先，缺 `announce_tts_entity` 时回落投 `speak`；**DB 侧 speak 已消费的硬读数由 DB 出**，MA 不背无法自证的读数 | 任务表 **#82** |
| vendored 0.3.1→0.3.2 | 授权 | 认权威 sha `19bc83a67a96931c4556caec52f29c190aaa03a0a7036e0b41c242a533fb5505`，MA 自取 `E:\\\\NAS\\\\homesdk\\\\dist\\\\homesdk-0.3.2-py3-none-any.whl`；仓内换 `vendor/` + `Dockerfile:24` + `homesdk>=0.3.2`，**进运行面随下一次既有变更窗**（不单独开窗） | 任务表 **#82** |
| 登录限速 / 8086 | **乙+丙**，8086 **暂不收口** | 新增 `MA_TRUST_PROXY`（与 AF `AUTOFORGE_TRUST_PROXY` 同名）默认关；开启时仅当请求方 IP ∈ 可信网段才解析 XFF，**可信网段＝caddy 服务名解析出的单 IP**；全局失败预算 **60 次/分钟**（与 IP 无关，超了统一退避 + WARNING）。8086 收口挂到 DB token 收敛同批（`doubao-butler/butler/config.py:134-135` 直连 8086 的依赖真实存在） | 任务表 **#81** |

**判例两条入库**（DCD §九，MA 侧同样受用）：
① 「静态 diff 只能出候选清单，判定必须换运行时」——委托形状 `return self.core.x(tr)` 面前静态尺是瞎的；
② 「`route` 是意图分类不是工具选择，别做硬映射」——给模型的正确形状是「正确的键 + 意图词表」，不是替模型选工具。
判例 ① 对本轮 #77 的读法也成立：静态尺给出的四格归零**只锁形状不锁语义**，语义级证明仍要运行时探针。

§五十 的量具按判例 ① 补一条待办：把 `chain` 解不动的 5 张脸（动态派发/同名歧义）与
`GREEN_BY_HELPER` 的 12 条边，在下一批并入运行时探针对两侧真实键的比对，而不是停在静态绿。

仍等外部输入的：裁6 Q6-1 / #38（SP 本居活动清单）、计划 §七 六件的**运行面**半边（镜像重烤随变更窗）、
`route_question` 甲案（硬映射）由 DCD 明确驳回、不再自办。
"""

with io.open(LEDGER, 'rb') as fh:
    led = fh.read().decode('utf-8')
add = '\n' + sec50.strip() + '\n\n' + sec51.strip() + '\n'
new = led.rstrip('\n') + '\n' + add
assert '## 五十、' in new and '## 五十一、' in new
assert new.count('## 五十、') == 1 and new.count('## 五十一、') == 1
assert '\r' not in new
with io.open(LEDGER, 'wb') as fh:
    fh.write(new.encode('utf-8'))
print('LEDGER_APPENDED %d->%d' % (len(led), len(new)))

FOOTER = '—— MA 侧执行台账 · 2026-10-04（基准 HEAD `f02eefd`）'
sec18 = """## 十八、2026-10-07 追加（任务表 #77 收口 + DCD 20261007 七件落册，容器 run24）

- **#77（WebUI 读键 ↔ HTTP 载荷实际键）**：新在册量具 `scripts/scan_webui_payload_keys.py` + 判据锁
  `tests/test_webui_payload_keys.py`（17 条）。这一族缺陷的**消费方是人**：JS 读不存在的键不报错，
  只把那块面板变成空白/`—`，日志一个字都没有。今日读数四格全零（100 个 api.js 方法 / 208 条路由 /
  67 个读点），12 条「只有走通 helper 边才判绿」的读键按 `file:line` 逐条抽查过生产方。
  尺自己有**五条控制腿**（L5＝只把生产方键名改掉也必响），全部在容器 3.11 里响过。台账 §五十。
- **run24 门读数**：404 文件、聚合摘要 `8f19ebc56b3a9c013f1b6ca0af1589ee84ec57d17f27ac242f5acdc09a8a593b`
  三处逐字复现（本机 tar 前 / 容器 / POST）；`GATE_RC=0`（pyflakes 当前 0 / 基线 0）、
  `SUITE_RC=0` **1947 passed / 10 skipped / 502.81s**、`TARGETED_RC=0` 22 passed、`SELFTEST_RC=0`、
  `CTL_RUN_LEFT=0`、`POST_RC=0` 17 passed。
- **DCD 20261007 七件已落册**（台账 §五十一，任务表 #78~#83）：`TOOL_CATALOG` 甲（删 439 行 + 形状锁）、
  温控环比乙（门面注入 `climate_provider` + `climate_comparison` + 键集合锁）、`route_question`
  建门 + `window` 回溯两格、speak 调用点 B、0.3.2 vendoring（认 sha `19bc83a6…`，进运行面随既有变更窗）、
  登录限速乙+丙（`MA_TRUST_PROXY` 默认关 + 全局 60 次/分）、8086 **暂不收口**（DB 直连依赖真实）。
- **交接给下一位的两句话**：① 静态尺的四格归零只锁形状不锁语义，按 DCD 判例 ①，下一批要把 5 张盲区脸
  与 12 条 helper 边并进运行时探针比对；② 本批没动生产码，所以 run24 没有变异档——**下批（#78 起）
  是删 439 行的生产码改动，必须重开变异档**，别沿用 run24 的结论。

"""
with io.open(PROG, 'rb') as fh:
    prog = fh.read().decode('utf-8')
assert prog.count(FOOTER) == 1
key = '\n---\n\n' + FOOTER
assert prog.count(key) == 1, prog.count(key)
newp = prog.replace(key, '\n' + sec18 + '---\n\n' + FOOTER)
assert '## 十八、' in newp and '\r' not in newp
with io.open(PROG, 'wb') as fh:
    fh.write(newp.encode('utf-8'))
print('PROGRESS_APPENDED %d->%d' % (len(prog), len(newp)))
