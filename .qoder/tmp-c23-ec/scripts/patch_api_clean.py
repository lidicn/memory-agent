"""干净的 api.py 兼容性补丁。
从 git 原始版本出发，只做必要修改：
1. 给 6 个方法加 days 参数
2. 加 _days_to_range 辅助方法
3. 在方法体开头加 days 转换
4. 补 3 个缺失方法
5. 修复 infer_activities 参数
"""
import re

path = r"E:\NAS\memory-agent\src\memory_agent\insights\api.py"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

# === 1. 加 _days_to_range 辅助方法（在 _safe_entities 前）===
helper = '''
    def _days_to_range(self, days: int, start: str, end: str):
        """days 兼容：days>0 且 start/end 为空时计算时间范围。"""
        if days and not start and not end:
            from datetime import datetime, timedelta
            _end = datetime.now()
            _start = _end - timedelta(days=days)
            start = _start.isoformat(timespec="seconds")
            end = _end.isoformat(timespec="seconds")
        return start, end

'''
src = src.replace(
    "    def _safe_entities(self)",
    helper + "    def _safe_entities(self)"
)

# === 2. 给方法加 days 参数 ===
# 这些方法的签名都是 def method(self, start: str = "", end: str = "",
methods_days = ["search_events", "device_usage", "behavior_insights", "anomaly_report", "device_health"]
for m in methods_days:
    src = src.replace(
        f"def {m}(self, start: str = \"\", end: str = \"\",",
        f"def {m}(self, days: int = 0, start: str = \"\", end: str = \"\","
    )

# entity_catalog 签名不同
src = src.replace(
    "def entity_catalog(self, room: str = \"\", category: str = \"\", query: str = \"\",",
    "def entity_catalog(self, days: int = 0, room: str = \"\", category: str = \"\", query: str = \"\","
)

# === 3. 在方法体开头加 days 转换 ===
# search_events 调用 self._search
src = src.replace(
    "        return self._search(start, end, entity_id, room, category, query,",
    "        start, end = self._days_to_range(days, start, end)\n        return self._search(start, end, entity_id, room, category, query,"
)

# device_usage 调用 self.core.usage
src = src.replace(
    "        return self.core.usage(tr, room=room, category=category, query=query,",
    "        start, end = self._days_to_range(days, start, end)\n        return self.core.usage(tr, room=room, category=category, query=query,"
)

# behavior_insights
src = src.replace(
    "        return self.core.behavior_insights(tr, room=room, category=category, query=query)",
    "        start, end = self._days_to_range(days, start, end)\n        return self.core.behavior_insights(tr, room=room, category=category, query=query)"
)

# anomaly_report
src = src.replace(
    "        return self.core.anomaly_report(tr, room=room, category=category, query=query)",
    "        start, end = self._days_to_range(days, start, end)\n        return self.core.anomaly_report(tr, room=room, category=category, query=query)"
)

# device_health
src = src.replace(
    "        return self.core.device_health(tr, room=room, category=category, query=query)",
    "        start, end = self._days_to_range(days, start, end)\n        return self.core.device_health(tr, room=room, category=category, query=query)"
)

# === 4. 修复 infer_activities 参数 ===
src = src.replace(
    "return self.core.infer_activities(tr, rooms=rooms, activities=activities)",
    "return self.core.infer_activities(tr, rooms=rooms)"
)

# === 5. 补缺失方法（在 data_coverage 前）===
missing = '''
    def get_events(self, days: int = 7, start: str = "", end: str = "",
                   entity_id: str = "", room: str = "", limit: int = 50,
                   offset: int = 0) -> Dict[str, Any]:
        """获取事件列表（兼容 legacy）。"""
        start, end = self._days_to_range(days, start, end)
        from .models import TimeRange as _TR
        try:
            tr = _TR.from_iso(start, end) if start else _TR.default(self.config)
        except Exception:
            tr = _TR.default(self.config)
        events = self.core.load_events(tr, entity_ids=[entity_id] if entity_id else None,
                                        room=room or None, limit=limit, offset=offset)
        return {"events": events, "total": len(events), "offset": offset,
                "limit": limit, "has_more": len(events) >= limit}

    def compare_insights(self, compare_days: int = 7, base_days: int = 7) -> Dict[str, Any]:
        """对比洞察（兼容 legacy）。"""
        try:
            return self.core.compare_insights(compare_days=compare_days)
        except Exception as exc:
            LOG.exception("compare_insights 失败")
            return {"periods": [], "changes": [], "error": str(exc)}

    def insights_report(self, days: int = 7, fmt: str = "json") -> Any:
        """洞察报告（兼容 legacy）。"""
        insights = self.behavior_insights(days=days)
        try:
            from .report import ReportBuilder
            return ReportBuilder().insights_report(insights, fmt)
        except Exception as exc:
            LOG.exception("insights_report 失败")
            return {"error": str(exc)}

'''
src = src.replace(
    "    def data_coverage(self, days: int = 7,",
    missing + "    def data_coverage(self, days: int = 7,"
)

with open(path, "w", encoding="utf-8") as f:
    f.write(src)

print("Patch applied")
