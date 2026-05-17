import pytest

from tests.conftest import AGENT2, HEADERS, HEADERS2, TEST_AGENT


@pytest.fixture
def mem_payload():
    return {
        "content": "Paris is the capital of France",
        "type": "memory",
        "scope": "project",
        "tags": ["geo"],
        "parents": [],
        "confidence": 1.0,
    }


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

    r2 = await client.patch(
        f"/memory/{eid}", json={"content": "Berlin is the capital of Germany"}, headers=HEADERS
    )
    assert r2.status_code == 200
    assert r2.json()["content"] == "Berlin is the capital of Germany"
    assert r2.json()["version"] == 2


async def test_patch_confidence_by_other_agent_forbidden(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    r2 = await client.patch(f"/memory/{eid}", json={"confidence": 0.5}, headers=HEADERS2)
    assert r2.status_code == 403


async def test_patch_type_by_other_agent_forbidden(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    r2 = await client.patch(f"/memory/{eid}", json={"type": "doc"}, headers=HEADERS2)
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
    await client.post(
        "/memory",
        json={
            "content": "alpha entry",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    await client.post(
        "/memory",
        json={
            "content": "beta entry",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory/search", params={"q": "alpha"}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert len(results) >= 1


async def test_delta(client, mem_payload):
    await client.post("/memory", json=mem_payload, headers=HEADERS)

    r = await client.get(
        "/memory/delta", params={"since": "1970-01-01T00:00:00.000Z"}, headers=HEADERS
    )
    assert r.status_code == 200
    assert len(r.json()) >= 1


async def test_private_scope_hidden_from_others(client):
    r = await client.post(
        "/memory",
        json={
            "content": "secret",
            "type": "memory",
            "scope": "agent",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    eid = r.json()["id"]

    r2 = await client.get(f"/memory/{eid}", headers=HEADERS2)
    assert r2.status_code == 403


async def test_list_memory_by_type(client):
    await client.post(
        "/memory",
        json={
            "content": "doc entry",
            "type": "doc",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    await client.post(
        "/memory",
        json={
            "content": "memory entry",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory", params={"type": "doc"}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert all(e["type"] == "doc" for e in results)
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


async def test_list_filter_by_tag(client):
    await client.post(
        "/memory",
        json={
            "content": "tagged entry",
            "type": "memory",
            "scope": "project",
            "tags": ["deploy"],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    await client.post(
        "/memory",
        json={
            "content": "other entry",
            "type": "memory",
            "scope": "project",
            "tags": ["infra"],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory", params={"tag": "deploy"}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert len(results) == 1
    assert results[0]["tags"] == ["deploy"]


async def test_list_filter_by_agent(client):
    await client.post(
        "/memory",
        json={
            "content": "from agent1",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    await client.post(
        "/memory",
        json={
            "content": "from agent2",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS2,
    )

    r = await client.get("/memory", params={"agent": AGENT2}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert all(e["agent_id"] == AGENT2 for e in results)


async def test_list_filter_by_confidence_min(client):
    await client.post(
        "/memory",
        json={
            "content": "high confidence",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 0.9,
        },
        headers=HEADERS,
    )
    await client.post(
        "/memory",
        json={
            "content": "low confidence",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 0.3,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory", params={"confidence_min": 0.8}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert all(e["confidence"] >= 0.8 for e in results)
    assert any(e["content"] == "high confidence" for e in results)
    assert not any(e["content"] == "low confidence" for e in results)


async def test_search_filter_by_tag(client):
    await client.post(
        "/memory",
        json={
            "content": "deploy pipeline config",
            "type": "memory",
            "scope": "project",
            "tags": ["deploy"],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    await client.post(
        "/memory",
        json={
            "content": "deploy pipeline config",
            "type": "memory",
            "scope": "project",
            "tags": ["infra"],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory/search", params={"q": "deploy", "tag": "deploy"}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert all("deploy" in e["tags"] for e in results)


async def test_project_scope_no_project_visible_to_all(client, monkeypatch):
    import artel.server.config as cfg_mod

    monkeypatch.setattr(cfg_mod.settings, "agent_keys", "restricted-agent:restrictedkey:proj-a")

    import artel.store.db as db_mod

    db_mod.get_db().execute(
        "INSERT OR IGNORE INTO agents (id, api_key) VALUES (?, ?)",
        ("restricted-agent", "restrictedkey"),
    )
    db_mod.get_db().commit()

    restricted_headers = {"x-agent-id": "restricted-agent", "x-api-key": "restrictedkey"}

    await client.post(
        "/memory",
        json={
            "content": "shared with all",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    await client.post(
        "/memory",
        json={
            "content": "only proj-b members",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
            "project": "proj-b",
        },
        headers=HEADERS,
    )

    r = await client.get("/memory", headers=restricted_headers)
    assert r.status_code == 200
    contents = [e["content"] for e in r.json()]
    assert "shared with all" in contents
    assert "only proj-b members" not in contents


async def test_private_scope_hidden_from_list(client):
    await client.post(
        "/memory",
        json={
            "content": "my secret",
            "type": "memory",
            "scope": "agent",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory", headers=HEADERS2)
    assert r.status_code == 200
    assert not any(e["content"] == "my secret" for e in r.json())


async def test_soft_delete_not_in_list(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]
    await client.delete(f"/memory/{eid}", headers=HEADERS)

    r2 = await client.get("/memory", headers=HEADERS)
    assert not any(e["id"] == eid for e in r2.json())


async def test_get_memory_by_id_prefix(client):
    from tests.conftest import HEADERS

    r = await client.post(
        "/memory", json={"content": "prefix-resolvable memory entry"}, headers=HEADERS
    )
    eid = r.json()["id"]

    r2 = await client.get(f"/memory/{eid[:8]}", headers=HEADERS)
    assert r2.status_code == 200
    assert r2.json()["id"] == eid


async def test_get_memory_unknown_prefix_404(client):
    from tests.conftest import HEADERS

    r = await client.get("/memory/nosuchid", headers=HEADERS)
    assert r.status_code == 404
