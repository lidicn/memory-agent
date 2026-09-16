"""OpenSHS 宽表 -> Memory-Agent 事件流 转换器 + 真值（ground truth）抽取。

设计要点
--------
- **边沿触发（edge-triggered）**：OpenSHS 是每秒一行的「状态快照」，若把每一行
  每个传感器都展开成事件，会产生海量重复事件，且破坏 MA 基于「静默间隔」的
  睡眠 / 离家检测（静默 = 连续无 *变化* 事件）。因此只在该传感器 0/1 发生变化时
  才产出一条事件，与 HA 的「状态变化日志」语义一致。
- 事件字段对齐 ``events`` 表：``entity_id`` / ``new_state``(on|off) / ``room`` /
  ``ts``；``domain`` 由 entity_id 前缀自动推导，无需另外给。
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime

from .openshs_schema import (
    ACTIVITY_MAP,
    OPENSHS_COLUMNS,
    SENSOR_MAP,
)

_TS_COL = "timestamp"
_ACT_COL = "Activity"


def _norm_state(v: str) -> str:
    return "on" if str(v).strip() in ("1", "1.0", "true", "True", "on", "开") else "off"


def wide_to_events(csv_path: str) -> list[dict]:
    """读 OpenSHS 宽表 CSV，返回边沿触发的事件列表（已按 ts 升序）。"""
    events: list[dict] = []
    # 初始态视为全 0（关），避免首行对未变化的传感器误触发事件。
    prev: dict[str, str] = {col: "off" for col in SENSOR_MAP}
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = (row.get(_TS_COL) or "").strip()
            if not ts:
                continue
            for col, (eid, room, _domain) in SENSOR_MAP.items():
                cur = _norm_state(row.get(col, "0"))
                if prev.get(col) == cur:
                    continue
                prev[col] = cur
                ts_iso = _iso(ts)
                events.append({
                    "entity_id": eid,
                    "room": room,
                    "new_state": cur,
                    "old_state": "off" if cur == "on" else "on",
                    "ts": ts_iso,
                    "day": ts_iso[:10],
                })
    events.sort(key=lambda e: e["ts"])
    return events


def ground_truth(csv_path: str, activity_map: dict | None = None) -> dict:
    """抽取真值。

    参数
    ----
    activity_map: 标签映射（OpenSHS 标签 -> MA 活动）。默认 ``ACTIVITY_MAP``
        （细粒度/样本）；真实粗粒度数据集传 ``COARSE_ACTIVITY_MAP``。

    返回
    ----
    {
      "days": {day: set(ma_activity)},     # 当天出现过的 MA 活动（已映射）
      "segments": [                         # 连续同类活动的片段（诊断用）
          {"activity": openshs_label, "ma": ma_activity|None,
           "start": ts, "end": ts, "day": day},
          ...
      ],
    }
    """
    amap = activity_map if activity_map is not None else ACTIVITY_MAP
    days: dict[str, set] = defaultdict(set)
    segments: list[dict] = []
    last_label = None
    seg_start = seg_end = None
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = (row.get(_TS_COL) or "").strip()
            if not ts:
                continue
            ts_iso = _iso(ts)
            label = (row.get(_ACT_COL) or "").strip()
            ma = amap.get(label)
            day = ts_iso[:10]
            if label != last_label:
                if last_label is not None and seg_start is not None:
                    segments.append({
                        "activity": last_label,
                        "ma": amap.get(last_label),
                        "start": seg_start,
                        "end": seg_end,
                        "day": seg_start[:10],
                    })
                last_label = label
                seg_start = ts_iso
            seg_end = ts_iso
    if last_label is not None and seg_start is not None:
        segments.append({
            "activity": last_label,
            "ma": amap.get(last_label),
            "start": seg_start,
            "end": seg_end,
            "day": seg_start[:10],
        })
    # 按「连续片段起始日」归因真值：跨午夜的睡眠（23:00→次日06:30）只记到起始日，
    # 与 MA 的「夜→入睡那一夜」归因保持一致，避免次日被误判为漏检。
    days = defaultdict(set)
    for seg in segments:
        if seg.get("ma"):
            days[seg["day"]].add(seg["ma"])
    return {"days": {k: sorted(v) for k, v in days.items()}, "segments": segments}


def start_end(csv_path: str) -> tuple[str, str]:
    """返回数据集的时间窗口 [min_ts, max_ts]。"""
    mn = mx = None
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = (row.get(_TS_COL) or "").strip()
            if not ts:
                continue
            if mn is None or ts < mn:
                mn = ts
            if mx is None or ts > mx:
                mx = ts
    return _iso(mn), _iso(mx)


def _iso(ts: str) -> str:
    """把 'YYYY-MM-DD HH:MM:SS' 转成 ISO 'YYYY-MM-DDTHH:MM:SS'。

    真实 OpenSHS 数据集的 timestamp 用下划线分隔时分秒
    （如 ``2021-03-01 07_55_16``），先规整为冒号再解析。
    """
    ts = (ts or "").strip().replace("_", ":")
    if "T" in ts:
        return ts
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").isoformat(sep="T")
    except ValueError:
        return ts
