"""只读探针：去抖丢掉的 30 秒内同实体重复事件，到底是"同一状态重复"还是"状态迁移"。

判红前必须先找反例：若被丢的都是同状态重复，_debounce 是在做它该做的事，我不判它红。
"""
import sys
from collections import defaultdict
from datetime import timedelta

sys.path.insert(0, "/app/src")

from memory_agent.config import get_config
from memory_agent.store import Store, now_local
from memory_agent.activity_inference import ActivityInferenceService, rule_horizon_minutes

cfg = get_config()


class FakeRT:
    pass


rt = FakeRT()
rt.config = cfg
rt.store = Store(cfg.db_path, tz_offset_hours=cfg.tz_offset_hours)
rt.insights = None
svc = ActivityInferenceService(rt)

now = now_local(cfg.tz_offset_hours)
horizon = rule_horizon_minutes(svc.rules)
wmin = max(int(getattr(cfg, "activity_window_minutes", 15) or 15), horizon)


def parse(ts):
    try:
        return __import__("memory_agent.activity_inference", fromlist=["x"])._parse_ts(ts or "")
    except Exception:
        return None


# 用近 6 小时的窗口统计，样本比 25 分钟大一个量级
start = (now - timedelta(hours=6)).isoformat(sep="T")
raw = [dict(e) for e in svc.store.query_events(start=start, end=now.isoformat(sep="T"),
                                               order="asc", limit=5000)]
names = svc._friendly_names()
RULE = {"door", "light", "climate", "computer"}

groups = defaultdict(list)
for e in raw:
    tags = svc._tags_of(e.get("entity_id") or "", names.get(e.get("entity_id") or "", ""))
    if tags & RULE:
        groups[e.get("entity_id")].append((parse(e.get("ts")), e.get("new_state"), sorted(tags)))

same = diff = 0
examples = []
for eid, seq in groups.items():
    seq = [s for s in seq if s[0] is not None]
    seq.sort(key=lambda s: s[0])
    for i in range(1, len(seq)):
        gap = (seq[i][0] - seq[i - 1][0]).total_seconds()
        if 0 <= gap < 30:
            if seq[i][1] == seq[i - 1][1]:
                same += 1
            else:
                diff += 1
                if len(examples) < 6:
                    examples.append((eid.split(".")[0], f"{seq[i-1][1]} -> {seq[i][1]}", round(gap, 1)))

print(f"近 6 小时带规则 tag 的事件 = {sum(len(v) for v in groups.values())} 条 / "
      f"{len(groups)} 个实体")
print(f"30 秒内的同实体相邻事件对：同状态重复 = {same} 条 / **状态迁移 = {diff} 条**")
print("   迁移示例（domain, 状态变化, 间隔秒）:", examples)
print("\n判据：被丢的若几乎都是同状态重复 → _debounce 无罪；若状态迁移占可比量级 → 它砍的是规则的步。")

# 再测一次：_debounce 实际丢弃数
kept = svc._prepare_events([dict(e) for e in raw], 30)
kept_pairs = {(e.get("entity_id"), e.get("new_state")) for e in kept}
lost = [e for e in raw if (e.get("entity_id"), e.get("new_state")) not in kept_pairs]
svc_tags = [e for e in lost if svc._tags_of(e.get("entity_id") or "", names.get(e.get("entity_id") or "", "")) & RULE]
print(f"\n_debounce 总丢弃 = {len(raw) - len(kept)}，其中带规则 tag = {len(svc_tags)}")
