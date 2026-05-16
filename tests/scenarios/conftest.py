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

    async def role_agent(self, uid: str, role: str) -> AgentHandle:
        import secrets

        import artel.store.db as db_mod

        if uid not in self._agents:
            api_key = secrets.token_urlsafe(32)
            db = db_mod.get_db()
            db.execute(
                "INSERT INTO agents (id, api_key, role) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET api_key=excluded.api_key, role=excluded.role",
                (uid, api_key, role),
            )
            db.commit()
            http = AsyncClient(
                transport=self._transport,
                base_url="http://test",
                headers={"x-agent-id": uid, "x-api-key": api_key},
            )
            self._agents[uid] = AgentHandle(uid, http)
        return self._agents[uid]

    async def owner_agent(self) -> AgentHandle:
        import artel.server.config as cfg_mod

        return await self.role_agent(cfg_mod.settings.ui_agent_id, "owner")

    async def viewer_agent(self) -> AgentHandle:
        import artel.server.config as cfg_mod

        return await self.role_agent(cfg_mod.settings.viewer_agent_id, "viewer")

    async def archivist_agent(self) -> AgentHandle:
        import artel.server.config as cfg_mod

        return await self.role_agent(cfg_mod.settings.archivist_agent_id, "archivist")

    async def admin_delete(self, agent_id: str) -> None:
        owner = await self.owner_agent()
        r = await owner._http.delete(f"/agents/{agent_id}")
        r.raise_for_status()

    async def admin_list_agents(self) -> list[dict]:
        owner = await self.owner_agent()
        r = await owner._http.get("/agents")
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
