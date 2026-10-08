"""精确修复 api.py 兼容性问题。"""
path = r"E:\NAS\memory-agent\src\memory_agent\insights\api.py"
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

out = []
i = 0
while i < len(lines):
    line = lines[i]

    # 1. entity_catalog: 加 days 和 limit 参数
    if "def entity_catalog(self, room: str = \"\", category: str = \"\", query: str = \"\"," in line:
        out.append("    def entity_catalog(self, days: int = 0, room: str = \"\", category: str = \"\", query: str = \"\",\n")
        # 跳过原行
        i += 1
        # 找下一行，应该是 limit/offset 参数行
        while i < len(lines) and "limit" not in lines[i] and "offset" not in lines[i]:
            out.append(lines[i])
            i += 1
        # 替换 limit 行，确保 limit 有默认值
        if i < len(lines):
            out.append(lines[i].replace("limit: int", "limit: int = 100"))
            i += 1
        continue

    # 2. search_events: 加 days 参数
    if "def search_events(self, start: str = \"\", end: str = \"\", entity_id: str = \"\"," in line:
        out.append("    def search_events(self, days: int = 0, start: str = \"\", end: str = \"\", entity_id: str = \"\",\n")
        i += 1
        continue

    # 3. device_usage: 加 days
    if "def device_usage(self, start: str = \"\", end: str = \"\", room: str = \"\"," in line:
        out.append("    def device_usage(self, days: int = 0, start: str = \"\", end: str = \"\", room: str = \"\",\n")
        i += 1
        continue

    # 4. behavior_insights: 加 days
    if "def behavior_insights(self, start: str = \"\", end: str = \"\", room: str = \"\"," in line:
        out.append("    def behavior_insights(self, days: int = 0, start: str = \"\", end: str = \"\", room: str = \"\",\n")
        i += 1
        continue

    # 5. anomaly_report: 加 days
    if "def anomaly_report(self, start: str = \"\", end: str = \"\", room: str = \"\"," in line:
        out.append("    def anomaly_report(self, days: int = 0, start: str = \"\", end: str = \"\", room: str = \"\",\n")
        i += 1
        continue

    # 6. device_health: 加 days
    if "def device_health(self, start: str = \"\", end: str = \"\", room: str = \"\"," in line:
        out.append("    def device_health(self, days: int = 0, start: str = \"\", end: str = \"\", room: str = \"\",\n")
        i += 1
        continue

    # 7. 在 search_events 方法体开头加 days 转换
    if 'return self._search(start, end, entity_id, room, category, query,' in line:
        out.append("        start, end = self._days_to_range(days, start, end)\n")
        out.append(line)
        i += 1
        continue

    # 8. device_usage 方法体: 加 days 转换
    if 'return self.core.usage(tr, room=room, category=category, query=query,' in line:
        # 找前面的 tr = 行，在它之前加转换
        # 先输出已缓存的行（从 out 末尾往回找 tr =）
        # 简单做法：直接在这行前加转换
        out.append("        start, end = self._days_to_range(days, start, end)\n")
        out.append(line)
        i += 1
        continue

    # 9. behavior_insights 方法体
    if 'return self.core.behavior_insights(tr, room=room, category=category, query=query)' in line:
        out.append("        start, end = self._days_to_range(days, start, end)\n")
        out.append(line)
        i += 1
        continue

    # 10. anomaly_report 方法体
    if 'return self.core.anomaly_report(tr, room=room, category=category, query=query)' in line:
        out.append("        start, end = self._days_to_range(days, start, end)\n")
        out.append(line)
        i += 1
        continue

    # 11. device_health 方法体
    if 'return self.core.device_health(tr, room=room, category=category, query=query)' in line:
        out.append("        start, end = self._days_to_range(days, start, end)\n")
        out.append(line)
        i += 1
        continue

    # 12. entity_catalog 方法体: 加 days 转换 + limit 处理
    if 'return self.repo.entity_catalog(room=room or None, category=category or None,' in line:
        out.append("        start, end = self._days_to_range(days, start, end)\n")
        out.append(line)
        i += 1
        continue

    # 13. 修复 get_events 的 _safe_range
    if 'tr = self._safe_range(start, end)' in line:
        out.append("        from .models import TimeRange as _TR\n")
        out.append("        tr = _TR.from_iso(start, end) if start else _TR.default(self.config)\n")
        i += 1
        continue

    # 14. 修复 insights_report 的 reports 属性
    if 'return self.core.reports.insights_report(insights, fmt)' in line:
        out.append("        from .report import ReportBuilder\n")
        out.append("        return ReportBuilder().insights_report(insights, fmt)\n")
        i += 1
        continue

    out.append(line)
    i += 1

with open(path, "w", encoding="utf-8") as f:
    f.writelines(out)

print("Patched successfully")
