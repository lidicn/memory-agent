# 变异自咬（run7 / 容器口径）：把乙′ 的七个零件逐个拆掉，每条都必须被指定锁咬住，
# 跑完逐字还原并校验字节一致。
#
# 这一份是 .qoder/tmp-c36-mutate.py 的**可移植版**：本地那版把 PY 写死成 E 盘的
# Python313，塞进容器会当场找不到解释器。这里 PY=sys.executable（容器 3.11 / 本机 3.13
# 都能跑），根目录交给 MUT_ROOT，缺省用当前工作目录。
#
# 「什么都不改」档由改动前那份全绿读数充当（本地 1407 passed / 容器 SUITE），
# 不在这里重复一次。
import os
import subprocess
import sys

ROOT = os.environ.get("MUT_ROOT", ".")
SRC = os.path.join(ROOT, "src/memory_agent/insights/repository.py")
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
    ("M8 不夹紧：窗口首尾两截交给分支里的宽区间（本批已删掉那条宽谓词）",
     "            if lo < start_iso:\n                lo = start_iso\n"
     "            if hi > window_hi:\n                hi = window_hi",
     "            pass"),
    ("M9 上界朝内取整（窗口终点那一秒的行被挤出去）",
     "            window_hi = (datetime.fromisoformat(end_iso)\n"
     "                         + timedelta(seconds=1)).isoformat(timespec=\"seconds\")",
     "            window_hi = datetime.fromisoformat(end_iso).isoformat(timespec=\"seconds\")"),
    ("M10 分支带回 `day = ?`（规划器弃 idx_events_ts 改选 idx_events_day：408ms/天）",
     '                    + " AND ".join(filt + ["ts >= ?", "ts < ?"])',
     '                    + " AND ".join(filt + ["day = ?", "ts >= ?", "ts < ?"])'),
]

with open(SRC, "rb") as fh:
    original = fh.read()
text = original.decode("utf-8")

bad = 0
for name, old, new in MUTS:
    if text.count(old) != 1:
        print("%s -> 锚点命中 %d 次，变异未施加（自咬失败）" % (name, text.count(old)))
        bad += 1
        continue
    # 先算出字节再写：`open(p,'wb')` 会立刻截断，串在可能失败的表达式前面会清空原文件
    mutated = text.replace(old, new).encode("utf-8")
    with open(SRC, "wb") as fh:
        fh.write(mutated)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [os.path.join(ROOT, "src"), os.environ.get("PYLIBS", "/tmp/pylibs"), ROOT]))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header",
                        "-p", "no:cacheprovider"] + TESTS.split(),
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=ROOT, env=env)
    with open(SRC, "wb") as fh:
        fh.write(original)
    failed = [ln.split(" ")[1] for ln in r.stdout.splitlines() if ln.startswith("FAILED")]
    tail = [ln for ln in r.stdout.splitlines() if " passed" in ln or " failed" in ln]
    print("%s\n    RC=%d -> %s" % (name, r.returncode,
                                   "咬住了" if r.returncode else "没咬住（变异存活）"))
    print("    %s" % (tail[-1] if tail else "无汇总行"))
    for f in failed:
        print("      x %s" % f)
    if r.returncode == 0:
        bad += 1
    if not failed:
        print("    异常：RC 非 0 但没有 FAILED 行（可能是收集期崩了）")
        bad += 1

with open(SRC, "rb") as fh:
    restored = fh.read() == original
print("restored_identical=%s" % ("OK" if restored else "FAIL"))
print("MUTATION_BAD=%d" % bad)
