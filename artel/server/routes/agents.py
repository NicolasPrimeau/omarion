import secrets

from fastapi import APIRouter, Depends, HTTPException, Request

from ...store.db import get_db
from ..auth import AgentDep, _last_seen, require_registration_key
from ..config import settings
from ..models import AgentCreated, AgentRegister, AgentRename

router = APIRouter(prefix="/agents", tags=["agents"])


def _mcp_config(mcp_url: str, agent_id: str, api_key: str, project: str | None = None) -> dict:
    env = {"ARTEL_URL": mcp_url.replace("/sse", "").replace(":8001", ":8000")}
    if project:
        env["MCP_PROJECT"] = project
    return {
        "mcpServers": {
            "artel": {
                "type": "sse",
                "url": mcp_url,
                "headers": {
                    "x-agent-id": agent_id,
                    "x-api-key": api_key,
                },
            }
        }
    }


def _row_to_agent(row) -> AgentCreated:
    return AgentCreated(
        agent_id=row["id"],
        api_key=row["api_key"],
        project=row["project"],
        created_at=row["created_at"],
    )


@router.post("/register", response_model=AgentCreated, status_code=201,
             dependencies=[Depends(require_registration_key)])
async def register_agent(body: AgentRegister, request: Request):
    if not body.agent_id or not body.agent_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=422, detail="agent_id must be alphanumeric with - or _")
    db = get_db()
    if db.execute("SELECT id FROM agents WHERE id=?", (body.agent_id,)).fetchone():
        raise HTTPException(status_code=409, detail="agent_id already registered")
    if body.agent_id in settings.api_keys().values():
        raise HTTPException(status_code=409, detail="agent_id already registered")
    api_key = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO agents (id, api_key, project) VALUES (?, ?, ?)",
        (body.agent_id, api_key, body.project),
    )
    db.commit()
    row = db.execute("SELECT * FROM agents WHERE id=?", (body.agent_id,)).fetchone()
    _last_seen[body.agent_id] = row["created_at"]
    base_url = settings.public_url or str(request.base_url).rstrip("/")
    mcp_url = (settings.mcp_url or base_url.replace(":8000", ":8001")).rstrip("/") + "/sse"
    return AgentCreated(
        agent_id=row["id"],
        api_key=api_key,
        project=row["project"],
        created_at=row["created_at"],
        mcp_config=_mcp_config(mcp_url, row["id"], api_key, row["project"]),
    )


@router.patch("/me", response_model=AgentCreated)
async def rename_self(body: AgentRename, agent_id: str = AgentDep):
    new_id = body.new_id.strip()
    if not new_id or not new_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=422, detail="new_id must be alphanumeric with - or _")
    if new_id == agent_id:
        raise HTTPException(status_code=422, detail="new_id is same as current id")
    db = get_db()
    if db.execute("SELECT id FROM agents WHERE id=?", (new_id,)).fetchone():
        raise HTTPException(status_code=409, detail="agent_id already taken")
    if new_id in settings.api_keys().values():
        raise HTTPException(status_code=409, detail="agent_id already taken")
    row = db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=422, detail="static agents cannot be renamed via API — update AGENT_KEYS in .env")
    db.execute("UPDATE agents SET id=? WHERE id=?", (new_id, agent_id))
    db.execute("UPDATE memory SET agent_id=? WHERE agent_id=?", (new_id, agent_id))
    db.execute("UPDATE tasks SET created_by=? WHERE created_by=?", (new_id, agent_id))
    db.execute("UPDATE tasks SET assigned_to=? WHERE assigned_to=?", (new_id, agent_id))
    db.execute("UPDATE messages SET from_agent=? WHERE from_agent=?", (new_id, agent_id))
    db.execute("UPDATE messages SET to_agent=? WHERE to_agent=?", (new_id, agent_id))
    db.execute("UPDATE events SET agent_id=? WHERE agent_id=?", (new_id, agent_id))
    db.execute("UPDATE session_handoffs SET agent_id=? WHERE agent_id=?", (new_id, agent_id))
    db.commit()
    if agent_id in _last_seen:
        _last_seen[new_id] = _last_seen.pop(agent_id)
    updated = db.execute("SELECT * FROM agents WHERE id=?", (new_id,)).fetchone()
    return _row_to_agent(updated)


@router.delete("/{agent_id}", status_code=204,
               dependencies=[Depends(require_registration_key)])
async def delete_agent(agent_id: str):
    if agent_id in settings.api_keys().values():
        raise HTTPException(status_code=422, detail="static agents cannot be deleted via API — remove from AGENT_KEYS in .env")
    db = get_db()
    row = db.execute("SELECT id FROM agents WHERE id=?", (agent_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="agent not found")
    db.execute("DELETE FROM agents WHERE id=?", (agent_id,))
    db.commit()
    _last_seen.pop(agent_id, None)


@router.get("", response_model=list[AgentCreated],
            dependencies=[Depends(require_registration_key)])
async def list_agents():
    db = get_db()
    rows = db.execute("SELECT * FROM agents ORDER BY created_at").fetchall()
    dynamic = [_row_to_agent(r) for r in rows]
    static = [
        AgentCreated(agent_id=aid, api_key=key, created_at="static")
        for key, aid in settings.api_keys().items()
    ]
    return static + dynamic
