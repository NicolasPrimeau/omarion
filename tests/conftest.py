import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

TEST_AGENT = "testagent"
TEST_KEY = "testkey"
HEADERS = {"x-agent-id": TEST_AGENT, "x-api-key": TEST_KEY}

AGENT2 = "otheragent"
KEY2 = "otherkey"
HEADERS2 = {"x-agent-id": AGENT2, "x-api-key": KEY2}


@pytest.fixture(autouse=True)
def mock_embed(monkeypatch):
    import artel.store.embeddings as emb

    monkeypatch.setattr(emb, "embed", lambda text: [0.0] * 384)


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    import artel.server.broadcast as bc_mod
    import artel.server.config as cfg_mod
    import artel.store.db as db_mod

    db_mod._conn = None
    bc_mod._subscribers.clear()

    test_db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(cfg_mod.settings, "db_path", test_db_path)
    monkeypatch.setattr(cfg_mod.settings, "registration_key", "regkey")

    conn = db_mod.get_db(test_db_path)
    conn.execute("INSERT INTO agents (id, api_key) VALUES (?, ?)", (TEST_AGENT, TEST_KEY))
    conn.execute("INSERT INTO agents (id, api_key) VALUES (?, ?)", (AGENT2, KEY2))
    conn.commit()

    from artel.server.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    if db_mod._conn:
        db_mod._conn.close()
        db_mod._conn = None
    bc_mod._subscribers.clear()
