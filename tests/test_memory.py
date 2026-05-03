import pytest
from tests.conftest import AGENT2, HEADERS, HEADERS2, TEST_AGENT


@pytest.fixture
def mem_payload():
    return {"content": "Paris is the capital of France", "type": "memory", "scope": "shared", "tags": ["geo"], "parents": [], "confidence": 1.0}


async def test_write_and_get(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    assert r.status_code == 201
    entry = r.json()
    assert entry["content"] == mem_payload["content"]
    assert entry["agent_id"] == TEST_AGENT

    r2 = await client.get(f"/memory/{entry['id']}", headers=HEADERS)
    assert r2.status_code == 200
    assert r2.json()["id"] == entry["id"]


async def test_patch_content(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    r2 = await client.patch(f"/memory/{eid}", json={"content": "Berlin is the capital of Germany"}, headers=HEADERS)
    assert r2.status_code == 200
    assert r2.json()["content"] == "Berlin is the capital of Germany"
    assert r2.json()["version"] == 2


async def test_patch_confidence_by_other_agent_forbidden(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    r2 = await client.patch(f"/memory/{eid}", json={"confidence": 0.5}, headers=HEADERS2)
    assert r2.status_code == 403


async def test_patch_content_by_other_agent_forbidden(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    r2 = await client.patch(f"/memory/{eid}", json={"content": "hijacked"}, headers=HEADERS2)
    assert r2.status_code == 403


async def test_delete(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    r2 = await client.delete(f"/memory/{eid}", headers=HEADERS)
    assert r2.status_code == 204

    r3 = await client.get(f"/memory/{eid}", headers=HEADERS)
    assert r3.status_code == 404


async def test_delete_by_other_agent_forbidden(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    r2 = await client.delete(f"/memory/{eid}", headers=HEADERS2)
    assert r2.status_code == 403


async def test_search(client):
    await client.post("/memory", json={"content": "alpha entry", "type": "memory", "scope": "shared", "tags": [], "parents": [], "confidence": 1.0}, headers=HEADERS)
    await client.post("/memory", json={"content": "beta entry", "type": "memory", "scope": "shared", "tags": [], "parents": [], "confidence": 1.0}, headers=HEADERS)

    r = await client.get("/memory/search", params={"q": "alpha"}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert len(results) >= 1


async def test_delta(client, mem_payload):
    await client.post("/memory", json=mem_payload, headers=HEADERS)

    r = await client.get("/memory/delta", params={"since": "1970-01-01T00:00:00.000Z"}, headers=HEADERS)
    assert r.status_code == 200
    assert len(r.json()) >= 1


async def test_private_scope_hidden_from_others(client):
    r = await client.post("/memory", json={"content": "secret", "type": "memory", "scope": "private", "tags": [], "parents": [], "confidence": 1.0}, headers=HEADERS)
    eid = r.json()["id"]

    r2 = await client.get(f"/memory/{eid}", headers=HEADERS2)
    assert r2.status_code == 403


async def test_list_memory_by_type(client):
    await client.post("/memory", json={"content": "scratch note", "type": "scratch", "scope": "shared", "tags": [], "parents": [], "confidence": 1.0}, headers=HEADERS)
    await client.post("/memory", json={"content": "real memory", "type": "memory", "scope": "shared", "tags": [], "parents": [], "confidence": 1.0}, headers=HEADERS)

    r = await client.get("/memory", params={"type": "scratch"}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert all(e["type"] == "scratch" for e in results)
    assert len(results) == 1


async def test_get_nonexistent(client):
    r = await client.get("/memory/does-not-exist", headers=HEADERS)
    assert r.status_code == 404


async def test_memory_event_written_to_db(client, mem_payload):
    await client.post("/memory", json=mem_payload, headers=HEADERS)

    r = await client.get("/events", params={"since": "1970-01-01T00:00:00.000Z"}, headers=HEADERS)
    assert r.status_code == 200
    events = r.json()
    types = [e["type"] for e in events]
    assert "memory.written" in types
