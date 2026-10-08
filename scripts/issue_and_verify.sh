#!/bin/bash
# 签发令牌后立即重启容器，确保应用加载新令牌
python3 << 'PYEOF'
import sys
sys.path.insert(0, "/app/src")
from memory_agent.service_tokens import ServiceTokenStore, BUTLER_SCOPES, APP_SCOPES

store = ServiceTokenStore()

# 清理旧的
for name in ["doubao-butler", "autoforge"]:
    try:
        store.revoke(name)
    except Exception:
        pass

# 签发 DB
db_scopes = sorted(set(BUTLER_SCOPES + APP_SCOPES))
db = store.generate("doubao-butler", db_scopes, source="butler")
print(f"DB: {db.get('token')}")

# 签发 AF
af = store.generate("autoforge", BUTLER_SCOPES, source="")
print(f"AF: {af.get('token')}")

# 验证落盘
import json
with open("/data/config.json") as f:
    raw = json.load(f)
print(f"落盘确认: {list(raw.get('service_tokens', {}).keys())}")
PYEOF
