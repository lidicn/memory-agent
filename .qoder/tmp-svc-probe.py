import os
import sys

sys.path.insert(0, "/app/src")
from memory_agent.config import get_config  # noqa: E402
from memory_agent.service_tokens import ServiceTokenStore  # noqa: E402

cfg = get_config()
st = ServiceTokenStore(cfg)
rows = st.list_tokens()
print("config.service_tokens_rows =", len(cfg.service_tokens or {}))
print("store.count() =", st.count())
print("BUTLER_TOKEN_configured =", bool(getattr(cfg, "butler_token", "")))
print("APP_TOKEN_configured =", bool(getattr(cfg, "app_token", "")))
keys = sorted({k for r in rows for k in r})
print("record_keys =", keys)
print("expiry_field_present =", any("expiry" in k or "expires" in k for k in keys))
for r in rows:
    print("  - name=", r.get("name"), "source=", r.get("source"),
          "persisted=", r.get("persisted"), "imported_from=", r.get("imported_from"),
          "scopes=", len(r.get("scopes") or []), "use_count=", r.get("use_count"))
