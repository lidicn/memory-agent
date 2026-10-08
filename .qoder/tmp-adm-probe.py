"""ADM presence 取证订阅器：只打印主题与载荷，凭据从 MA 配置读取、绝不输出。

用法（在 memory-agent 容器内）：
  python adm_probe.py <秒数> [主题 ...]
默认主题：ma/presence ma/device-health adm/memory-agent/status adm/memory-agent/caps
"""
import json
import sys
import time

from memory_agent.config import get_config

TOPICS = sys.argv[2:] or [
    "ma/presence",
    "ma/device-health",
    "adm/memory-agent/status",
    "adm/memory-agent/caps",
    "butler/inbox/notify",
]
try:
    WINDOW = float(sys.argv[1])
except IndexError:
    WINDOW = 20.0

cfg = get_config()

import paho.mqtt.client as mqtt  # noqa: E402

got = []


def on_connect(client, userdata, flags, rc, *args):
    print(f"CONN rc={rc} ({'ok' if rc == 0 else 'refused'})")
    if rc == 0:
        client.subscribe([(t, 1) for t in TOPICS])


def on_message(client, userdata, msg):
    body = msg.payload.decode("utf-8", "replace")
    got.append((msg.topic, body, msg.retain, msg.qos))
    print(f"{time.strftime('%H:%M:%S')} MSG retain={int(msg.retain)} qos={msg.qos} "
          f"{msg.topic} :: {body[:400]}", flush=True)


try:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="ma-adm-probe")
except AttributeError:  # pragma: no cover
    client = mqtt.Client(client_id="ma-adm-probe")
client.on_connect = on_connect
client.on_message = on_message
user = getattr(cfg, "tv_mqtt_user", "") or ""
if user:
    client.username_pw_set(user, getattr(cfg, "tv_mqtt_pass", "") or "")  # 凭据只用于建连
client.connect(getattr(cfg, "tv_mqtt_host", "") or "", int(getattr(cfg, "tv_mqtt_port", 1883)), 30)
client.loop_start()
deadline = time.time() + WINDOW
while time.time() < deadline:
    time.sleep(0.2)
client.loop_stop()
client.disconnect()
print("SUMMARY " + json.dumps(
    {"window_s": WINDOW, "topics_seen": sorted({t for t, _, _, _ in got}),
     "retained_adm": [f"{t}::{body[:120]}" for t, body, r, _ in got
                      if r and t.startswith("adm/")],
     "count": len(got)},
    ensure_ascii=False))
