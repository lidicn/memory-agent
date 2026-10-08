import io, os

DRAFT = '.qoder/tmp-c74-sec48.md'
LEDGER = 'doc/审计报告/修复与核实/审计核实与修复_20261001.md'

CONTAINER = """- 快照树身份：本机与容器跑同一份 `_hashes.py`，清单 **401** 个文件、`HASH_MISSING=0`，
  聚合摘要两侧**逐字相同**：`934f2beeceae3614e86d9231f379b833bf86dd092dc487d492cfdafb204caf70`
  （run21 那档是 `3600c38a…15922`，树变了 ⇒ 旧摘要作废，这一行才是 HEAD `fec2c2c` 的指纹）。
- 格 -1 TOOLCHAIN：`CONTAINER_PRESENT=1`、`Python 3.11.16`、`PYTEST=9.1.1`、`PYFLAKES=4.0.2`、`TOOLCHAIN_RC=0`。
- 格 0C/0C2/0C3 ANCHOR（容器读数，与本机 guard 逐字对）：`NEW_HEADLINE=1 OLD_HEADLINE=0 MCP_HAS_DATA_OTHER=2
  SVC_START_DAY=1 SVC_PEAK=1 WITH_WINDOW=5` / `SKILL_HAS_DATA=0 SKILL_FIRSTLAST=0 SKILL_DAYCOV=1
  JS_HAS_DATA=0 JS_FIRSTLAST=0` / `PROSE_SURFACES=5 PROSE_TESTS=2`；`ANCHOR_RC=ANCHOR_PROSE_RC=ANCHOR_TEST_RC=0`。
- 格 0D HARNESS：`PATH_APPEND=1 STDERR_TAIL=1 VERIFY_FLAG=2 OLD_CLOBBER=0 FAILED_GUARD=1`，`HARNESS_RC=0`。
- 门 1 pyflakes：`GATE_RC=0`，`pyflakes: 当前 0 条，基线 0 条，新增 0，已修 0`（口径 `src/memory_agent`，基线只准减）。
- 门 2 全量回归：`SUITE_RC=0` ⇒ **`1920 passed, 10 skipped in 467.20s`**；
  10 条 skip 的名单里是 `river` 缺模块 4 条 + 精简检出无法比 provenance 1 条 + 其余 5 条既有项，
  失败名单 grep 无命中（`^_{3,} ` / `^FAILED ` 两式都空）。
- 定向 1（主战场：文案锁 8 + window 回显 15 + 门面契约 12）：`TARGETED_RC=0` ⇒ **`35 passed in 23.73s`**。
- 定向 2（读这三处面的 13 个文件，含 `test_tool_schema.py` / `test_mcp_surface_parity.py` / `test_acp_server.py`）：
  `FACES_RC=0` ⇒ **`315 passed in 179.12s`**；失败名单 grep 同样无命中。
- 门 3 自证：`VERIFY_RC=0` ⇒ `MUTANTS_PARSED=6 VERIFY_BAD=0`；六条腿容器读数（`MUT74_RC=0`）——

      M-0 基线          -> RC=0  23 passed in 9.60s
      L1 docstring 回滚  -> RC=1  failed=2  restored=OK | 2 failed, 21 passed in 8.56s
      L2 载荷丢 peak_hours -> RC=1  failed=1  restored=OK | 1 failed, 22 passed in 9.29s
      L3 start_day 越界   -> RC=1  failed=1  restored=OK | 1 failed, 22 passed in 8.27s
      L4 SKILL.md 回滚    -> RC=1  failed=1  restored=OK | 1 failed, 22 passed in 9.26s
      L5 手册回滚         -> RC=1  failed=2  restored=OK | 2 failed, 21 passed in 9.75s
      MUT_COUNT=6 MUTATION_BAD=0

  与本机 3.13 那档（`M-0 23 passed / L1 2 / L2 1 / L3 1 / L4 1 / L5 2`）**逐腿同形**。
- 还原自证 POST：`POST_RC=0` ⇒ `23 passed in 8.90s`（变异跑完原样再跑同一份用例）。
- 收尾：`REMOTE_BATCH_RC=0 REMOTE_DRIVER_RC=0 CONTAINER_BATCH_RC=0 DRIVER_RC=0`；
  整档留档 `.qoder/tmp-c74-run22b.out`（首跑那档被预检拦下，留档 `.qoder/tmp-c74-run22.out`）。"""

MUT74 = """本机 3.13 先跑一遍（`.qoder/tmp-c74-mut74-local22.out`，`MUT74_RC=0`）：
`MUTANTS_PARSED=6 VERIFY_BAD=0`、`MUT_COUNT=6 MUTATION_BAD=0`，六条腿 `restored=` 全 `OK`；
跑完与工作树逐文件按字节 md5 对账 `FILES_COMPARED=401 DIFFS=0 MISSING=0`（不是抽查）。
容器 3.11 的同一份读数在下一节。"""

t = io.open(DRAFT, encoding='utf-8', newline='').read()
t = t.replace('__TBD_CONTAINER__', CONTAINER).replace('__TBD_MUT74__', MUT74)
t = t.replace('`mcp_server.py:1618-1621`（改前 5 行）', '`mcp_server.py:1619-1623`（改前 5 行，改后 7 行 `:1619-1625`）')
assert '__TBD' not in t, [ln for ln in t.split('\n') if '__TBD' in ln]
io.open(DRAFT, 'w', encoding='utf-8', newline='').write(t)

# 落册：先拼到临时文件，成功后再 os.replace —— 不用 `open(LEDGER,'wb').write(before+block)`
# 那种"先截断再求值"的写法（踩过：表达式一抛，原文件整份清空）。
with open(DRAFT, 'rb') as fh:
    block = fh.read()
if not block.endswith(b'\n'):
    block += b'\n'
with open(LEDGER, 'rb') as fh:
    before = fh.read()
merged = before + block
TMP = LEDGER + '.tmp48'
with open(TMP, 'wb') as fh:
    fh.write(merged)
    fh.flush()
    os.fsync(fh.fileno())
os.replace(TMP, LEDGER)
with open(LEDGER, 'rb') as fh:
    after = fh.read()
print('LEDGER_BEFORE=%d LEDGER_AFTER=%d APPENDED=%d' % (len(before), len(after), len(after) - len(before)))
print('LEDGER_CR=%d' % after.count(b'\r'))
print('PREFIX_INTACT=%s' % (after.startswith(before)))
print('SEC48=%d SEC47=%d' % (after.count('## 四十八'.encode()), after.count('## 四十七'.encode())))
print('TAIL=%r' % after[-80:].decode('utf-8'))
