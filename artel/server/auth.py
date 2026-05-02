from fastapi import Depends, Header, HTTPException

from ..store.db import get_db
from .config import settings


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
    return x_agent_id


async def require_registration_key(
    x_registration_key: str = Header(...),
) -> None:
    if not settings.registration_key or x_registration_key != settings.registration_key:
        raise HTTPException(status_code=401, detail="invalid registration key")


def check_project(agent_id: str, project: str | None) -> None:
    allowed = settings.agent_projects().get(agent_id)
    if allowed is None:
        return
    if project is None:
        return
    if project not in allowed:
        raise HTTPException(status_code=403, detail="project access denied")


def project_filter(agent_id: str) -> tuple[str, list]:
    allowed = settings.agent_projects().get(agent_id)
    if allowed is None:
        return "", []
    placeholders = ",".join("?" * len(allowed))
    return f"(project IS NULL OR project IN ({placeholders}))", list(allowed)


AgentDep = Depends(require_agent)
