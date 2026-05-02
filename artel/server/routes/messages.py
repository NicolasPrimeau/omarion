import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ...store.db import get_db
from ..auth import require_agent
from ..models import MessageEntry, MessageSend, new_id

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


@router.post("", response_model=MessageEntry, status_code=201)
async def send_message(body: MessageSend, agent_id: str = Depends(require_agent)):
    db = get_db()
    msg_id = new_id()
    db.execute(
        "INSERT INTO messages (id, from_agent, to_agent, subject, body) VALUES (?,?,?,?,?)",
        (msg_id, agent_id, body.to, body.subject, body.body),
    )
    db.execute(
        "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
        (new_id(), "message.received", agent_id,
         json.dumps({"message_id": msg_id, "to": body.to})),
    )
    db.commit()
    row = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    return _row_to_msg(row)


@router.get("/inbox", response_model=list[MessageEntry])
async def inbox(agent_id: str = Depends(require_agent)):
    db = get_db()
    rows = db.execute(
        """SELECT * FROM messages WHERE (to_agent=? OR to_agent='broadcast')
           AND read=0 ORDER BY created_at DESC""",
        (agent_id,),
    ).fetchall()
    return [_row_to_msg(r) for r in rows]


@router.post("/{msg_id}/read", response_model=MessageEntry)
async def mark_read(msg_id: str, agent_id: str = Depends(require_agent)):
    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["to_agent"] != agent_id and row["to_agent"] != "broadcast":
        raise HTTPException(status_code=403, detail="forbidden")
    db.execute("UPDATE messages SET read=1 WHERE id=?", (msg_id,))
    db.commit()
    row = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    return _row_to_msg(row)
