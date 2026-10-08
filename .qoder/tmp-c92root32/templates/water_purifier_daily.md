# 净水器每日用水分析模版

## 数据源
- **Entity**: `event.chunmi_cn_334432105_600f2_water_out_finish_e_7_1`
- **属性**: `出水数据`
- **格式**: `开始时间戳-结束时间戳-出水量mL-入水TDS,出水TDS`

## 查询示例

### 查询最近N天出水记录
```
GET /api/history/period/{start_time}?filter_entity_id=event.chunmi_cn_334432105_600f2_water_out_finish_e_7_1
```

### 解析逻辑
```python
def parse_water_out(record):
    """解析单次出水记录"""
    data = record["attributes"]["出水数据"]
    if not data:
        return None
    parts = data.split("-")
    start_ts = int(parts[0])
    end_ts = int(parts[1])
    volume_ml = int(parts[2].split(",")[0])
    tds_in = int(parts[2].split(",")[1].split(",")[0])
    tds_out = int(parts[3])
    return {
        "volume_ml": volume_ml,
        "duration_sec": end_ts - start_ts,
        "tds_in": tds_in,
        "tds_out": tds_out,
        "tds_reduction": round((1 - tds_out/tds_in) * 100, 1) if tds_in > 0 else 0
    }
```

### 按日汇总
```python
from collections import defaultdict
from datetime import datetime

daily_stats = defaultdict(lambda: {"count": 0, "total_ml": 0, "records": []})

for record in history_records:
    parsed = parse_water_out(record)
    if parsed:
        day = record["last_changed"][:10]
        daily_stats[day]["count"] += 1
        daily_stats[day]["total_ml"] += parsed["volume_ml"]
        daily_stats[day]["records"].append(parsed)
```

## 输出格式

| 日期 | 出水次数 | 总出水量(L) | 平均单次(mL) | 平均TDS去除率 |
|------|---------|------------|-------------|--------------|
| 2026-07-03 | 6 | 1.16 | 193 | 84.8% |
| 2026-07-04 | 7 | 2.05 | 293 | 84.8% |

## 关键指标

- **日均用水量**: 正常家庭约 5-10L/天
- **单次出水量**: 正常范围 100-500mL（接一杯水）
- **TDS去除率**: RO膜正常应 >90%，低于80%需关注滤芯
- **滤芯寿命**: PPC剩余37%，RO剩余18%（RO偏低，建议近期更换）

## 当前数据摘要

| 指标 | 值 |
|------|-----|
| 有数据日期 | 7/3, 7/4（共2天） |
| 总出水次数 | 13次 |
| 总出水量 | 3.21L |
| 平均日用水 | 1.61L |
| 假期模式 | 开启中（7/4后无数据） |
