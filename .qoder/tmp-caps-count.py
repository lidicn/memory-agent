"""presence caps 取证：只数工具条数与抽查名字，不打印整份载荷。"""
import json
import sys

from memory_agent.config import get_config
import paho.mqtt.client as mqtt  # noqa: E402

cfg = get_config()
out = {}


def on_connect(c, u, f, rc, props=None):
    c.subscribe("adm/memory-agent/caps", qos=1)


def on_message(c, u, msg):
    try:
        d = json.loads(msg.payload.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        print("CAPS_PARSE_FAIL", e, "bytes", len(msg.payload))
        return
    tools = d.get("tools") or []
    print("CAPS_BYTES =", len(msg.payload))
    print("tools_len =", len(tools), "version =", d.get("version"), "mcp =", d.get("mcp"))
    ts = set(tools)
    print("diary_three_present =",
          {"read_self_diary", "write_self_diary", "generate_self_diary"} <= ts)
    print("spot_present =", sorted(n for n in (
        "list_members", "query_device_usage", "list_device_health", "revoke_active_rule",
        "assign_member_device", "counterfactual_query") if n in ts))
    out["done"] = True
    c.disconnect()


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="ma-caps-count")
client.on_connect = on_connect
client.on_message = on_message
user = getattr(cfg, "tv_mqtt_user", "") or ""
if user:
    client.username_pw_set(user, getattr(cfg, "tv_mqtt_pass", "") or "")  # 凭据只用于建连
client.connect(getattr(cfg, "tv_mqtt_host", "") or "",
               int(getattr(cfg, "tv_mqtt_port", 1883)), 15)
client.loop_start()
import time  # noqa: E402
for _ in range(int(sys.argv[1]) if len(sys.argv) > 1 else 25):
    if out.get("done"):
        break
    time.sleep(1)
client.loop_stop()
if not out.get("done"):
    print("NO_CAPS_MESSAGE in window")
