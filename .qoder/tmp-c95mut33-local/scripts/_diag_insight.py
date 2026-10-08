import asyncio, json, sys
sys.path.insert(0, "/app/src")
sys.path.insert(0, "/tmp/tests")
from test_insight_query import _FakeRT, _tpl, _request
from memory_agent.api import insight_routes
rt = _FakeRT([_tpl()])
req = _request({"template_id": "tpl_x", "days": 2}, rt)
out = asyncio.run(insight_routes.insight_query(req))
body = out.body if hasattr(out, "body") else out
if isinstance(body, bytes):
    body = body.decode()
print(str(body)[:2000])
