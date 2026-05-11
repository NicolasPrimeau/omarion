import json
import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from ...store.db import get_db
from ..auth import require_agent
from ..broadcast import broadcast
from ..models import EventEntry, MessageEntry, MessageSend, new_id

router = APIRouter(prefix="/messages", tags=["messages"])


def _row_to_msg(row: sqlite3.Row) -> MessageEntry:
    return MessageEntry(
        id=row["id"],
        from_agent=row["from_agent"],
        to_agent=row["to_agent"],
        subject=row["subject"],
        body=row["body"],
        read=bool(row["read"]),
        created_at=row["created_at"],
    )


@router.post(
    "",
    response_model=MessageEntry,
    status_code=201,
    summary="Send a message to an agent or broadcast",
)
async def send_message(body: MessageSend, agent_id: str = Depends(require_agent)):
    from ..config import settings

    db = get_db()
    if body.to != "broadcast":
        in_db = db.execute("SELECT id FROM agents WHERE id=?", (body.to,)).fetchone()
        in_config = body.to in settings.api_keys().values()
        if not in_db and not in_config:
            raise HTTPException(status_code=404, detail="recipient not found")
    msg_id = new_id()
    event_id = new_id()
    with db:
        db.execute(
            "INSERT INTO messages (id, from_agent, to_agent, subject, body) VALUES (?,?,?,?,?)",
            (msg_id, agent_id, body.to, body.subject, body.body),
        )
        db.execute(
            "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
            (
                event_id,
                "message.received",
                agent_id,
                json.dumps({"message_id": msg_id, "to": body.to}),
            ),
        )

    broadcast(
        EventEntry(
            id=event_id,
            type="message.received",
            agent_id=agent_id,
            payload={"message_id": msg_id, "to": body.to},
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        )
    )

    row = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    return _row_to_msg(row)


@router.get("/inbox", response_model=list[MessageEntry], summary="Fetch unread messages")
async def inbox(
    agent: str | None = Query(default=None),
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    target = agent or agent_id
    rows = db.execute(
        """SELECT * FROM messages WHERE (
            (to_agent=? AND read=0) OR
            (to_agent='broadcast' AND id NOT IN (
                SELECT message_id FROM message_reads WHERE agent_id=?
            ))
        ) ORDER BY created_at DESC""",
        (target, target),
    ).fetchall()
    return [_row_to_msg(r) for r in rows]


@router.post("/inbox/read-all", summary="Mark all unread inbox messages as read")
async def mark_inbox_read(agent_id: str = Depends(require_agent)):
    db = get_db()
    with db:
        db.execute("UPDATE messages SET read=1 WHERE to_agent=? AND read=0", (agent_id,))
        db.execute(
            """INSERT OR IGNORE INTO message_reads (agent_id, message_id)
               SELECT ?, id FROM messages WHERE to_agent='broadcast'
               AND id NOT IN (SELECT message_id FROM message_reads WHERE agent_id=?)""",
            (agent_id, agent_id),
        )
    return {"ok": True}


@router.post("/{msg_id}/read", response_model=MessageEntry, summary="Mark a message as read")
async def mark_read(msg_id: str, agent_id: str = Depends(require_agent)):
    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["to_agent"] != agent_id and row["to_agent"] != "broadcast":
        raise HTTPException(status_code=403, detail="forbidden")
    if row["to_agent"] == "broadcast":
        with db:
            db.execute(
                "INSERT OR IGNORE INTO message_reads (agent_id, message_id) VALUES (?, ?)",
                (agent_id, msg_id),
            )
    else:
        with db:
            db.execute("UPDATE messages SET read=1 WHERE id=?", (msg_id,))
    row = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    return _row_to_msg(row)
