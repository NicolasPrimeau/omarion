import json
import secrets
import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from ...store.db import get_db
from ..auth import OwnerDep, ReaderDep
from ..models import MeshToken, MeshTokenCreate, MeshTokenUpdate, PeerLink, PeerLinkCreate, new_id

router = APIRouter(prefix="/mesh", tags=["mesh"])


def _row_to_link(row: sqlite3.Row) -> PeerLink:
    return PeerLink(
        id=row["id"],
        peer_url=row["peer_url"],
        project=row["project"],
        feed_id=row["feed_id"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        last_fetched_at=row["last_fetched_at"] if "last_fetched_at" in row.keys() else None,
    )


def _row_to_token(row: sqlite3.Row) -> MeshToken:
    return MeshToken(
        id=row["id"],
        token=row["token"],
        label=row["label"],
        project=row["project"],
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


def _peer_feed_url(peer_url: str, project: str | None, peer_token: str) -> str:
    base = peer_url.rstrip("/")
    url = f"{base}/memory/feed.json?mesh_token={quote(peer_token)}"
    if project:
        url += f"&project={quote(project)}"
    return url


@router.post("/tokens", response_model=MeshToken, status_code=201, summary="Create a mesh token")
async def create_token(body: MeshTokenCreate, agent_id: str = OwnerDep):
    db = get_db()
    token_id = new_id()
    token = secrets.token_urlsafe(32)
    with db:
        db.execute(
            "INSERT INTO mesh_tokens (id, token, label, project, created_by) VALUES (?,?,?,?,?)",
            (token_id, token, body.label, body.project, agent_id),
        )
    row = db.execute("SELECT * FROM mesh_tokens WHERE id=?", (token_id,)).fetchone()
    return _row_to_token(row)


@router.get("/tokens", response_model=list[MeshToken], summary="List mesh tokens")
async def list_tokens(agent_id: str = OwnerDep):
    db = get_db()
    rows = db.execute("SELECT * FROM mesh_tokens ORDER BY created_at DESC").fetchall()
    return [_row_to_token(r) for r in rows]


@router.patch("/tokens/{token_id}", response_model=MeshToken, summary="Update a mesh token")
async def update_token(token_id: str, body: MeshTokenUpdate, agent_id: str = OwnerDep):
    db = get_db()
    row = db.execute("SELECT * FROM mesh_tokens WHERE id=?", (token_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    fields: dict = {}
    if body.label is not None:
        fields["label"] = body.label
    if body.project is not None:
        fields["project"] = body.project or None
    if fields:
        set_clause = ", ".join(f"{k}=?" for k in fields)
        with db:
            db.execute(
                f"UPDATE mesh_tokens SET {set_clause} WHERE id=?",
                (*fields.values(), token_id),
            )
    row = db.execute("SELECT * FROM mesh_tokens WHERE id=?", (token_id,)).fetchone()
    return _row_to_token(row)


@router.delete("/tokens/{token_id}", status_code=204, summary="Revoke a mesh token")
async def revoke_token(token_id: str, agent_id: str = OwnerDep):
    db = get_db()
    if not db.execute("SELECT 1 FROM mesh_tokens WHERE id=?", (token_id,)).fetchone():
        raise HTTPException(status_code=404, detail="not found")
    with db:
        db.execute("DELETE FROM mesh_tokens WHERE id=?", (token_id,))


@router.post("/peers", response_model=PeerLink, status_code=201, summary="Link a peer Artel")
async def link_peer(body: PeerLinkCreate, agent_id: str = OwnerDep):
    base = body.peer_url.rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="peer_url must be http(s)")

    db = get_db()
    feed_id = new_id()
    link_id = new_id()
    url = _peer_feed_url(base, body.project, body.peer_token)
    with db:
        db.execute(
            """INSERT INTO feed_subscriptions
               (id, agent_id, project, url, name, tags, interval_min, max_per_poll)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                feed_id,
                agent_id,
                body.project,
                url,
                f"mesh:{base}",
                json.dumps(["mesh", "peer"]),
                30,
                100,
            ),
        )
        db.execute(
            """INSERT INTO peer_links (id, peer_url, project, feed_id, created_by)
               VALUES (?,?,?,?,?)""",
            (link_id, base, body.project, feed_id, agent_id),
        )
    row = db.execute(
        """SELECT p.*, f.last_fetched_at FROM peer_links p
           LEFT JOIN feed_subscriptions f ON f.id = p.feed_id WHERE p.id=?""",
        (link_id,),
    ).fetchone()
    return _row_to_link(row)


@router.get("/peers", response_model=list[PeerLink], summary="List linked peer Artels")
async def list_peers(agent_id: str = ReaderDep):
    db = get_db()
    rows = db.execute(
        """SELECT p.*, f.last_fetched_at FROM peer_links p
           LEFT JOIN feed_subscriptions f ON f.id = p.feed_id
           ORDER BY p.created_at DESC"""
    ).fetchall()
    return [_row_to_link(r) for r in rows]


@router.delete("/peers/{link_id}", status_code=204, summary="Unlink a peer Artel")
async def unlink_peer(link_id: str, agent_id: str = OwnerDep):
    db = get_db()
    row = db.execute("SELECT feed_id FROM peer_links WHERE id=?", (link_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    with db:
        db.execute("DELETE FROM feed_subscriptions WHERE id=?", (row["feed_id"],))
        db.execute("DELETE FROM peer_links WHERE id=?", (link_id,))
