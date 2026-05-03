import asyncio

from .models import EventEntry

_subscribers: list[asyncio.Queue] = []


def broadcast(event: EventEntry) -> None:
    data = event.model_dump_json()
    dead: list[asyncio.Queue] = []
    for q in _subscribers:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _subscribers.remove(q)
