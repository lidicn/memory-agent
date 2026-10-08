"""P3-2（days 极值 / 负值窗口反转）+ P3-4（_CLIENT_POOL 无界）改前/改后对读数。

同一份脚本在改前、改后各跑一遍，输入完全相同。只印数量、比值与 ISO 时间戳，
不印任何文本内容、entity_id、friendly_name 或人名。

用法：PYTHONPATH 由外部给（本机 = <repo>/src，容器 = /tmp/<snap>/src）。
"""
import asyncio
import os
import sys
import types
from datetime import datetime, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.environ.get("MA_SRC", os.path.join(_ROOT, "src")))

RC = 0


def say(tag, **kv):
    print(f"{tag} " + " ".join(f"{k}={v}" for k, v in kv.items()), flush=True)


def show_window(fn, label, days):
    """真调用 resolve_nl_window，只报 start/end 的先后关系与跨度天数。"""
    try:
        start, end, meta = fn("总结一下", days)
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        say(label, days=_desc(days), reversed=("yes" if s > e else "no"),
            span_days=round((e - s).total_seconds() / 86400, 2), note=meta.get("note", ""))
    except Exception as exc:  # noqa: BLE001
        say(label, days=_desc(days), raised=type(exc).__name__, detail=repr(exc)[:80])


def _desc(v):
    return "None" if v is None else (f"1e{len(str(abs(int(v))))-1}" if abs(int(v)) >= 1000 else str(v))


# ── A. 真调用：第八轮报的那条路径（ask_memory → resolve_nl_window）───────────
try:
    from memory_agent.insights.utils import resolve_nl_window as _rnl

    for d in (7, 3650, 10 ** 6, -5):
        show_window(_rnl, "A1_resolve_nl_window_default", d)
except Exception as exc:  # noqa: BLE001
    say("A1_IMPORT_FAIL", detail=repr(exc)[:120])
    RC = 1

# ── B. 真调用：从问句里解析出的「最近 N 天」（N 由文本决定，非默认参数）────────
try:
    from memory_agent.insights.utils import resolve_nl_window as _rnl2

    for q in ("最近 30 天发生了什么", "最近 999999 天发生了什么", "最近 0 天发生了什么"):
        try:
            start, end, meta = _rnl2(q, 7)
            s = datetime.fromisoformat(start)
            e = datetime.fromisoformat(end)
            say("B1_resolve_nl_window_text", q_len=len(q), reversed=("yes" if s > e else "no"),
                span_days=round((e - s).total_seconds() / 86400, 2), note=meta.get("note", ""))
        except Exception as exc:  # noqa: BLE001
            say("B1_resolve_nl_window_text", q_len=len(q), raised=type(exc).__name__,
                detail=repr(exc)[:80])
except Exception as exc:  # noqa: BLE001
    say("B1_IMPORT_FAIL", detail=repr(exc)[:120])
    RC = 1

# ── C. 同族兄弟站点：days 来自函数形参（可达性由量具判定），此处取表达式级读数 ─
SIBLINGS = [
    ("C1_analysis.resolve_range", "max(1, int(days or 7))", "lo_only"),
    ("C2_mcp._fetch_attribution_events", "max(1, int(days))", "lo_only"),
    ("C3_store.get_behavior_summary", "max(1, int(days)) - 1", "lo_only"),
    ("C4_daily_profile", "days", "unguarded"),
    ("C5_rule_engine", "days", "unguarded"),
    ("C6_vision_service", "days", "unguarded"),
    ("C7_mcp.read_self_diary", "days", "unguarded"),
    ("C8_summary_queries", "d", "unguarded"),
    ("C9_change_attribution.lookback_days", "lookback_days", "unguarded"),
]
for name, expr, bucket in SIBLINGS:
    now = datetime(2026, 10, 5, 12, 0, 0)
    for d in (10 ** 6, -5):
        days = lookback_days = d
        try:
            dt = now - timedelta(days=eval(expr))
            say(name, input=_desc(d), bucket=bucket,
                reversed=("yes" if dt > now else "no"),
                start_year=dt.year)
        except Exception as exc:  # noqa: BLE001
            say(name, input=_desc(d), bucket=bucket, raised=type(exc).__name__,
                detail=repr(exc)[:60])

# ── D. P3-4：连接池在 token 轮换下的增长（真 HAClient，真 _session）───────────
# 本机每次 httpx.Client() 建号约 2.9s（环境侧 WPAD/代理探测，非代码缺陷），
# 所以 ROT 由环境变量给：本机默认 30，容器默认 200。
try:
    import time
    import httpx
    from memory_agent.ha_client import HAClient, _CLIENT_POOL

    ROT = int(os.environ.get("C30_ROT", "30"))

    def _pool_state():
        return len(_CLIENT_POOL), sum(1 for c in _CLIENT_POOL.values() if not c.is_closed)

    _CLIENT_POOL.clear()
    t0 = time.time()
    for i in range(ROT):
        ha = HAClient(types.SimpleNamespace(
            hass_server="http://192.0.2.1:8123", hass_token=f"rot-{i}", tz_offset_hours=8.0))
        with ha._session() as c:
            pass
    elapsed = time.time() - t0
    entries, open_clients = _pool_state()
    say("D1_pool_growth", rot=ROT, entries=entries, open_clients=open_clients,
        closed_entries=entries - open_clients,
        per_client_ms=round(1000 * elapsed / max(1, ROT), 1))

    # 同 token 复用必须仍然是 1 条（第七轮买回来的收益，不能被本批弄坏）
    _CLIENT_POOL.clear()
    same = HAClient(types.SimpleNamespace(
        hass_server="http://192.0.2.1:8123", hass_token="stable", tz_offset_hours=8.0))
    with same._session() as c1:
        pass
    with same._session() as c2:
        pass
    say("D2_same_token_reuse", entries=len(_CLIENT_POOL), reused=("yes" if c1 is c2 else "no"))

    # 被淘汰的连接必须是**真被关闭**（状态字 = httpx 自己的 is_closed），
    # 且"最近用过的那条"不能被关闭（否则会把在途请求拆掉）。
    # 口径：evicted_closed 只统计**已离开池**的那些——池内值当然都是开着的，
    # 上一版对着池内数 is_closed 恒为 0，是一条不会响的读数（改后专属修正，改前行不可比）。
    _CLIENT_POOL.clear()
    keep_tok = "keep-me"
    keep_ha = HAClient(types.SimpleNamespace(
        hass_server="http://192.0.2.1:8123", hass_token=keep_tok, tz_offset_hours=8.0))
    seen = {}
    with keep_ha._session() as keep_client:
        pass
    seen[id(keep_client)] = keep_client
    for i in range(ROT):
        ha = HAClient(types.SimpleNamespace(
            hass_server="http://192.0.2.1:8123", hass_token=f" churn-{i}", tz_offset_hours=8.0))
        with ha._session() as c:
            seen[id(c)] = c
        # 每轮再把 keep 用一次，让它始终是"最近使用"
        with keep_ha._session():
            pass
    pooled = list(_CLIENT_POOL.values())
    still_there = any(c is keep_client for c in pooled)
    evicted = [c for k, c in seen.items() if not any(c is p for p in pooled)]
    say("D3_lru_safety", entries=len(pooled), created=len(seen),
        keep_survived=("yes" if still_there else "no"),
        keep_closed=(keep_client.is_closed),
        evicted=len(evicted),
        evicted_closed=sum(1 for c in evicted if c.is_closed))
except Exception as exc:  # noqa: BLE001
    say("D1_IMPORT_FAIL", detail=repr(exc)[:120])
    RC = 1

# ── E. 真调用：MCP 工具面 ask_memory（只有装了 mcp SDK 的一侧能跑到）──────────
# 环境缺依赖时记 note，不算失败（两侧读数口径不同，各自登记）。
try:
    from memory_agent import mcp_server as ms

    server = getattr(ms, "mcp", None)
    mgr = getattr(server, "_tool_manager", None)
    tools = getattr(mgr, "_tools", None) if mgr else None
    if not isinstance(tools, dict):
        say("E1_mcp_ask_memory", note="mcp_sdk_unavailable")
    else:
        tool = tools.get("ask_memory")
        if tool is None:
            say("E1_mcp_ask_memory", note="tool_absent")
        else:
            import tempfile
            from memory_agent.store import Store

            tmp = tempfile.mkdtemp(prefix="c30mcp")
            store = Store(os.path.join(tmp, "t.db"), tz_offset_hours=8.0)
            store.init_schema()
            try:
                ms.get_runtime = lambda: types.SimpleNamespace(store=store)
                for d in (7, 10 ** 6, -5):
                    try:
                        res = asyncio.run(tool.fn(question="家里最近有什么异常", days=d))
                        ok = res.get("ok") if isinstance(res, dict) else None
                        say("E1_mcp_ask_memory", days=_desc(d), ok=ok,
                            keys=len(res) if isinstance(res, dict) else -1,
                            has_error=("yes" if isinstance(res, dict) and res.get("error") else "no"))
                    except Exception as exc:  # noqa: BLE001
                        say("E1_mcp_ask_memory", days=_desc(d), raised=type(exc).__name__,
                            detail=repr(exc)[:70])
            finally:
                try:
                    store.close()
                except Exception:  # noqa: BLE001
                    pass
except Exception as exc:  # noqa: BLE001
    say("E1_mcp_ask_memory", note="import_failed", detail=repr(exc)[:90])

print(f"PROBE_RC={RC}")
sys.exit(RC)
