import base64
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ...store.db import get_db
from ..config import settings
from ..jwt_utils import sign_token

router = APIRouter(tags=["oauth"])

_AGENT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_AUTH_CODE_TTL = 600.0


def _safe_agent_id(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "-", name.strip().lower()).strip("-")
    return cleaned or "oauth-client"


def _gc_codes() -> None:
    get_db().execute("DELETE FROM oauth_codes WHERE expires_at < ?", (time.time(),))


def _validate_client(client_id: str, client_secret: str) -> tuple[str, str] | None:
    api_keys = settings.api_keys()
    if client_secret in api_keys and api_keys[client_secret] == client_id:
        return client_id, client_secret
    db = get_db()
    row = db.execute(
        "SELECT id, api_key FROM agents WHERE id=? AND api_key=?",
        (client_id, client_secret),
    ).fetchone()
    if row:
        return row["id"], row["api_key"]
    return None


def _lookup_agent(client_id: str) -> tuple[str, str] | None:
    db = get_db()
    row = db.execute("SELECT id, api_key FROM agents WHERE id=?", (client_id,)).fetchone()
    if row:
        return row["id"], row["api_key"]
    return None


def _redirect_with_params(redirect_uri: str, params: dict[str, str]) -> str:
    parsed = urlparse(redirect_uri)
    sep = "&" if parsed.query else "?"
    return f"{redirect_uri}{sep}{urlencode(params)}"


@router.post("/oauth/token", summary="OAuth 2.1 token endpoint")
async def token_endpoint(
    grant_type: str = Form(...),
    client_id: str | None = Form(default=None),
    client_secret: str | None = Form(default=None),
    code: str | None = Form(default=None),
    code_verifier: str | None = Form(default=None),
    redirect_uri: str | None = Form(default=None),
):
    if grant_type == "client_credentials":
        if not client_id or not client_secret:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        result = _validate_client(client_id, client_secret)
        if not result:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        agent_id, api_key = result
        access_token = sign_token(agent_id, api_key, settings.jwt_ttl)
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": settings.jwt_ttl,
        }

    if grant_type == "authorization_code":
        if not code:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        db = get_db()
        with db:
            _gc_codes()
            row = db.execute(
                "SELECT agent_id, api_key, client_id, code_challenge, expires_at"
                " FROM oauth_codes WHERE code=?",
                (code,),
            ).fetchone()
            if not row:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            db.execute("DELETE FROM oauth_codes WHERE code=?", (code,))
        if row["expires_at"] < time.time():
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if client_id and client_id != row["client_id"]:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        challenge = row["code_challenge"]
        if challenge:
            if not code_verifier:
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            digest = hashlib.sha256(code_verifier.encode()).digest()
            expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            if expected != challenge:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
        access_token = sign_token(row["agent_id"], row["api_key"], settings.jwt_ttl)
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": settings.jwt_ttl,
        }

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
async def oauth_server_metadata(request: Request):
    base = settings.public_url or str(request.base_url).rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "token_endpoint_auth_methods_supported": ["client_secret_post", "none"],
        "grant_types_supported": ["authorization_code", "client_credentials"],
        "response_types_supported": ["code", "token"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["mcp"],
    }


@router.get("/oauth/authorize", include_in_schema=False)
async def authorize_endpoint(
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    state: str | None = Query(default=None),
    code_challenge: str | None = Query(default=None),
    code_challenge_method: str | None = Query(default=None),
    scope: str | None = Query(default=None),
):
    if response_type != "code":
        params = {"error": "unsupported_response_type"}
        if state:
            params["state"] = state
        return RedirectResponse(_redirect_with_params(redirect_uri, params), status_code=302)

    found = _lookup_agent(client_id)
    if not found:
        params = {"error": "unauthorized_client"}
        if state:
            params["state"] = state
        return RedirectResponse(_redirect_with_params(redirect_uri, params), status_code=302)

    if code_challenge and code_challenge_method not in (None, "S256"):
        params = {"error": "invalid_request"}
        if state:
            params["state"] = state
        return RedirectResponse(_redirect_with_params(redirect_uri, params), status_code=302)

    agent_id, api_key = found
    code = secrets.token_urlsafe(32)
    db = get_db()
    with db:
        _gc_codes()
        db.execute(
            "INSERT INTO oauth_codes (code, agent_id, api_key, client_id, code_challenge,"
            " redirect_uri, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                code,
                agent_id,
                api_key,
                client_id,
                code_challenge,
                redirect_uri,
                time.time() + _AUTH_CODE_TTL,
            ),
        )
    params = {"code": code}
    if state:
        params["state"] = state
    return RedirectResponse(_redirect_with_params(redirect_uri, params), status_code=302)


@router.post("/oauth/register", status_code=201, include_in_schema=False)
async def register_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    client_name = body.get("client_name") or body.get("software_id") or "oauth-client"
    base = _safe_agent_id(str(client_name))
    db = get_db()
    candidate, i = base, 1
    while db.execute("SELECT 1 FROM agents WHERE id=?", (candidate,)).fetchone():
        candidate = f"{base}-{i}"
        i += 1
    api_key = secrets.token_urlsafe(32)
    db.execute("INSERT INTO agents (id, api_key) VALUES (?, ?)", (candidate, api_key))
    db.commit()
    return {
        "client_id": candidate,
        "client_secret": api_key,
        "client_id_issued_at": int(time.time()),
        "client_secret_expires_at": 0,
        "redirect_uris": body.get("redirect_uris") or [],
        "token_endpoint_auth_method": "client_secret_post",
        "grant_types": ["authorization_code", "client_credentials"],
        "response_types": ["code", "token"],
    }
