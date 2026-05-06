import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ...store.db import get_db
from ..auth import project_filter, require_agent
from ..models import HandoffPost, HandoffResponse, MemoryEntry, new_id

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
    return MemoryEntry(
        id=row["id"],
        type=row["type"],
        agent_id=row["agent_id"],
        project=row["project"],
        scope=row["scope"],
        content=row["content"],
        confidence=row["confidence"],
        parents=json.loads(row["parents"]),
        tags=json.loads(row["tags"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


@router.post("/handoff", status_code=201, summary="Save session end state")
async def post_handoff(body: HandoffPost, agent_id: str = Depends(require_agent)):
    db = get_db()
    handoff_id = new_id()
    db.execute(
        """INSERT INTO session_handoffs
           (id, agent_id, host, summary, in_progress, next_steps, memory_refs)
           VALUES (?,?,?,?,?,?,?)""",
        (
            handoff_id,
            agent_id,
            body.host,
            body.summary,
            json.dumps(body.in_progress),
            json.dumps(body.next_steps),
            json.dumps(body.memory_refs),
        ),
    )
    db.commit()
    return {"id": handoff_id}


@router.get(
    "/handoff/{target_agent_id}",
    response_model=HandoffResponse,
    summary="Load last handoff and memory delta since then",
)
async def get_handoff(
    target_agent_id: str,
    agent_id: str = Depends(require_agent),
):
    if target_agent_id != agent_id:
        raise HTTPException(status_code=403, detail="forbidden")
    db = get_db()
    row = db.execute(
        """SELECT * FROM session_handoffs WHERE agent_id=?
           ORDER BY created_at DESC LIMIT 1""",
        (target_agent_id,),
    ).fetchone()

    last_handoff = None
    since = "1970-01-01T00:00:00.000Z"
    if row:
        last_handoff = {
            "id": row["id"],
            "agent_id": row["agent_id"],
            "host": row["host"],
            "summary": row["summary"],
            "in_progress": json.loads(row["in_progress"]),
            "next_steps": json.loads(row["next_steps"]),
            "memory_refs": json.loads(row["memory_refs"]),
            "created_at": row["created_at"],
        }
        since = row["created_at"]

    pf_clause, pf_params = project_filter(target_agent_id)
    delta_sql = """SELECT * FROM memory
                   WHERE updated_at > ? AND deleted_at IS NULL
                     AND (scope != 'private' OR agent_id = ?)"""
    delta_params: list = [since, target_agent_id]
    if pf_clause:
        delta_sql += f" AND {pf_clause}"
        delta_params.extend(pf_params)
    delta_sql += " ORDER BY updated_at"
    delta_rows = db.execute(delta_sql, delta_params).fetchall()

    return HandoffResponse(
        last_handoff=last_handoff,
        memory_delta=[_row_to_entry(r) for r in delta_rows],
    )
