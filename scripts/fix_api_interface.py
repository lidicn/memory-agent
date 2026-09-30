"""修复 api.py 与 BehaviorService 的接口不匹配。"""
path = r"E:\NAS\memory-agent\src\memory_agent\insights\api.py"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

# 1. _search 中 self.core.load_events -> self.repo.load_events
src = src.replace(
    "events = self.core.load_events(tr, entity_ids=ids, room=room,",
    "events = self.repo.load_events(tr, entity_ids=ids, room=room,"
)

# 2. 移除 BehaviorService 调用中的 query=query
src = src.replace(
    "return self.core.usage(tr, room=room, category=category, query=query,",
    "return self.core.usage(tr, room=room, category=category,"
)
src = src.replace(
    "return self.core.behavior_insights(tr, room=room, category=category, query=query)",
    "return self.core.behavior_insights(tr, room=room, category=category)"
)
src = src.replace(
    "return self.core.anomaly_report(tr, room=room, category=category, query=query)",
    "return self.core.anomaly_report(tr, room=room, category=category)"
)
src = src.replace(
    "return self.core.device_health(tr, room=room, category=category, query=query)",
    "return self.core.device_health(tr, room=room, category=category)"
)

# 3. 修复 get_events: 用 self._tr 替代不存在的 _TR.from_iso/default
old_get_events = '''        from .models import TimeRange as _TR
        try:
            tr = _TR.from_iso(start, end) if start else _TR.default(self.config)
        except Exception:
            tr = _TR.default(self.config)
        events = self.core.load_events(tr, entity_ids=[entity_id] if entity_id else None,
                                        room=room or None, limit=limit, offset=offset)'''
new_get_events = '''        tr = self._tr(start, end, days=days)
        events = self.repo.load_events(tr, entity_ids=[entity_id] if entity_id else None,
                                        room=room or None, limit=limit, offset=offset)'''
src = src.replace(old_get_events, new_get_events)

# 4. entity_catalog 加 limit 参数
src = src.replace(
    "def entity_catalog(self, days: int = 0, room: str = \"\", category: str = \"\", query: str = \"\",",
    "def entity_catalog(self, days: int = 0, room: str = \"\", category: str = \"\", query: str = \"\", limit: int = 100,"
)

with open(path, "w", encoding="utf-8") as f:
    f.write(src)

print("Fixed api.py interface mismatches")
