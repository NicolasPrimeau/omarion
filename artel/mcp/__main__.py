import uvicorn
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import settings
from .server import _agent_id, _api_key, mcp


class AgentAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            qs = dict(p.split(b"=", 1) for p in scope.get("query_string", b"").split(b"&") if b"=" in p)
            aid = (headers.get(b"x-agent-id") or qs.get(b"agent_id") or b"").decode() or settings.mcp_agent_id
            akey = (headers.get(b"x-api-key") or qs.get(b"api_key") or b"").decode() or settings.mcp_agent_key
            t1 = _agent_id.set(aid)
            t2 = _api_key.set(akey)
            try:
                await self.app(scope, receive, send)
            finally:
                _agent_id.reset(t1)
                _api_key.reset(t2)
        else:
            await self.app(scope, receive, send)


def main():
    if settings.mcp_transport == "sse":
        app: ASGIApp = AgentAuthMiddleware(mcp.sse_app())
        uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
