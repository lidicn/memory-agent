## 三十九、A2/A7 静态结论核销第三批：三处"只有静态结论"变成有读数，P4-3 的评级被实测推翻，以及一次把 BOM 当量具问题（2026-10-06，任务表 #61，容器 run11）

审计报告锚点（按 `decisions/20261005-AF用户WebUI与MA四件与CVE-裁定.md` **:58** 的引用纪律，一律「报告 + 行号」）：
`元宝/memory-agent_第二轮审计报告.md` :121（P2-4）、`元宝/memory-agent_第四轮审计报告.md` :40（P2-5）、
`元宝/memory-agent_第九轮审计报告.md` :30（P2-8）、`元宝/A5_依赖供应链审计.md` :23（P4-1）、
`元宝/memory-agent_第十三轮审计报告.md` :54（P4-2）与 :108（P4-3）、
`元宝/A2_…` :247/:249 与 `元宝/A7_…` §5.1（静态结论清单）。
"编号"三套台账会撞号（任务表 #NN / 报告 :NN / commit hash），本节一律写明是哪一套。

口径（任务表 #61 的原话）：**能运行时验证的在容器里用生产库只读验证；属信息完整性/跨仓键名的走 DCD，不私自新增出境键。**

### 一、四件处置一览

| 件 | 审计评级 | 本批处置 | 依据 |
|---|---|---|---|
| P4-3 `intent_inference.py` 的 `now` 兜底 | P4（观察项，判「只在所有 ts 都失败时兜底」） | **改码 + 3 把锁**——评级被实测推翻：它是静默空结果 + 500 双通道缺陷 | §二 |
| P2-4 同日多次出现的到家后活动预测 | P2（第二轮点名，判"要运行时才能确认"） | 现读已修（`_appearances_by_day:97` 分组 + `_arrival_dt:122` 排序），补 **2 把顺序不变锁** + N3 变异 | §三 |
| P2-5 `KEYWORD_DOMAINS` 同名覆盖 | P2（第四轮 :40） | 修的是**注释**、0 条测试 ⇒ 补 **6 把锁**（AST 数定义 + 键人口 + 同名表一致性）；词表合并**不自主**，呈 DCD | §四 |
| P2-8 `learning_*` 出 src | P2（第九轮 :30） | 补 **6 把锁**（含 8 参数量化的不可导入）+ **在役镜像现读**：仓内成立、`/app` 不成立 | §五 |
| P4-1 pm4py AGPLv3 | P4 | **呈 DCD**（许可面 = 商用决定，不是技术决定） | §六 |
| P4-2 `define_activity` 的 `ok` | P4 | **核销为"观察前提已过时"**，不加出境键 | §六 |

### 二、P4-3：不是"所有 ts 都失败才兜底"，是"任何一条非空坏 ts 就兜底"

第十三轮 :108 那句 `max()` 外面包着 `try/except (ValueError, TypeError) → latest_ts = datetime.now()`。
审计按"全批解析失败才兜底"定级 P4。同一进程内成对实测（本机 3.13，`window_min=10`，历史事件取
`2026-03-05T21:00:00`——挑一个远早于墙钟的日子，一旦兜到墙钟就必然落空）：

| 用例 | 改前（`git show HEAD:` 那份） | 改后（本批） |
|---|---|---|
| A 只有一条历史好行 | `HIT in_win=1` | `HIT in_win=1` |
| B 历史好行 + `0000-00-00 00:00:00` | **`NONE`** | `HIT in_win=1` |
| C 历史好行 + `not-a-ts` | **`NONE`** | `HIT in_win=1` |
| D 历史好行 + `None` + 空串 | `HIT in_win=1` | `HIT in_win=1` |
| E 只有一条 int 型 ts（`1700000000`） | **`RAISED TypeError`** | `NONE` |

两条失败通道：

1. **静默归零**（B/C）：一条坏行使整句生成器抛 `ValueError` ⇒ 基准换成容器墙钟 ⇒ 窗口变成 `now-10min` ⇒
   历史事件全部落在窗外 ⇒ `infer_intent` 返回 None ⇒ `/api/behaviors/intents` 交出 `{"intents": []}` 且 `ok:true`；
2. **500**（E）：`max()` 里的 `TypeError` 被外层 catch 兜成 `now`，但**过滤循环**里
   `fromisoformat(1700000000)` 会再抛一次，那里只有 `except ValueError` ⇒ 逃出到调用点，
   而 `api/behavior_routes.py:990`、`mcp_server.py:2770` 都没有 try/except。

D 给出毒发的**准确条件**：`None`/空串不会毒（`max` 那句里的 `if ev.get("server_ts")` 把它们挡在生成器外），
必须是"非空但解析不出"那一类。这条不写清楚，下一个读代码的人会把守卫放错位置。

改法：新增 `_event_dt(raw)`（`intent_inference.py:112`）——非字符串 / 空 / 畸形一律返回 `None`、绝不抛；
基准取解析成功集合的 `max()`，一条都解析不出就 `return None`；`datetime.now()` 整条路径撤掉。
它在这条路径上不只是危险，还是**死代码**：没有任何 ts 能解析时，过滤循环本来就会把每条事件丢掉 ⇒
`recent_events` 为空 ⇒ 返回 None，所以撤兜底对可达输入集行为不变。
锁加在 `tests/test_vma121_intent_and_pii.py`（3 把），其中一把把 `imod.datetime` 换成
`class _NoClock(datetime)`（`now()` 直接 `raise AssertionError`）⇒ 有人把墙钟兜底改回来就当场判红。
变异 N1（`except ValueError: return datetime.now()`）、N2（拆掉 `isinstance(raw, str)` 守卫）各咬一次。

### 三、P2-4：顺序不变性 + 同日窗口精确计数

第二轮 :121 要的是"同日多次出现会不会重复计天"的运行时答案。现读：实现早已改成
`_appearances_by_day`（`:97`）按家庭墙钟自然日 `dict` 分组、`_arrival_dt`（`:122`）先 `sorted()` 再取午后首次，
所以答案是"不会重复计"。但**没有任何锁**看守这条顺序依赖，于是补两把：

- `test_interleaved_same_day_events_do_not_inflate_day_counts`：同一批事件按**升序 / 降序 / 交错**三种喂法各跑一次，
  三档必须交出同一读数（`data_days == 3`、`sample_days == 3`、`predicted_hour == 19.0`）。
  活动集合按**频次字典**比而不是列表顺序——`predict_post_arrival_activities` 的标签来自每窗口一个 `set`，
  同频并列时列表顺序本来就不稳定，按顺序断言等于把实现的不确定性冻进测试；
- `test_foreign_persons_days_do_not_open_extra_windows`：别人的日子不许给这个人开窗口。

现网取数就是 `ORDER BY server_ts DESC`（`store.py:2369`），所以"降序喂"不是假想敌。
变异 N3（`sorted(dts)` → `list(dts)`）被第一把咬住。

### 四、P2-5：修复只有注释在守，补成 AST 锁；剩下的三处口径呈 DCD

第四轮 :40 那份分叉（英文 7 键被中文版覆盖、`ac` 从 `('climate.ac',)` 退成 `('climate',)`）当时是**合并成一份**修的：
现读 `insights/utils.py` 里 `^KEYWORD_DOMAINS` = **1**（:1104），:744/:1102 是解释性注释。
但合并之后**一条测试都没加**——注释挡不住第三次同名定义。补 6 把：定义次数（AST，含 `AnnAssign`——
真实那份就是带注解的赋值，只数 `ast.Assign` 会一条都量不到）、七个英文键逐个出 domain、`ac/aircon` 的值、
ASCII 键**人口集合**、中文键不回归、以及"同名 `_DOMAINS` 表若有第二份则内容必须相等"。

同一批 AST 普查（量具不是文本 grep）顺手量出**三处口径**，都不动、呈 DCD：

| 位置 | 内容 | 关系 |
|---|---|---|
| `insights/utils.py:1104` | `KEYWORD_DOMAINS` 26 键（8 ASCII + 18 中文） | 服务 `resolve_domains:1036`，`insights_legacy.py:369/:425` 调 |
| `insights/parser/entity.py:35` | `KEYWORD_DOMAINS` 33 键（5 ASCII + 28 中文） | 服务新引擎门面 `insights/api.py:321` |
| `insights/utils.py:271` 与 `:1091` | `CATEGORY_DOMAINS` 两份，7 键**逐键逐值相同** | 后份静默覆盖前份，内容相等 ⇒ 冗余而非分叉 |

两份 `KEYWORD_DOMAINS` 的 ASCII 键交集只有 `ac/light/tv`：`aircon/switch/media/sensor/climate` 只在 utils 版，
`motion/occupancy` 只在 parser 版 ⇒ 同一句英文查询走哪条读路径会解出不同 domain 集合（中文两侧都覆盖，现网看不出来）。
**改内置词表不在自主范围**（任务表 #38 那条裁定明令），所以本批只把差异锁住、不合并。
变异 N4（再加一处模块级定义）咬住第 1 把，N8（只改 `CATEGORY_DOMAINS` 后一份）咬住最后一把。

### 五、P2-8：仓里成立，在役镜像里不成立

第九轮 :30 要的是"learning_* 移出去之后，谁都别再在运行时撞上它们"。两代形状都有读数：

- **快照 / 仓**（`/tmp/c37snap20261006a`，容器 3.11）：`src/memory_agent/learning_*.py` = **0** 个，
  `attic/learning/learning_*.py` = **8** 个；`importlib.import_module("memory_agent.learning_api")` ⇒
  `ModuleNotFoundError: No module named 'memory_agent.learning_api'`（**命名空间里就不存在**，
  不再是上一代"存在但一碰 `learning_models` 就炸"）；按包风格加载 attic 那份
  （`spec_from_file_location("memory_agent.learning_analyzer", "attic/learning/…")`）⇒
  `ModuleNotFoundError: No module named 'learning_models'`（裸绝对导入**按设计保留**，登记不悄悄改）。
- **在役 `/app`（本节唯一让人不舒服的读数）**：`ls /app/src/memory_agent/ | grep -c learning_` = **8**、
  `/app` 下**没有** `attic`（`APP_ATTIC_PRESENT=0`）；逐个 import 的结果是 `learning_models IMPORTED`、
  其余七个 `ModuleNotFoundError`（报的都是裸名 `learning_analyzer` / `learning_models`）
  ⇒ **线上镜像仍是 attic 之前的布局**。这不是判据失败，而是"镜像重烤未获授权"的既有格：
  与 #57 的网络隔离、端口 9080/9443 同一类——**出网的是仓，在役的是旧镜像**，
  凡读"线上行为"必须以 `P28_LIVE` 那格为准，不能拿仓里的锁代替线上。

6 把锁（含 8 参数量化的命名空间不可导入、attic 裸导入计数 18/7/0、`build_router` 在 src+tests 的调用点 = 0）。
`build_router` 这把最初写成 `assert "build_router" not in fh.read()`，被自家测试文件的 docstring 咬了一次
（扫描器读到了自己）⇒ 改成 AST 的 `ast.Call` 名字扫描。变异 N5（src 里放回一个 learning 桩模块）、
N6（把 attic 的裸导入改成包内相对）各咬一次。

### 六、P4×3 的首次处置：一件改码、一件核销为过时、一件呈裁

- **P4-1（A5 :23-45，pm4py AGPLv3）**：现读 `find_spec("pm4py")` 在 `/app` 与 `/app + /tmp/pylibs` 两条
  PYTHONPATH 下都是 **None**（两次 `RC=0`，2026-10-05T18:15Z）⇒ 在役运行时不可达；
  许可说明早在 `pyproject.toml:24` 自标；主路径是 `algo_kernel.py:488-495` 的自研 DFG，
  pm4py 只在 `:618` 那条可选增强参数里。要裁的是**商用档位**
  （甲 维持 + 发版前法务门 / 乙 从 extras 删除 / 丙 采购许可），不是技术可达性 ⇒ 呈 DCD。
- **P4-2（第十三轮 :54）**：审计引的那句 message（含「尚未套用自定义规则」）在当前 HEAD 上**已不存在**
  （`grep -rn '尚未套用自定义规则' src tests` = **0**，唯一命中在审计报告自己那一行），
  `insights/api.py:694-700` 现在写的是「下次 `infer_activities` 起生效」，并把 legacy 的
  `coverage_warning` 原样拼进 message（`insights_legacy.py:2185` 出）。"规则真的被读到并套用"由既有锁看守
  （`tests/test_vma_activity_semantic.py:162`，本批 TARGETED/TOUCHED 链里跑过）
  ⇒ 第十三轮描述的"已存储但不生效"这个前提被任务表 #37 那批改掉了，`ok=true` 与真实语义一致，
  **不新增 `partial`/`applied` 这类出境键**，按"前提过时 + 既有锁自证"核销。
- **P4-3**：见 §二，已改码（评级从 P4 升为真缺陷是本批自己判的，判据是上面那组成对读数）。

### 七、BOM：量具读不动的那三个文件，以及为什么 CR 不在这批锁死

三个在册 `.py` 以 U+FEFF 开头（`scripts/reindex_embeddings.py`、`tests/test_change_attribution.py`、
`tests/test_wo_ma_012_g1_security.py`）。CPython 的 importer 会嗅 `utf-8-sig`，所以 pytest 一直绿；
但任何按 `open(p, encoding="utf-8")` + `ast.parse` 写的门读它们就是 `SyntaxError`，
而带 `except SyntaxError: continue` 的扫描器会把这个文件**从审计覆盖面里静默摘掉**——
和上一批"工作区 CRLF 让 `PATCH_NOT_FOUND` 被读成语义改不动"是同一族（量具的形状问题被读成代码结论）。
处置：字节级去掉前 3 字节（每个文件 −3 bytes，CR 一个都不碰），加 3 把锁
（四个扫描根无 BOM / 每个 .py 以 plain utf-8 解析且**不许** `utf-8-sig` 兜底 / 那三个文件确实在池子里且池 ≥300）。
变异 N7 给 `reindex_embeddings.py` 加回 BOM，3 把一起响。

CR 这批复核了，**没有**锁死，理由写在这里：`git show HEAD:scripts/reindex_embeddings.py | od -c` 里约 120 个 CR，
工作区那份只有 1 个 ⇒ blob 与检出在 `.gitattributes`（`* text=auto eol=lf`）下不是同一形状，
仓里另有约 40 个在册文件工作区带 CR。在这批里加一条"全仓 CR == 0"的锁会把一批与本批无关的既红带进门。
按交付纪律 §六.9：本批**我自己写的每个文件**按字节量 CR=0、BOM=False，既有形状只在台账登记。

### 八、run11 权威门读数（容器 `memory-agent`，快照 `/tmp/c37snap20261006a`，基线 HEAD `c3f2719`，371 文件，2026-10-05T18:12→18:25Z）

| 格 | 读数 | RC |
|---|---|---|
| TOOLCHAIN | pytest **9.1.1** / pyflakes **4.0.2** / Python **3.11.16**（`/tmp/pylibs` 未被 recreate 清掉） | 0 |
| SOURCES | 乙′/#60 十三件指纹 SCAN_DAY=3 HOUR_BUCKET=3 OFFSET_BIND=1 TS_GE=1 SCAN_HOURS=4 WINDOW_HI=5 SUBSTR_WHOLE=7 SUBSTR_IN_HOUR_STMT=0 BRANCH_FROM_FILT=1 HOUR_STMT_LINES=32 TEST_DEFS=13 SCAN_CLASS_CONST=2 SCAN_RECEIVERS=1 SCAN_DUP_RETURN=1 ⇒ **与 run10 一字不动**；本批 INTENT_NOW=0 INTENT_EVENT_DT=2 UTILS_KD_DEF=1 UTILS_CD_DEF=2 SRC_LEARNING=0 ATTIC_LEARNING=8 BOM_PY=0 P25_TESTDEFS=6 P28_TESTDEFS=6 BOM_TESTDEFS=3；wc 合计 **2903** | 0 |
| GATE | pyflakes 当前 0 / 基线 0 / 新增 0 / 已修 0，`GATE_MENTIONS_ATTIC=0` | 0 |
| SUITE | **1449 passed, 10 skipped in 307.81s**（run10 是 1420/10 ⇒ 本批净 +29 条） | 0 |
| TARGETED | 本批 5 个文件 **61 passed** | 0 |
| PREVBATCH | run10 那条链 **69 passed**（与 run10 同数） | 0 |
| TOUCHED | **338 passed, 3 skipped**（= run10 的 277 + 本批 61） | 0 |
| DAYBATCH | self-test 21 HIT / 12 CLEAN；src total=**97** external=44 literal=43 config=2 date_math=6 local=2 bounded=42 unguarded=55 marked=2；attic 4/4/2/2 ⇒ **与 run10 同一份台账，本批没扰动量具** | 0 |
| ROUTEBATCH | handler_shaped=197 mounted=196 referenced=1 unmounted=0 problems=0 | 0 |
| P28_LIVE / P28_SNAP | 见 §五（`/app` 8 个模块仍在；快照侧命名空间不可导入） | 0 / 0 |
| MUT11 | N1..N8 **全部咬住**，`restored_identical=OK`，`MUTATION_BAD=0` | 0 |
| MUT10 | M1..M10 **全部咬住**，咬到的条数 **5/6/5/8/3/2/4/1/1/24** 与 run10 **逐格相同**，`MUTATION_BAD=0` | 0 |

**没有 PROBE 格**（本批刻意不碰 insights 读路径）：生产库成对读数与 run10 同一棵树，沿用 §三十八 的正式基准
（分层 = 改前单条 LIMIT 的 **0.67×**、= 前缀量具的 **3.28×**）。MUT10 那一格逐格同数，就是"没渗到读路径"的自证。
本机 3.13 的 P4-3 成对读数（§二）口径标注清楚：**本机成对**，容器侧同一判据由 TARGETED 那 61 条锁看守。

### 九、交付登记与下一步

本节对应的 commit（出网后现读 `git rev-list --count origin/main..HEAD` 必须为 **0**）分三件：
P4-3 的改码 + 3 把锁；三族锁 + BOM 卫生；三份文档登记。
**零新增出境键**，跨仓契约面无变化；`.gates-baseline.txt` 一字未改（`git diff --stat` 对它是空）。

- 呈 DCD：`关键决策部/inbox/20261006-MA-pm4py许可档位与内置词表三处口径-决策申请.md`
  （Q1 pm4py 档位、Q2 词表口径档位、Q3 追认 `CATEGORY_DOMAINS:271` 那份冗余可否删）。
- 仍开着的格（不在本批）：任务表 #40 Q-B 的⑤⑥、#58 Q-D 的参数级核对、#9/#10/#38/#5、Q5 区间数字、
  FTS 短词下限、端口 9080/9443 追认、A7 §5.1 的三项覆盖缺口（`llm_ask(CC=45)` 重端点压测、72h 长跑、
  `voice_util` 35.1% / `behavior_predictor` 27.8%）；在役面（attic、网络隔离、端口）统一等**镜像重烤 + recreate 窗口**。
- 顺手登记一条给并行工位的：`scripts/hc_check.py`（未跟踪，`import json` 未使用）会被快照带走；
  pyflakes 门只扫 `src/memory_agent` 所以不会造成假红，但它不属于任何台账，请它的主人收编或删掉。
