#!/bin/bash
# 重新签发 DB + AF 令牌，并立即测试 /api/metrics/ingest
python3 << 'PYEOF'
import sys
sys.path.insert(0, "/app/src")
from memory_agent.service_tokens import ServiceTokenStore, BUTLER_SCOPES, APP_SCOPES

store = ServiceTokenStore()

# 清理旧的（如果有）
for name in ["doubao-butler", "autoforge"]:
    try:
        store.revoke(name)
    except Exception:
        pass

# DB：butler + app
db_scopes = sorted(set(BUTLER_SCOPES + APP_SCOPES))
db = store.generate("doubao-butler", db_scopes, source="butler")
print(f"DB token: {db.get('token')}")

# AF：butler
af = store.generate("autoforge", BUTLER_SCOPES, source="")
print(f"AF token: {af.get('token')}")

# 验证
print(f"DB verify: {store.verify(db.get('token','')) is not None}")
print(f"AF verify: {store.verify(af.get('token','')) is not None}")
PYEOF
