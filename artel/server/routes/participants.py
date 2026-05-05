from fastapi import APIRouter, Depends

from ...store.db import get_db
from ..auth import _last_seen, require_agent
from ..config import settings
from ..models import Participant

router = APIRouter(prefix="/participants", tags=["participants"])


@router.get("", response_model=list[Participant])
async def list_participants(agent_id: str = Depends(require_agent)):
    db = get_db()
    last_seen: dict[str, str | None] = {aid: None for aid in settings.api_keys().values()}
    for row in db.execute("SELECT id FROM agents").fetchall():
        last_seen.setdefault(row["id"], None)
    for row in db.execute(
        "SELECT agent_id, MAX(created_at) AS ts FROM events GROUP BY agent_id"
    ).fetchall():
        if row["agent_id"] in last_seen:
            last_seen[row["agent_id"]] = row["ts"]
    for aid, ts in _last_seen.items():
        prev = last_seen.get(aid)
        last_seen[aid] = max(ts, prev) if prev else ts

    projects: dict[str, str | None] = {}
    for row in db.execute("SELECT id, project FROM agents").fetchall():
        projects[row["id"]] = row["project"]
    for key, aid in settings.api_keys().items():
        if aid not in projects:
            parts = key.split(":")
            projects[aid] = parts[2] if len(parts) > 2 else None

    active_tasks: dict[str, str | None] = {}
    for row in db.execute("SELECT assigned_to, id FROM tasks WHERE status='claimed'").fetchall():
        active_tasks[row["assigned_to"]] = row["id"]

    return [
        Participant(
            agent_id=aid,
            last_seen=ts,
            project=projects.get(aid),
            active_task_id=active_tasks.get(aid),
        )
        for aid, ts in sorted(last_seen.items())
    ]
