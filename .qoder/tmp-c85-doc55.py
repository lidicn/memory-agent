import io
import re
import sys

LEDGER = "doc/审计报告/修复与核实/审计核实与修复_20261001.md"

STATUS = {
    "MA-25": "✅ **已修** `800c17b`",
    "MA-26": "✅ **已修** `800c17b`（锁在本机 skip、容器 3.11 档真跑）",
    "MA-27": "✅ **已修** `800c17b`",
    "MA-28": "✅ **已修** `800c17b`（`scope` 不收紧，口径见 §五十五 三.1）",
    "MA-29": "⏸ **不自办**：产品口径，随 20261008 回执 §四 Q1 呈 DCD",
    "MA-30": "✅ **已修** `800c17b`（选如实 501，理由见 §五十五 三.2）",
    "MA-31": "✅ **已修** `800c17b`（修在“接住之后要能重跑”，非补 try）",
    "MA-32": "⏸ **不自办**：二选一呈 DCD（回执 §四 Q2）",
    "MA-33": "✅ **已修** `800c17b`",
    "MA-34": "⏸ **不自办**：写盘默认值口径，呈 DCD（回执 §四 Q3）",
    "MA-35": "✅ **已修** `800c17b`（新增 config 键，见 §五十五 三.3）",
}

SEC = """
## 五十五、任务表 #85 落码：MA-25~MA-35 八条修复 + 三条呈 DCD（commit `800c17b`，run28 容器权威档）

### 一、核销索引表（把 §五十四 那张"状态"列变成能逐条查锁的一张表）

编号 / 首报轮次 / 严重度按报告原样；轮次取自 `grep -lF "MA-XX　" doc/审计报告/2期/*.md`
（认标题里那个全角空格），MA-36/37 由第二十轮（终极轮）立号。落点与锁号是**当前树现读**，
不是报告锚在审计快照上的行号。

| 编号 | 首报轮次 | 严重度 | 一句话缺陷 | 处置（commit） | 修复落点 ／ 判据锁 file:line | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| MA-25 | 第十一轮 | 🔴 High | `LLMRouter.reconfigure` 整表重建后不关旧 provider ⇒ 每次 `/api/config` 热更新漏一批 `httpx.AsyncClient` | `800c17b` | `llm_client.py:438`~`:442`（`stale` 先留手，建新表后逐个 `close()`）／ `tests/test_rounds11_19_ma25_35_fixes.py:58`、`:78`（反例：新表不能被顺手关） | ✅ 闭合 |
| MA-26 | 第十二轮 | 🔴 High | `save_skill` 的自增基数只从**入参** frontmatter 取，从不读盘上那份 ⇒ 入参不带版本号时盘上 v5 被覆盖回 v1（版本倒退，消费方按版本号判"有没有更新"） | `800c17b` | `mcp_server.py:2716`（`max(盘上, 入参) + 1`）／ `tests/test_rounds11_19_ma25_35_fixes.py:104`（1→2→盘 v5→6→入参 40→41） | ✅ 闭合（本机 skip：`mcp` SDK 过旧取不到已注册工具；容器 3.11 档真跑） |
| MA-27 | 第十二轮 | 🟠 Medium | `list_agent_memories` 空 `member_id` 时回显 `"all"`，而实现是 fail-closed 只返回公共记忆 ⇒ 响应把自己说成跨成员全量 | `800c17b` | `agent_memory.py:525`、`:526`（新增 `member_scope`）／ `tests/test_rounds11_19_ma25_35_fixes.py:160`、`:169` | ✅ 闭合 |
| MA-28 | 第十三轮 | 🔴 High | `teach_signal` 的 `dry_run` 在 MCP 门面里一句 `return {"ok": True, … "参数校验通过"}` —— 一行判据都没走 | `800c17b` | 校验并入本体 `signal_learning.py:119`~`:141`（与写入同一串判据），门面 `mcp_server.py:1749` 只转发／ `tests/test_rounds11_19_ma25_35_fixes.py:203`、`:211`、`:219`、`:229`、`:236`、`:248`（枚举与 ToolSpec 同源）、`:264`（AST：门面不再有假回执） | ✅ 闭合 |
| MA-29 | 第十四轮 | 🔴 High | 规则引擎五个动作是桩（各带 `# TODO`、只 `logger.info`）却回 `{"ok": True}` | 不自办 | — | ⏸ 呈 DCD（20261008 回执 §四 Q1） |
| MA-30 | 第十四轮 | 🔴 High | `POST /api/nr/execute-action` 零实现却回「动作已执行」⇒ Node-RED 侧以为设备被动过 | `800c17b` | `api/nr_routes.py:56`~`:70`（如实 501，**路径不变**）／ `tests/test_rounds11_19_ma25_35_fixes.py:295`、`:305`（两条路由仍在册） | ✅ 闭合 |
| MA-31 | 第十五轮 | 🔴 High | 自我日记任务一次异常即永久停摆（机制更正见 §五十四 二：不是"没接住"，是"接住了不重跑"） | `800c17b` | `runtime.py:298`（外壳 `while` + 指数退避重启，上限 3600s）、`:318`（轮次顶层 handler 改为 `print` + `raise`）／ `tests/test_rounds11_19_ma25_35_fixes.py:328`、`:346`、`:358` | ✅ 闭合 |
| MA-32 | 第十六轮 | 🟠 Medium | `auto_discovery` 开关可写、可回显、有 UI，但全仓无业务消费点 | 不自办 | — | ⏸ 呈 DCD（回执 §四 Q2） |
| MA-33 | 第十七轮 | 🔴 High | 内置技能声明"真源 = 最新版本"，同步判据却只有 `os.path.isfile(dst)` ⇒ 旧部署升级后永久停在旧版且无日志 | `800c17b` | `mcp_server.py:219`~`:278`（版本比较；解析不出版本当 0 以修复；更高盘上版本受保护并计 skip 数）／ `tests/test_rounds11_19_ma25_35_fixes.py:401`、`:411`、`:421`、`:430`、`:441` | ✅ 闭合 |
| MA-34 | 第十八轮 | 🟠 Medium | `persist: bool = True` 的默认写盘口径 | 不自办 | — | ⏸ 呈 DCD（回执 §四 Q3） |
| MA-35 | 第十九轮 | 🔴 High | 保留期清理只在启动跑一次（唯一调用点在 `startup`）⇒ 长跑不重启的容器里 events 跨度远大于声明的 90 天 | `800c17b` | `runtime.py:137`~`:166`（常驻 `while`，每轮独立 try，失败按 `min(interval, 600)` 短退避）、`config.py:107`（`data_retention_interval_seconds`）／ `tests/test_rounds11_19_ma25_35_fixes.py:486`、`:498`、`:509`（AST：必须是 `While` 且 `startup` 指向它） | ✅ 闭合 |
| MA-36 | 第二十轮 | 🔴 **Critical** | ACP `M_PROMPT` 是第四个读 `sessionId` 却不校验属主的入口 | `af3e0fe`（run27） | `acp_server.py:433`（`M_PROMPT` 入口的 `check_owner`）／ `tests/test_acp_round20_owner_isolation.py`（8 锁 + 入口对等门） | ✅ 闭合 |
| MA-37 | 第二十轮 | 🔴 High | `SessionStore.new` 对已存在 sid 无条件覆盖属主 ⇒ 后来者夺走会话 | `af3e0fe` | `acp_server.py:85`~`:86`（冲突抛 `SessionOwnerConflict`，`:377` 转 `-32602` 错误信封）／ 同上 | ✅ 闭合 |

### 二、这一批的修法有一条共同原则

八条全部属于同一族：**声明与实现不是一条线**。所以每条锁都不只看"改没改"，而是把
报告「回归验证清单」那一行原样变成断言；三条锁干脆是 AST 形状锁，防的是"以后有人把它改回去"：

- `test_mcp_facade_no_longer_short_circuits_dry_run` —— 门面的字面量里再出现「参数校验通过」就红；
- `test_self_diary_round_reraises_to_the_wrapper` —— **只罩函数体第一层 try 的 handler**：
  内层那格 per-iteration「生成失败」按语义该吞掉继续等下一天，把 raise 塞进内层反而会让
  一轮失败终止整个循环（这条口径是本机跑红以后才写对的）；
- `test_retention_task_is_registered_at_startup` —— `_run_retention_cleanup` 里必须有 `While`，
  且 `startup` 的调用点仍指向它。

### 三、四处口径偏离（呈 DCD 追认，不自己给自己盖章）

1. **MA-28 的 `scope` 不做枚举收紧**。ToolSpec 里 `scope` 有枚举，但硬排除实际按 `entity_id`
   生效（`insights/repository.py` 的取用口径），`scope` 只是标签；收紧它会改变既有语义，
   不是修 bug。本轮只把 `kind` / `exclusion_type` 两项按声明收紧。
2. **MA-30 选"如实 501"而不是补执行器**。报告给的两支里，实测 `/api/nr/*` 在全仓
   （含 `web/`、测试、手册）**零消费点**，出货的 Node-RED 流只吃 `/api/analyze/water_purifier`；
   补执行器等于新增一个没有授权来源的设备写面，风险大于收益。红线照旧：路径一律不改，
   回执只允许从"假成功"变成"如实失败"。
3. **MA-35 新增的 `data_retention_interval_seconds` 不进 `WRITABLE_FIELDS`**，因此也不进
   `NUMERIC_BOUNDS`。`test_bounds_table_is_exactly_the_writable_numeric_surface` 断言的是
   "表 == HTTP 写得动的数值键全集"，本批实测 72 个数值字段 / 24 条区间 / 24 个可写 ⇒ 仍绿。
   间隔只能经 `config.json` / 环境变量改，与既有的 47（现 48）个非可写数值键同口径。
4. **MA-25 关旧 provider 失败走 `print` 而不是 `except: pass`**。门禁 `except-pass-broad`
   的判据是"空体"，用 `pass` 就要往 `.gates-baseline.txt` 塞新豁免 —— 而那份台账的红线是
   **只准减**。本批实际减 1 条（门面假回执消失后 `mcp_server.py#fake-ok-const#_build_server.teach_signal`
   不再命中），175 → 174 行。

### 四、跑出来的东西（本机 3.13.2；容器 3.11 权威档见本节末）

- 新增锁文件 `tests/test_rounds11_19_ma25_35_fixes.py`：本机 **27 passed, 1 skipped**
  —— skip 的那条实测报的就是 MA-26（`SKIPPED [1] tests\\test_rounds11_19_ma25_35_fixes.py:108:
  本机 mcp SDK 过旧`），容器档真跑。
- 门禁两格由红转绿：`test_no_new_gate_violations` / `test_baseline_shrinks_when_you_fix_things`
  —— 前者是我自己的新代码踩的（见三.4），后者是 MA-28 修好了存量条目。
- 全量本机档 `.qoder/tmp-c85-suite-local.out`：**2023 passed, 25 skipped in 257.83s**，
  尾行 `LOCAL_SUITE_RC=0`（上一档同口径 2021 passed，差的就是这 28 条新锁里本机跑的 27 条 + 1 skip）。
- 11 腿变异自证 `.qoder/tmp-c85-mut.py`（注入前 `ast.parse`、锚点唯一性先判、跑在一次性副本树
  `.qoder/tmp-c85-mut/`，不碰工作树）：对照腿 **Q-0 绿**（`27 passed, 1 skipped`，rc=0），
  结论 **killed=10 survived=1 invalid=0 legs=11，`WT_UNTOUCHED=True`**，`MUT_LOCAL_RC=2`
  （2 是"有腿存活"的退出码，不是崩溃）。
  - 唯一存活的 Q02 是 MA-26：它在这台机器上只有那条被 skip 的锁可杀，本机判不出红是**量具够不着**，
    不是护栏缺失 —— 由 run28 容器档判。
  - 首跑报过 `WT_UNTOUCHED=False`：原因是我当时正并发编辑 `nr_routes.py` 的行尾（见下条），
    harness 的工作树前后哈希把我自己的编辑当成了泄漏。停掉并发编辑重跑即 `True`。
    教训已入库：变异腿跑的那段时间工作树必须只有 harness 一个人动。
- 对锚预检 `.qoder/tmp-c85-anchorcheck.py`（把 c81/c82/r20/c85 四份 harness 的锚点拿到当前树数命中）：
  **53 条锚点全部命中恰好 1 次，`ANCHOR_PRECHECK_BAD=0`**。这一步本机先红过一次，根因不是靶面挪了，
  而是量具自己瞎：本机 `core.autocrlf=true` ⇒ 工作副本 CRLF、入库 blob LF，跨行锚点只写 `\\n`
  在盘上命中 0 次；两份脚本都补了按目标文件自身行尾适配的 `to_eol()` 以后才有效。
- 引用行号现读门 `.qoder/tmp-c85-linecheck.py`：本节表里 17 个落点 + 25 个锁号逐条打开对内容，
  第一版判红 4 条（`llm_client.py:432`/`nr_routes.py:71`/`acp_server.py:85`/`:428` 都指偏了），
  已按现读改成 `:438`~`:442`/`:70`/`:85`~`:86`/`:433`。

### 五、run28 容器权威档（占位，跑完按现读填）

TOOLCHAIN / SNAP / GATE / SUITE / 定向格 / 变异 / POST 逐格读数待 run28 出档后原样登记；
在此之前本节不留任何"应该已经通过"的写法。
"""


def main():
    with io.open(LEDGER, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    assert "\r\n" not in text, "台账里出现 CRLF，先修行尾再动笔"
    lines = text.split("\n")
    changed = 0
    for i, ln in enumerate(lines):
        m = re.match(r"^\| (MA-\d\d) \|", ln)
        if not m:
            continue
        key = m.group(1)
        if key not in STATUS:
            continue
        cells = ln.split("|")
        # 行尾是 `|`，所以状态格恒为倒数第二个字段
        status = cells[-2].strip()
        if not (status.startswith("待修") or status.startswith("二选一")):
            raise AssertionError(f"{key} 行的状态列形状不认识：{status!r}")
        cells[-2] = " " + STATUS[key] + " "
        lines[i] = "|".join(cells)
        changed += 1
    assert changed == len(STATUS), f"应回填 {len(STATUS)} 行，实改 {changed}"
    new = "\n".join(lines)
    assert "## 五十五" not in new, "§五十五 已存在，别重复插"
    new = new.rstrip("\n") + "\n" + SEC
    with io.open(LEDGER, "w", encoding="utf-8", newline="") as fh:
        fh.write(new)
    print("LEDGER_UPDATED rows=%d lines=%d->%d" % (changed, len(lines), new.count("\n")))
    print("CR_BYTES=%d" % new.count("\r"))


if __name__ == "__main__":
    sys.exit(main())
