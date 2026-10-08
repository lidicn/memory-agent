import json
try:
    with open("/data/config.json") as f:
        cfg = json.load(f)
    print("config.json valid")
    print(f"service_tokens: {list(cfg.get('service_tokens', {}).keys())}")
    print(f"file size: {len(json.dumps(cfg))}")
except Exception as e:
    print(f"ERROR: {e}")
