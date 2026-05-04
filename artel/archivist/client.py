import json

import httpx

from .config import settings


class ArtelClient:
    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url=settings.artel_url,
            headers={
                "x-agent-id": settings.archivist_id,
                "x-api-key": settings.archivist_key,
            },
            timeout=30.0,
        )

    async def aclose(self):
        await self._http.aclose()

    async def get_memory(self, entry_id: str) -> dict:
        r = await self._http.get(f"/memory/{entry_id}")
        r.raise_for_status()
        return r.json()

    async def search_memory(
        self, q: str, limit: int = 10, max_distance: float | None = None
    ) -> list[dict]:
        params: dict = {"q": q, "limit": limit}
        if max_distance is not None:
            params["max_distance"] = max_distance
        r = await self._http.get("/memory/search", params=params)
        r.raise_for_status()
        return r.json()

    async def write_memory(
        self,
        content: str,
        type: str = "doc",
        tags: list[str] | None = None,
        parents: list[str] | None = None,
        confidence: float = 1.0,
        project: str | None = None,
    ) -> dict:
        r = await self._http.post(
            "/memory",
            json={
                "content": content,
                "type": type,
                "scope": "shared",
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

    async def list_entries(
        self,
        type: str | None = None,
        updated_before: str | None = None,
        created_before: str | None = None,
        min_version: int | None = None,
        limit: int = 100,
    ) -> list[dict]:
        params: dict = {"limit": limit}
        if type:
            params["type"] = type
        if updated_before:
            params["updated_before"] = updated_before
        if created_before:
            params["created_before"] = created_before
        if min_version is not None:
            params["min_version"] = min_version
        r = await self._http.get("/memory", params=params)
        r.raise_for_status()
        return r.json()

    async def get_delta(self, since: str) -> list[dict]:
        r = await self._http.get("/memory/delta", params={"since": since})
        r.raise_for_status()
        return r.json()

    async def stream_events(self, event_type: str | None = None):
        params = {}
        if event_type:
            params["type"] = event_type
        async with self._http.stream("GET", "/events/stream", params=params) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:].strip()
                    if data:
                        yield json.loads(data)
