from fastapi import APIRouter, Depends

from ...store.db import get_db
from ..auth import require_agent
from ..config import settings
from ..models import Participant

router = APIRouter(prefix="/participants", tags=["participants"])


@router.get("", response_model=list[Participant])
async def list_participants(agent_id: str = Depends(require_agent)):
    db = get_db()
    last_seen: dict[str, str | None] = {
        aid: None for aid in settings.api_keys().values()
    }
    rows = db.execute(
        "SELECT agent_id, MAX(created_at) AS ts FROM events GROUP BY agent_id"
    ).fetchall()
    for row in rows:
        if row["agent_id"] in last_seen:
            last_seen[row["agent_id"]] = row["ts"]
    return [
        Participant(agent_id=aid, last_seen=ts)
        for aid, ts in sorted(last_seen.items())
    ]
