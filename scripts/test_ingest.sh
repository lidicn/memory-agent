#!/bin/bash
# 用 wget 测试（容器内可能没有 curl）
# 令牌从环境变量读取：export MA_INGEST_TOKEN=svc_xxx
TOKEN="${MA_INGEST_TOKEN:?请先 export MA_INGEST_TOKEN=svc_xxx}"
echo "=== 测试 POST /api/metrics/ingest ==="
python3 << PYEOF
import urllib.request
import json

token = "$TOKEN"
data = json.dumps({"automation_id": "test-auth", "dedupe_key": "test-auth-001", "metrics": {}}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:8000/api/metrics/ingest",
    data=data,
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"Status: {resp.status}")
        print(f"Body: {resp.read().decode()}")
except urllib.error.HTTPError as e:
    print(f"Status: {e.code}")
    print(f"Body: {e.read().decode()}")
PYEOF
