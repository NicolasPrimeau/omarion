import json
import sqlite3
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from ...store.db import AmbiguousId, get_db, instance_id, resolve_id
from ...store.embeddings import embed
from ..auth import (
    ActorDep,
    FeedAuth,
    ReaderDep,
    _memberships,
    can_curate_memory,
    feed_auth_dep,
    project_filter,
)
from ..broadcast import broadcast
from ..config import settings
from ..models import EventEntry, MemoryEntry, MemoryPatch, MemoryWrite, new_id

_ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("", _ATOM_NS)


def _a(tag: str) -> str:
    return f"{{{_ATOM_NS}}}{tag}"


def _fetch_feed_rows(
    db,
    agent_id: str,
    project: str | None,
    tag: str | None,
    type_: str | None,
    limit: int,
    include_deleted: bool = False,
    mesh_project: str | None = None,
):
    clauses = ["(scope != 'agent' OR agent_id = ?)"]
    if not include_deleted:
        clauses.append("deleted_at IS NULL")
    params: list = [agent_id]

    if mesh_project is not None:
        effective_project = mesh_project if mesh_project else project
        if effective_project:
            if mesh_project and project and project != mesh_project:
                return []
            clauses.append("project = ?")
            params.append(effective_project)
    elif project:
        from ..auth import _memberships as _m

        allowed = _m(agent_id)
        if allowed is not None and project not in allowed:
            return []
        clauses.append("project = ?")
        params.append(project)
    else:
        pf_clause, pf_params = project_filter(agent_id)
        if pf_clause:
            clauses.append(pf_clause)
            params.extend(pf_params)

    if tag:
        clauses.append("EXISTS (SELECT 1 FROM json_each(tags) WHERE value = ?)")
        params.append(tag)
    if type_:
        clauses.append("type = ?")
        params.append(type_)
    params.append(limit)
    return db.execute(
        f"SELECT * FROM memory WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?",
        params,
    ).fetchall()


router = APIRouter(prefix="/memory", tags=["memory"])


def _resolve_entry(entry_id: str) -> str:
    try:
        resolved = resolve_id("memory", entry_id)
    except AmbiguousId:
        raise HTTPException(status_code=400, detail="ambiguous memory id prefix")
    if resolved is None:
        raise HTTPException(status_code=404, detail="not found")
    return resolved


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
        origin=row["origin"] if "origin" in keys else None,
    )


@router.post("", response_model=MemoryEntry, status_code=201, summary="Write a memory entry")
async def write_memory(
    body: MemoryWrite,
    agent_id: str = ActorDep,
):
    db = get_db()
    if body.type == "directive" and not can_curate_memory(agent_id):
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
        if vec is not None:
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
    agent_id: str = ReaderDep,
):
    db = get_db()
    vec = embed(q)
    allowed = _memberships(agent_id)

    if vec is None:
        return []

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
    agent_id: str = ReaderDep,
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
    agent_id: str = ReaderDep,
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


@router.get("/feed.atom", summary="Atom feed of memory entries")
async def memory_feed_atom(
    project: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    type: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    auth: FeedAuth = Depends(feed_auth_dep),
):
    db = get_db()
    rows = _fetch_feed_rows(
        db, auth.agent_id, project, tag, type, limit, mesh_project=auth.mesh_project
    )
    base = settings.public_url or f"http://localhost:{settings.port}"
    feed_url = f"{base}/memory/feed.atom"
    if project:
        feed_url += f"?project={project}"

    feed = ET.Element(_a("feed"))
    title = ET.SubElement(feed, _a("title"))
    title.text = f"Artel Memory{f' / {project}' if project else ''}"
    ET.SubElement(feed, _a("link"), rel="self", href=feed_url)
    feed_id = ET.SubElement(feed, _a("id"))
    feed_id.text = feed_url
    updated = ET.SubElement(feed, _a("updated"))
    updated.text = rows[0]["updated_at"] if rows else datetime.now(UTC).isoformat()

    for row in rows:
        entry = ET.SubElement(feed, _a("entry"))
        eid = ET.SubElement(entry, _a("id"))
        eid.text = f"{base}/memory/{row['id']}"
        etitle = ET.SubElement(entry, _a("title"))
        etitle.text = row["content"].split("\n")[0][:80].strip() or row["id"]
        eupdated = ET.SubElement(entry, _a("updated"))
        eupdated.text = row["updated_at"]
        epublished = ET.SubElement(entry, _a("published"))
        epublished.text = row["created_at"]
        author = ET.SubElement(entry, _a("author"))
        ET.SubElement(author, _a("name")).text = row["agent_id"]
        content_el = ET.SubElement(entry, _a("content"), type="text")
        content_el.text = row["content"]
        for t in json.loads(row["tags"]):
            ET.SubElement(entry, _a("category"), term=t)

    xml_bytes = ET.tostring(feed, encoding="utf-8", xml_declaration=True)
    return Response(content=xml_bytes, media_type="application/atom+xml; charset=utf-8")


@router.get("/feed.json", summary="JSON Feed of memory entries")
async def memory_feed_json(
    project: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    type: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    include_deleted: bool = Query(default=False),
    auth: FeedAuth = Depends(feed_auth_dep),
):
    db = get_db()
    rows = _fetch_feed_rows(
        db,
        auth.agent_id,
        project,
        tag,
        type,
        limit,
        include_deleted,
        mesh_project=auth.mesh_project,
    )
    base = settings.public_url or f"http://localhost:{settings.port}"
    iid = instance_id()

    items = [
        {
            "id": f"{base}/memory/{row['id']}",
            "title": row["content"].split("\n")[0][:80].strip() or row["id"],
            "content_text": row["content"],
            "date_published": row["created_at"],
            "date_modified": row["updated_at"],
            "authors": [{"name": row["agent_id"]}],
            "tags": json.loads(row["tags"]),
            "_artel": {
                "memory_id": row["id"],
                "type": row["type"],
                "confidence": row["confidence"],
                "project": row["project"],
                "scope": row["scope"],
                "agent_id": row["agent_id"],
                "version": row["version"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "deleted_at": row["deleted_at"],
                "parents": json.loads(row["parents"]),
                "origin": row["origin"] or iid,
            },
        }
        for row in rows
    ]

    payload = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": f"Artel Memory{f' / {project}' if project else ''}",
        "home_page_url": base,
        "feed_url": f"{base}/memory/feed.json",
        "items": items,
    }
    return JSONResponse(content=payload, media_type="application/feed+json")


@router.get("/{entry_id}", response_model=MemoryEntry, summary="Get a memory entry by ID")
async def get_memory(
    entry_id: str,
    agent_id: str = ReaderDep,
):
    entry_id = _resolve_entry(entry_id)
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
    agent_id: str = ActorDep,
):
    entry_id = _resolve_entry(entry_id)
    db = get_db()
    row = db.execute(
        "SELECT * FROM memory WHERE id=? AND deleted_at IS NULL", (entry_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")

    is_entry_owner = row["agent_id"] == agent_id or can_curate_memory(agent_id)
    wants_owner_fields = any(
        f is not None
        for f in (body.content, body.tags, body.scope, body.project, body.confidence, body.type)
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
            if body.content is not None and vec is not None:
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
    agent_id: str = ActorDep,
):
    entry_id = _resolve_entry(entry_id)
    db = get_db()
    row = db.execute(
        "SELECT * FROM memory WHERE id=? AND deleted_at IS NULL", (entry_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["agent_id"] != agent_id and not can_curate_memory(agent_id):
        raise HTTPException(status_code=403, detail="forbidden")
    with db:
        db.execute(
            "UPDATE memory SET deleted_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
            (entry_id,),
        )
