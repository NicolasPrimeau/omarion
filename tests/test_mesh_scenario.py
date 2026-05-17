"""End-to-end mesh convergence: two real Artel DBs, real feed.json, real poller.

Unlike tests/test_feeds.py (which feeds controlled payloads to the poller),
this drives the *actual* /memory/feed.json serialization of one instance into
the *actual* feed_poller of another, proving the replication path works wired
together — convergence, origin preservation, and idempotent (loop-free) re-poll.
"""

import json
from unittest.mock import MagicMock, patch

from httpx import ASGITransport, AsyncClient

import artel.server.broadcast as bc_mod
import artel.server.config as cfg_mod
import artel.store.db as db_mod


async def _boot(path, monkeypatch):
    db_mod._conn = None
    bc_mod._subscribers.clear()
    monkeypatch.setattr(cfg_mod.settings, "db_path", path)
    monkeypatch.setattr(cfg_mod.settings, "registration_key", "regkey")
    conn = db_mod.get_db(path)
    conn.execute("INSERT INTO agents (id, api_key) VALUES (?, ?)", ("bot", "k"))
    conn.commit()
    from artel.server.app import app

    return app, db_mod.instance_id()


def _resp(payload):
    m = MagicMock()
    m.text = json.dumps(payload)
    m.headers = {"content-type": "application/feed+json"}
    m.raise_for_status = MagicMock()
    return m


async def test_two_instance_convergence(tmp_path, monkeypatch):
    hdr = {"x-agent-id": "bot", "x-api-key": "k"}
    a_db = str(tmp_path / "a.db")
    b_db = str(tmp_path / "b.db")

    # ── Instance A: write a memory entry, capture its real peer feed ──────────
    app_a, a_iid = await _boot(a_db, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app_a), base_url="http://a") as ca:
        await ca.post("/projects/p/join", headers=hdr)
        w = await ca.post(
            "/memory",
            json={"content": "shared finding from A", "project": "p", "tags": ["x"]},
            headers=hdr,
        )
        assert w.status_code == 201
        gid = w.json()["id"]
        feed_a = (await ca.get("/memory/feed.json?project=p", headers=hdr)).json()
    # the feed must carry the replication extension with A's origin
    item = next(i for i in feed_a["items"] if i["_artel"]["memory_id"] == gid)
    assert item["_artel"]["origin"] == a_iid

    # ── Instance B: subscribe to A and poll A's real feed ────────────────────
    app_b, b_iid = await _boot(b_db, monkeypatch)
    assert b_iid != a_iid
    db_b = db_mod.get_db()
    async with AsyncClient(transport=ASGITransport(app=app_b), base_url="http://b") as cb:
        await cb.post("/projects/p/join", headers=hdr)
        db_b.execute(
            """INSERT INTO feed_subscriptions
               (id, agent_id, project, url, name, tags, interval_min, max_per_poll)
               VALUES ('f1','bot','p','http://a/memory/feed.json?project=p','mesh:A','[]',30,100)"""
        )
        db_b.commit()
        feed_row = dict(db_b.execute("SELECT * FROM feed_subscriptions").fetchone())

        from artel.server import feed_poller

        with patch("httpx.AsyncClient.get", return_value=_resp(feed_a)):
            await feed_poller._poll_feed(feed_row)

        # converged: same id, A's origin preserved (multi-hop safe)
        row = db_b.execute("SELECT id, origin, content FROM memory WHERE id=?", (gid,)).fetchone()
        assert row is not None
        assert row["origin"] == a_iid
        assert row["content"] == "shared finding from A"

        # idempotent re-poll: no amplification (loop-free)
        with patch("httpx.AsyncClient.get", return_value=_resp(feed_a)):
            await feed_poller._poll_feed(feed_row)
            await feed_poller._poll_feed(feed_row)
        n = db_b.execute("SELECT COUNT(*) FROM memory WHERE id=?", (gid,)).fetchone()[0]
        assert n == 1

        # B re-serves the entry with A's origin intact (so a third hop won't re-mint)
        feed_b = (await cb.get("/memory/feed.json?project=p", headers=hdr)).json()
        bi = next(i for i in feed_b["items"] if i["_artel"]["memory_id"] == gid)
        assert bi["_artel"]["origin"] == a_iid

    db_mod._conn = None
    bc_mod._subscribers.clear()
