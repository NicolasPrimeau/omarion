import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import AGENT2, HEADERS, HEADERS2, KEY2, TEST_AGENT, TEST_KEY


async def _subscribe(client, project="artel", url="https://example.com/feed.xml", name="Test Feed"):
    await client.post(f"/projects/{project}/join", headers=HEADERS)
    r = await client.post(
        "/feeds",
        json={"url": url, "name": name, "project": project, "tags": ["test"]},
        headers=HEADERS,
    )
    return r


async def test_feed_subscribe_returns_entry(client):
    r = await _subscribe(client)
    assert r.status_code == 201
    data = r.json()
    assert data["url"] == "https://example.com/feed.xml"
    assert data["name"] == "Test Feed"
    assert data["project"] == "artel"
    assert data["agent_id"] == TEST_AGENT
    assert data["interval_min"] == 30
    assert data["max_per_poll"] == 20
    assert "test" in data["tags"]
    assert data["last_fetched_at"] is None


async def test_feed_subscribe_requires_project_membership(client):
    r = await client.post(
        "/feeds",
        json={"url": "https://x.com/feed.xml", "name": "X", "project": "private-project"},
        headers=HEADERS,
    )
    assert r.status_code == 403


async def test_feed_list_returns_subscriptions(client):
    await _subscribe(client, name="Feed A")
    await _subscribe(client, url="https://b.com/feed.xml", name="Feed B")
    r = await client.get("/feeds", headers=HEADERS)
    assert r.status_code == 200
    names = [f["name"] for f in r.json()]
    assert "Feed A" in names
    assert "Feed B" in names


async def test_feed_list_scoped_to_project(client):

    await client.post("/projects/proj-a/join", headers=HEADERS)
    await client.post("/projects/proj-b/join", headers=HEADERS2)
    await client.post(
        "/feeds",
        json={"url": "https://a.com/feed.xml", "name": "A feed", "project": "proj-a"},
        headers=HEADERS,
    )
    await client.post(
        "/feeds",
        json={"url": "https://b.com/feed.xml", "name": "B feed", "project": "proj-b"},
        headers=HEADERS2,
    )
    r = await client.get("/feeds", headers=HEADERS)
    names = [f["name"] for f in r.json()]
    assert "A feed" in names
    assert "B feed" not in names


async def test_feed_unsubscribe(client):
    r = await _subscribe(client)
    feed_id = r.json()["id"]
    r2 = await client.delete(f"/feeds/{feed_id}", headers=HEADERS)
    assert r2.status_code == 204
    r3 = await client.get("/feeds", headers=HEADERS)
    assert all(f["id"] != feed_id for f in r3.json())


async def test_feed_unsubscribe_not_found(client):
    r = await client.delete("/feeds/00000000-0000-0000-0000-000000000000", headers=HEADERS)
    assert r.status_code == 404


async def test_feed_unsubscribe_wrong_owner(client):
    r = await _subscribe(client)
    feed_id = r.json()["id"]
    await client.post("/projects/artel/join", headers=HEADERS2)
    r2 = await client.delete(f"/feeds/{feed_id}", headers=HEADERS2)
    assert r2.status_code == 403


async def test_feed_unsubscribe_other_agent_no_membership_forbidden(client):
    r = await _subscribe(client)
    feed_id = r.json()["id"]
    r2 = await client.delete(f"/feeds/{feed_id}", headers=HEADERS2)
    assert r2.status_code == 403


# ── Poller ────────────────────────────────────────────────────────────────────


def _make_feed_entry(title, link, guid=None, summary=""):
    e = MagicMock()
    e.get = lambda k, default="": {
        "title": title,
        "link": link,
        "id": guid or link,
        "summary": summary,
        "description": summary,
        "published": "2024-01-15",
    }.get(k, default)
    return e


def _make_parsed(entries):
    parsed = MagicMock()
    parsed.entries = entries
    return parsed


def _mock_rss_resp(text="<rss/>"):
    mock_resp = MagicMock()
    mock_resp.text = text
    mock_resp.headers = {"content-type": "application/rss+xml"}
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


async def test_poller_writes_new_items_as_memories(client):
    import artel.store.db as db_mod

    await _subscribe(client)
    db = db_mod.get_db()
    feed = dict(db.execute("SELECT * FROM feed_subscriptions").fetchone())

    entries = [
        _make_feed_entry("Issue #1", "https://example.com/1", summary="First issue"),
        _make_feed_entry("Issue #2", "https://example.com/2", summary="Second issue"),
    ]
    parsed = _make_parsed(entries)

    from artel.server import feed_poller

    with (
        patch("artel.server.feed_poller.feedparser.parse", return_value=parsed),
        patch("httpx.AsyncClient.get") as mock_get,
    ):
        mock_get.return_value = _mock_rss_resp()
        await feed_poller._poll_feed(feed)

    memories = db.execute(
        "SELECT content, confidence FROM memory WHERE agent_id=? AND project='artel'",
        (TEST_AGENT,),
    ).fetchall()
    assert len(memories) == 2
    assert all(m["confidence"] == 0.5 for m in memories)
    contents = [m["content"] for m in memories]
    assert any("Issue #1" in c for c in contents)
    assert any("Issue #2" in c for c in contents)


async def test_poller_skips_seen_items(client):
    import artel.store.db as db_mod
    from artel.server import feed_poller

    await _subscribe(client)
    db = db_mod.get_db()
    feed = dict(db.execute("SELECT * FROM feed_subscriptions").fetchone())

    entries = [_make_feed_entry("Old issue", "https://example.com/old")]
    parsed = _make_parsed(entries)

    db.execute(
        "INSERT INTO feed_items_seen (feed_id, item_guid) VALUES (?,?)",
        (feed["id"], "https://example.com/old"),
    )
    db.commit()

    with (
        patch("artel.server.feed_poller.feedparser.parse", return_value=parsed),
        patch("httpx.AsyncClient.get") as mock_get,
    ):
        mock_get.return_value = _mock_rss_resp()
        await feed_poller._poll_feed(feed)

    count = db.execute("SELECT COUNT(*) FROM memory WHERE agent_id=?", (TEST_AGENT,)).fetchone()[0]
    assert count == 0


async def test_poller_respects_max_per_poll(client):
    import artel.store.db as db_mod
    from artel.server import feed_poller

    await client.post("/projects/artel/join", headers=HEADERS)
    await client.post(
        "/feeds",
        json={"url": "https://x.com/feed.xml", "name": "X", "project": "artel", "max_per_poll": 2},
        headers=HEADERS,
    )
    feed = dict(db_mod.get_db().execute("SELECT * FROM feed_subscriptions").fetchone())

    entries = [_make_feed_entry(f"Issue #{i}", f"https://x.com/{i}") for i in range(5)]
    parsed = _make_parsed(entries)

    with (
        patch("artel.server.feed_poller.feedparser.parse", return_value=parsed),
        patch("httpx.AsyncClient.get") as mock_get,
    ):
        mock_get.return_value = _mock_rss_resp()
        await feed_poller._poll_feed(feed)

    count = (
        db_mod.get_db()
        .execute("SELECT COUNT(*) FROM memory WHERE agent_id=?", (TEST_AGENT,))
        .fetchone()[0]
    )
    assert count == 2


async def test_poller_updates_last_fetched_at(client):
    import artel.store.db as db_mod
    from artel.server import feed_poller

    await _subscribe(client)
    db = db_mod.get_db()
    feed = dict(db.execute("SELECT * FROM feed_subscriptions").fetchone())
    assert feed["last_fetched_at"] is None

    parsed = _make_parsed([])

    with (
        patch("artel.server.feed_poller.feedparser.parse", return_value=parsed),
        patch("httpx.AsyncClient.get") as mock_get,
    ):
        mock_get.return_value = _mock_rss_resp()
        await feed_poller._poll_feed(feed)

    updated = db.execute(
        "SELECT last_fetched_at FROM feed_subscriptions WHERE id=?", (feed["id"],)
    ).fetchone()
    assert updated["last_fetched_at"] is not None


async def test_poller_tags_items_with_feed_item_and_unprocessed(client):
    import artel.store.db as db_mod
    from artel.server import feed_poller

    await _subscribe(client, name="Claude Code")
    db = db_mod.get_db()
    feed = dict(db.execute("SELECT * FROM feed_subscriptions").fetchone())

    entries = [_make_feed_entry("v1.0.0 released", "https://example.com/v1")]
    parsed = _make_parsed(entries)

    with (
        patch("artel.server.feed_poller.feedparser.parse", return_value=parsed),
        patch("httpx.AsyncClient.get") as mock_get,
    ):
        mock_get.return_value = _mock_rss_resp()
        await feed_poller._poll_feed(feed)

    row = db.execute("SELECT tags FROM memory WHERE agent_id=?", (TEST_AGENT,)).fetchone()
    tags = json.loads(row["tags"])
    assert "feed-item" in tags
    assert "unprocessed" in tags
    assert "test" in tags


# ── Outbound feed endpoints ───────────────────────────────────────────────────


async def test_memory_feed_atom_returns_entries(client):
    import xml.etree.ElementTree as ET

    await client.post(
        "/memory",
        json={"content": "test atom entry\nsecond line", "tags": ["t1"]},
        headers=HEADERS,
    )
    r = await client.get("/memory/feed.atom", headers=HEADERS)
    assert r.status_code == 200
    assert "atom+xml" in r.headers["content-type"]

    root = ET.fromstring(r.content)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall("a:entry", ns)
    assert len(entries) == 1
    assert entries[0].find("a:title", ns).text == "test atom entry"
    assert entries[0].find("a:content", ns).text == "test atom entry\nsecond line"
    assert entries[0].find("a:author/a:name", ns).text == TEST_AGENT


async def test_memory_feed_json_returns_entries(client):
    await client.post(
        "/memory",
        json={"content": "test json entry", "tags": ["t1", "t2"]},
        headers=HEADERS,
    )
    r = await client.get("/memory/feed.json", headers=HEADERS)
    assert r.status_code == 200
    assert "feed+json" in r.headers["content-type"]

    data = r.json()
    assert data["version"] == "https://jsonfeed.org/version/1.1"
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["content_text"] == "test json entry"
    assert item["tags"] == ["t1", "t2"]
    assert item["authors"] == [{"name": TEST_AGENT}]
    assert item["_artel"]["type"] == "memory"
    assert item["_artel"]["confidence"] == 1.0


async def test_memory_feed_auth_via_query_params(client):
    await client.post("/memory", json={"content": "cross-artel test"}, headers=HEADERS)
    r = await client.get(f"/memory/feed.json?agent_id={TEST_AGENT}&api_key={TEST_KEY}")
    assert r.status_code == 200
    data = r.json()
    assert any(i["content_text"] == "cross-artel test" for i in data["items"])


async def test_memory_feed_atom_requires_auth(client):
    r = await client.get("/memory/feed.atom")
    assert r.status_code == 401


async def test_memory_feed_json_requires_auth(client):
    r = await client.get("/memory/feed.json")
    assert r.status_code == 401


async def test_memory_feed_tag_filter(client):
    await client.post("/memory", json={"content": "tagged A", "tags": ["alpha"]}, headers=HEADERS)
    await client.post("/memory", json={"content": "tagged B", "tags": ["beta"]}, headers=HEADERS)

    r = await client.get("/memory/feed.json?tag=alpha", headers=HEADERS)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["content_text"] == "tagged A"


async def test_poller_ingests_json_feed(client):
    import artel.store.db as db_mod
    from artel.server import feed_poller

    await _subscribe(client)
    db = db_mod.get_db()
    feed = dict(db.execute("SELECT * FROM feed_subscriptions").fetchone())

    json_payload = json.dumps(
        {
            "version": "https://jsonfeed.org/version/1.1",
            "title": "Artel Memory / artel",
            "items": [
                {
                    "id": "https://other.artel/memory/abc",
                    "title": "Cross-Artel entry",
                    "content_text": "learned from another artel instance",
                    "date_published": "2026-05-16T00:00:00Z",
                    "url": "https://other.artel/memory/abc",
                }
            ],
        }
    )

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = json_payload
        mock_resp.headers = {"content-type": "application/feed+json"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        await feed_poller._poll_feed(feed)

    memories = db.execute(
        "SELECT content FROM memory WHERE agent_id=? AND project='artel'", (TEST_AGENT,)
    ).fetchall()
    assert len(memories) == 1
    assert "Cross-Artel entry" in memories[0]["content"]
    assert "learned from another artel instance" in memories[0]["content"]


async def test_poller_deduplicates_json_feed_items(client):
    import artel.store.db as db_mod
    from artel.server import feed_poller

    await _subscribe(client)
    db = db_mod.get_db()
    feed = dict(db.execute("SELECT * FROM feed_subscriptions").fetchone())

    json_payload = json.dumps(
        {
            "version": "https://jsonfeed.org/version/1.1",
            "title": "Test",
            "items": [
                {
                    "id": "https://other.artel/memory/seen",
                    "title": "Already seen",
                    "content_text": "old",
                }
            ],
        }
    )
    db.execute(
        "INSERT INTO feed_items_seen (feed_id, item_guid) VALUES (?,?)",
        (feed["id"], "https://other.artel/memory/seen"),
    )
    db.commit()

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = json_payload
        mock_resp.headers = {"content-type": "application/feed+json"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        await feed_poller._poll_feed(feed)

    count = db.execute("SELECT COUNT(*) FROM memory WHERE agent_id=?", (TEST_AGENT,)).fetchone()[0]
    assert count == 0


# ── MCP tools ─────────────────────────────────────────────────────────────────


async def test_mcp_feed_subscribe(mcp):
    import artel.mcp.server as mcp_mod

    result = await mcp_mod.feed_subscribe(
        url="https://example.com/feed.xml",
        name="Claude Code",
        project="artel",
    )
    assert "subscribed [" in result
    assert "Claude Code" in result
    assert "project=artel" in result


async def test_mcp_feed_list(mcp):
    import artel.mcp.server as mcp_mod

    await mcp_mod.feed_subscribe(
        url="https://example.com/feed.xml", name="Claude Code", project="artel"
    )
    result = await mcp_mod.feed_list()
    assert "Claude Code" in result
    assert "artel" in result


async def test_mcp_feed_unsubscribe(mcp):
    import artel.mcp.server as mcp_mod

    sub = await mcp_mod.feed_subscribe(
        url="https://example.com/feed.xml", name="Claude Code", project="artel"
    )
    feed_id = sub.split("[")[1].split("]")[0]
    result = await mcp_mod.feed_unsubscribe(feed_id)
    assert "unsubscribed" in result

    listed = await mcp_mod.feed_list()
    assert "Claude Code" not in listed


@pytest.fixture
def mcp(tmp_path, monkeypatch):
    import artel.mcp.server as mcp_mod
    import artel.server.broadcast as bc_mod
    import artel.server.config as cfg_mod
    import artel.store.db as db_mod

    db_mod._conn = None
    bc_mod._subscribers.clear()
    test_db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(cfg_mod.settings, "db_path", test_db_path)
    monkeypatch.setattr(cfg_mod.settings, "registration_key", "regkey")
    monkeypatch.setattr(mcp_mod.settings, "mcp_agent_id", TEST_AGENT)

    conn = db_mod.get_db(test_db_path)
    conn.execute("INSERT INTO agents (id, api_key) VALUES (?, ?)", (TEST_AGENT, TEST_KEY))
    conn.execute("INSERT INTO agents (id, api_key) VALUES (?, ?)", (AGENT2, KEY2))
    conn.execute(
        "INSERT INTO project_members (project_id, agent_id) VALUES (?, ?)", ("artel", TEST_AGENT)
    )
    conn.commit()

    from artel.server.app import app

    def test_http():
        return AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"x-agent-id": TEST_AGENT, "x-api-key": TEST_KEY},
            timeout=30.0,
        )

    monkeypatch.setattr(mcp_mod, "_http", test_http)
    yield mcp_mod
    if db_mod._conn:
        db_mod._conn.close()
        db_mod._conn = None
    bc_mod._subscribers.clear()
