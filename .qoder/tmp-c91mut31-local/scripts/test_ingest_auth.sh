#!/bin/bash
# 测试 /api/metrics/ingest 鉴权
TOKEN="svc_-xd8ZTmbld1bRur9Ya4KipJFu_7I5Bb0Dnf9L2kWUhM"
echo "=== 测试 autoforge 令牌 ==="
curl -s -X POST http://127.0.0.1:8000/api/metrics/ingest \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"automation_id":"test-debug","dedupe_key":"test-debug-001"}'
echo ""
echo "=== 检查 config.json ==="
python3 -c "
import json
with open('/data/config.json') as f:
    cfg = json.load(f)
st = cfg.get('service_tokens', {})
print('keys:', list(st.keys()))
for k,v in st.items():
    print(f'  {k}: prefix={v.get(\"prefix\")} scopes_count={len(v.get(\"scopes\",[]))}')
"
echo "=== 验证令牌 ==="
python3 -c "
import sys; sys.path.insert(0,'/app/src')
from memory_agent.service_tokens import get_service_token_store
store = get_service_token_store()
r = store.verify('$TOKEN')
print('verify result:', r)
"
