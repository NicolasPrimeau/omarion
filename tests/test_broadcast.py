import asyncio
import json

import pytest
import artel.server.broadcast as bc
from tests.conftest import AGENT2, HEADERS, HEADERS2


@pytest.fixture(autouse=True)
def clean_subscribers():
    bc._subscribers.clear()
    yield
    bc._subscribers.clear()


async def test_memory_write_broadcasts_event(client):
    queue: asyncio.Queue = asyncio.Queue()
    bc._subscribers.append(queue)

    await client.post("/memory", json={
        "content": "broadcast test",
        "type": "memory",
        "scope": "shared",
        "tags": [],
        "parents": [],
        "confidence": 1.0,
    }, headers=HEADERS)

    assert not queue.empty()
    event = json.loads(queue.get_nowait())
    assert event["type"] == "memory.written"
    assert "memory_id" in event["payload"]


async def test_message_send_broadcasts_event(client):
    queue: asyncio.Queue = asyncio.Queue()
    bc._subscribers.append(queue)

    await client.post("/messages", json={"to": AGENT2, "body": "ping"}, headers=HEADERS)

    assert not queue.empty()
    event = json.loads(queue.get_nowait())
    assert event["type"] == "message.received"
    assert event["payload"]["to"] == AGENT2


async def test_explicit_emit_broadcasts(client):
    queue: asyncio.Queue = asyncio.Queue()
    bc._subscribers.append(queue)

    await client.post("/events", json={"type": "custom.thing", "payload": {"x": 1}}, headers=HEADERS)

    assert not queue.empty()
    event = json.loads(queue.get_nowait())
    assert event["type"] == "custom.thing"
    assert event["payload"] == {"x": 1}


async def test_full_queue_subscriber_removed(client):
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    queue.put_nowait("filler")
    bc._subscribers.append(queue)

    await client.post("/memory", json={
        "content": "overflow test",
        "type": "memory",
        "scope": "shared",
        "tags": [],
        "parents": [],
        "confidence": 1.0,
    }, headers=HEADERS)

    assert queue not in bc._subscribers


async def test_multiple_subscribers_all_receive(client):
    q1: asyncio.Queue = asyncio.Queue()
    q2: asyncio.Queue = asyncio.Queue()
    bc._subscribers.extend([q1, q2])

    await client.post("/events", json={"type": "multi.test", "payload": {}}, headers=HEADERS)

    assert not q1.empty()
    assert not q2.empty()
