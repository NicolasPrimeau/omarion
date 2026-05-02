import json

import httpx

from .config import settings


class OmarionClient:
    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url=settings.omarion_url,
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

    async def search_memory(self, q: str, limit: int = 10) -> list[dict]:
        r = await self._http.get("/memory/search", params={"q": q, "limit": limit})
        r.raise_for_status()
        return r.json()

    async def write_memory(
        self,
        content: str,
        type: str = "doc",
        tags: list[str] | None = None,
        parents: list[str] | None = None,
        confidence: float = 1.0,
    ) -> dict:
        r = await self._http.post("/memory", json={
            "content": content,
            "type": type,
            "scope": "shared",
            "tags": tags or [],
            "parents": parents or [],
            "confidence": confidence,
        })
        r.raise_for_status()
        return r.json()

    async def patch_memory(self, entry_id: str, **kwargs) -> dict:
        r = await self._http.patch(f"/memory/{entry_id}", json=kwargs)
        r.raise_for_status()
        return r.json()

    async def delete_memory(self, entry_id: str) -> None:
        r = await self._http.delete(f"/memory/{entry_id}")
        r.raise_for_status()

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
