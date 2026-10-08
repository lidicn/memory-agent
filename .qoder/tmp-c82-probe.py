"""run26 RUNTIME 格：裁 B 的四格就绪矩阵，在真解释器、真 import 下走一遍。

AST 锁证明"形状对"，这一档证明**真调用路径按裁定分流**：四格分别覆盖
总闸关 / HA 两键齐 / 缺播放设备 / 两条都不通，逐格读 `switch`、`enabled`、`inbox_ready`
与实际打了哪条路。`PROBE_BAD=0` 才算过。

用法：`python .qoder/tmp-c82-probe.py`（本机）或容器内 `/tmp/${SNAP}_probe.py`。
"""
import os
import sys

# 取数根：容器里这份脚本被 scp 到 /tmp/<SNAP>_probe.py，与被测树不在同一目录下，
# 按 `__file__` 的祖父目录去推 `src` 会算出 `/src`。所以根由环境变量显式给，
# 路径推算只当本机默认值。
_ROOT = os.environ.get("PROBE_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from memory_agent.announcer import Announcer  # noqa: E402
from memory_agent.perception_ingest import PerceptionEvent  # noqa: E402

TTS = "tts.edgetts_zh_cn_xiaoxiaoneural"
TARGET = "media_player.living_speaker"


class RecHA:
    def __init__(self):
        self.calls = []

    def execute_action(self, action):
        self.calls.append(action)
        return {"ok": True, "status_code": 200}


class RecMqtt:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.calls = []

    def publish_speak(self, text, *, trace_id, **kw):
        self.calls.append({"text": text, "trace_id": trace_id})
        return True


def case(name, *, switch, tts, target, bridge, expect_enabled, expect_inbox, expect_route):
    ha, mqtt = RecHA(), (RecMqtt() if bridge == "on" else (RecMqtt(enabled=False) if bridge == "off" else None))
    a = Announcer(ha, None, tts_entity=tts, enabled=switch, cooldown_sec=0, target=target, mqtt=mqtt)
    ret = a.announce(PerceptionEvent(source="edge_ai", kind="face_known", room="客厅",
                                     payload={"friendly_name": "爸爸"}))
    ha_hits, inbox_hits = len(ha.calls), len(mqtt.calls) if mqtt is not None else 0
    # 走的是哪条路**只从命中数倒推**：探针不许自己复述一遍路由逻辑再跟自己比对。
    route = "ha" if ha_hits else ("inbox" if inbox_hits else "none")
    ok = (a.enabled is expect_enabled and a.inbox_ready is expect_inbox
          and route == expect_route
          and ha_hits == (1 if expect_route == "ha" else 0)
          and inbox_hits == (1 if expect_route == "inbox" else 0)
          and ret is (expect_route != "none"))
    tid = mqtt.calls[0]["trace_id"] if inbox_hits else ""
    print("CASE_%s SWITCH=%s ENABLED=%s INBOX_READY=%s ROUTE=%s HA_HITS=%d INBOX_HITS=%d RET=%s TID_LEN=%d OK=%s"
          % (name, a.switch, a.enabled, a.inbox_ready, route, ha_hits, inbox_hits, ret,
             len(tid), ok))
    return 0 if ok else 1


bad = 0
# 1) 总闸关：两键齐、桥也开，两条路都不许动
bad += case("switch_off", switch=False, tts=TTS, target=TARGET, bridge="on",
            expect_enabled=False, expect_inbox=False, expect_route="none")
# 2) HA 两键齐：直发优先，收件箱必须静默（裁 B 驳掉"并行"的那一格）
bad += case("ha_wins", switch=True, tts=TTS, target=TARGET, bridge="on",
            expect_enabled=True, expect_inbox=True, expect_route="ha")
# 3) 只配实体不配播放设备：HA 会只合成不出声 → 回落投收件箱
bad += case("fallback_no_target", switch=True, tts=TTS, target="", bridge="on",
            expect_enabled=False, expect_inbox=True, expect_route="inbox")
# 4) 两条都不通（HA 缺键 + 桥 disabled）：如实 False，不许"当作发了"
bad += case("no_route", switch=True, tts="", target="", bridge="off",
            expect_enabled=False, expect_inbox=False, expect_route="none")
print("PROBE_BAD=%d" % bad)
sys.exit(0 if bad == 0 else 2)
