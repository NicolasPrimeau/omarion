import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from .agent import AgentHandle


class Scenario:
    """
    Orchestrates multiple named agents against a shared in-process Artel server.

    Each call to agent("name") registers the agent on first use and returns a
    typed handle with methods for every Artel primitive.
    """

    def __init__(self, transport: ASGITransport):
        self._transport = transport
        self._agents: dict[str, AgentHandle] = {}
        self._admin = AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"x-registration-key": "regkey"},
        )

    async def agent(self, agent_id: str) -> AgentHandle:
        if agent_id not in self._agents:
            r = await self._admin.post("/agents/register", json={"agent_id": agent_id})
            r.raise_for_status()
            api_key = r.json()["api_key"]
            http = AsyncClient(
                transport=self._transport,
                base_url="http://test",
                headers={"x-agent-id": agent_id, "x-api-key": api_key},
            )
            self._agents[agent_id] = AgentHandle(agent_id, http)
        return self._agents[agent_id]

    async def promote_admin(self, agent_id: str) -> None:
        from artel.store.db import get_db

        db = get_db()
        db.execute("UPDATE agents SET role='admin' WHERE id=?", (agent_id,))
        db.commit()

    async def admin_delete(self, agent_id: str) -> None:
        r = await self._admin.delete(f"/agents/{agent_id}")
        r.raise_for_status()

    async def admin_list_agents(self) -> list[dict]:
        r = await self._admin.get("/agents")
        r.raise_for_status()
        return r.json()

    async def aclose(self) -> None:
        await self._admin.aclose()
        for handle in self._agents.values():
            await handle._http.aclose()


@pytest_asyncio.fixture
async def scenario(tmp_path, monkeypatch):
    import artel.server.broadcast as bc_mod
    import artel.server.config as cfg_mod
    import artel.store.db as db_mod
    import artel.store.embeddings as emb

    monkeypatch.setattr(emb, "embed", lambda text: [0.0] * 384)

    db_mod._conn = None
    bc_mod._subscribers.clear()

    db_path = str(tmp_path / "scenario.db")
    monkeypatch.setattr(cfg_mod.settings, "db_path", db_path)
    monkeypatch.setattr(cfg_mod.settings, "registration_key", "regkey")
    object.__setattr__(cfg_mod.settings, "_keys_cache", None)
    object.__setattr__(cfg_mod.settings, "_projects_cache", None)

    db_mod.get_db(db_path)

    from artel.server.app import app

    sc = Scenario(ASGITransport(app=app))
    yield sc
    await sc.aclose()

    if db_mod._conn:
        db_mod._conn.close()
        db_mod._conn = None
    bc_mod._subscribers.clear()
