import re
import secrets
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from ...store.db import get_db
from ..config import settings
from ..jwt_utils import sign_token

router = APIRouter(tags=["oauth"])

_AGENT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _safe_agent_id(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "-", name.strip().lower()).strip("-")
    return cleaned or "oauth-client"


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


@router.post("/oauth/token", summary="OAuth 2.1 client_credentials token endpoint")
async def token_endpoint(
    grant_type: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
):
    if grant_type != "client_credentials":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
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
async def authorize_endpoint():
    return JSONResponse(
        {
            "error": "unsupported_response_type",
            "error_description": "Artel only supports client_credentials grant",
        },
        status_code=400,
    )


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
        "grant_types": ["client_credentials"],
        "response_types": ["token"],
    }
