import json
import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from ...store.db import get_db
from ..auth import OwnerDep, ReaderDep, _memberships
from ..models import PeerLink, PeerLinkCreate, new_id

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


def _peer_feed_url(peer_url: str, project: str, agent_id: str, api_key: str) -> str:
    base = peer_url.rstrip("/")
    return (
        f"{base}/memory/feed.json?project={quote(project)}"
        f"&agent_id={quote(agent_id)}&api_key={quote(api_key)}"
    )


@router.post("/peers", response_model=PeerLink, status_code=201, summary="Link a peer Artel")
async def link_peer(body: PeerLinkCreate, agent_id: str = OwnerDep):
    allowed = _memberships(agent_id)
    if allowed is not None and body.project not in allowed:
        raise HTTPException(status_code=403, detail="not a member of this project")
    base = body.peer_url.rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="peer_url must be http(s)")

    db = get_db()
    feed_id = new_id()
    link_id = new_id()
    url = _peer_feed_url(base, body.project, body.peer_agent_id, body.peer_api_key)
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
    allowed = _memberships(agent_id)
    rows = db.execute(
        """SELECT p.*, f.last_fetched_at FROM peer_links p
           LEFT JOIN feed_subscriptions f ON f.id = p.feed_id
           ORDER BY p.created_at DESC"""
    ).fetchall()
    links = [_row_to_link(r) for r in rows]
    if allowed is not None:
        links = [link for link in links if link.project in allowed]
    return links


@router.delete("/peers/{link_id}", status_code=204, summary="Unlink a peer Artel")
async def unlink_peer(link_id: str, agent_id: str = OwnerDep):
    db = get_db()
    row = db.execute("SELECT feed_id FROM peer_links WHERE id=?", (link_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    with db:
        db.execute("DELETE FROM feed_subscriptions WHERE id=?", (row["feed_id"],))
        db.execute("DELETE FROM peer_links WHERE id=?", (link_id,))
