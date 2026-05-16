import asyncio
import json
import sqlite3

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from ...store.db import get_db
from ..auth import ActorDep, ReaderDep
from ..broadcast import _subscribers, broadcast
from ..models import EventEmit, EventEntry, new_id

router = APIRouter(prefix="/events", tags=["events"])


def _row_to_event(row: sqlite3.Row) -> EventEntry:
    return EventEntry(
        id=row["id"],
        type=row["type"],
        agent_id=row["agent_id"],
        payload=json.loads(row["payload"]),
        created_at=row["created_at"],
    )


@router.post("", response_model=EventEntry, status_code=201, summary="Emit a custom event")
async def emit_event(body: EventEmit, agent_id: str = ActorDep):
    db = get_db()
    event_id = new_id()
    db.execute(
        "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
        (event_id, body.type, agent_id, json.dumps(body.payload)),
    )
    db.commit()
    row = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    event = _row_to_event(row)
    broadcast(event)
    return event


@router.get("", summary="Poll events since a timestamp")
async def poll_events(
    since: str = Query(...),
    type: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    agent_id: str = ReaderDep,
):
    db = get_db()
    sql = "SELECT * FROM events WHERE created_at > ? "
    params: list = [since]
    if type:
        sql += "AND type=? "
        params.append(type)
    if agent:
        sql += "AND agent_id=? "
        params.append(agent)
    sql += "ORDER BY created_at"
    rows = db.execute(sql, params).fetchall()
    return [_row_to_event(r) for r in rows]


@router.get("/stream", summary="SSE stream of real-time events")
async def event_stream(
    type: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    agent_id: str = ReaderDep,
):
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.append(queue)

    async def generate():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    if type or agent:
                        event = json.loads(data)
                        if type and event.get("type") != type:
                            continue
                        if agent and event.get("agent_id") != agent:
                            continue
                    yield f"data: {data}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if queue in _subscribers:
                _subscribers.remove(queue)

    return StreamingResponse(generate(), media_type="text/event-stream")
