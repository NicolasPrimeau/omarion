import secrets
from datetime import UTC, datetime, timedelta

from jose import jwt

_ALGORITHM = "HS256"
_ISSUER = "artel"


def _get_secret() -> str:
    from ..store.db import get_db

    db = get_db()
    row = db.execute("SELECT value FROM kv WHERE key='jwt_secret'").fetchone()
    if row:
        return row["value"]
    s = secrets.token_hex(32)
    db.execute("INSERT OR IGNORE INTO kv (key, value) VALUES ('jwt_secret', ?)", (s,))
    db.commit()
    return s


def sign_token(agent_id: str, api_key: str, ttl_seconds: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "iss": _ISSUER,
        "sub": agent_id,
        "key": api_key,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, _get_secret(), algorithm=_ALGORITHM)


def verify_token(token: str) -> tuple[str, str]:
    payload = jwt.decode(token, _get_secret(), algorithms=[_ALGORITHM], issuer=_ISSUER)
    return payload["sub"], payload["key"]
