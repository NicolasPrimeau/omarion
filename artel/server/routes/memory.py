import json
import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from ...store.db import get_db
from ...store.embeddings import embed
from ..auth import _memberships, is_owner, project_filter, require_agent
from ..broadcast import broadcast
from ..models import EventEntry, MemoryEntry, MemoryPatch, MemoryWrite, new_id

router = APIRouter(prefix="/memory", tags=["memory"])


def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
    keys = row.keys()
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
        expires_at=row["expires_at"] if "expires_at" in keys else None,
    )


@router.post("", response_model=MemoryEntry, status_code=201, summary="Write a memory entry")
async def write_memory(
    body: MemoryWrite,
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    if body.type == "directive" and not is_owner(agent_id):
        raise HTTPException(status_code=403, detail="directive writes require elevated permission")
    if body.project:
        allowed = _memberships(agent_id)
        if allowed is not None and body.project not in allowed:
            raise HTTPException(status_code=403, detail="not a member of this project")
    confidence = 1.0 if body.type == "directive" else body.confidence
    entry_id = new_id()
    vec = embed(body.content)
    now = datetime.now(UTC).isoformat()

    event_id = new_id()
    with db:
        db.execute(
            """INSERT INTO memory (id, type, agent_id, project, scope, content,
               confidence, parents, tags, expires_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                entry_id,
                body.type,
                agent_id,
                body.project,
                body.scope,
                body.content,
                confidence,
                json.dumps(body.parents),
                json.dumps(body.tags),
                body.expires_at,
                now,
                now,
            ),
        )
        db.execute(
            "INSERT INTO memory_vec (id, embedding) VALUES (?, ?)",
            (entry_id, json.dumps(vec)),
        )
        db.execute(
            "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
            (event_id, "memory.written", agent_id, json.dumps({"memory_id": entry_id})),
        )

    broadcast(
        EventEntry(
            id=event_id,
            type="memory.written",
            agent_id=agent_id,
            payload={"memory_id": entry_id},
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        )
    )

    row = db.execute("SELECT * FROM memory WHERE id=?", (entry_id,)).fetchone()
    return _row_to_entry(row)


@router.get("/search", response_model=list[MemoryEntry], summary="Semantic search over memory")
async def search_memory(
    q: str = Query(...),
    limit: int = Query(default=10, le=50),
    project: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    type: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    max_distance: float | None = Query(default=None),
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    vec = embed(q)
    allowed = _memberships(agent_id)

    # Over-fetch when project/membership filtering will reduce results
    has_filter = bool(project) or (allowed is not None)
    fetch_k = limit * 5 if has_filter else limit

    rows = db.execute(
        """SELECT m.*, mv.distance
           FROM memory_vec mv
           JOIN memory m ON m.id = mv.id
           WHERE mv.embedding MATCH ? AND k=?
             AND m.deleted_at IS NULL
             AND (m.scope != 'agent' OR m.agent_id = ?)
           ORDER BY mv.distance""",
        (json.dumps(vec), fetch_k, agent_id),
    ).fetchall()

    if project:
        if allowed is not None and project not in allowed:
            return []
        rows = [r for r in rows if r["project"] == project]
    elif allowed is not None:
        rows = [r for r in rows if r["project"] is None or r["project"] in allowed]
    if max_distance is not None:
        rows = [r for r in rows if r["distance"] <= max_distance]
    if tag:
        rows = [r for r in rows if tag in json.loads(r["tags"])]
    if type:
        rows = [r for r in rows if r["type"] == type]
    if agent:
        rows = [r for r in rows if r["agent_id"] == agent]

    return [_row_to_entry(r) for r in rows[:limit]]


@router.get("", response_model=list[MemoryEntry], summary="List memory with optional filters")
async def list_memory(
    type: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    project: str | None = Query(default=None),
    confidence_min: float | None = Query(default=None, ge=0.0, le=1.0),
    updated_before: str | None = Query(default=None),
    created_before: str | None = Query(default=None),
    min_version: int | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    clauses = ["deleted_at IS NULL", "(scope != 'agent' OR agent_id = ?)"]
    params: list = [agent_id]
    if project:
        allowed = _memberships(agent_id)
        if allowed is not None and project not in allowed:
            return []
        clauses.append("project = ?")
        params.append(project)
    else:
        pf_clause, pf_params = project_filter(agent_id)
        if pf_clause:
            clauses.append(pf_clause)
            params.extend(pf_params)
    if type:
        clauses.append("type = ?")
        params.append(type)
    if tag:
        clauses.append("EXISTS (SELECT 1 FROM json_each(tags) WHERE value = ?)")
        params.append(tag)
    if agent:
        clauses.append("agent_id = ?")
        params.append(agent)
    if confidence_min is not None:
        clauses.append("confidence >= ?")
        params.append(confidence_min)
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
        f"SELECT * FROM memory WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [_row_to_entry(r) for r in rows]


@router.get(
    "/delta", response_model=list[MemoryEntry], summary="Memory entries updated since a timestamp"
)
async def memory_delta(
    since: str = Query(...),
    agent: str | None = Query(default=None),
    project: str | None = Query(default=None),
    type: str | None = Query(default=None),
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    pf_clause, pf_params = project_filter(agent_id)
    sql = """SELECT * FROM memory
             WHERE updated_at > ? AND deleted_at IS NULL
               AND (scope != 'agent' OR agent_id = ?)"""
    params: list = [since, agent_id]
    if agent:
        sql += " AND agent_id = ?"
        params.append(agent)
    if project:
        allowed = _memberships(agent_id)
        if allowed is not None and project not in allowed:
            return []
        sql += " AND project = ?"
        params.append(project)
    elif pf_clause:
        sql += f" AND {pf_clause}"
        params.extend(pf_params)
    if type:
        sql += " AND type = ?"
        params.append(type)
    sql += " ORDER BY updated_at"
    rows = db.execute(sql, params).fetchall()
    return [_row_to_entry(r) for r in rows]


@router.get("/{entry_id}", response_model=MemoryEntry, summary="Get a memory entry by ID")
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
    if row["scope"] == "agent" and row["agent_id"] != agent_id:
        raise HTTPException(status_code=403, detail="forbidden")
    if row["project"]:
        allowed = _memberships(agent_id)
        if allowed is not None and row["project"] not in allowed:
            raise HTTPException(status_code=403, detail="not a member of this project")
    return _row_to_entry(row)


@router.patch(
    "/{entry_id}", response_model=MemoryEntry, summary="Update a memory entry (owner only)"
)
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

    is_entry_owner = row["agent_id"] == agent_id or is_owner(agent_id)
    wants_owner_fields = any(
        f is not None for f in (body.content, body.tags, body.scope, body.project)
    )
    if not is_entry_owner and wants_owner_fields:
        raise HTTPException(status_code=403, detail="forbidden")

    updates: dict = {}
    vec = None
    if body.content is not None:
        updates["content"] = body.content
        vec = embed(body.content)
    if body.tags is not None:
        updates["tags"] = json.dumps(body.tags)
    if body.scope is not None:
        updates["scope"] = body.scope
    if body.confidence is not None:
        updates["confidence"] = body.confidence
    if body.type is not None:
        updates["type"] = body.type
    if body.project is not None:
        allowed = _memberships(agent_id)
        if allowed is not None and body.project not in allowed:
            raise HTTPException(status_code=403, detail="not a member of this project")
        updates["project"] = body.project
    if body.scope == "project":
        effective_project = body.project if body.project is not None else row["project"]
        if not effective_project:
            raise HTTPException(status_code=422, detail="scope='project' requires a project field")

    if updates:
        set_parts = [f"{k}=?" for k in updates]
        set_parts += [
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
            f"version={row['version'] + 1}",
        ]
        with db:
            if body.content is not None:
                db.execute("DELETE FROM memory_vec WHERE id=?", (entry_id,))
                db.execute(
                    "INSERT INTO memory_vec (id, embedding) VALUES (?,?)",
                    (entry_id, json.dumps(vec)),
                )
            db.execute(
                f"UPDATE memory SET {', '.join(set_parts)} WHERE id=?",
                [*updates.values(), entry_id],
            )

    row = db.execute("SELECT * FROM memory WHERE id=?", (entry_id,)).fetchone()
    return _row_to_entry(row)


@router.delete("/{entry_id}", status_code=204, summary="Soft-delete a memory entry (owner only)")
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
    if row["agent_id"] != agent_id and not is_owner(agent_id):
        raise HTTPException(status_code=403, detail="forbidden")
    with db:
        db.execute(
            "UPDATE memory SET deleted_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
            (entry_id,),
        )
