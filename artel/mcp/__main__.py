import json
import logging
import socket
import time

import httpx
import uvicorn
from jose import JWTError
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import _creds_file, settings
from .server import _agent_id, _api_key, mcp

log = logging.getLogger(__name__)


def _auto_register() -> tuple[str, str]:
    _creds_file.parent.mkdir(parents=True, exist_ok=True)
    suggested = socket.gethostname().split(".")[0]
    headers: dict[str, str] = {}
    if settings.mcp_registration_key:
        headers["x-registration-key"] = settings.mcp_registration_key
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(5):
        try:
            resp = httpx.post(
                f"{settings.artel_url}/agents/self-register",
                json={"agent_id": suggested, "project": settings.mcp_project or None},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            _creds_file.write_text(
                f"MCP_AGENT_ID={data['agent_id']}\nMCP_AGENT_KEY={data['api_key']}\n"
            )
            return data["agent_id"], data["api_key"]
        except Exception as e:
            last_exc = e
            delay = 2.0**attempt
            log.warning(
                "registration attempt %d failed: %s, retrying in %.0fs", attempt + 1, e, delay
            )
            time.sleep(delay)
    raise RuntimeError(f"failed to register after 5 attempts: {last_exc}")


def _protected_resource_body() -> bytes:
    base = settings.artel_url.rstrip("/")
    mcp_url = f"{base.rsplit(':', 1)[0]}:{settings.mcp_port}" if ":" in base else base
    return json.dumps(
        {
            "resource": mcp_url,
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
        }
    ).encode()


class AgentAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == "/.well-known/oauth-protected-resource":
            body = _protected_resource_body()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            qs = dict(
                p.split(b"=", 1) for p in scope.get("query_string", b"").split(b"&") if b"=" in p
            )
            agent_id = api_key = ""

            auth_header = headers.get(b"authorization", b"").decode()
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                try:
                    from artel.server.jwt_utils import verify_token

                    agent_id, api_key = verify_token(token)
                    # revocation check
                    from artel.server.auth import _verify_agent

                    if not _verify_agent(agent_id, api_key):
                        raise ValueError("revoked")
                except (JWTError, KeyError, ValueError, Exception):
                    base = settings.artel_url.rstrip("/")
                    meta_url = (
                        f"{base.rsplit(':', 1)[0]}:{settings.mcp_port}" if ":" in base else base
                    )
                    www_auth = (
                        f'Bearer realm="artel", error="invalid_token",'
                        f' resource_metadata="{meta_url}/.well-known/oauth-protected-resource"'
                    )
                    body = b'{"error":"invalid_token"}'
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 401,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"www-authenticate", www_auth.encode()),
                                (b"content-length", str(len(body)).encode()),
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    return
            else:
                agent_id = (
                    headers.get(b"x-agent-id") or qs.get(b"agent_id") or b""
                ).decode() or settings.mcp_agent_id
                api_key = (
                    headers.get(b"x-api-key") or qs.get(b"api_key") or b""
                ).decode() or settings.mcp_agent_key

            t1 = _agent_id.set(agent_id)
            t2 = _api_key.set(api_key)
            try:
                await self.app(scope, receive, send)
            finally:
                _agent_id.reset(t1)
                _api_key.reset(t2)
        else:
            await self.app(scope, receive, send)


def _credentials_valid() -> bool:
    try:
        resp = httpx.get(
            f"{settings.artel_url}/agents/me",
            headers={"x-agent-id": settings.mcp_agent_id, "x-api-key": settings.mcp_agent_key},
            timeout=5,
        )
        return resp.status_code != 401
    except Exception as e:
        log.warning("credentials check failed: %s", e)
        return False


def main():
    if settings.mcp_transport in ("sse", "streamable-http"):
        app_fn = (
            mcp.streamable_http_app if settings.mcp_transport == "streamable-http" else mcp.sse_app
        )
        app: ASGIApp = AgentAuthMiddleware(app_fn())
        uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port)
    else:
        if not settings.mcp_agent_key or not _credentials_valid():
            settings.mcp_agent_id, settings.mcp_agent_key = _auto_register()
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
