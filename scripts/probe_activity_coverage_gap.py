"""MA-裁6 Q1=A 的覆盖率缺口探针（生产库，只读）。

Q4 读数②在生产库上量到：新引擎 30 天只产出 9 条语义活动，而 legacy 有 116 条，
且 `bath`/`sleep` 两条内置规则**一条都没出**。这个脚本回答"卡在哪一环"，逐信号给三段数：

1. **解析**：`resolver` 按 房间/关键词/类别/domain 解析出几个实体（解析不到 = 规则空转）；
2. **全窗口事件量**：这些实体在该窗口的真 COUNT（`count_events`，不带 LIMIT）；
3. **扫描切片**：`load_events` 受 `max_scan` 截断后，这些实体还剩几条在场
   —— 引擎算会话时长用的是切片后的事件，所以这一维直接决定 `min_minutes` 能不能达。

三段数摆在一起才能分辨「规则不匹配设备命名」与「被扫描上限饿死」是两回事。

只读：全程 SELECT 与内存计算，不建 schema、不写库、不打印凭据；
不打印 entity_id（含厂商用户号）与 friendly_name，只报数量、时长与规则里写的通用词。

    ssh lidicn@192.168.2.200 'docker exec -w /app memory-agent sh -c \\
        "PYTHONPATH=/app/src python /app/scripts/probe_activity_coverage_gap.py"'
"""
from collections import defaultdict

from memory_agent.config import get_config
from memory_agent.insights import InsightService as Facade
from memory_agent.insights.activity import (BUILTIN_ACTIVITIES, ActivityEngine,
                                            _day_midnight)
from memory_agent.insights.parser.timeframe import split_days
from memory_agent.store import Store

cfg = get_config()
st = Store(cfg.db_path, tz_offset_hours=cfg.tz_offset_hours)
fac = Facade(st, cfg)

print(f"db={cfg.db_path} tz={cfg.tz_offset_hours} max_scan={fac.repo.scan_limit}")

print("\n=== 切片：加载条数 vs 全窗口真值 ===")
for days in (1, 7, 30):
    tr = fac._tr(days=days)
    loaded = fac.repo.load_events(tr)
    total = fac.repo.count_events(tr)
    span = ("%s → %s" % (str(loaded[0].dt)[:16], str(loaded[-1].dt)[:16])) if loaded else "-"
    capped = len(loaded) >= fac.repo.scan_limit
    print(f"  days={days:2} 加载={len(loaded)} 全窗口真值={total} "
          f"{'命中扫描上限，切片只覆盖窗口前段 ' + span if capped else '未截断'}")

tr = fac._tr(days=30)
events = fac.repo.load_events(tr)
engine = ActivityEngine(fac.resolver, fac.core.config)
by_entity = defaultdict(list)
for ev in sorted(events, key=lambda e: e.ts):
    by_entity[ev.entity_id].append(ev)

print(f"\n=== 目录与房间 ===")
print(f"  解析器目录实体数={len(fac.resolver.all())} 房间={fac.resolver.rooms()}")

print("\n=== 逐内置规则、逐信号的三段数 ===")
days = split_days(tr)
for rule in BUILTIN_ACTIVITIES:
    signals = rule.requires + rule.any_of
    kind = ["必要"] * len(rule.requires) + ["可选"] * (len(signals) - len(rule.requires))
    print(f"  {rule.key}（{rule.name}）房间={rule.room or '全屋'} 窗口={rule.window} "
          f"min_minutes={rule.min_minutes}")
    for idx, (sig, tag) in enumerate(zip(signals, kind)):
        ids = engine._signal_ids(sig, rule)
        full = fac.repo.count_events(tr, entity_ids=ids) if ids else 0
        in_slice = sum(len(by_entity.get(i, ())) for i in ids)
        best_min, best_count = 0.0, 0
        for day, _lo, _hi in days:
            s, e = engine._window_range(_day_midnight(day), rule.window)
            s, e = tr.clip(s, e)
            if e <= s:
                continue
            met = engine._signal_metrics(by_entity, ids, s, e)
            if met["minutes"] > best_min:
                best_min = met["minutes"]
            best_count = max(best_count, met["count"])
        gate = ("达阈值" if sig.min_minutes and best_min >= sig.min_minutes
                else f"未达（最长 {best_min} 分 / 需 {sig.min_minutes} 分）" if sig.min_minutes
                else f"次数档 {best_count} vs min_count={sig.min_count}")
        print(f"    [{tag}] room={sig.room or '-'} query={sig.query or '-'} "
              f"category={sig.category or '-'} domain={sig.domain or '-'}: "
              f"实体={len(ids)} 全窗口事件={full} 切片内={in_slice} 最长会话={best_min} 分 -> {gate}")

print("\n=== 判读口径 ===")
print("  实体=0：规则关键词与现网设备命名对不上，属**词表**问题（改规则或加标签，不改引擎）。")
print("  实体>0 且 切片内=0 而 全窗口事件>0：被 max_scan 截断饿死，属**扫描口径**问题"
      "（裁5 Q4=A 明令不提高上限，所以这条只能作为读数登记，不在本件改）。")
print("  实体>0、切片内>0 但未达阈值：时长/次数条件在现网确实不成立，规则判为无产出是正确的。")
