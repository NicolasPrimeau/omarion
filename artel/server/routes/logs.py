import json
import sqlite3

from fastapi import APIRouter, Query

from ...store.db import get_db
from ..auth import ActorDep, OwnerDep
from ..models import LogEntry, LogWrite, new_id

router = APIRouter(prefix="/logs", tags=["logs"])

_MAX_ROWS = 10_000


def _row_to_entry(row: sqlite3.Row) -> LogEntry:
    return LogEntry(
        id=row["id"],
        created_at=row["created_at"],
        level=row["level"],
        source=row["source"],
        action=row["action"],
        message=row["message"],
        details=json.loads(row["details"]),
    )


@router.post("", response_model=LogEntry, status_code=201, summary="Write an archivist log entry")
async def write_log(body: LogWrite, agent_id: str = ActorDep):
    db = get_db()
    lid = new_id()
    with db:
        db.execute(
            "INSERT INTO archivist_logs (id, level, source, action, message, details) VALUES (?,?,?,?,?,?)",
            (lid, body.level, body.source, body.action, body.message, json.dumps(body.details)),
        )
        db.execute(
            """DELETE FROM archivist_logs WHERE id IN (
               SELECT id FROM archivist_logs ORDER BY created_at DESC LIMIT -1 OFFSET ?)""",
            (_MAX_ROWS,),
        )
    row = db.execute("SELECT * FROM archivist_logs WHERE id=?", (lid,)).fetchone()
    return _row_to_entry(row)


@router.get("", response_model=list[LogEntry], summary="List archivist log entries (owner only)")
async def list_logs(
    level: str | None = Query(default=None),
    source: str | None = Query(default=None),
    action: str | None = Query(default=None),
    since: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    agent_id: str = OwnerDep,
):
    db = get_db()
    sql = "SELECT * FROM archivist_logs WHERE 1=1"
    params: list = []
    if level:
        sql += " AND level=?"
        params.append(level)
    if source:
        sql += " AND source=?"
        params.append(source)
    if action:
        sql += " AND action=?"
        params.append(action)
    if since:
        sql += " AND created_at>?"
        params.append(since)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [_row_to_entry(r) for r in rows]
