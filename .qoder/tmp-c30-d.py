import sys, os, time, types
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
t0=time.time()
import httpx
print(f"import httpx {time.time()-t0:.2f}s httpx={httpx.__version__}", flush=True)
from memory_agent.ha_client import HAClient, _CLIENT_POOL
t0=time.time()
c=httpx.Client()
print(f"one client {time.time()-t0:.3f}s", flush=True)
t0=time.time()
for i in range(20):
    ha=HAClient(types.SimpleNamespace(hass_server="http://192.0.2.1:8123", hass_token=f"rot-{i}", tz_offset_hours=8.0))
    with ha._session() as cc:
        pass
    print(f"rot {i} t={time.time()-t0:.2f}s entries={len(_CLIENT_POOL)}", flush=True)
print("DONE", flush=True)
