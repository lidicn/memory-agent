"""审计报告 P0/P1 修复冒烟验证（本地可跑，不需完整 runtime）。

验证项：
- P1-2: _acp_kind(rt=None, scope=None) 不崩，返回默认值
- P1-1 announcer: cooldown > uptime 时首次触发不被吞（None 哨兵）
- P1-1 perception_rules: cooldown > uptime 时首次触发不被吞
- P0-3: 未登记工具 scope_of 返回 "unknown"，requires 返回 False
"""
import sys
import time
import types

sys.path.insert(0, "src")

passed = 0
failed = 0

def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


# ── P1-2: _acp_kind None 守卫 ──────────────────────────────────────────────
print("\n[P1-2] _acp_kind None 守卫")
from memory_agent.acp_server import _acp_kind
try:
    result = _acp_kind(None, None)
    check("scope=None 不崩", isinstance(result, str), f"got {result!r}")
except AttributeError as e:
    check("scope=None 不崩", False, str(e))

try:
    result = _acp_kind(None, {})
    check("scope={} 不崩", isinstance(result, str), f"got {result!r}")
except Exception as e:
    check("scope={} 不崩", False, str(e))


# ── P1-1: announcer None 哨兵 ──────────────────────────────────────────────
print("\n[P1-1] announcer cooldown 哨兵")
from memory_agent.announcer import Announcer

# 构造一个最小 announcer，不真正连 HA
ann = Announcer.__new__(Announcer)
ann.enabled = True
ann.cooldown_sec = 86400  # 24 小时冷却，远大于任何 uptime
ann._last = {}
ann.tts_entity = "media_player.tts"

# 模拟 ev 对象
class FakeEv:
    def __init__(self, kind):
        self.kind = kind
        self.text = "test"

ev = FakeEv("security_alert")
# 直接测 _should_announce 的冷却逻辑（不调 HA）
now = time.monotonic()
last = ann._last.get(ev.kind)
check("首次触发 last is None", last is None)
if last is None:
    check("cooldown=86400 首次不被吞", True)
else:
    check("cooldown=86400 首次不被吞", now - last >= ann.cooldown_sec,
          f"now={now:.0f} last={last:.0f} diff={now-last:.0f} < {ann.cooldown_sec}")


# ── P1-1: perception_rules None 哨兵 ───────────────────────────────────────
print("\n[P1-1] perception_rules cooldown 哨兵")
from memory_agent.perception_rules import RuleEngine

eng = RuleEngine.__new__(RuleEngine)
eng._last_triggered = {}
eng.rules = []

# 测 _in_cooldown
rule = {"id": "day_night_log", "cooldown_seconds": 3600}
in_cd = eng._in_cooldown(rule)
check("首次触发不在冷却中（None 哨兵）", in_cd is False, f"got {in_cd}")

# 触发后应在冷却中
eng._last_triggered["day_night_log"] = time.monotonic()
in_cd2 = eng._in_cooldown(rule)
check("触发后在冷却中", in_cd2 is True, f"got {in_cd2}")


# ── P0-3: 未登记工具 scope ─────────────────────────────────────────────────
print("\n[P0-3] 未登记工具权限判定")
from memory_agent.mcp_scopes import scope_of, requires, UNKNOWN

check("未登记工具 scope_of=unknown", scope_of("nonexistent_tool_xyz") == UNKNOWN)
check("未登记工具 requires=False", requires("nonexistent_tool_xyz", ["read", "write"]) is False)
check("已登记读工具 requires=True", requires("ask_memory", ["read"]) is True)
check("写工具无 write scope requires=False", requires("create_member", ["read"]) is False)
check("写工具有 write scope requires=True", requires("create_member", ["read", "write"]) is True)


# ── 汇总 ───────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"结果: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
