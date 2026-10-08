"""给 api.py 加 days 兼容参数 + 补缺失方法。"""
import re

path = r"E:\NAS\memory-agent\src\memory_agent\insights\api.py"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

# 1. 给用 start/end 的方法加 days 参数
# 方法列表: entity_catalog, search_events, device_usage, behavior_insights, anomaly_report, device_health
methods_to_patch = [
    "entity_catalog", "search_events", "device_usage",
    "behavior_insights", "anomaly_report", "device_health",
]

for method in methods_to_patch:
    # 匹配 def method(self, start: str = "", end: str = "",
    pattern = rf'def {method}\(self, start: str = "", end: str = "",'
    replacement = f'def {method}(self, days: int = 0, start: str = "", end: str = "",'
    src = src.replace(pattern, replacement)

# 2. 在每个方法体开头加 days -> start/end 转换
# 用一个统一的辅助方法
helper = '''
    def _days_to_range(self, days: int, start: str, end: str):
        """days 参数兼容：如果 days>0 且 start/end 为空，计算 start/end。"""
        if days and not start and not end:
            from datetime import datetime, timedelta
            end_dt = datetime.now()
            start_dt = end_dt - timedelta(days=days)
            start = start_dt.isoformat(timespec="seconds")
            end = end_dt.isoformat(timespec="seconds")
        return start, end

'''

# 在 _safe_entities 方法前插入 helper
src = src.replace(
    "    # ------------------------------------------------------------------\n    # 内部工具\n    # ------------------------------------------------------------------\n    def _safe_entities",
    helper + "    # ------------------------------------------------------------------\n    # 内部工具\n    # ------------------------------------------------------------------\n    def _safe_entities"
)

# 3. 给每个 patched 方法加 start, end = self._days_to_range(days, start, end)
for method in methods_to_patch:
    # 找到方法定义后的第一行（通常是 docstring 或 tr = TimeRange）
    # 在 docstring 后插入转换
    pattern = rf'(def {method}\(self, days: int = 0, start: str = "", end: str = "",.*?\n(?:        """.*?"""\n)?)'
    def add_range_call(m):
        body = m.group(1)
        # 在 docstring 后（或方法定义后）插入转换
        if '"""' in body:
            # 有 docstring，在 docstring 结束后插入
            parts = body.split('"""', 2)
            if len(parts) >= 3:
                return parts[0] + '"""' + parts[1] + '"""' + parts[2] + "        start, end = self._days_to_range(days, start, end)\n"
        return body + "        start, end = self._days_to_range(days, start, end)\n"
    src = re.sub(pattern, add_range_call, src, flags=re.DOTALL)

# 4. 补缺失方法: get_events, compare_insights, insights_report
missing_methods = '''
    def get_events(self, days: int = 7, start: str = "", end: str = "",
                   entity_id: str = "", room: str = "", limit: int = 50,
                   offset: int = 0) -> Dict[str, Any]:
        """获取事件列表（兼容 legacy 接口）。"""
        start, end = self._days_to_range(days, start, end)
        tr = self._safe_range(start, end)
        events = self.core.load_events(tr, entity_ids=[entity_id] if entity_id else None,
                                        room=room or None, limit=limit, offset=offset)
        return {"events": events, "total": len(events), "offset": offset,
                "limit": limit, "has_more": len(events) >= limit}

    def compare_insights(self, compare_days: int = 7, base_days: int = 7) -> Dict[str, Any]:
        """对比洞察（兼容 legacy 接口，委托 core.compare_insights）。"""
        try:
            return self.core.compare_insights(compare_days=compare_days)
        except Exception as exc:
            LOG.exception("compare_insights 失败")
            return {"periods": [], "changes": [], "error": str(exc)}

    def insights_report(self, days: int = 7, fmt: str = "json") -> Any:
        """洞察报告（兼容 legacy 接口）。"""
        insights = self.behavior_insights(days=days)
        try:
            return self.core.reports.insights_report(insights, fmt)
        except Exception as exc:
            LOG.exception("insights_report 失败")
            return {"error": str(exc)}
'''

# 在 data_coverage 方法前插入缺失方法
src = src.replace(
    "    def data_coverage(self, days: int = 7,",
    missing_methods + "\n    def data_coverage(self, days: int = 7,"
)

# 5. 修复 infer_activities 的 activities 参数
src = src.replace(
    "return self.core.infer_activities(tr, rooms=rooms, activities=activities)",
    "return self.core.infer_activities(tr, rooms=rooms)"
)

with open(path, "w", encoding="utf-8") as f:
    f.write(src)

print("Patched api.py")
print(f"New size: {len(src)} chars")
