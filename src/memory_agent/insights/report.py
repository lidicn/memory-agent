"""报告生成模块：洞察 / 异常 / 数据质量 / 画像，输出 Markdown 或 JSON。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .models import InsightConfig

__all__ = ["ReportBuilder"]

_FORMATS = ("markdown", "json", "md", "text")


class ReportBuilder:
    """把已算好的字典结果渲染成 Markdown / JSON。"""

    def __init__(self, config: Optional[InsightConfig] = None) -> None:
        self.config = config or InsightConfig()

    # ---------------- 入口 ----------------
    def insights_report(self, data: Mapping[str, Any], fmt: str = "markdown") -> str:
        rows = [[i.get("title", ""), i.get("detail", ""), i.get("room", "") or "-"]
                for i in data.get("insights", [])]
        return self._render("行为洞察报告", data, ["洞察", "说明", "房间"], rows, fmt)

    def anomaly_report(self, data: Mapping[str, Any], fmt: str = "markdown") -> str:
        rows = [[a.get("type", ""), a.get("title", ""), a.get("detail", ""),
                 a.get("severity", "")] for a in data.get("anomalies", [])]
        return self._render("异常报告", data, ["类型", "对象", "说明", "级别"], rows, fmt)

    def quality_report(self, data: Mapping[str, Any], fmt: str = "markdown") -> str:
        rows = [[i.get("type", ""), i.get("title", ""), i.get("detail", "")]
                for i in data.get("issues", [])]
        payload = dict(data)
        payload.setdefault("summary", "数据质量评分 %s（%s）" % (
            data.get("score", "-"), data.get("grade", "-")))
        return self._render("数据质量报告", payload, ["类型", "对象", "说明"], rows, fmt)

    def persona_report(self, data: Mapping[str, Any], fmt: str = "markdown") -> str:
        persona = dict(data.get("persona") or data)
        rows = [[t, ""] for t in persona.get("traits", [])]
        payload = dict(data)
        payload.setdefault("summary", persona.get("summary", ""))
        return self._render("用户画像报告", payload, ["特征", ""], rows, fmt)

    # ---------------- 渲染 ----------------
    def _render(self, title: str, data: Mapping[str, Any],
                headers: Sequence[str], rows: Sequence[Sequence[str]],
                fmt: str) -> str:
        style = (fmt or "markdown").lower()
        if style not in _FORMATS:
            style = "markdown"
        if style == "json":
            return json.dumps(dict(data), ensure_ascii=False, indent=2, default=str)
        return self.to_markdown(title, data, headers, rows)

    @staticmethod
    def to_markdown(title: str, data: Mapping[str, Any],
                    headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
        lines: List[str] = ["# %s" % title, ""]
        summary = data.get("summary")
        if summary:
            lines.append("> %s" % summary)
            lines.append("")
        tr = data.get("time_range")
        if isinstance(tr, Mapping):
            lines.append("- 时间范围：%s ~ %s（%.2f 天）" % (
                tr.get("start", ""), tr.get("end", ""), float(tr.get("days", 0) or 0)))
        lines.append("- 共 %s 条，offset=%s，has_more=%s" % (
            data.get("total", 0), data.get("offset", 0), data.get("has_more", False)))
        lines.append("")
        if headers:
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("|" + "|".join(["---"] * len(headers)) + "|")
            for row in rows:
                lines.append("| " + " | ".join(str(c) for c in row) + " |")
        else:
            lines.append("（无数据）")
        return "\n".join(lines) + "\n"

    @staticmethod
    def to_json(data: Mapping[str, Any]) -> str:
        return json.dumps(dict(data), ensure_ascii=False, indent=2, default=str)
