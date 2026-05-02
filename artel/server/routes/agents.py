import secrets

from fastapi import APIRouter, Depends, HTTPException

from ...store.db import get_db
from ..auth import require_registration_key
from ..config import settings
from ..models import AgentCreated, AgentRegister

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("/register", response_model=AgentCreated, status_code=201,
             dependencies=[Depends(require_registration_key)])
async def register_agent(body: AgentRegister):
    if not body.agent_id or not body.agent_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=422, detail="agent_id must be alphanumeric with - or _")
    db = get_db()
    if db.execute("SELECT id FROM agents WHERE id=?", (body.agent_id,)).fetchone():
        raise HTTPException(status_code=409, detail="agent_id already registered")
    if body.agent_id in settings.api_keys().values():
        raise HTTPException(status_code=409, detail="agent_id already registered")
    api_key = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO agents (id, api_key) VALUES (?, ?)",
        (body.agent_id, api_key),
    )
    db.commit()
    row = db.execute("SELECT * FROM agents WHERE id=?", (body.agent_id,)).fetchone()
    return AgentCreated(agent_id=row["id"], api_key=api_key, created_at=row["created_at"])


@router.get("", response_model=list[AgentCreated],
            dependencies=[Depends(require_registration_key)])
async def list_agents():
    db = get_db()
    rows = db.execute("SELECT * FROM agents ORDER BY created_at").fetchall()
    dynamic = [AgentCreated(agent_id=r["id"], api_key=r["api_key"], created_at=r["created_at"]) for r in rows]
    static = [
        AgentCreated(agent_id=aid, api_key=key, created_at="static")
        for key, aid in settings.api_keys().items()
    ]
    return static + dynamic
