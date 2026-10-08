import asyncio
import sys

sys.path.insert(0, "src")

from starlette.requests import Request
from memory_agent.api import debug_routes as d


def make_request(run_id: str, user=None):
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/api/debug/llm/run/{run_id}",
        "headers": [],
        "state": {"user": user} if user else {},
    }
    async def receive():
        return {"type": "http.disconnect"}

    return Request(scope, receive)


async def main():
    dbg_user = {"username": "debug", "is_admin": False, "debug": True}
    for rid in ("zzz-not-real", "de935610d2ff439d9e20a480c7f1f542"):
        print(f"\n=== debug_status run_id={rid!r} (dbg user) ===")
        try:
            resp = await d.debug_status(make_request(rid, dbg_user), rid)
            body = resp.body
            if isinstance(body, bytes):
                body = body.decode("utf-8", "replace")
            print(f"  status_code={resp.status_code}")
            print(f"  body={body[:800]}")
        except Exception as e:
            import traceback
            print(f"  RAISED: {type(e).__name__}: {e}")
            traceback.print_exc()

    # also test the no-user path (should 401)
    print("\n=== debug_status no user (expect 401) ===")
    resp = await d.debug_status(make_request("zzz", None), "zzz")
    print(f"  status_code={resp.status_code}")


if __name__ == "__main__":
    asyncio.run(main())
