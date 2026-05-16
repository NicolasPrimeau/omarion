import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ...store.db import get_db
from ..auth import _memberships, is_owner, require_agent
from ..models import FeedCreate, FeedEntry, new_id

router = APIRouter(prefix="/feeds", tags=["feeds"])


def _row_to_entry(row: sqlite3.Row) -> FeedEntry:
    return FeedEntry(
        id=row["id"],
        agent_id=row["agent_id"],
        project=row["project"],
        url=row["url"],
        name=row["name"],
        tags=json.loads(row["tags"]),
        interval_min=row["interval_min"],
        max_per_poll=row["max_per_poll"],
        last_fetched_at=row["last_fetched_at"],
        created_at=row["created_at"],
    )


@router.post("", response_model=FeedEntry, status_code=201, summary="Subscribe to an RSS/Atom feed")
async def subscribe(body: FeedCreate, agent_id: str = Depends(require_agent)):
    allowed = _memberships(agent_id)
    if allowed is not None and body.project not in allowed:
        raise HTTPException(status_code=403, detail="not a member of this project")
    db = get_db()
    feed_id = new_id()
    with db:
        db.execute(
            """INSERT INTO feed_subscriptions
               (id, agent_id, project, url, name, tags, interval_min, max_per_poll)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                feed_id,
                agent_id,
                body.project,
                body.url,
                body.name,
                json.dumps(body.tags),
                body.interval_min,
                body.max_per_poll,
            ),
        )
    row = db.execute("SELECT * FROM feed_subscriptions WHERE id=?", (feed_id,)).fetchone()
    return _row_to_entry(row)


@router.get("", response_model=list[FeedEntry], summary="List feed subscriptions")
async def list_feeds(agent_id: str = Depends(require_agent)):
    allowed = _memberships(agent_id)
    db = get_db()
    if allowed is None:
        rows = db.execute("SELECT * FROM feed_subscriptions ORDER BY created_at").fetchall()
    else:
        if not allowed:
            return []
        placeholders = ",".join("?" * len(allowed))
        rows = db.execute(
            f"SELECT * FROM feed_subscriptions WHERE project IN ({placeholders}) ORDER BY created_at",
            allowed,
        ).fetchall()
    return [_row_to_entry(r) for r in rows]


@router.delete("/{feed_id}", status_code=204, summary="Unsubscribe from a feed")
async def unsubscribe(feed_id: str, agent_id: str = Depends(require_agent)):
    db = get_db()
    row = db.execute("SELECT agent_id FROM feed_subscriptions WHERE id=?", (feed_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="feed not found")
    if row["agent_id"] != agent_id and not is_owner(agent_id):
        raise HTTPException(status_code=403, detail="not your subscription")
    with db:
        db.execute("DELETE FROM feed_items_seen WHERE feed_id=?", (feed_id,))
        db.execute("DELETE FROM feed_subscriptions WHERE id=?", (feed_id,))
