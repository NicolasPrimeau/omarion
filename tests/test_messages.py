from tests.conftest import AGENT2, HEADERS, HEADERS2, TEST_AGENT


async def test_send_and_receive(client):
    r = await client.post(
        "/messages", json={"to": AGENT2, "subject": "hello", "body": "world"}, headers=HEADERS
    )
    assert r.status_code == 201
    msg = r.json()
    assert msg["from_agent"] == TEST_AGENT
    assert msg["to_agent"] == AGENT2
    assert msg["read"] is False


async def test_inbox_shows_unread(client):
    await client.post("/messages", json={"to": AGENT2, "body": "msg1"}, headers=HEADERS)
    await client.post("/messages", json={"to": AGENT2, "body": "msg2"}, headers=HEADERS)

    r = await client.get("/messages/inbox", headers=HEADERS2)
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_inbox_excludes_other_agent(client):
    await client.post(
        "/messages", json={"to": TEST_AGENT, "body": "for testagent"}, headers=HEADERS2
    )

    r = await client.get("/messages/inbox", headers=HEADERS2)
    assert r.status_code == 200
    assert len(r.json()) == 0


async def test_mark_read(client):
    r = await client.post("/messages", json={"to": AGENT2, "body": "read me"}, headers=HEADERS)
    mid = r.json()["id"]

    r2 = await client.post(f"/messages/{mid}/read", headers=HEADERS2)
    assert r2.status_code == 200
    assert r2.json()["read"] is True


async def test_inbox_empty_after_mark_read(client):
    r = await client.post("/messages", json={"to": AGENT2, "body": "ephemeral"}, headers=HEADERS)
    mid = r.json()["id"]

    await client.post(f"/messages/{mid}/read", headers=HEADERS2)

    r2 = await client.get("/messages/inbox", headers=HEADERS2)
    assert len(r2.json()) == 0


async def test_broadcast_message_received_by_all(client):
    r = await client.post(
        "/messages", json={"to": "broadcast", "body": "attention all"}, headers=HEADERS
    )
    assert r.status_code == 201

    r1 = await client.get("/messages/inbox", headers=HEADERS)
    r2 = await client.get("/messages/inbox", headers=HEADERS2)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert len(r1.json()) == 1
    assert len(r2.json()) == 1


async def test_mark_read_wrong_agent_forbidden(client):
    r = await client.post("/messages", json={"to": AGENT2, "body": "private"}, headers=HEADERS)
    mid = r.json()["id"]

    r2 = await client.post(f"/messages/{mid}/read", headers=HEADERS)
    assert r2.status_code == 403


async def test_message_event_written_to_db(client):
    await client.post("/messages", json={"to": AGENT2, "body": "event check"}, headers=HEADERS)

    r = await client.get("/events", params={"since": "1970-01-01T00:00:00.000Z"}, headers=HEADERS)
    events = r.json()
    types = [e["type"] for e in events]
    assert "message.received" in types
