from httpx import AsyncClient


class AgentHandle:
    def __init__(self, agent_id: str, http: AsyncClient):
        self.id = agent_id
        self._http = http

    async def write_memory(self, content: str, **kwargs) -> dict:
        r = await self._http.post("/memory", json={"content": content, **kwargs})
        r.raise_for_status()
        return r.json()

    async def search_memory(self, q: str, **kwargs) -> list[dict]:
        r = await self._http.get("/memory/search", params={"q": q, **kwargs})
        r.raise_for_status()
        return r.json()

    async def list_memory(self, **kwargs) -> list[dict]:
        r = await self._http.get("/memory", params=kwargs)
        r.raise_for_status()
        return r.json()

    async def get_memory(self, entry_id: str) -> dict:
        r = await self._http.get(f"/memory/{entry_id}")
        r.raise_for_status()
        return r.json()

    async def update_memory(self, entry_id: str, **kwargs) -> dict:
        r = await self._http.patch(f"/memory/{entry_id}", json=kwargs)
        r.raise_for_status()
        return r.json()

    async def delete_memory(self, entry_id: str) -> None:
        r = await self._http.delete(f"/memory/{entry_id}")
        r.raise_for_status()

    async def send_message(self, to: str, body: str, subject: str = "") -> dict:
        r = await self._http.post("/messages", json={"to": to, "subject": subject, "body": body})
        r.raise_for_status()
        return r.json()

    async def inbox(self) -> list[dict]:
        r = await self._http.get("/messages/inbox")
        r.raise_for_status()
        return r.json()

    async def mark_inbox_read(self) -> None:
        r = await self._http.post("/messages/inbox/read-all")
        r.raise_for_status()

    async def mark_message_read(self, msg_id: str) -> dict:
        r = await self._http.post(f"/messages/{msg_id}/read")
        r.raise_for_status()
        return r.json()

    async def create_task(self, title: str, **kwargs) -> dict:
        r = await self._http.post("/tasks", json={"title": title, **kwargs})
        r.raise_for_status()
        return r.json()

    async def list_tasks(self, **kwargs) -> list[dict]:
        r = await self._http.get("/tasks", params=kwargs)
        r.raise_for_status()
        return r.json()

    async def get_task(self, task_id: str) -> dict:
        r = await self._http.get(f"/tasks/{task_id}")
        r.raise_for_status()
        return r.json()

    async def claim_task(self, task_id: str) -> dict:
        r = await self._http.post(f"/tasks/{task_id}/claim")
        r.raise_for_status()
        return r.json()

    async def complete_task(self, task_id: str) -> dict:
        r = await self._http.post(f"/tasks/{task_id}/complete")
        r.raise_for_status()
        return r.json()

    async def fail_task(self, task_id: str) -> dict:
        r = await self._http.post(f"/tasks/{task_id}/fail")
        r.raise_for_status()
        return r.json()

    async def unclaim_task(self, task_id: str) -> dict:
        r = await self._http.post(f"/tasks/{task_id}/unclaim")
        r.raise_for_status()
        return r.json()

    async def reopen_task(self, task_id: str, body: str = "") -> dict:
        r = await self._http.post(f"/tasks/{task_id}/reopen", json={"body": body})
        r.raise_for_status()
        return r.json()

    async def update_task(self, task_id: str, **kwargs) -> dict:
        r = await self._http.patch(f"/tasks/{task_id}", json=kwargs)
        r.raise_for_status()
        return r.json()

    async def save_handoff(self, summary: str, **kwargs) -> dict:
        r = await self._http.post("/sessions/handoff", json={"summary": summary, **kwargs})
        r.raise_for_status()
        return r.json()

    async def load_handoff(self) -> dict:
        r = await self._http.get(f"/sessions/handoff/{self.id}")
        r.raise_for_status()
        return r.json()

    async def join_project(self, project_id: str) -> None:
        r = await self._http.post(f"/projects/{project_id}/join")
        r.raise_for_status()

    async def leave_project(self, project_id: str) -> None:
        r = await self._http.delete(f"/projects/{project_id}/leave")
        r.raise_for_status()

    async def emit_event(self, event_type: str, payload: dict | None = None) -> dict:
        r = await self._http.post("/events", json={"type": event_type, "payload": payload or {}})
        r.raise_for_status()
        return r.json()

    async def poll_events(self, since: str, **kwargs) -> list[dict]:
        r = await self._http.get("/events", params={"since": since, **kwargs})
        r.raise_for_status()
        return r.json()

    async def participants(self) -> list[dict]:
        r = await self._http.get("/participants")
        r.raise_for_status()
        return r.json()

    async def rename(self, new_id: str) -> dict:
        r = await self._http.patch("/agents/me", json={"new_id": new_id})
        r.raise_for_status()
        self.id = new_id
        self._http.headers = dict(self._http.headers) | {"x-agent-id": new_id}
        return r.json()

    async def delete_self(self) -> None:
        r = await self._http.delete("/agents/me")
        r.raise_for_status()
