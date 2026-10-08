import json
cfg = json.load(open("/data/config.json"))
st = cfg.get("service_tokens", {})
print("service_tokens keys:", list(st.keys()))
for k, v in st.items():
    print(f"  {k}: prefix={v.get('prefix')} scopes={len(v.get('scopes', []))}")
    print(f"    scopes: {v.get('scopes', [])}")

# 验证 autoforge 令牌
from memory_agent.service_tokens import get_service_token_store
store = get_service_token_store()
token = "svc_-xd8ZTmbld1bRur9Ya4KipJFu_7I5Bb0Dnf9L2kWUhM"
result = store.verify(token)
print(f"\nautoforge verify result: {result}")
