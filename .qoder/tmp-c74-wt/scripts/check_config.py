import json
with open("/data/config.json") as f:
    cfg = json.load(f)
st = cfg.get("service_tokens", {})
print("service_tokens keys:", list(st.keys()))
for k, v in st.items():
    print(f"  {k}: prefix={v.get('prefix')} scopes={len(v.get('scopes', []))}")
