import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from ..store.db import get_db
from .config import settings
from .mdns import MDNSService
from .routes.agents import router as agents_router
from .routes.events import router as events_router
from .routes.memory import router as memory_router
from .routes.messages import router as messages_router
from .routes.oauth import router as oauth_router
from .routes.onboard import router as onboard_router
from .routes.participants import router as participants_router
from .routes.projects import router as projects_router
from .routes.sessions import router as sessions_router
from .routes.tasks import router as tasks_router

_UI = Path(__file__).parent / "static" / "index.html"
_sessions: dict[str, float] = {}
_SESSION_TTL = 86400.0

_LOGIN = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>artel</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font:15px/1.6 Inter,system-ui,sans-serif;background:#0d0d0d;color:#c8c8c8;display:flex;justify-content:center;align-items:center;min-height:100vh}
form{display:flex;flex-direction:column;gap:12px;width:280px}
h1{font-size:20px;color:#4af;font-weight:500;letter-spacing:.5px;margin-bottom:4px}
input{background:#161616;color:#c8c8c8;border:1px solid #2a2a2a;padding:9px 12px;font:15px Inter,sans-serif;border-radius:3px}
input:focus{outline:none;border-color:#4af}
button{background:#161616;color:#4af;border:1px solid #4af;padding:9px;font:15px Inter,sans-serif;border-radius:3px;cursor:pointer}
button:hover{background:#0a1a2a}
.err{color:#f55;font-size:13px}
</style>
</head>
<body>
<form method="POST" action="/ui/login">
  <h1>artel</h1>
  {error}
  <input type="password" name="password" placeholder="password" autofocus>
  <button type="submit">login</button>
</form>
</body>
</html>
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_db(settings.db_path)
    mdns = MDNSService(settings.port)
    try:
        await mdns.start()
    except Exception:
        pass
    yield
    try:
        await mdns.stop()
    except Exception:
        pass


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


_LLMS_TXT = (Path(__file__).parent.parent.parent / "llms.txt").read_text()


@app.get("/llms.txt", response_class=PlainTextResponse, include_in_schema=False)
async def llms_txt():
    return _LLMS_TXT


@app.get("/health", summary="Health check")
async def health():
    try:
        get_db().execute("SELECT 1").fetchone()
        return {"status": "ok"}
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=503)


def _authed(request: Request) -> bool:
    if not settings.ui_password:
        return True
    token = request.cookies.get("session", "")
    ts = _sessions.get(token)
    if ts is None:
        return False
    if time.time() - ts > _SESSION_TTL:
        _sessions.pop(token, None)
        return False
    return True


@app.get("/ui/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(error: str = ""):
    err = '<p class="err">incorrect password</p>' if error else ""
    return _LOGIN.replace("{error}", err)


@app.post("/ui/login", include_in_schema=False)
async def login(password: str = Form(...)):
    if password == settings.ui_password:
        token = secrets.token_urlsafe(32)
        _sessions[token] = time.time()
        r = RedirectResponse("/ui", status_code=303)
        r.set_cookie("session", token, httponly=True, samesite="lax")
        return r
    return RedirectResponse("/ui/login?error=1", status_code=303)


@app.get("/ui/logout", include_in_schema=False)
async def logout(request: Request):
    _sessions.pop(request.cookies.get("session", ""), None)
    r = RedirectResponse("/ui/login", status_code=303)
    r.delete_cookie("session")
    return r


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def ui(request: Request):
    if not _authed(request):
        return RedirectResponse("/ui/login")
    aid = settings.ui_agent_id
    akey = settings.ui_agent_key()
    if not akey:
        row = get_db().execute("SELECT api_key FROM agents WHERE id=?", (aid,)).fetchone()
        if row:
            akey = row["api_key"]
    regkey = settings.registration_key
    html = _UI.read_text().replace(
        "/*CREDS*/",
        f"window._aid={json.dumps(aid)};window._akey={json.dumps(akey)};window._regkey={json.dumps(regkey)};",
    )
    return HTMLResponse(html)
