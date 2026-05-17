from tests.conftest import HEADERS, HEADERS2, TEST_AGENT


def _make_owner(agent_id=TEST_AGENT):
    import artel.store.db as db_mod

    db = db_mod.get_db()
    db.execute("UPDATE agents SET role='owner' WHERE id=?", (agent_id,))
    db.commit()


async def _link(client, project="mesh-proj", peer="https://peer.example.com"):
    await client.post(f"/projects/{project}/join", headers=HEADERS)
    return await client.post(
        "/mesh/peers",
        json={
            "peer_url": peer,
            "project": project,
            "peer_agent_id": "remote-bot",
            "peer_api_key": "remote-secret",
        },
        headers=HEADERS,
    )


async def test_link_peer_creates_subscription(client):
    import artel.store.db as db_mod

    _make_owner()
    r = await _link(client)
    assert r.status_code == 201
    link = r.json()
    assert link["peer_url"] == "https://peer.example.com"
    assert link["project"] == "mesh-proj"

    db = db_mod.get_db()
    feed = db.execute("SELECT * FROM feed_subscriptions WHERE id=?", (link["feed_id"],)).fetchone()
    assert feed is not None
    assert "/memory/feed.json" in feed["url"]
    assert "project=mesh-proj" in feed["url"]
    assert "agent_id=remote-bot" in feed["url"]
    assert "api_key=remote-secret" in feed["url"]


async def test_link_peer_requires_owner(client):
    # AGENT2 has default 'agent' role
    await client.post("/projects/mesh-proj/join", headers=HEADERS2)
    r = await client.post(
        "/mesh/peers",
        json={
            "peer_url": "https://peer.example.com",
            "project": "mesh-proj",
            "peer_agent_id": "x",
            "peer_api_key": "y",
        },
        headers=HEADERS2,
    )
    assert r.status_code == 403


async def test_link_peer_rejects_non_http(client):
    _make_owner()
    await client.post("/projects/mesh-proj/join", headers=HEADERS)
    r = await client.post(
        "/mesh/peers",
        json={
            "peer_url": "ftp://nope",
            "project": "mesh-proj",
            "peer_agent_id": "x",
            "peer_api_key": "y",
        },
        headers=HEADERS,
    )
    assert r.status_code == 422


async def test_list_peers_does_not_leak_api_key(client):
    _make_owner()
    await _link(client)
    r = await client.get("/mesh/peers", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert "api_key" not in body[0]
    assert "peer_api_key" not in body[0]


async def test_unlink_peer_detaches(client):
    import artel.store.db as db_mod

    _make_owner()
    link = (await _link(client)).json()
    r = await client.delete(f"/mesh/peers/{link['id']}", headers=HEADERS)
    assert r.status_code == 204

    db = db_mod.get_db()
    assert (
        db.execute("SELECT COUNT(*) FROM peer_links WHERE id=?", (link["id"],)).fetchone()[0] == 0
    )
    assert (
        db.execute(
            "SELECT COUNT(*) FROM feed_subscriptions WHERE id=?", (link["feed_id"],)
        ).fetchone()[0]
        == 0
    )


async def test_unlink_unknown_404(client):
    _make_owner()
    r = await client.delete("/mesh/peers/nope", headers=HEADERS)
    assert r.status_code == 404
