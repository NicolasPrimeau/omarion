import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from ...store.db import get_db
from ...store.embeddings import embed
from ..auth import require_agent
from ..models import MemoryEntry, MemoryPatch, MemoryWrite, new_id

router = APIRouter(prefix="/memory", tags=["memory"])


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


@router.post("", response_model=MemoryEntry, status_code=201)
async def write_memory(
    body: MemoryWrite,
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    entry_id = new_id()
    vec = embed(body.content)

    db.execute(
        """INSERT INTO memory (id, type, agent_id, project, scope, content,
           confidence, parents, tags) VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            entry_id, body.type, agent_id, body.project, body.scope,
            body.content, body.confidence,
            json.dumps(body.parents), json.dumps(body.tags),
        ),
    )
    db.execute(
        "INSERT INTO memory_vec (id, embedding) VALUES (?, ?)",
        (entry_id, json.dumps(vec)),
    )
    db.execute(
        "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
        (new_id(), "memory.written", agent_id, json.dumps({"memory_id": entry_id})),
    )
    db.commit()

    row = db.execute("SELECT * FROM memory WHERE id=?", (entry_id,)).fetchone()
    return _row_to_entry(row)


@router.get("/search", response_model=list[MemoryEntry])
async def search_memory(
    q: str = Query(...),
    limit: int = Query(default=10, le=50),
    project: str | None = Query(default=None),
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    vec = embed(q)

    results = db.execute(
        """SELECT m.*, mv.distance
           FROM memory_vec mv
           JOIN memory m ON m.id = mv.id
           WHERE mv.embedding MATCH ? AND k=?
             AND m.deleted_at IS NULL
             AND (m.scope != 'private' OR m.agent_id = ?)
           ORDER BY mv.distance""",
        (json.dumps(vec), limit, agent_id),
    ).fetchall()

    if project:
        results = [r for r in results if r["project"] == project]

    return [_row_to_entry(r) for r in results]


@router.get("/delta", response_model=list[MemoryEntry])
async def memory_delta(
    since: str = Query(...),
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    rows = db.execute(
        """SELECT * FROM memory
           WHERE updated_at > ? AND deleted_at IS NULL
             AND (scope != 'private' OR agent_id = ?)
           ORDER BY updated_at""",
        (since, agent_id),
    ).fetchall()
    return [_row_to_entry(r) for r in rows]


@router.get("/{entry_id}", response_model=MemoryEntry)
async def get_memory(
    entry_id: str,
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    row = db.execute(
        "SELECT * FROM memory WHERE id=? AND deleted_at IS NULL", (entry_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["scope"] == "private" and row["agent_id"] != agent_id:
        raise HTTPException(status_code=403, detail="forbidden")
    return _row_to_entry(row)


@router.patch("/{entry_id}", response_model=MemoryEntry)
async def patch_memory(
    entry_id: str,
    body: MemoryPatch,
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    row = db.execute(
        "SELECT * FROM memory WHERE id=? AND deleted_at IS NULL", (entry_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["agent_id"] != agent_id:
        raise HTTPException(status_code=403, detail="forbidden")

    updates: dict = {}
    if body.content is not None:
        updates["content"] = body.content
        vec = embed(body.content)
        db.execute("DELETE FROM memory_vec WHERE id=?", (entry_id,))
        db.execute(
            "INSERT INTO memory_vec (id, embedding) VALUES (?,?)",
            (entry_id, json.dumps(vec)),
        )
    if body.confidence is not None:
        updates["confidence"] = body.confidence
    if body.tags is not None:
        updates["tags"] = json.dumps(body.tags)
    if body.scope is not None:
        updates["scope"] = body.scope

    if updates:
        set_parts = [f"{k}=?" for k in updates]
        set_parts += [
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
            f"version={row['version'] + 1}",
        ]
        db.execute(
            f"UPDATE memory SET {', '.join(set_parts)} WHERE id=?",
            [*updates.values(), entry_id],
        )
        db.commit()

    row = db.execute("SELECT * FROM memory WHERE id=?", (entry_id,)).fetchone()
    return _row_to_entry(row)


@router.delete("/{entry_id}", status_code=204)
async def delete_memory(
    entry_id: str,
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    row = db.execute(
        "SELECT * FROM memory WHERE id=? AND deleted_at IS NULL", (entry_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["agent_id"] != agent_id:
        raise HTTPException(status_code=403, detail="forbidden")
    db.execute(
        "UPDATE memory SET deleted_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
        (entry_id,),
    )
    db.commit()
