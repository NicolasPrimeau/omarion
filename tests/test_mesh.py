from tests.conftest import HEADERS, HEADERS2, TEST_AGENT


def _make_owner(agent_id=TEST_AGENT):
    import artel.store.db as db_mod

    db = db_mod.get_db()
    db.execute("UPDATE agents SET role='owner' WHERE id=?", (agent_id,))
    db.commit()


async def _create_token(client, project=None, label=None):
    _make_owner()
    return await client.post(
        "/mesh/tokens",
        json={"project": project, "label": label},
        headers=HEADERS,
    )


async def _link(client, project=None, peer="https://peer.example.com", peer_token="tok123"):
    return await client.post(
        "/mesh/peers",
        json={"peer_url": peer, "peer_token": peer_token, "project": project},
        headers=HEADERS,
    )


# ── Token CRUD ────────────────────────────────────────────────────────────────


async def test_create_token_returns_secret(client):
    r = await _create_token(client)
    assert r.status_code == 201
    body = r.json()
    assert "token" in body
    assert len(body["token"]) > 16
    assert body["project"] is None
    assert body["label"] is None


async def test_create_token_with_project(client):
    r = await _create_token(client, project="artel", label="ci")
    assert r.status_code == 201
    body = r.json()
    assert body["project"] == "artel"
    assert body["label"] == "ci"


async def test_list_tokens(client):
    await _create_token(client)
    await _create_token(client, project="p2")
    r = await client.get("/mesh/tokens", headers=HEADERS)
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_update_token_label(client):
    tok = (await _create_token(client, label="old")).json()
    r = await client.patch(f"/mesh/tokens/{tok['id']}", json={"label": "new"}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["label"] == "new"


async def test_revoke_token(client):
    tok = (await _create_token(client)).json()
    r = await client.delete(f"/mesh/tokens/{tok['id']}", headers=HEADERS)
    assert r.status_code == 204
    r2 = await client.get("/mesh/tokens", headers=HEADERS)
    assert len(r2.json()) == 0


async def test_token_requires_owner(client):
    r = await client.post("/mesh/tokens", json={}, headers=HEADERS2)
    assert r.status_code == 403


async def test_feed_json_accepts_mesh_token(client):

    tok = (await _create_token(client)).json()
    r = await client.get(f"/memory/feed.json?mesh_token={tok['token']}")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data


async def test_feed_json_rejects_bad_token(client):
    r = await client.get("/memory/feed.json?mesh_token=notavalidtoken")
    assert r.status_code == 401


async def test_scoped_token_restricts_project(client):
    tok = (await _create_token(client, project="proj-a")).json()
    r = await client.get(f"/memory/feed.json?mesh_token={tok['token']}&project=proj-b")
    assert r.status_code == 200
    assert r.json()["items"] == []


async def test_scoped_token_returns_entries_for_own_project(client):
    await client.post("/projects/proj-a/join", headers=HEADERS)
    r = await client.post(
        "/memory",
        json={"content": "proj-a fact", "project": "proj-a", "scope": "project"},
        headers=HEADERS,
    )
    assert r.status_code == 201
    tok = (await _create_token(client, project="proj-a")).json()
    r = await client.get(f"/memory/feed.json?mesh_token={tok['token']}")
    assert r.status_code == 200
    ids = [i["_artel"]["memory_id"] for i in r.json()["items"]]
    assert len(ids) == 1


async def test_unscoped_token_sees_all_projects(client):
    for proj in ("alpha", "beta"):
        await client.post(f"/projects/{proj}/join", headers=HEADERS)
        r = await client.post(
            "/memory",
            json={"content": f"fact in {proj}", "project": proj, "scope": "project"},
            headers=HEADERS,
        )
        assert r.status_code == 201
    tok = (await _create_token(client)).json()
    r = await client.get(f"/memory/feed.json?mesh_token={tok['token']}")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 2


async def test_revoked_token_rejected_by_feed(client):
    tok = (await _create_token(client)).json()
    await client.delete(f"/mesh/tokens/{tok['id']}", headers=HEADERS)
    r = await client.get(f"/memory/feed.json?mesh_token={tok['token']}")
    assert r.status_code == 401


async def test_feed_atom_accepts_mesh_token(client):
    tok = (await _create_token(client)).json()
    r = await client.get(f"/memory/feed.atom?mesh_token={tok['token']}")
    assert r.status_code == 200
    assert "xml" in r.headers["content-type"]


async def test_token_list_requires_owner(client):
    r = await client.get("/mesh/tokens", headers=HEADERS2)
    assert r.status_code == 403


async def test_token_patch_requires_owner(client):
    tok = (await _create_token(client, label="x")).json()
    r = await client.patch(f"/mesh/tokens/{tok['id']}", json={"label": "y"}, headers=HEADERS2)
    assert r.status_code == 403


async def test_token_delete_requires_owner(client):
    tok = (await _create_token(client)).json()
    r = await client.delete(f"/mesh/tokens/{tok['id']}", headers=HEADERS2)
    assert r.status_code == 403


# ── Peer links ────────────────────────────────────────────────────────────────


async def test_link_peer_creates_subscription(client):
    import artel.store.db as db_mod

    _make_owner()
    r = await _link(client, project="mesh-proj", peer_token="secret-tok")
    assert r.status_code == 201
    link = r.json()
    assert link["peer_url"] == "https://peer.example.com"
    assert link["project"] == "mesh-proj"

    db = db_mod.get_db()
    feed = db.execute("SELECT * FROM feed_subscriptions WHERE id=?", (link["feed_id"],)).fetchone()
    assert feed is not None
    assert "/memory/feed.json" in feed["url"]
    assert "mesh_token=secret-tok" in feed["url"]
    assert "project=mesh-proj" in feed["url"]
    assert "agent_id" not in feed["url"]


async def test_link_peer_no_project_omits_project_param(client):
    import artel.store.db as db_mod

    _make_owner()
    r = await _link(client, project=None, peer_token="tok")
    assert r.status_code == 201
    db = db_mod.get_db()
    feed = db.execute(
        "SELECT * FROM feed_subscriptions WHERE id=?", (r.json()["feed_id"],)
    ).fetchone()
    assert "project=" not in feed["url"]


async def test_link_peer_requires_owner(client):
    r = await client.post(
        "/mesh/peers",
        json={"peer_url": "https://peer.example.com", "peer_token": "x"},
        headers=HEADERS2,
    )
    assert r.status_code == 403


async def test_link_peer_rejects_non_http(client):
    _make_owner()
    r = await client.post(
        "/mesh/peers",
        json={"peer_url": "ftp://nope", "peer_token": "x"},
        headers=HEADERS,
    )
    assert r.status_code == 422


async def test_list_peers_does_not_leak_token(client):
    _make_owner()
    await _link(client)
    r = await client.get("/mesh/peers", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert "peer_token" not in body[0]
    assert "token" not in body[0]


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
