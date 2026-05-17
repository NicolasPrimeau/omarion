import json
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import AsyncClient

import artel.store.db as db_mod
from artel.archivist.synthesis import decay_confidence, run_promotion, run_synthesis

ARCHIVIST_ID = "mesh-archivist"
PEER_ORIGIN = "peer-instance-deadbeef"


class _MeshArchivistClient:
    def __init__(self, http: AsyncClient):
        self._http = http

    async def get_directives(self, project=None):
        params = {"type": "directive", "scope": "project", "limit": 200}
        r = await self._http.get("/memory", params=params)
        r.raise_for_status()
        return r.json()

    async def get_delta(self, since: str) -> list[dict]:
        r = await self._http.get("/memory/delta", params={"since": since})
        r.raise_for_status()
        return r.json()

    async def list_entries(
        self, type=None, updated_before=None, min_version=None, limit=200
    ) -> list[dict]:
        params: dict = {"limit": limit}
        if type:
            params["type"] = type
        if updated_before:
            params["updated_before"] = updated_before
        if min_version is not None:
            params["min_version"] = min_version
        r = await self._http.get("/memory", params=params)
        r.raise_for_status()
        return r.json()

    async def write_memory(
        self, content, type="memory", tags=None, parents=None, confidence=1.0, project=None
    ) -> dict:
        r = await self._http.post(
            "/memory",
            json={
                "content": content,
                "type": type,
                "scope": "project",
                "tags": tags or [],
                "parents": parents or [],
                "confidence": confidence,
                "project": project,
            },
        )
        r.raise_for_status()
        return r.json()

    async def patch_memory(self, entry_id: str, **fields) -> dict:
        r = await self._http.patch(f"/memory/{entry_id}", json=fields)
        r.raise_for_status()
        return r.json()

    async def delete_memory(self, entry_id: str) -> None:
        r = await self._http.delete(f"/memory/{entry_id}")
        r.raise_for_status()

    async def get_memory(self, entry_id: str) -> dict:
        r = await self._http.get(f"/memory/{entry_id}")
        r.raise_for_status()
        return r.json()

    async def search_memory(self, q: str, limit: int = 10, max_distance=None) -> list[dict]:
        params: dict = {"q": q, "limit": limit}
        r = await self._http.get("/memory/search", params=params)
        r.raise_for_status()
        return r.json()

    async def list_tasks(self, status=None, limit=50) -> list[dict]:
        params: dict = {"limit": limit}
        if status:
            params["status"] = status
        r = await self._http.get("/tasks", params=params)
        r.raise_for_status()
        return r.json()

    async def log(self, action, message, level="info", source="archivist", details=None) -> None:
        await self._http.post(
            "/logs",
            json={
                "level": level,
                "source": source,
                "action": action,
                "message": message,
                "details": details or {},
            },
        )


@pytest_asyncio.fixture
async def mesh_scenario(scenario):
    r = await scenario._admin.post("/agents/register", json={"agent_id": ARCHIVIST_ID})
    r.raise_for_status()
    api_key = r.json()["api_key"]
    db = db_mod.get_db()
    db.execute("UPDATE agents SET role='owner' WHERE id=?", (ARCHIVIST_ID,))
    db.commit()
    http = AsyncClient(
        transport=scenario._transport,
        base_url="http://test",
        headers={"x-agent-id": ARCHIVIST_ID, "x-api-key": api_key},
    )
    client = _MeshArchivistClient(http)
    yield scenario, client
    await http.aclose()


def _stamp_peer(entry_id: str) -> None:
    db = db_mod.get_db()
    db.execute("UPDATE memory SET origin=? WHERE id=?", (PEER_ORIGIN, entry_id))
    db.commit()


def _stamp_old(entry_id: str, days: int = 30) -> None:
    db = db_mod.get_db()
    db.execute(
        "UPDATE memory SET updated_at=datetime('now', ?) WHERE id=?",
        (f"-{days} days", entry_id),
    )
    db.commit()


def _bump_version(entry_id: str, version: int) -> None:
    db = db_mod.get_db()
    db.execute("UPDATE memory SET version=? WHERE id=?", (version, entry_id))
    db.commit()


async def test_synthesis_excludes_peer_entries(mesh_scenario):
    scenario, client = mesh_scenario
    agent = await scenario.agent("alice")

    await agent.write_memory("local insight alpha", confidence=0.9)
    await agent.write_memory("local insight beta", confidence=0.9)
    peer = await agent.write_memory("peer insight replicated from remote instance", confidence=0.9)
    _stamp_peer(peer["id"])

    llm_response = json.dumps([{"op": "prune", "entry": peer["id"]}])

    with (
        patch("artel.archivist.synthesis.is_configured", return_value=True),
        patch("artel.archivist.synthesis.settings") as mock_settings,
        patch("artel.archivist.synthesis.complete", new=AsyncMock(return_value=llm_response)),
    ):
        mock_settings.archivist_id = ARCHIVIST_ID
        mock_settings.directive_conflict_threshold = 0.85
        mock_settings.decay_floor = 0.05
        await run_synthesis(client)

    after = await client.get_memory(peer["id"])
    assert after["confidence"] == 0.9
    assert "archivist-flagged" not in after.get("tags", [])


async def test_synthesis_still_operates_on_local_entries(mesh_scenario):
    scenario, client = mesh_scenario
    agent = await scenario.agent("bob")

    local_a = await agent.write_memory("local note one", confidence=0.9)
    local_b = await agent.write_memory("local note two", confidence=0.9)
    peer = await agent.write_memory("peer note replicated", confidence=0.9)
    _stamp_peer(peer["id"])

    llm_response = json.dumps(
        [{"op": "prune", "entry": local_a["id"]}, {"op": "prune", "entry": local_b["id"]}]
    )

    with (
        patch("artel.archivist.synthesis.is_configured", return_value=True),
        patch("artel.archivist.synthesis.settings") as mock_settings,
        patch("artel.archivist.synthesis.complete", new=AsyncMock(return_value=llm_response)),
    ):
        mock_settings.archivist_id = ARCHIVIST_ID
        mock_settings.directive_conflict_threshold = 0.85
        mock_settings.decay_floor = 0.05
        await run_synthesis(client)

    after_a = await client.get_memory(local_a["id"])
    after_b = await client.get_memory(local_b["id"])
    assert after_a["confidence"] < 0.9 or "archivist-flagged" in after_a.get("tags", [])
    assert after_b["confidence"] < 0.9 or "archivist-flagged" in after_b.get("tags", [])


async def test_decay_skips_peer_entries(mesh_scenario):
    scenario, client = mesh_scenario
    agent = await scenario.agent("carol")

    local = await agent.write_memory("local stale entry", confidence=0.9)
    peer = await agent.write_memory("peer stale entry replicated", confidence=0.9)
    _stamp_peer(peer["id"])
    _stamp_old(local["id"], days=30)
    _stamp_old(peer["id"], days=30)

    with patch("artel.archivist.synthesis.settings") as mock_settings:
        mock_settings.archivist_id = ARCHIVIST_ID
        mock_settings.decay_floor = 0.05
        mock_settings.decay_rate = 0.9
        mock_settings.decay_window_days = 7
        await decay_confidence(client)

    after_local = await client.get_memory(local["id"])
    after_peer = await client.get_memory(peer["id"])
    assert after_local["confidence"] < 0.9
    assert after_peer["confidence"] == 0.9


async def test_promotion_skips_peer_entries(mesh_scenario):
    scenario, client = mesh_scenario
    agent = await scenario.agent("dave")

    local = await agent.write_memory("stable local doc candidate", type="memory", confidence=1.0)
    peer = await agent.write_memory(
        "stable peer doc candidate replicated", type="memory", confidence=1.0
    )
    _stamp_peer(peer["id"])

    for entry_id in (local["id"], peer["id"]):
        _bump_version(entry_id, 5)
        _stamp_old(entry_id, days=30)

    with patch("artel.archivist.synthesis.settings") as mock_settings:
        mock_settings.archivist_id = ARCHIVIST_ID
        mock_settings.promotion_memory_min_version = 3
        mock_settings.promotion_stability_days = 7
        await run_promotion(client)

    after_local = await client.get_memory(local["id"])
    after_peer = await client.get_memory(peer["id"])
    assert after_local["type"] == "doc"
    assert after_peer["type"] == "memory"
