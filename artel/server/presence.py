_last_seen: dict[str, str] = {}


def update_seen(agent_id: str, ts: str) -> None:
    _last_seen[agent_id] = ts
