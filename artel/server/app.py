import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from starlette.types import ASGIApp, Receive, Scope, Send

from ..mcp.server import mcp as mcp_server
from ..store.db import get_db
from .config import settings
from .feed_poller import run_poller
from .jwt_utils import verify_token
from .mdns import MDNSService
from .routes.agents import router as agents_router
from .routes.events import router as events_router
from .routes.feeds import router as feeds_router
from .routes.memory import router as memory_router
from .routes.messages import router as messages_router
from .routes.oauth import router as oauth_router
from .routes.onboard import router as onboard_router
from .routes.participants import router as participants_router
from .routes.projects import router as projects_router
from .routes.sessions import router as sessions_router
from .routes.tasks import router as tasks_router

mcp_server.settings.streamable_http_path = "/"
from mcp.server.fastmcp import FastMCP as _FastMCP  # noqa: E402

_mcp_asgi = _FastMCP.streamable_http_app(mcp_server)

_UI = Path(__file__).parent / "static" / "index.html"
_SESSION_TTL = 86400.0

_LOGIN = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>artel</title>
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font:15px/1.6 Inter,system-ui,sans-serif;background:#1d2021;color:#ebdbb2;display:flex;justify-content:center;align-items:center;min-height:100vh}
form{display:flex;flex-direction:column;gap:12px;width:280px}
h1{font-size:20px;color:#d79921;font-weight:500;letter-spacing:.5px;margin-bottom:4px}
input{background:#282828;color:#ebdbb2;border:1px solid #504945;padding:9px 12px;font:15px Inter,sans-serif;border-radius:3px}
input:focus{outline:none;border-color:#d79921}
button{background:#282828;color:#d79921;border:1px solid #d79921;padding:9px;font:15px Inter,sans-serif;border-radius:3px;cursor:pointer}
button:hover{background:#3c3836}
.err{color:#fb4934;font-size:13px}
.guest{color:#928374;font-size:13px;text-align:center;text-decoration:none}
.guest:hover{color:#d79921}
</style>
</head>
<body>
<form method="POST" action="/ui/login">
  <h1>artel</h1>
  {error}
  <input type="password" name="password" placeholder="admin password" autofocus>
  <button type="submit">login</button>
  <a class="guest" href="/ui">continue read-only →</a>
</form>
</body>
</html>
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = get_db(settings.db_path)
    for special_id, special_role in (
        (settings.ui_agent_id, "owner"),
        (settings.archivist_agent_id, "archivist"),
        (settings.viewer_agent_id, "viewer"),
    ):
        if not db.execute("SELECT 1 FROM agents WHERE id=?", (special_id,)).fetchone():
            db.execute(
                "INSERT INTO agents (id, api_key, role) VALUES (?, ?, ?)",
                (special_id, secrets.token_urlsafe(32), special_role),
            )
        db.execute("UPDATE agents SET role=? WHERE id=?", (special_role, special_id))
    db.commit()
    mdns = MDNSService(settings.port)
    try:
        await mdns.start()
    except Exception:
        pass
    poller = asyncio.create_task(run_poller())
    async with _mcp_asgi.router.lifespan_context(_mcp_asgi):
        yield
    poller.cancel()
    await asyncio.gather(poller, return_exceptions=True)
    try:
        await mdns.stop()
    except Exception:
        pass


def _protected_resource_body() -> bytes:
    base = settings.public_url or f"http://localhost:{settings.port}"
    return json.dumps(
        {
            "resource": f"{base}/mcp",
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
        }
    ).encode()


class MCPAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from ..mcp.server import _agent_id, _api_key

        headers = dict(scope.get("headers", []))
        qs = dict(p.split(b"=", 1) for p in scope.get("query_string", b"").split(b"&") if b"=" in p)
        auth_header = headers.get(b"authorization", b"").decode()

        aid = api_key = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                from .auth import _verify_agent

                aid, api_key = verify_token(token)
                if not _verify_agent(aid, api_key):
                    raise ValueError("revoked")
            except Exception:
                base = settings.public_url or f"http://localhost:{settings.port}"
                www_auth = (
                    f'Bearer realm="artel", error="invalid_token",'
                    f' resource_metadata="{base}/.well-known/oauth-protected-resource"'
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
            aid = (headers.get(b"x-agent-id") or qs.get(b"agent_id") or b"").decode()
            api_key = (headers.get(b"x-api-key") or qs.get(b"api_key") or b"").decode()
            if not aid or not api_key:
                from ..mcp.config import settings as mcp_settings

                aid = aid or mcp_settings.mcp_agent_id
                api_key = api_key or mcp_settings.api_key()

        t1 = _agent_id.set(aid)
        t2 = _api_key.set(api_key)
        try:
            await self.app(scope, receive, send)
        finally:
            _agent_id.reset(t1)
            _api_key.reset(t2)


app = FastAPI(
    title="Artel",
    version="0.1.0",
    description="Self-hosted coordination server for AI agent fleets. Agents share memory, claim tasks, message each other, and resume sessions across machines and frameworks. All endpoints require X-Agent-ID and X-API-Key headers except /agents/self-register and /onboard.",
    lifespan=lifespan,
)

app.include_router(agents_router)
app.include_router(oauth_router)
app.include_router(onboard_router)
app.include_router(memory_router)
app.include_router(tasks_router)
app.include_router(messages_router)
app.include_router(events_router)
app.include_router(sessions_router)
app.include_router(participants_router)
app.include_router(projects_router)
app.include_router(feeds_router)


@app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
async def oauth_protected_resource():
    return JSONResponse(content=json.loads(_protected_resource_body()))


app.mount("/mcp", MCPAuthMiddleware(_mcp_asgi))


_LLMS_TXT = (Path(__file__).parent.parent.parent / "llms.txt").read_text()
_FAVICON = Path(__file__).parent / "static" / "favicon.ico"


@app.get("/llms.txt", response_class=PlainTextResponse, include_in_schema=False)
async def llms_txt():
    return _LLMS_TXT


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(_FAVICON, media_type="image/x-icon")


@app.get("/health", summary="Health check")
async def health():
    try:
        get_db().execute("SELECT 1").fetchone()
        return {"status": "ok"}
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=503)


def _gc_ui_sessions() -> None:
    cutoff = time.time() - _SESSION_TTL
    get_db().execute("DELETE FROM ui_sessions WHERE last_seen_at < ?", (cutoff,))


def _authed(request: Request) -> bool:
    if not settings.ui_password:
        return True
    token = request.cookies.get("session", "")
    if not token:
        return False
    db = get_db()
    row = db.execute("SELECT last_seen_at FROM ui_sessions WHERE token=?", (token,)).fetchone()
    if not row:
        return False
    now = time.time()
    if now - row["last_seen_at"] > _SESSION_TTL:
        with db:
            db.execute("DELETE FROM ui_sessions WHERE token=?", (token,))
        return False
    with db:
        db.execute("UPDATE ui_sessions SET last_seen_at=? WHERE token=?", (now, token))
    return True


_NO_STORE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


@app.get("/ui/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(error: str = ""):
    err = '<p class="err">incorrect password</p>' if error else ""
    return HTMLResponse(_LOGIN.replace("{error}", err), headers=_NO_STORE)


@app.post("/ui/login", include_in_schema=False)
async def login(password: str = Form(...)):
    if password == settings.ui_password:
        token = secrets.token_urlsafe(32)
        now = time.time()
        db = get_db()
        with db:
            _gc_ui_sessions()
            db.execute(
                "INSERT INTO ui_sessions (token, created_at, last_seen_at) VALUES (?, ?, ?)",
                (token, now, now),
            )
        r = RedirectResponse("/ui", status_code=303)
        r.set_cookie("session", token, httponly=True, samesite="lax")
        return r
    return RedirectResponse("/ui/login?error=1", status_code=303)


@app.get("/ui/logout", include_in_schema=False)
async def logout(request: Request):
    token = request.cookies.get("session", "")
    if token:
        db = get_db()
        with db:
            db.execute("DELETE FROM ui_sessions WHERE token=?", (token,))
    r = RedirectResponse("/ui/login", status_code=303)
    r.delete_cookie("session")
    r.headers["Clear-Site-Data"] = '"cache", "cookies", "storage"'
    return r


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def ui(request: Request):
    db = get_db()
    if _authed(request):
        aid = settings.ui_agent_id
        akey = settings.ui_agent_key()
        agent_row = db.execute("SELECT api_key, role FROM agents WHERE id=?", (aid,)).fetchone()
        if not akey and agent_row:
            akey = agent_row["api_key"]
        agent_role = agent_row["role"] if agent_row else "owner"
        regkey = settings.registration_key
    else:
        aid = settings.viewer_agent_id
        agent_row = db.execute("SELECT api_key, role FROM agents WHERE id=?", (aid,)).fetchone()
        akey = agent_row["api_key"] if agent_row else ""
        agent_role = agent_row["role"] if agent_row else "viewer"
        regkey = ""
    html = _UI.read_text().replace(
        "/*CREDS*/",
        f"window._aid={json.dumps(aid)};window._akey={json.dumps(akey)};window._regkey={json.dumps(regkey)};window._ui_agent_id={json.dumps(aid)};window._agent_role={json.dumps(agent_role)};",
    )
    return HTMLResponse(html, headers=_NO_STORE)
