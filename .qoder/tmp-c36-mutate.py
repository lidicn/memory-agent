# 变异自咬：把乙′ 的四个零件逐个拆掉，每条都必须被指定锁咬住（跑完还原并校验字节一致）。
import subprocess
import sys

PY = r"C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe"
SRC = r"src/memory_agent/insights/repository.py"
TESTS = ("tests/test_vma_q62_daily_batch_scan.py tests/test_vma_activity_semantic.py "
         "tests/test_vma_dcd_20261004b_event_total.py")

MUTS = [
    ("M1 分层整体停用=裁6 首版的按天前缀",
     "            if day_limit < self.SCAN_HOURS:",
     "            if True:"),
    ("M2 小时配额当天花板（删掉缺口补读）",
     "                open_hours = [hh for hh in sorted(pools) if capped.get(hh)]",
     "                open_hours = []"),
    ("M3 判据退回「砍满即截断」（嫌疑当证据）",
     "            return day_rows, bool(leftover > 0 or any(capped.values()))",
     "            return day_rows, bool(leftover > 0 or any(len(r) >= hour_cap for r in pools.values()))"),
    ("M4 去掉探针行（LIMIT 只绑配额）",
     "            pools = _pools(list(self.HOURS), hour_cap + 1, {hh: 0 for hh in self.HOURS})",
     "            pools = _pools(list(self.HOURS), hour_cap, {hh: 0 for hh in self.HOURS})"),
    ("M5 分摊变成新增预算（日限额=整窗剩余）",
     "                day_limit = min(daily_limit, limit - len(all_rows))",
     "                day_limit = limit - len(all_rows)"),
    ("M6 小时格上界改成闭区间（带毫秒的尾行两头都不沾）",
     '                hi = day_str + "T" + "%02d" % (int(hh) + 1) + ":00:00"',
     '                hi = day_str + "T" + hh + ":59:59"'),
    ("M7 补读的 OFFSET 恒 0（原地重取，重复交行）",
     "                got = _pools(open_hours, rest + 1, {hh: len(pools[hh]) for hh in open_hours})",
     "                got = _pools(open_hours, rest + 1, {hh: 0 for hh in open_hours})"),
]

original = open(SRC, "rb").read()
bad = 0
for name, old, new in MUTS:
    text = original.decode("utf-8")
    if text.count(old) != 1:
        print("%s -> 锚点命中 %d 次，变异未施加（自咬失败）" % (name, text.count(old)))
        bad += 1
        continue
    # 先算出字节再写：`open(p,'wb')` 会立刻截断，串在可能失败的表达式前面会清空原文件
    mutated = text.replace(old, new).encode("utf-8")
    open(SRC, "wb").write(mutated)
    r = subprocess.run([PY, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]
                       + TESTS.split(), capture_output=True, text=True, encoding="utf-8")
    open(SRC, "wb").write(original)
    failed = [ln.split(" ")[1] for ln in r.stdout.splitlines() if ln.startswith("FAILED")]
    tail = [ln for ln in r.stdout.splitlines() if " passed" in ln or " failed" in ln]
    print("%s\n    RC=%d -> %s" % (name, r.returncode, "咬住了" if r.returncode else "没咬住（变异存活）"))
    print("    %s" % (tail[-1] if tail else "无汇总行"))
    for f in failed:
        print("      x %s" % f)
    if r.returncode == 0:
        bad += 1
    if not failed:
        print("    异常：RC 非 0 但没有 FAILED 行（可能是收集期崩了）")
        bad += 1

restored = open(SRC, "rb").read() == original
print("restored_identical=%s" % ("OK" if restored else "FAIL"))
print("MUTATION_BAD=%d" % bad)
