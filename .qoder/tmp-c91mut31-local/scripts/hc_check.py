import urllib.request
import json
try:
    r = urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=15)
    data = r.read().decode()
    print(f"STATUS={r.status}")
    print(f"BODY={data[:500]}")
except Exception as e:
    print(f"ERROR={e}")
