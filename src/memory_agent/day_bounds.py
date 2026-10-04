"""`timedelta(days=...)` 的天数收口：极值/负值防抖（审计第八轮 P3-2）。

**为什么要有这一层**：`days` 一路从 MCP 工具形参、HTTP 查询参数、乃至"从问句里正则出来的
数字"（`最近 999999 天`）走到 `timedelta(days=...)`，中间没人管。实测两种坏法：

- 极大值直接 `OverflowError: date value out of range`（`now - timedelta(days=1_000_000)`
  会退到公元 1 年之前），整条查询/周期任务抛异常；
- 负值不报错，而是**把窗口悄悄算反**（`start > end`），返回形状合法的空结果或倒序区间，
  调用方看不出口径变了——这正是本轮审计反复命中的那一族缺陷。

**两类处置不一样，不能一律 clamp 到 `[1, 3650]`**：

- 查询窗口（"最近 N 天"）：上下界都收，`[DAY_WINDOW_MIN, DAY_WINDOW_MAX]`。越界只影响
  一次查询的扫描量，收紧是净收益。
- 保留期与记忆有效期（`retention_days` / `ttl_days` / `keep_days`）：上界**不能**按 10 年裁。
  这类值语义是"留多久才删"，把 200 年裁成 10 年等于把"永久保留"变成"删掉 10 年前的数据"。
  所以它们用 `LONG_WINDOW_MAX`——一个大到任何家用数据都不可能超过、又能让日期算式留在
  `datetime` 域内的天花板，越界等价于"什么都不删"。下界保持各自的现状（保留期在调用点
  另有 `<= 0 → 不 purge` 的约定，不在这里改）。

`# day-ok: 理由` 是给 `scripts/scan_day_bounds.py` 的归属标记：那些值不来自外部
（字面量元组、FastAPI `Query(ge=..., le=...)` 已声明收敛、构造上有界的日期算式）的站点
不需要再包一层 clamp，理由写在标记里让下一个人复核。
"""

from __future__ import annotations

import math

__all__ = ["DAY_WINDOW_MIN", "DAY_WINDOW_MAX", "LONG_WINDOW_MAX", "clamp_days"]

#: 查询窗口下界：0 天窗口是空话，负数会把窗口算反。
DAY_WINDOW_MIN = 1

#: 查询窗口上界，沿用审计第八轮 P3-2 的口径（10 年）。
DAY_WINDOW_MAX = 3650

#: 保留期/TTL 的天花板（≈547 年）。只为把日期算式留在 `datetime` 域内，
#: 不是一个"会真的删到"的保留期。
LONG_WINDOW_MAX = 200_000


def clamp_days(value, default: int = DAY_WINDOW_MIN,
               lo: int = DAY_WINDOW_MIN, hi: int = DAY_WINDOW_MAX):
    """把外部传来的天数收敛进 `[lo, hi]`。

    - 非数字 / 无法解析的字符串 → `default`（调用点决定退回哪个默认窗口）；
    - `NaN` → `default`；`±inf` → 同号边界（"想要无穷"取上界，不裁小）；
    - 整数值返回 `int`、小数天数返回 `float`（`TimeRange.shift` 一类允许半天）；
    - 超大整数（连 `float()` 都溢出）走 `isinstance(value, int)` 快路径，直接落 `hi`。

    越界不静默：这些函数的返回值里都带 `start`/`end`（或 `expires_at`），
    收口后的窗口就是调用方看到的那个窗口，不存在"报了 999999 天、实际查了 3650 天还不说"。
    """
    if isinstance(value, int):  # 含 bool；不经 float()，避免 10**400 自身溢出
        return min(hi, max(lo, value))
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(n):
        return default
    if math.isinf(n):
        return hi if n > 0 else lo
    n = float(min(hi, max(lo, n)))   # 夹到边界时 min/max 会返回 int 边界本身，先归回 float
    return int(n) if n.is_integer() else n
