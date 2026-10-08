"""发布 ma/insights 测试消息。"""
import json
import time

import paho.mqtt.client as mqtt

client = mqtt.Client(client_id="ma-test-publisher")
client.connect("192.168.2.200", 1883, 60)
time.sleep(1)

payload = {
    "kind": "test",
    "summary": "MA联动收尾端到端测试",
    "evidence": [],
    "persons": [],
    "source": "ma",
    "ts": "2026-10-06T19:35:00",
}
result = client.publish("ma/insights", json.dumps(payload, ensure_ascii=False), qos=1)
print(f"publish result: rc={result.rc}, mid={result.mid}")
time.sleep(2)
client.disconnect()
print("done")
