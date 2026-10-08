import sys, time
sys.path.insert(0,"/data/workspace/ma/src")
from memory_agent.perception_rules import RuleEngine, STATIC_RULES
print("系统 uptime:", round(time.monotonic(),1), "秒")
print("STATIC_RULES 中带 cooldown_seconds 的规则:")
for r in STATIC_RULES:
    cd = r.get("cooldown_seconds", 0)
    print(f"   {r['id']:<28} cooldown={cd}")
print()
print("="*62)
eng = RuleEngine()
print("首次 evaluate 命中规则数:", len(eng.evaluate("motion", "客厅")))
hits = eng.evaluate("motion", "客厅")
print("第二次 evaluate 命中:", len(hits), "(应为 0 —— 冷却生效)")
print()
print("★ 关键：若任一 STATIC_RULE 的 cooldown_seconds > 系统 uptime，")
print("  该规则在设备重启后 uptime 达到该值之前，永远无法首次触发。")
print()
for r in STATIC_RULES:
    cd = float(r.get("cooldown_seconds", 0) or 0)
    if cd > 0:
        blocked = time.monotonic() < cd
        print(f"   {r['id']:<28} cooldown={cd:<8} 首次触发 {'★被永久抑制' if blocked else '正常'}")
