from tests.conftest import HEADERS


async def test_emit_event(client):
    r = await client.post("/events", json={"type": "custom.event", "payload": {"key": "val"}}, headers=HEADERS)
    assert r.status_code == 201
    event = r.json()
    assert event["type"] == "custom.event"
    assert event["payload"] == {"key": "val"}


async def test_poll_events(client):
    await client.post("/events", json={"type": "evt.a", "payload": {}}, headers=HEADERS)
    await client.post("/events", json={"type": "evt.b", "payload": {}}, headers=HEADERS)

    r = await client.get("/events", params={"since": "1970-01-01T00:00:00.000Z"}, headers=HEADERS)
    assert r.status_code == 200
    types = [e["type"] for e in r.json()]
    assert "evt.a" in types
    assert "evt.b" in types


async def test_poll_events_filtered_by_type(client):
    await client.post("/events", json={"type": "want.this", "payload": {}}, headers=HEADERS)
    await client.post("/events", json={"type": "not.this", "payload": {}}, headers=HEADERS)

    r = await client.get("/events", params={"since": "1970-01-01T00:00:00.000Z", "type": "want.this"}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert all(e["type"] == "want.this" for e in results)
    assert len(results) == 1


async def test_poll_events_since_filters(client):
    await client.post("/events", json={"type": "first", "payload": {}}, headers=HEADERS)
    await client.post("/events", json={"type": "second", "payload": {}}, headers=HEADERS)

    r_all = await client.get("/events", params={"since": "1970-01-01T00:00:00.000Z"}, headers=HEADERS)
    types_all = [e["type"] for e in r_all.json()]
    assert "first" in types_all
    assert "second" in types_all

    r_future = await client.get("/events", params={"since": "2099-01-01T00:00:00.000Z"}, headers=HEADERS)
    assert r_future.json() == []
