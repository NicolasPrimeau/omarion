from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Query, Request

from ..store.db import get_db
from .config import settings
from .presence import update_seen


def _verify_agent(agent_id: str, api_key: str) -> bool:
    keys = settings.api_keys()
    if api_key in keys and keys[api_key] == agent_id:
        return True
    db = get_db()
    row = db.execute(
        "SELECT id FROM agents WHERE id=? AND api_key=?", (agent_id, api_key)
    ).fetchone()
    return row is not None


async def require_agent(
    request: Request,
    x_agent_id: str = Header(default=""),
    x_api_key: str = Header(default=""),
) -> str:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        try:
            from .jwt_utils import verify_token

            agent_id, api_key = verify_token(auth[7:])
            if not _verify_agent(agent_id, api_key):
                raise HTTPException(status_code=401, detail="invalid credentials")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=401, detail="invalid credentials")
        update_seen(agent_id, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        return agent_id

    if not x_agent_id or not x_api_key:
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not _verify_agent(x_agent_id, x_api_key):
        raise HTTPException(status_code=401, detail="invalid credentials")
    update_seen(x_agent_id, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
    return x_agent_id


async def require_registration_key(
    x_registration_key: str = Header(default=""),
) -> None:
    if not settings.registration_key or x_registration_key != settings.registration_key:
        raise HTTPException(status_code=401, detail="invalid registration key")


ROLE_RANK = {"viewer": 0, "agent": 1, "archivist": 2, "owner": 3}


def role_of(agent_id: str) -> str:
    db = get_db()
    row = db.execute("SELECT role FROM agents WHERE id=?", (agent_id,)).fetchone()
    if row is not None and row["role"] in ROLE_RANK:
        return row["role"]
    return "agent"


def is_owner(agent_id: str) -> bool:
    return role_of(agent_id) == "owner"


def can_curate_memory(agent_id: str) -> bool:
    return role_of(agent_id) in ("owner", "archivist")


def require_role(minimum: str):
    async def _dep(agent_id: str = Depends(require_agent)) -> str:
        if ROLE_RANK[role_of(agent_id)] < ROLE_RANK[minimum]:
            raise HTTPException(status_code=403, detail="insufficient role")
        return agent_id

    return _dep


def _memberships(agent_id: str) -> list[str] | None:
    if agent_id == settings.ui_agent_id:
        return None
    is_static = agent_id in settings.api_keys().values()
    static = settings.agent_projects().get(agent_id)
    db = get_db()
    rows = db.execute(
        "SELECT project_id FROM project_members WHERE agent_id=?", (agent_id,)
    ).fetchall()
    db_projects = [r["project_id"] for r in rows]
    if is_static and static is None:
        return None
    return list(set((static or []) + db_projects))


def project_filter(agent_id: str) -> tuple[str, list]:
    allowed = _memberships(agent_id)
    if allowed is None:
        return "", []
    if not allowed:
        return "(project IS NULL)", []
    placeholders = ",".join("?" * len(allowed))
    return f"(project IS NULL OR project IN ({placeholders}))", list(allowed)


async def require_agent_feed(
    request: Request,
    agent_id_q: str = Query(default="", alias="agent_id"),
    api_key_q: str = Query(default="", alias="api_key"),
    x_agent_id: str = Header(default=""),
    x_api_key: str = Header(default=""),
) -> str:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        try:
            from .jwt_utils import verify_token

            aid, key = verify_token(auth[7:])
            if not _verify_agent(aid, key):
                raise HTTPException(status_code=401, detail="invalid credentials")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=401, detail="invalid credentials")
        update_seen(aid, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        return aid
    aid = x_agent_id or agent_id_q
    key = x_api_key or api_key_q
    if not aid or not key:
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not _verify_agent(aid, key):
        raise HTTPException(status_code=401, detail="invalid credentials")
    update_seen(aid, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
    return aid


AgentDep = Depends(require_agent)
ReaderDep = Depends(require_role("viewer"))
ActorDep = Depends(require_role("agent"))
OwnerDep = Depends(require_role("owner"))
