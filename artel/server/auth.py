from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException

from ..store.db import get_db
from .config import settings

_last_seen: dict[str, str] = {}


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
    x_agent_id: str = Header(...),
    x_api_key: str = Header(...),
) -> str:
    if not _verify_agent(x_agent_id, x_api_key):
        raise HTTPException(status_code=401, detail="invalid credentials")
    _last_seen[x_agent_id] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return x_agent_id


async def require_registration_key(
    x_registration_key: str = Header(...),
) -> None:
    if not settings.registration_key or x_registration_key != settings.registration_key:
        raise HTTPException(status_code=401, detail="invalid registration key")


def _memberships(agent_id: str) -> list[str] | None:
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


AgentDep = Depends(require_agent)
