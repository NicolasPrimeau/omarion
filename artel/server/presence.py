from ..store.db import get_db


def update_seen(agent_id: str, ts: str) -> None:
    db = get_db()
    with db:
        db.execute("UPDATE agents SET last_seen_at=? WHERE id=?", (ts, agent_id))
