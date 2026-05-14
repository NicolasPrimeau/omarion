from tests.conftest import HEADERS, TEST_AGENT


async def test_handoff_store_and_retrieve(client):
    r = await client.post(
        "/sessions/handoff",
        json={
            "summary": "finished memory work",
            "next_steps": ["deploy", "monitor"],
            "in_progress": [],
        },
        headers=HEADERS,
    )
    assert r.status_code == 201
    assert "id" in r.json()

    r2 = await client.get(f"/sessions/handoff/{TEST_AGENT}", headers=HEADERS)
    assert r2.status_code == 200
    data = r2.json()
    assert data["last_handoff"]["summary"] == "finished memory work"
    assert data["last_handoff"]["next_steps"] == ["deploy", "monitor"]


async def test_no_previous_session(client):
    r = await client.get(f"/sessions/handoff/{TEST_AGENT}", headers=HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert data["last_handoff"] is None
    assert data["memory_delta"] == []


async def test_handoff_cross_agent_forbidden(client):
    r = await client.post("/sessions/handoff", json={"summary": "mine"}, headers=HEADERS)
    assert r.status_code == 201

    from tests.conftest import HEADERS2

    r2 = await client.get(f"/sessions/handoff/{TEST_AGENT}", headers=HEADERS2)
    assert r2.status_code == 403


async def test_memory_delta_included_in_context(client):
    import asyncio

    await client.post("/sessions/handoff", json={"summary": "first session"}, headers=HEADERS)
    await asyncio.sleep(0.005)

    await client.post(
        "/memory",
        json={
            "content": "new knowledge since handoff",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get(f"/sessions/handoff/{TEST_AGENT}", headers=HEADERS)
    data = r.json()
    assert len(data["memory_delta"]) >= 1
    contents = [e["content"] for e in data["memory_delta"]]
    assert any("new knowledge" in c for c in contents)


async def test_latest_handoff_returned(client):
    await client.post("/sessions/handoff", json={"summary": "first"}, headers=HEADERS)
    await client.post("/sessions/handoff", json={"summary": "second"}, headers=HEADERS)

    r = await client.get(f"/sessions/handoff/{TEST_AGENT}", headers=HEADERS)
    assert r.json()["last_handoff"]["summary"] == "second"


async def test_admin_can_read_other_agent_handoff(client):
    r = await client.post("/sessions/handoff", json={"summary": "agent1 session"}, headers=HEADERS)
    assert r.status_code == 201

    from artel.store.db import get_db
    from tests.conftest import HEADERS2, TEST_AGENT

    db = get_db()
    db.execute("UPDATE agents SET role='admin' WHERE id=?", (HEADERS2["x-agent-id"],))
    db.commit()

    r2 = await client.get(f"/sessions/handoff/{TEST_AGENT}", headers=HEADERS2)
    assert r2.status_code == 200
    assert r2.json()["last_handoff"]["summary"] == "agent1 session"
