import secrets
import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ...store.db import get_db
from ..auth import AgentDep, require_registration_key
from ..config import settings
from ..models import AgentCreated, AgentRegister, AgentRename, AgentSelfRegister
from ..presence import update_seen

router = APIRouter(prefix="/agents", tags=["agents"])


def _valid_agent_id(value: str) -> bool:
    return bool(value) and value.replace("-", "").replace("_", "").isalnum()


def _id_taken(db: sqlite3.Connection, agent_id: str) -> bool:
    return bool(
        db.execute("SELECT id FROM agents WHERE id=?", (agent_id,)).fetchone()
        or agent_id in settings.api_keys().values()
    )


def _cascade_rename(db: sqlite3.Connection, old_id: str, new_id: str) -> None:
    db.execute("UPDATE agents SET id=? WHERE id=?", (new_id, old_id))
    db.execute("UPDATE memory SET agent_id=? WHERE agent_id=?", (new_id, old_id))
    db.execute("UPDATE tasks SET created_by=? WHERE created_by=?", (new_id, old_id))
    db.execute("UPDATE tasks SET assigned_to=? WHERE assigned_to=?", (new_id, old_id))
    db.execute("UPDATE messages SET from_agent=? WHERE from_agent=?", (new_id, old_id))
    db.execute("UPDATE messages SET to_agent=? WHERE to_agent=?", (new_id, old_id))
    db.execute("UPDATE events SET agent_id=? WHERE agent_id=?", (new_id, old_id))
    db.execute("UPDATE session_handoffs SET agent_id=? WHERE agent_id=?", (new_id, old_id))
    db.execute("UPDATE project_members SET agent_id=? WHERE agent_id=?", (new_id, old_id))
    db.execute("UPDATE message_reads SET agent_id=? WHERE agent_id=?", (new_id, old_id))


def _mcp_config(mcp_url: str, agent_id: str, api_key: str, project: str | None = None) -> dict:
    return {
        "mcpServers": {
            "artel": {
                "type": "http",
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
        role=row["role"] if "role" in row.keys() else "agent",
    )


@router.post(
    "/register",
    response_model=AgentCreated,
    status_code=201,
    summary="Register a new agent (registration key required)",
    dependencies=[Depends(require_registration_key)],
)
async def register_agent(body: AgentRegister, request: Request):
    if not _valid_agent_id(body.agent_id):
        raise HTTPException(status_code=422, detail="agent_id must be alphanumeric with - or _")
    db = get_db()
    if _id_taken(db, body.agent_id):
        raise HTTPException(status_code=409, detail="agent_id already registered")
    api_key = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO agents (id, api_key, project) VALUES (?, ?, ?)",
        (body.agent_id, api_key, body.project),
    )
    db.commit()
    row = db.execute("SELECT * FROM agents WHERE id=?", (body.agent_id,)).fetchone()
    update_seen(body.agent_id, row["created_at"])
    base_url = settings.public_url or str(request.base_url).rstrip("/")
    mcp_url = (settings.mcp_url or base_url).rstrip("/") + "/mcp"
    return AgentCreated(
        agent_id=row["id"],
        api_key=api_key,
        project=row["project"],
        created_at=row["created_at"],
        mcp_config=_mcp_config(mcp_url, row["id"], api_key, row["project"]),
    )


@router.post(
    "/self-register",
    response_model=AgentCreated,
    status_code=201,
    summary="Register a new agent; requires registration key when one is configured",
)
async def self_register(body: AgentSelfRegister, x_registration_key: str = Header(default="")):
    if settings.registration_key and x_registration_key != settings.registration_key:
        raise HTTPException(status_code=401, detail="invalid registration key")
    base = (body.agent_id or "agent").strip()
    if not _valid_agent_id(base):
        raise HTTPException(status_code=422, detail="agent_id must be alphanumeric with - or _")
    db = get_db()
    existing = db.execute("SELECT * FROM agents WHERE id=?", (base,)).fetchone()
    if existing:
        return _row_to_agent(existing)
    candidate, i = base, 1
    while _id_taken(db, candidate):
        candidate = f"{base}-{i}"
        i += 1
    api_key = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO agents (id, api_key, project) VALUES (?, ?, ?)",
        (candidate, api_key, body.project),
    )
    db.commit()
    row = db.execute("SELECT * FROM agents WHERE id=?", (candidate,)).fetchone()
    update_seen(candidate, row["created_at"])
    return AgentCreated(
        agent_id=row["id"], api_key=api_key, project=row["project"], created_at=row["created_at"]
    )


@router.get("/me", response_model=AgentCreated, summary="Get your own agent record")
async def get_self(agent_id: str = AgentDep):
    db = get_db()
    row = db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    if row:
        return _row_to_agent(row)
    key = next((k for k, v in settings.api_keys().items() if v == agent_id), None)
    return AgentCreated(agent_id=agent_id, api_key=key or "", created_at="static")


@router.patch(
    "/me", response_model=AgentCreated, summary="Rename yourself; cascades across all records"
)
async def rename_self(body: AgentRename, agent_id: str = AgentDep):
    new_id = body.new_id.strip()
    if not _valid_agent_id(new_id):
        raise HTTPException(status_code=422, detail="new_id must be alphanumeric with - or _")
    if new_id == agent_id:
        raise HTTPException(status_code=422, detail="new_id is same as current id")
    db = get_db()
    if _id_taken(db, new_id):
        raise HTTPException(status_code=409, detail="agent_id already taken")
    if not db.execute("SELECT id FROM agents WHERE id=?", (agent_id,)).fetchone():
        raise HTTPException(
            status_code=422,
            detail="static agents cannot be renamed via API — update AGENT_KEYS in .env",
        )
    with db:
        _cascade_rename(db, agent_id, new_id)
    return _row_to_agent(db.execute("SELECT * FROM agents WHERE id=?", (new_id,)).fetchone())


@router.delete("/me", status_code=204, summary="Delete your own agent record")
async def delete_self(agent_id: str = AgentDep):
    if agent_id in settings.api_keys().values():
        raise HTTPException(
            status_code=422,
            detail="static agents cannot be deleted via API — remove from AGENT_KEYS in .env",
        )
    db = get_db()
    if not db.execute("SELECT id FROM agents WHERE id=?", (agent_id,)).fetchone():
        raise HTTPException(status_code=404, detail="agent not found")
    with db:
        db.execute("DELETE FROM agents WHERE id=?", (agent_id,))
        db.execute("DELETE FROM project_members WHERE agent_id=?", (agent_id,))
        db.execute("DELETE FROM message_reads WHERE agent_id=?", (agent_id,))


@router.patch(
    "/{agent_id}",
    response_model=AgentCreated,
    summary="Rename any agent (registration key required)",
    dependencies=[Depends(require_registration_key)],
)
async def rename_agent(agent_id: str, body: AgentRename):
    new_id = body.new_id.strip()
    if not _valid_agent_id(new_id):
        raise HTTPException(status_code=422, detail="new_id must be alphanumeric with - or _")
    if new_id == agent_id:
        raise HTTPException(status_code=422, detail="new_id is same as current id")
    db = get_db()
    if _id_taken(db, new_id):
        raise HTTPException(status_code=409, detail="agent_id already taken")
    if not db.execute("SELECT id FROM agents WHERE id=?", (agent_id,)).fetchone():
        raise HTTPException(status_code=404, detail="agent not found or is a static agent")
    with db:
        _cascade_rename(db, agent_id, new_id)
    return _row_to_agent(db.execute("SELECT * FROM agents WHERE id=?", (new_id,)).fetchone())


@router.delete(
    "/{agent_id}",
    status_code=204,
    summary="Delete any agent (registration key required)",
    dependencies=[Depends(require_registration_key)],
)
async def delete_agent(agent_id: str):
    if agent_id in settings.api_keys().values():
        raise HTTPException(
            status_code=422,
            detail="static agents cannot be deleted via API — remove from AGENT_KEYS in .env",
        )
    db = get_db()
    if not db.execute("SELECT id FROM agents WHERE id=?", (agent_id,)).fetchone():
        raise HTTPException(status_code=404, detail="agent not found")
    with db:
        db.execute("DELETE FROM agents WHERE id=?", (agent_id,))
        db.execute("DELETE FROM project_members WHERE agent_id=?", (agent_id,))
        db.execute("DELETE FROM message_reads WHERE agent_id=?", (agent_id,))


@router.get(
    "",
    response_model=list[AgentCreated],
    summary="List all agents (registration key required)",
    dependencies=[Depends(require_registration_key)],
)
async def list_agents():
    db = get_db()
    rows = db.execute("SELECT * FROM agents ORDER BY created_at").fetchall()
    dynamic = [_row_to_agent(r) for r in rows]
    static = [
        AgentCreated(agent_id=aid, api_key=key, created_at="static")
        for key, aid in settings.api_keys().items()
    ]
    return static + dynamic
