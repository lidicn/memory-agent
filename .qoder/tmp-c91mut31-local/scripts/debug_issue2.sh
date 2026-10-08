#!/bin/bash
# 重新签发并立即检查落盘
python3 << 'PYEOF'
import sys
sys.path.insert(0, "/app/src")
import json
from memory_agent.service_tokens import ServiceTokenStore, BUTLER_SCOPES

store = ServiceTokenStore()

# 检查当前 config
from memory_agent.config import get_config, CONFIG_FILE
cfg = get_config()
print(f"CONFIG_FILE: {CONFIG_FILE}")
print(f"当前 service_tokens: {list(cfg.service_tokens.keys())}")

# 签发
res = store.generate("autoforge-test", BUTLER_SCOPES, source="")
print(f"签发结果: ok={res.get('ok')} name={res.get('name')}")

# 立即重新读 config
cfg2 = get_config()
print(f"签发后 service_tokens: {list(cfg2.service_tokens.keys())}")

# 直接读文件
with open(CONFIG_FILE) as f:
    raw = json.load(f)
print(f"文件中 service_tokens: {list(raw.get('service_tokens', {}).keys())}")

# 验证
r = store.verify(res.get("token", ""))
print(f"verify 结果: {r}")

# 清理
store.revoke("autoforge-test")
PYEOF
