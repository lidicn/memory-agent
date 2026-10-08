"""变异自证：把 behavior_predictor / daily_profile 的缺陷现场逐个还原，确认每条锁真的咬住。

纪律：锚点必须恰好命中 1 次，否则 ABORT；改源、跑锁、还原、再跑一次证明还原成功。
"""
import subprocess
import sys

BP = "src/memory_agent/behavior_predictor.py"
DP = "src/memory_agent/daily_profile.py"
TESTS = ["tests/test_vma_behavior_predictor_shapes.py", "tests/test_audit_arrival_time.py"]

MUTS = [
    ("M1 只读 persons_json（门面已换成 persons）", BP,
     '    raw: Any = ev.get("persons")\n',
     '    raw: Any = None\n'),
    ("M2 旧格式字符串元素不认（写死 p.get）", BP,
     "        elif isinstance(p, str):\n            name = p.strip()  # 旧格式：字符串本身就是姓名\n",
     "        elif isinstance(p, str):\n            continue\n"),
    ("M3 坏数据抛出（调用点无兜底=500）", BP,
     "        except (ValueError, UnicodeDecodeError):\n            return []\n",
     "        except (ValueError, UnicodeDecodeError):\n            raise\n"),
    ("M4 aware 形状不折算家庭墙钟", BP,
     "    if dt.tzinfo is not None:\n        dt = dt.astimezone(_house_tz(tz_offset_hours)).replace(tzinfo=None)\n",
     "    if False:\n        dt = dt.replace(tzinfo=None)\n"),
    ("M5 按天聚合用 server_ts 字符串前缀", BP,
     '        by_day.setdefault(dt.strftime("%Y-%m-%d"), []).append(dt)\n',
     '        by_day.setdefault(str(ev.get("server_ts", ""))[:10], []).append(dt)\n'),
    ("M6 离家取当天最早（旧顺序依赖的等价位）", BP,
     "    hours = [max(_as_hour(d) for d in dts) for dts in by_day.values()]\n",
     "    hours = [min(_as_hour(d) for d in dts) for dts in by_day.values()]\n"),
    ("M7 data_days 回到整表口径", BP,
     '        "data_days": len(by_day),\n',
     '        "data_days": len(set(str(ev.get("server_ts", ""))[:10] for ev in events)),\n'),
    ("M8 daily_profile 只认 persons dict 元素", DP,
     "        if person not in person_names(ev):\n",
     "        if not any(p.get(\"name\") == person for p in (ev.get(\"persons\") or [])):\n"),
    ("M9 daily_profile 截掉时区尾巴", DP,
     '        dt = house_dt(ev.get("server_ts"), tz_offset_hours)\n',
     '        dt = house_dt(str(ev.get("server_ts", ""))[:19], tz_offset_hours)\n'),
]


def run_locks():
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = [ln for ln in p.stdout.splitlines() if ln.startswith("FAILED")]
    return p.returncode, tail


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


rc0, failed0 = run_locks()
print("BASELINE_RC=%d BASELINE_FAILED=%d" % (rc0, len(failed0)))
if rc0 != 0:
    print("ABORT: 基线不绿，变异读数无意义")
    for ln in failed0:
        print("  ", ln)
    raise SystemExit(2)

bitten = 0
aborts = []
for name, path, old, new in MUTS:
    text = read(path)
    hits = text.count(old)
    if hits != 1:
        aborts.append("%s 锚点命中 %d 次" % (name, hits))
        print("ABORT_ANCHOR %s hits=%d" % (name, hits))
        continue
    write(path, text.replace(old, new))
    rc, failed = run_locks()
    restored = True
    write(path, read(path).replace(new, old))
    if read(path) != text:
        restored = False
        write(path, text)
    status = "咬住" if rc != 0 else "漏咬"
    if rc != 0:
        bitten += 1
    print("%-46s MUT_RC=%d FAILED=%d %s RESTORED=%s" % (
        name, rc, len(failed), status, restored))
    for ln in failed[:3]:
        print("      ", ln.split("::")[-1])

rc_after, failed_after = run_locks()
print("BITTEN=%d/%d ABORTS=%d" % (bitten, len(MUTS), len(aborts)))
print("AFTER_RESTORE_RC=%d AFTER_RESTORE_FAILED=%d" % (rc_after, len(failed_after)))
cr = read(BP).count("\r") + read(DP).count("\r")
print("CR_AFTER=%d" % cr)
raise SystemExit(0 if (bitten == len(MUTS) and not aborts and rc_after == 0 and cr == 0) else 1)
