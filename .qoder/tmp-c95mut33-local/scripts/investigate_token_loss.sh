#!/bin/bash
# 检查 config.json 中 service_tokens 状态，并调查根因
python3 << 'PYEOF'
import json, sys
sys.path.insert(0, "/app/src")

# 1. 直接读文件
with open("/data/config.json") as f:
    raw = json.load(f)
print("=== 文件中 service_tokens ===")
print(f"keys: {list(raw.get('service_tokens', {}).keys())}")

# 2. 通过 get_config() 读
from memory_agent.config import get_config
cfg = get_config()
print(f"\n=== get_config() service_tokens ===")
print(f"keys: {list(cfg.service_tokens.keys())}")

# 3. 检查 ServiceTokenStore
from memory_agent.service_tokens import get_service_token_store
store = get_service_token_store()
print(f"\n=== ServiceTokenStore.list_tokens() ===")
for t in store.list_tokens():
    print(f"  {t['name']}: prefix={t['prefix']} persisted={t.get('persisted')}")

# 4. 验证令牌
token = "svc_bBOOEsC7qcXcGFctr3wxz35pBM-FuNTVYAVrtMjToes"
print(f"\n=== verify('{token[:20]}...') ===")
print(f"result: {store.verify(token)}")
PYEOF
