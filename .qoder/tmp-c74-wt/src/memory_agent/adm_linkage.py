"""契约 v2.0 §B/§C 的本仓出口：统一错误码 + 联动失败的留痕。

真源是 homesdk 0.3.2 的 `homesdk.adm.errors`。**本仓运行面装的仍是 vendored 0.3.1**
（`Dockerfile:24`），那份没有 `adm` 子包 ⇒ 库缺席时这里按契约 §B **逐字**自带一份同键同值的词表。
两条路产出的码字符串必须一致，否则"装没装库"会改变对端在 `inbox_events` 里看到的码——
这正是裁定 §四 判例 1 警告的那类静默漂移。

三档降级（§C）在本仓的落法：

- **fail-closed**（写面 / 不可逆）：拒发 + 码 + 留痕。收件箱投递属这一档。
- **degrade-flag**（读面 / 可重试）：继续 + `degraded` + 码。MQTT 断连属这一档，
  paho 的 loop 线程自己会重连，所以这里只记码，不阻断采集主链路。
- **fail-open**（纯提示）：放行 + 日志。快照、字幕这类缺了也不影响判断的旁路。

`status reasons[]` 与 MCP·HTTP `{ok:false, code, message}` 那两个契约落点**尚未翻线**：
它们要求 `adm/*/status` 从字面量改成 JSON，而那是合并窗动作（计划 §七 件 2/3，前置=件 1）。
码先记进 `LinkageJournal`，窗内由 status 编码器一次性读走。
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

#: 契约表 §七 B 的六枚码；键名与值都逐字照抄（值就是名字本身，homesdk 那份同形）。
_CONTRACT_CODES = (
    "ADM_ERR_BROKER_UNREACHABLE",
    "ADM_ERR_PEER_OFFLINE",
    "ADM_ERR_PAYLOAD_INVALID",
    "ADM_ERR_AUTH_REQUIRED",
    "ADM_ERR_UPSTREAM_TIMEOUT",
    "ADM_ERR_INTERNAL",
)

#: homesdk 在场时指向库里的模块，缺席时为 ``False``（探测只发生一次，与 house_time 同口径）。
_errors_probe: Any = None
_JOURNAL_LIMIT = 64


def homesdk_errors() -> Any:
    """返回 ``homesdk.adm.errors``，缺席则 ``False``。"""
    global _errors_probe
    if _errors_probe is None:
        try:
            from homesdk.adm import errors as _errors  # noqa: PLC0415
        except Exception:  # noqa: BLE001 - 可选依赖，缺席只是走本仓同值词表
            _errors_probe = False
        else:
            _errors_probe = _errors if getattr(_errors, "ADM_ERRORS", None) else False
    return _errors_probe


def reset_errors_probe() -> None:
    """清掉探测缓存（热更新后库可能才到位）。"""
    global _errors_probe
    _errors_probe = None


def code(name: str) -> str:
    """取一枚契约码的字符串值：库在就用库的常量，库不在就用本仓那份同值字面量。

    传错名字不当机：回 ``ADM_ERR_INTERNAL`` 并留痕——兜底档本来就是"未分类"，
    比抛异常更适合旁路能力（抛进采集链路等于把提示失败升级成服务失败）。
    """
    if name not in _CONTRACT_CODES:
        return "ADM_ERR_INTERNAL"
    errors = homesdk_errors()
    if errors:
        return str(getattr(errors, name, name))
    return name


def is_adm_err(value: Any) -> bool:
    """这枚字符串是不是契约码（库在时按库的 ``ADM_ERRORS`` 判，缺席按本仓词表判）。"""
    errors = homesdk_errors()
    if errors:
        check = getattr(errors, "is_adm_err", None)
        if callable(check):
            return bool(check(value))
    return value in _CONTRACT_CODES


class LinkageJournal:
    """联动失败留痕：码 + 主题 + 原因，环形缓冲，只给进程内读。

    为什么不是日志行：契约要求"失败必须带码"且落点可被对端读走（status `reasons[]`）。
    日志里的中文描述没法判等，也没法在窗内喂给 status 编码器。
    """

    def __init__(self, limit: int = _JOURNAL_LIMIT):
        self._limit = int(limit)
        self._items: deque[dict] = deque(maxlen=self._limit)

    def note(self, code_value: str, message: str, *, topic: str = "",
             trace_id: str = "") -> dict:
        entry = {
            "code": code_value if is_adm_err(code_value) else "ADM_ERR_INTERNAL",
            "message": (message or "")[:300],
            "topic": topic,
            "trace_id": trace_id,
            # 留痕给运维看，口径与事件类载荷一致用家庭墙钟（收件箱那条例外只在载荷里）
            "ts": time.time(),
        }
        self._items.append(entry)
        return entry

    def entries(self) -> list[dict]:
        return list(self._items)

    def reasons(self) -> list[str]:
        """按契约 §七 A 给 status `reasons[]` 用：去重、保持首次出现顺序。"""
        seen: list[str] = []
        for it in self._items:
            if it["code"] not in seen:
                seen.append(it["code"])
        return seen

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)
