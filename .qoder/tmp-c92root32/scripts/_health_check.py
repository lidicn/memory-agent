import urllib.request, sys, json
try:
    r = urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=10)
    body = r.read().decode()
    print("HEALTH_OK", r.status)
    print(body[:500])
except Exception as e:
    print("HEALTH_FAIL", type(e).__name__, str(e)[:200])
    sys.exit(1)
