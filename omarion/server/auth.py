from fastapi import Depends, Header, HTTPException

from .config import settings


async def require_agent(
    x_agent_id: str = Header(...),
    x_api_key: str = Header(...),
) -> str:
    keys = settings.api_keys()
    if x_api_key not in keys or keys[x_api_key] != x_agent_id:
        raise HTTPException(status_code=401, detail="invalid credentials")
    return x_agent_id


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
