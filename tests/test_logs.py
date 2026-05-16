import pytest

from tests.conftest import HEADERS

OWNER_AGENT = "ownerboss"
OWNER_KEY = "ownerkey"
OWNER_HEADERS = {"x-agent-id": OWNER_AGENT, "x-api-key": OWNER_KEY}


@pytest.fixture
async def owner_client(client):
    import artel.store.db as db_mod

    db = db_mod.get_db()
    db.execute(
        "INSERT INTO agents (id, api_key, role) VALUES (?,?,?)",
        (OWNER_AGENT, OWNER_KEY, "owner"),
    )
    db.commit()
    return client


async def test_write_log_returns_entry(client):
    r = await client.post(
        "/logs",
        json={
            "level": "info",
            "source": "archivist",
            "action": "synthesis",
            "message": "pass done",
        },
        headers=HEADERS,
    )
    assert r.status_code == 201
    d = r.json()
    assert d["level"] == "info"
    assert d["source"] == "archivist"
    assert d["action"] == "synthesis"
    assert d["message"] == "pass done"
    assert d["details"] == {}
    assert "id" in d
    assert "created_at" in d


async def test_write_log_with_details(client):
    r = await client.post(
        "/logs",
        json={
            "level": "warning",
            "source": "poller",
            "action": "feed_poll",
            "message": "fetch failed",
            "details": {"feed_id": "abc", "count": 0},
        },
        headers=HEADERS,
    )
    assert r.status_code == 201
    assert r.json()["details"] == {"feed_id": "abc", "count": 0}


async def test_write_log_requires_agent_role(client):
    import artel.store.db as db_mod

    db = db_mod.get_db()
    db.execute(
        "INSERT INTO agents (id, api_key, role) VALUES (?,?,?)", ("viewer1", "vkey1", "viewer")
    )
    db.commit()
    r = await client.post(
        "/logs",
        json={"level": "info", "source": "archivist", "action": "synthesis", "message": "x"},
        headers={"x-agent-id": "viewer1", "x-api-key": "vkey1"},
    )
    assert r.status_code == 403


async def test_list_logs_requires_owner(client):
    await client.post(
        "/logs",
        json={"level": "info", "source": "archivist", "action": "synthesis", "message": "x"},
        headers=HEADERS,
    )
    r = await client.get("/logs", headers=HEADERS)
    assert r.status_code == 403


async def test_list_logs_owner_sees_all(owner_client):
    client = owner_client
    for i in range(3):
        await client.post(
            "/logs",
            json={
                "level": "info",
                "source": "archivist",
                "action": "synthesis",
                "message": f"pass {i}",
            },
            headers=HEADERS,
        )
    r = await client.get("/logs", headers=OWNER_HEADERS)
    assert r.status_code == 200
    assert len(r.json()) == 3


async def test_list_logs_filter_by_level(owner_client):
    client = owner_client
    await client.post(
        "/logs",
        json={"level": "info", "source": "archivist", "action": "synthesis", "message": "ok"},
        headers=HEADERS,
    )
    await client.post(
        "/logs",
        json={"level": "warning", "source": "poller", "action": "feed_poll", "message": "warn"},
        headers=HEADERS,
    )
    r = await client.get("/logs?level=warning", headers=OWNER_HEADERS)
    assert r.status_code == 200
    rows = r.json()
    assert all(e["level"] == "warning" for e in rows)
    assert len(rows) == 1


async def test_list_logs_filter_by_source(owner_client):
    client = owner_client
    await client.post(
        "/logs",
        json={"level": "info", "source": "archivist", "action": "synthesis", "message": "x"},
        headers=HEADERS,
    )
    await client.post(
        "/logs",
        json={"level": "info", "source": "poller", "action": "feed_poll", "message": "y"},
        headers=HEADERS,
    )
    r = await client.get("/logs?source=poller", headers=OWNER_HEADERS)
    assert r.status_code == 200
    rows = r.json()
    assert all(e["source"] == "poller" for e in rows)
    assert len(rows) == 1


async def test_list_logs_filter_by_action(owner_client):
    client = owner_client
    await client.post(
        "/logs",
        json={"level": "info", "source": "archivist", "action": "synthesis", "message": "x"},
        headers=HEADERS,
    )
    await client.post(
        "/logs",
        json={"level": "info", "source": "archivist", "action": "triage", "message": "y"},
        headers=HEADERS,
    )
    r = await client.get("/logs?action=triage", headers=OWNER_HEADERS)
    assert r.status_code == 200
    rows = r.json()
    assert all(e["action"] == "triage" for e in rows)
    assert len(rows) == 1


async def test_list_logs_most_recent_first(owner_client):
    client = owner_client
    for msg in ["first", "second", "third"]:
        await client.post(
            "/logs",
            json={"level": "info", "source": "archivist", "action": "synthesis", "message": msg},
            headers=HEADERS,
        )
    r = await client.get("/logs", headers=OWNER_HEADERS)
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["message"] == "third"


async def test_list_logs_limit(owner_client):
    client = owner_client
    for i in range(10):
        await client.post(
            "/logs",
            json={
                "level": "info",
                "source": "archivist",
                "action": "synthesis",
                "message": f"m{i}",
            },
            headers=HEADERS,
        )
    r = await client.get("/logs?limit=3", headers=OWNER_HEADERS)
    assert r.status_code == 200
    assert len(r.json()) == 3
