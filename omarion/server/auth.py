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


AgentDep = Depends(require_agent)
