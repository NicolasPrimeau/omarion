import json
import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from ...store.db import get_db
from ...store.embeddings import embed
from ..auth import check_project, project_filter, require_agent
from ..broadcast import broadcast
from ..config import settings
from ..models import EventEntry, MemoryEntry, MemoryPatch, MemoryWrite, new_id

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
    check_project(agent_id, body.project)
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
    event_id = new_id()
    db.execute(
        "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
        (event_id, "memory.written", agent_id, json.dumps({"memory_id": entry_id})),
    )
    db.commit()

    broadcast(EventEntry(
        id=event_id,
        type="memory.written",
        agent_id=agent_id,
        payload={"memory_id": entry_id},
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    ))

    row = db.execute("SELECT * FROM memory WHERE id=?", (entry_id,)).fetchone()
    return _row_to_entry(row)


@router.get("/search", response_model=list[MemoryEntry])
async def search_memory(
    q: str = Query(...),
    limit: int = Query(default=10, le=50),
    project: str | None = Query(default=None),
    max_distance: float | None = Query(default=None),
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

    allowed = settings.agent_projects().get(agent_id)
    if project:
        results = [r for r in results if r["project"] == project]
    elif allowed is not None:
        results = [r for r in results if r["project"] is None or r["project"] in allowed]
    if max_distance is not None:
        results = [r for r in results if r["distance"] <= max_distance]

    return [_row_to_entry(r) for r in results]


@router.get("", response_model=list[MemoryEntry])
async def list_memory(
    type: str | None = Query(default=None),
    updated_before: str | None = Query(default=None),
    created_before: str | None = Query(default=None),
    min_version: int | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    clauses = ["deleted_at IS NULL", "(scope != 'private' OR agent_id = ?)"]
    params: list = [agent_id]
    pf_clause, pf_params = project_filter(agent_id)
    if pf_clause:
        clauses.append(pf_clause)
        params.extend(pf_params)
    if type:
        clauses.append("type = ?")
        params.append(type)
    if updated_before:
        clauses.append("updated_at < ?")
        params.append(updated_before)
    if created_before:
        clauses.append("created_at < ?")
        params.append(created_before)
    if min_version is not None:
        clauses.append("version >= ?")
        params.append(min_version)
    params.append(limit)
    rows = db.execute(
        f"SELECT * FROM memory WHERE {' AND '.join(clauses)} ORDER BY updated_at LIMIT ?",
        params,
    ).fetchall()
    return [_row_to_entry(r) for r in rows]


@router.get("/delta", response_model=list[MemoryEntry])
async def memory_delta(
    since: str = Query(...),
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    pf_clause, pf_params = project_filter(agent_id)
    sql = """SELECT * FROM memory
             WHERE updated_at > ? AND deleted_at IS NULL
               AND (scope != 'private' OR agent_id = ?)"""
    params: list = [since, agent_id]
    if pf_clause:
        sql += f" AND {pf_clause}"
        params.extend(pf_params)
    sql += " ORDER BY updated_at"
    rows = db.execute(sql, params).fetchall()
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
    check_project(agent_id, row["project"])
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
    check_project(agent_id, row["project"])
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
    if body.tags is not None:
        updates["tags"] = json.dumps(body.tags)
    if body.scope is not None:
        updates["scope"] = body.scope
    if body.confidence is not None:
        updates["confidence"] = body.confidence
    if body.type is not None:
        updates["type"] = body.type

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
    check_project(agent_id, row["project"])
    db.execute(
        "UPDATE memory SET deleted_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
        (entry_id,),
    )
    db.commit()
