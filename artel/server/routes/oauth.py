from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from ...store.db import get_db
from ..config import settings
from ..jwt_utils import sign_token

router = APIRouter(tags=["oauth"])


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
        "token_endpoint": f"{base}/oauth/token",
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "grant_types_supported": ["client_credentials"],
        "response_types_supported": ["token"],
    }
