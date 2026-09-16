"""生成一份自包含的 OpenSHS 风格基准样本（宽表 CSV）。

用途：让 ``openshs_bench.py`` 离线、可复现地跑出基线报告，不依赖外网下载。
真实 OpenSHS 数据集（多日、含更多活动）可通过把 CSV 放到任意路径后
``python -m benchmarks.openshs_bench --csv <path>`` 替换。

语义尽量贴近 OpenSHS 真实行为：睡眠段只有 bedroomLight/bedroomCarp/bed 等
"存在/灯"信号 + mainDoorLock=1（常亮），**没有**门磁开合的"括号事件"——
这正是 MA 基于静默间隔的睡眠检测在原始 OpenSHS 上召回偏低的真实原因，
本样本刻意保留该特性以暴露这一基线局限。
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta

from .openshs_schema import OPENSHS_COLUMNS, SENSOR_MAP

# 段定义：(openshs_label, start, end, [激活的传感器列名])
# start/end 为 'HH:MM:SS'，跨午夜用次日时间（>24h 的小时数）。
SEGMENTS = [
    ("cook",       "07:00:00", "07:30:00", ["kitchenCarp", "kitchenLight", "oven", "fridge"]),
    ("work",       "09:00:00", "12:00:00", ["officeCarp", "office", "officeLight", "officeDoor"]),
    ("cook",       "12:00:00", "12:30:00", ["kitchenCarp", "kitchenLight", "oven", "fridge"]),
    ("leaveHouse", "14:00:00", "16:00:00", ["mainDoor"]),
    ("work",       "16:30:00", "19:00:00", ["officeCarp", "office", "officeLight", "officeDoor"]),
    ("cook",       "18:00:00", "18:30:00", ["kitchenCarp", "kitchenLight", "oven", "fridge"]),
    ("watchTV",    "20:00:00", "22:00:00", ["tv", "livingLight", "livingCarp", "couch"]),
    ("bathe",      "22:10:00", "22:30:00", ["bathroomCarp", "bathroomLight"]),
    # 睡眠：入睡前 mainDoorLock 上锁(on)，醒后开卧室门(on) 作为「起床锚点」——
    # MA 睡眠检测要求静默括号两端都是 active(on) 跳变，故醒时用一个开门事件而非关锁。
    # 两个子段边界对齐(30:30:00)避免中间出现 relax 空档把睡眠拆成两天。
    ("sleep",      "23:00:00", "30:30:00", ["bedroomLight", "bedroomCarp", "bed", "mainDoorLock"]),
    ("sleep",      "30:30:00", "30:30:30", ["bedroomDoor", "bedroomLight"]),
]


def _to_sec(hms: str) -> int:
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


def generate(out_path: str, base_date: str = "2016-04-01") -> int:
    starts = {lab: _to_sec(s) for lab, s, _e, _a in SEGMENTS for _ in (0,)}
    seg_spans = [(lab, _to_sec(s), _to_sec(e), set(act)) for lab, s, e, act in SEGMENTS]
    t0 = datetime.strptime(base_date + " 00:00:00", "%Y-%m-%d %H:%M:%S")
    # 覆盖到次日 06:31，确保醒时开门事件(06:30:01+)落在生成窗口内。
    total_sec = _to_sec("30:31:00")
    count = 0
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(OPENSHS_COLUMNS)
        cols = [c for c in OPENSHS_COLUMNS if c not in ("Activity", "timestamp")]
        for sec in range(total_sec + 1):
            dt = t0 + timedelta(seconds=sec)
            active: set[str] = set()
            label = "relax"
            for lab, s, e, act in seg_spans:
                if s <= sec <= e:
                    active |= act
                    label = lab
                    break
            row = []
            for c in cols:
                row.append("1" if c in active else "0")
            row.append(label)
            row.append(dt.strftime("%Y-%m-%d %H:%M:%S"))
            w.writerow(row)
            count += 1
    return count


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "benchmarks/data/openshs_sample.csv"
    n = generate(out)
    print(f"wrote {n} rows -> {out}")
