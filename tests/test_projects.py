from tests.conftest import AGENT2, HEADERS, HEADERS2, TEST_AGENT


async def test_join_and_list_mine(client):
    r = await client.post("/projects/alpha/join", headers=HEADERS)
    assert r.status_code == 204

    r2 = await client.get("/projects/mine", headers=HEADERS)
    assert r2.status_code == 200
    project_ids = [p["project_id"] for p in r2.json()]
    assert "alpha" in project_ids


async def test_join_idempotent(client):
    await client.post("/projects/alpha/join", headers=HEADERS)
    r = await client.post("/projects/alpha/join", headers=HEADERS)
    assert r.status_code == 204


async def test_leave(client):
    await client.post("/projects/alpha/join", headers=HEADERS)
    r = await client.delete("/projects/alpha/leave", headers=HEADERS)
    assert r.status_code == 204

    r2 = await client.get("/projects/mine", headers=HEADERS)
    ids = [p["project_id"] for p in r2.json()]
    assert "alpha" not in ids


async def test_list_members(client):
    await client.post("/projects/alpha/join", headers=HEADERS)
    await client.post("/projects/alpha/join", headers=HEADERS2)

    r = await client.get("/projects/alpha/members", headers=HEADERS)
    assert r.status_code == 200
    agent_ids = [m["agent_id"] for m in r.json()]
    assert TEST_AGENT in agent_ids
    assert AGENT2 in agent_ids


async def test_list_members_requires_membership(client):
    await client.post("/projects/alpha/join", headers=HEADERS)

    r = await client.get("/projects/alpha/members", headers=HEADERS2)
    assert r.status_code == 403


async def test_project_memory_visible_to_members(client):
    await client.post("/projects/alpha/join", headers=HEADERS)
    await client.post("/projects/alpha/join", headers=HEADERS2)

    await client.post(
        "/memory",
        json={
            "content": "alpha secret",
            "type": "memory",
            "scope": "project",
            "project": "alpha",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory", headers=HEADERS2)
    assert r.status_code == 200
    contents = [e["content"] for e in r.json()]
    assert "alpha secret" in contents


async def test_project_memory_hidden_from_non_members(client):
    await client.post("/projects/alpha/join", headers=HEADERS)

    await client.post(
        "/memory",
        json={
            "content": "alpha secret",
            "type": "memory",
            "scope": "project",
            "project": "alpha",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory", headers=HEADERS2)
    assert r.status_code == 200
    contents = [e["content"] for e in r.json()]
    assert "alpha secret" not in contents


async def test_project_list_includes_members(client):
    await client.post("/projects/alpha/join", headers=HEADERS)

    r = await client.get("/projects", headers=HEADERS)
    assert r.status_code == 200
    projects = {p["name"]: p for p in r.json()}
    assert "alpha" in projects
    assert TEST_AGENT in projects["alpha"]["agents"]
