import asyncio
import json
import logging

import httpx

from .config import settings

log = logging.getLogger(__name__)


class ArtelClient:
    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url=settings.artel_url,
            headers={
                "x-agent-id": settings.archivist_id,
                "x-api-key": settings.api_key(),
            },
            timeout=30.0,
        )

    async def aclose(self):
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(3):
            try:
                r = await self._http.request(method, path, **kwargs)
                r.raise_for_status()
                return r
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:
                    raise
                last_exc = e
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
            if attempt < 2:
                delay = 2.0**attempt
                log.warning(
                    "request %s %s failed, retrying in %.0fs: %s", method, path, delay, last_exc
                )
                await asyncio.sleep(delay)
        raise last_exc

    async def get_memory(self, entry_id: str) -> dict:
        r = await self._request("GET", f"/memory/{entry_id}")
        return r.json()

    async def search_memory(
        self, q: str, limit: int = 10, max_distance: float | None = None
    ) -> list[dict]:
        params: dict = {"q": q, "limit": limit}
        if max_distance is not None:
            params["max_distance"] = max_distance
        r = await self._request("GET", "/memory/search", params=params)
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
        r = await self._request(
            "POST",
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
        return r.json()

    async def patch_memory(self, entry_id: str, **fields) -> dict:
        r = await self._request("PATCH", f"/memory/{entry_id}", json=fields)
        return r.json()

    async def delete_memory(self, entry_id: str) -> None:
        await self._request("DELETE", f"/memory/{entry_id}")

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
        r = await self._request("GET", "/memory", params=params)
        return r.json()

    async def get_directives(self, project: str | None = None) -> list[dict]:
        results: list[dict] = []
        project_params: dict = {"type": "directive", "scope": "project", "limit": 200}
        if project:
            project_params["project"] = project
        r = await self._request("GET", "/memory", params=project_params)
        results.extend(r.json())
        agent_params: dict = {"type": "directive", "scope": "agent", "limit": 200}
        r = await self._request("GET", "/memory", params=agent_params)
        results.extend(r.json())
        return results

    async def get_delta(self, since: str) -> list[dict]:
        r = await self._request("GET", "/memory/delta", params={"since": since})
        return r.json()

    async def get_task(self, task_id: str) -> dict:
        r = await self._request("GET", f"/tasks/{task_id}")
        return r.json()

    async def list_tasks(self, status: str | None = None, limit: int = 50) -> list[dict]:
        params: dict = {"limit": limit}
        if status:
            params["status"] = status
        r = await self._request("GET", "/tasks", params=params)
        return r.json()

    async def create_task(
        self,
        title: str,
        description: str | None = None,
        priority: str = "medium",
        project: str | None = None,
    ) -> dict:
        r = await self._request(
            "POST",
            "/tasks",
            json={
                "title": title,
                "description": description,
                "priority": priority,
                "project": project,
            },
        )
        return r.json()

    async def add_task_comment(self, task_id: str, body: str) -> dict:
        r = await self._request("POST", f"/tasks/{task_id}/comments", json={"body": body})
        return r.json()

    async def send_message(self, to: str, subject: str, body: str) -> dict:
        r = await self._request(
            "POST",
            "/messages",
            json={"to": to, "subject": subject, "body": body},
        )
        return r.json()

    async def log(
        self,
        action: str,
        message: str,
        level: str = "info",
        source: str = "archivist",
        details: dict | None = None,
    ) -> None:
        try:
            await self._request(
                "POST",
                "/logs",
                json={
                    "level": level,
                    "source": source,
                    "action": action,
                    "message": message,
                    "details": details or {},
                },
            )
        except Exception as e:
            log.warning("could not write archivist log: %s", e)

    async def stream_events(self, event_type: str | None = None):
        params = {}
        if event_type:
            params["type"] = event_type
        async with self._http.stream("GET", "/events/stream", params=params) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:].strip()
                    if data:
                        yield json.loads(data)
