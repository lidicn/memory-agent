"""run32 落册：三册文档各插一节，数字全部来自 .qoder/tmp-c94-run32-hostlog.txt 与本机实测。

用法：python .qoder/tmp-c94-docs-run32.py check   # 只验锚点
      python .qoder/tmp-c94-docs-run32.py write  # 锚点全对才落盘
行尾按各文件自己的 EOL 归一（三份文档都是 LF，但仍按现状检测，避免整份重写式 diff）。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROG = os.path.join(ROOT, "doc", "审计报告", "进度与交接", "Qoder接手进度_20261004.md")
FIX = os.path.join(ROOT, "doc", "审计报告", "修复与核实", "审计核实与修复_20261001.md")
PLAN = os.path.join(ROOT, "doc", "路线图与规划", "ADM联动执行计划-MA.md")

PROG_ANCHOR = "\n---\n\n—— MA 侧执行台账 · 2026-10-04（基准 HEAD `f02eefd`）\n"
PLAN_ANCHOR = "\n---\n\n## 七、下一阶段：更紧密联动（DCD 2026-10-06）\n"

PROG_SECTION = """
## 二十五、run32 容器权威档：UNVERIFIED 18 → 12 的六格，两种收法各由变异腿自证（2026-10-08，HEAD `e0e684c`）

### 逐格读数（容器 Python 3.11.16，日志 `/tmp/ma_c92_run32.log`，08:18:36 → 08:41:52，本机副本 `.qoder/tmp-c94-run32-hostlog.txt`）

| 格 | 读数 | 这一格凭什么算数 |
| --- | --- | --- |
| -1 TOOLCHAIN | `Python 3.11.16`、`PYTEST=9.1.1`、`PYFLAKES=4.0.2`、`MCP_IMPORT_OK`、`TOOLCHAIN_RC=0` | `/tmp/pylibs` 在容器可写层，recreate 即清空 ⇒ 永远排第一 |
| 0 SNAP | `HASH_LISTED=415 HASH_FILES=415 HASH_MISSING=0`，聚合 `2244c310ebfd3befab7077bdfb89c0974993c8c25541b49339fab2cfff5dfa7f` | 与本机 `.qoder/tmp-c92-hashes-local32.out` 同一份 `hashes.py` + 同一份 filelist，逐字一致；415 = run31 的 414 + 本批新增 `tests/test_vma_phase4_claims_direct.py` |
| 0M MUTFILE | 十件靶文件 `463623 total`、`MUTFILE_RC=0`（harness `mut32=10556`、`anchor=3628`） | 副本树里没有靶文件 = 腿在打空气 |
| 0P ANCHOR | `ANCHOR_HARNESS …/c92snap32_mut32.py legs=9 anchor_ok=9`、`ANCHOR_OK=9 ANCHOR_BAD=0 ANCHOR_MISSING=0` | run31 新立的格在本批第一次真正生效：锚点漂移会在这一格停，不会到格 4 才读成 INVALID |
| 0A AST311 | `AST311_ALL_OK`、`AST311_RC=0` | 本批文件在 3.11 下可解析 |
| 1 PH4 | `13 passed`、`PH4_SKIPPED=0` | 新用例文件在容器一条都不 skip（有 skip 就是假登记） |
| 2 PAIR | `130 passed`、`PAIR_SKIPPED=0` | 后三格**指向的既有断言**（`test_vma_qb_param_landing.py` + `test_vma_phase2_batch3_shared_ruler.py`）在容器真跑，登记不是靠"用例名字像" |
| 3 LOCK | `7 passed` | `BASELINE_CAP` 下调到 12 后量具锁仍全绿，且锁里逐条核已减的 12 格 |
| 4 MUT32 | 对照腿 `[Q-0] rc=0 '143 passed' failed=[] skipped=0`、`WT_UNTOUCHED=True`、`totals: killed=9 survived=0 invalid=0 legs=9`、`MUT32_KILLED_LINES=9` | 143 = 130（PAIR）+ 13（PH4）⇒ 副本树与快照树同口径，后续红都由注入造成 |
| 8 G1..G4 | 四把门 `SELFTEST_*_RC=0` 且 `SCAN_*_RC=0`、四条 `PROBLEM=0`；G4 `DECLARED=41 REGISTERED=29 CASES=48 UNVERIFIED=12 PROBLEM=0` | G1 仍 `STUBS=5 EXEMPT_TOTAL=5 EXEMPT_HIT=5 EXEMPT_STALE=0` ⇒ MA-29 乙未落，五条豁免原样在册（落码那一档必须一起删表，否则 `EXEMPT_STALE` 判红） |
| 9 GATE | `[gates] pyflakes: 当前 0 条，基线 0 条，新增 0，已修 0`、`GATE_RC=0` | 基线只准减；容器 3.11 才是这一格的权威口径（本机 python 无 pyflakes） |
| 10 SUITE | `2109 passed, 10 skipped in 520.91s`、`SUITE_RC=0`、`SUITE_FAILNAMES_RC=1`（`grep '^FAILED '` 无命中）、`SUITE_SKIPPED_LINES=10` | 2109 − 2096（run31）= 13 = 本批新增用例数 ⇒ 新增去向可核；10 条 skip 全为模块级可选依赖（`river` ×4 可见于日志尾部 + provenance 精简检出 1 条等），**用例级 skip 为 0** |
| POST | 二次哈希 `415/415/MISSING=0` 且聚合与 SNAP 逐字相同；G4 复扫读数与跑前相同 | 跑完 520.91s 全量回归之后树还是跑前那棵 |
| 清理 | `MUT_DST_CLEANED_RC=0` | 只删自命名副本树 `/tmp/c92mut32`，别人的目录一律不碰 |

### 这一档证明的三件事

1. **"有断言"这句话不能由用例名证明。** 六格里有三格（`api._closed_ok` / `api.anomaly_report` / `app.metrics_ingest_endpoint`）**没有新增一行用例**——基线文字与现树不符，直接断言是后续批次补进去的、行忘了移。N06..N09 四条腿把这三格各退回行文描述过的坏那侧（恒 `True` 的 ok、实体集格关掉后仍答 0 条 ok、解析不出就回落全屋异常、dedupe_key 不再决定键位），既有 130 条用例当场判红 ⇒ 这三格的减记录是实测，不是目测。
2. **新增直接断言的三格由 N01..N05 证明。** `insert_behavior_event` 自增恒写 0、`get_behavior_event` 不存在返回 `{}` / persons 恒空、`save_arena_snapshot` 的 `MAX(version)` 不带 `WHERE arena_id` / version 恒 1 —— 五条腿都判红新用例，说明断言真的在盯这几位。
3. **旧档本批能跳，是由本档自己量的。** driver 尺4 现读 `SRC_UNTOUCHED=0`（本批只动 `scripts/scan_claimed_semantics.py` 与两份 tests），c81/c85/c82/c91 的靶文件一个都没动 ⇒ "跳过"的前提成立；run31 那次因为动了 `src/memory_agent/app.py` 就全量复跑 60 腿。**前提每次重新测，不引用上一档的记忆。**

### 下一只手（本档之后）

1. **run33**（任务表 #88）：DCD `20261008-AF两件与MA四回执-裁定.md` 三件落码——MA-29 乙（`rule_engine.py:1049-1100` 五支桩改 `{"ok": False, "not_implemented": True, "dry_run": True, …}` + `_log_trigger(..., dry_run=True)`，并删 `scan_stub_claims_success.py` 的 5 条 EXEMPT）、MA-32 甲（删 `auto_discover_persona`：`config.py:209`、`api/config_routes.py:36/:192`、`static/js/pages/settings.js:265-278` 整卡）、MA-34 甲（`activity_inference.py:548/:756/:887` 默认 `True→False`，`api/behavior_routes.py:209/:282/:340` 的 `body.get("persist", True)→False`，`runtime.py:751/:776/:789` 与 `tests/test_drift.py:100`、`tests/test_process_mining.py:326` 显式补 `persist=True`）。
2. **判例 2 的入口对等门**：`scripts/scan_session_owner_parity.py`（现于 `.qoder/tmp-c93-scan_session_owner_parity.py`，四格 self-test 全绿、真树 `BRANCHES=7 READERS=5 GUARDED=5 PROBLEM=0 STALE=0`）移入 `scripts/` 并补锁用例。本批已顺手修掉它的一个真缺陷：`ast.walk` 会把 `elif` 兄弟支的 `check_owner` 算成这一支的闸（"缺闸"假绿），改为只扫分支自有 body。
3. **仍不能自证的**：MA-22 部署面那半条（compose 8086 直曝）、计划 §七 六件里的合并窗与重烤 ⇒ 需 SP 授权窗口。

"""

FIX_SECTION = """
## 五十八、run32 核销：UNVERIFIED 12 的六格逐格对现读（2026-10-08，容器权威档 `REMOTE_DONE`）

| 上一节留下的说法 | 本档现读 | 证据 |
| --- | --- | --- |
| "基线只准减，减了要给直接断言" | `UNVERIFIED 18 → 12`，容器与本机同读 `DECLARED=41 REGISTERED=29 CASES=48 UNVERIFIED=12 PROBLEM=0` | `SCAN_G4_RC=0`、`POST_SCAN_G4_RC=0`（跑完全量回归后复扫同数）；`test_vma_phase2_claims_gauge.py` 的 `BASELINE_CAP = 12` 单跑 `7 passed` |
| "六格都有断言" | 三种收法各由腿证明：新增断言三格 N01..N05、指向既有断言三格 N06..N09 | `totals: killed=9 survived=0 invalid=0 legs=9`、`MUT32_KILLED_LINES=9`、对照腿 `143 passed skipped=0`、`WT_UNTOUCHED=True` |
| "既有用例真在跑（不是名字像）" | `130 passed`、`PAIR_SKIPPED=0` | 格 2 单跑 `test_vma_qb_param_landing.py` + `test_vma_phase2_batch3_shared_ruler.py` |
| "新用例不是本机 skip 假绿" | 容器 `13 passed`、`PH4_SKIPPED=0` | 格 1；本机 mcp/river 差异只影响别的文件，本批文件本机也全绿 |
| "上一档改过 src 所以要复跑 60 腿，这一档不改 src" | `SRC_UNTOUCHED=0`（本批只动 1 份 scripts + 2 份 tests） | driver 尺4 现读，非引用记忆 ⇒ 本批旧四档不成批复跑 |
| "G1 的五条 rule_engine 桩仍被豁免" | `STUBS=5 PROBLEM=0 EXEMPT_TOTAL=5 EXEMPT_HIT=5 EXEMPT_STALE=0` | 与 MA-29 乙未落码一致；落码那一档删表后这一行必须重读 |
| "工作树 = 跑前那棵" | SNAP 与 POST 聚合同为 `2244c310…dfa7f`（`415/415/MISSING=0`） | 本机 `.qoder/tmp-c92-hashes-local32.out` 与容器格 0/格 POST 三方逐字一致 |
| "全量回归没退化" | `2109 passed, 10 skipped in 520.91s`、`SUITE_RC=0` | 2109 − 2096 = 13 = 本批新增用例数；`SUITE_SKIPPED_LINES=10` 与摘要同数，尾部可见 4 条 `river` + 1 条 provenance，用例级 skip = 0 |
| pyflakes | `当前 0 条，基线 0 条，新增 0，已修 0` | 容器口径；本机那条门（无 pyflakes）不参与判定 |

### 本档新落的三件（为 run33 预备，全部现读）

- **MA-34 甲的调用点枚举已完成**：全仓 grep `mine_process|mine_drift|audit_rule_recall` ⇒ 依赖默认 `persist=True` 的只有 `behavior_routes.py:209/:282/:340`、`runtime.py:751/:776/:789`、`tests/test_drift.py:100`、`tests/test_process_mining.py:326` 六处。`mcp_server.py:1465/:1479/:1531/:1538/:1640/:1651` 与 `behavior_routes.py:305` 七个调用点**本来就显式传 persist**，翻转默认值不会静默改动 MCP 读写工具面。
- **MA-32 甲的引用面已完成**：`auto_discover_persona` 全仓仅 4 处（`config.py:209`、`api/config_routes.py:36/:192`、`static/js/pages/settings.js:274` 所在的 265-278 卡片），tests 目录零引用；`settings.js` 是 CRLF（CR=755），补丁必须 `newline=''` 保行尾。
- **MA-29 乙的下游口径已核**：`device_feed.py:266` 按 `res.get("dry_run")` 记 `logged_only`、按 `res.get("ok")` 记 `dispatched` ⇒ 五支桩改成 `ok=False + dry_run=True` 之后，`test_device_event_feed.py:285/:644/:699` 的 `logged_only==1 / dispatched==0` 断言方向不变，不会改出假绿。

### 判例 2 门在预备阶段修掉的一个真缺陷

`.qoder/tmp-c93-scan_session_owner_parity.py` 原按 `ast.walk(整个 If 节点)` 找闸，于是 **`elif` 兄弟支的 `check_owner` 会被算进这一支**——"某入口缺闸"这一格在合成用例里读成 0 判红（假绿）。改为只扫分支自有 `body` 之后，四格 self-test 全绿（clean 0/0、missing 1/0、unregistered 1/1、stale 0/1），真树现读 `FILE=src/memory_agent/acp_server.py BRANCHES=7 READERS=5 GUARDED=5 EXEMPT_TOTAL=0 EXEMPT_HIT=0 PROBLEM=0 STALE=0`，五个读者入口逐条给 file:line（`M_SESSION_NEW:374 conflict`、`M_SESSION_HISTORY:384`、`M_SESSION_DELETE:401`、`M_CANCEL:411`、`M_PROMPT:428` 均 `check_owner`）。这条门正是 MA-36 当初缺 `M_PROMPT` 闸的那类缺陷的门禁化。

"""

PLAN_SECTION = """
### 6.28 run32（2026-10-08）：语义覆盖声明第三批核销，`UNVERIFIED 18 → 12`

容器权威档（`c92snap32`，HEAD `e0e684c`，08:18:36 → 08:41:52）逐格：`TOOLCHAIN_RC=0`（3.11.16 + `MCP_IMPORT_OK`）、
`SNAP 415/415/MISSING=0` 聚合 `2244c310…dfa7f`（与本机逐字一致，POST 同值）、
`ANCHOR_OK=9 ANCHOR_BAD=0 ANCHOR_MISSING=0`、`PH4 13 passed SKIPPED=0`、`PAIR 130 passed SKIPPED=0`、`LOCK 7 passed`、
`MUT32 killed=9 survived=0 invalid=0` 且对照腿 `143 passed skipped=0`、`WT_UNTOUCHED=True`、
四把门 `PROBLEM=0`（G4：`DECLARED=41 REGISTERED=29 CASES=48 UNVERIFIED=12`）、pyflakes `0/0/0/0`、
`SUITE 2109 passed / 10 skipped / 520.91s`（2109 − 2096 = 13 = 本批新增用例数）、`MUT_DST_CLEANED_RC=0`。

六格的两种收法都在这里落定：三格新增直接断言（`store.insert_behavior_event` 自增、`store.get_behavior_event` 回读、
`store.save_arena_snapshot` 版本递增与跨分区隔离），三格指向既有断言（`api._closed_ok`、`api.anomaly_report`、
`app.metrics_ingest_endpoint`）——后者**没有新增一行用例**，由 N06..N09 四条变异腿把那三个语义退回坏的那侧、
让 `test_vma_qb_param_landing.py` / `test_vma_phase2_batch3_shared_ruler.py` 当场判红来证明登记不是目测。

DCD `20261008-AF两件与MA四回执-裁定.md` 的三件已按裁定转为待落码（run33，任务表 #88）：MA-29 乙五支桩 + 删 G1 豁免表、
MA-32 甲删 `auto_discover_persona` 四处、MA-34 甲 `persist` 默认翻转的六处调用点（枚举与反证已在本档现读）；
判例 2 的入口对等门已成形（`.qoder/tmp-c93-scan_session_owner_parity.py`，真树 `BRANCHES=7 READERS=5 GUARDED=5 PROBLEM=0 STALE=0`），
run33 移入 `scripts/` 并补锁。sessionId 走乙并已登记"下轮可再议甲"。

"""


def read(path):
    with io.open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def normalize(text, eol):
    if eol == "\r\n":
        return text.replace("\r\n", "\n").replace("\n", "\r\n")
    return text.replace("\r\n", "\n")


def planned():
    out = []
    for path, anchor, sect in ((PROG, PROG_ANCHOR, PROG_SECTION),
                               (FIX, None, FIX_SECTION),
                               (PLAN, PLAN_ANCHOR, PLAN_SECTION)):
        text = read(path)
        eol = "\r\n" if "\r\n" in text else "\n"
        body = normalize(sect, eol).strip("\n")
        head = body.split(eol, 1)[0]
        if head in text:
            print("PATCH_ABORT %s 这一节已在档里：%s" % (os.path.basename(path), head[:40]))
            return None
        if anchor is None:
            new = text.rstrip("\n") + eol + eol + body + eol
        else:
            a = normalize(anchor, eol)
            n = text.count(a)
            if n != 1:
                print("PATCH_ABORT %s 锚点命中 %d 次（要求恰好 1）" % (os.path.basename(path), n))
                return None
            new = text.replace(a, eol + body + eol + a, 1)
        out.append((path, new, eol))
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    items = planned()
    if items is None:
        return 2
    if mode != "write":
        for path, new, eol in items:
            print("ANCHOR_OK %s eol=%s +%d 行" % (os.path.basename(path), repr(eol),
                                                  new.count("\n") - read(path).count("\n")))
        return 0
    for path, new, eol in items:
        with io.open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
        raw = open(path, "rb").read()
        print("WROTE %s CR=%d LF=%d" % (os.path.basename(path), raw.count(b"\r"), raw.count(b"\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
