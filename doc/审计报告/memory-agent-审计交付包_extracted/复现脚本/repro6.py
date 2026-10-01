import sys, time
sys.path.insert(0,"/data/workspace/ma/src")
print("系统 uptime (time.monotonic):", round(time.monotonic(),1), "秒")
print()
print("复现 announcer 首次播报被误抑制的逻辑：")
from memory_agent.announcer import Announcer
class FakeHA:
    def __init__(self): self.calls=[]
    def execute_action(self, a): self.calls.append(a); return {"ok": True}
from memory_agent.perception_ingest import PerceptionEvent
def ev(): return PerceptionEvent(source="edge_ai", kind="face_known", room="客厅", payload={})

for cd in (30, 300, 1000, 3600, 86400):
    a = Announcer(FakeHA(), None, tts_entity="tts.x", target="media_player.y", enabled=True, cooldown_sec=cd)
    r = a.announce(ev())
    uptime_ok = time.monotonic() >= cd
    print(f"  cooldown_sec={cd:<6} → 首次 announce() 返回 {r}  "
          f"({'uptime 已超过 cooldown，正常' if uptime_ok else '★uptime < cooldown → 首次播报被误吞'})")
print()
print("★ 结论：哨兵值 0.0 与 time.monotonic() 语义不匹配。")
print("  设备重启后 uptime 归零，cooldown 配置 > uptime 期间，所有首次播报静默丢失。")
