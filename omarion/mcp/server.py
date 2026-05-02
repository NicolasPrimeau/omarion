import httpx
from mcp.server.fastmcp import FastMCP

from .config import settings

mcp = FastMCP("omarion", host=settings.mcp_host, port=settings.mcp_port)

_HEADERS = {
    "x-agent-id": settings.mcp_agent_id,
    "x-api-key": settings.mcp_agent_key,
}


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=settings.omarion_url, headers=_HEADERS, timeout=30.0)


@mcp.tool()
async def memory_write(
    content: str,
    type: str = "memory",
    project: str | None = None,
    tags: list[str] | None = None,
    confidence: float = 1.0,
) -> str:
    async with _http() as c:
        r = await c.post("/memory", json={
            "content": content,
            "type": type,
            "project": project,
            "scope": "shared",
            "tags": tags or [],
            "confidence": confidence,
        })
        r.raise_for_status()
        return r.json()["id"]


@mcp.tool()
async def memory_search(q: str, project: str | None = None, limit: int = 10) -> str:
    async with _http() as c:
        params: dict = {"q": q, "limit": min(limit, 50)}
        if project:
            params["project"] = project
        r = await c.get("/memory/search", params=params)
        r.raise_for_status()
        entries = r.json()
    if not entries:
        return "No results."
    return "\n".join(
        f"[{e['id']}] ({e['agent_id']}, {e['type']}) {e['content'][:200]}"
        for e in entries
    )


@mcp.tool()
async def memory_delta(since: str) -> str:
    async with _http() as c:
        r = await c.get("/memory/delta", params={"since": since})
        r.raise_for_status()
        entries = r.json()
    if not entries:
        return "No changes."
    return "\n".join(
        f"[{e['id']}] ({e['agent_id']}, {e['updated_at']}) {e['content'][:200]}"
        for e in entries
    )


@mcp.tool()
async def task_create(
    title: str,
    description: str = "",
    project: str | None = None,
    priority: str = "normal",
) -> str:
    async with _http() as c:
        r = await c.post("/tasks", json={
            "title": title,
            "description": description,
            "project": project,
            "priority": priority,
        })
        r.raise_for_status()
        return r.json()["id"]


@mcp.tool()
async def task_list(status: str | None = None, project: str | None = None) -> str:
    async with _http() as c:
        params: dict = {}
        if status:
            params["status"] = status
        r = await c.get("/tasks", params=params)
        r.raise_for_status()
        tasks = r.json()
    if project:
        tasks = [t for t in tasks if t.get("project") == project]
    if not tasks:
        return "No tasks."
    return "\n".join(
        f"[{t['id']}] [{t['status']}] [{t['priority']}] {t['title']}"
        for t in tasks
    )


@mcp.tool()
async def task_claim(task_id: str) -> str:
    async with _http() as c:
        r = await c.post(f"/tasks/{task_id}/claim")
        r.raise_for_status()
    return "claimed"


@mcp.tool()
async def task_complete(task_id: str) -> str:
    async with _http() as c:
        r = await c.post(f"/tasks/{task_id}/complete")
        r.raise_for_status()
    return "completed"


@mcp.tool()
async def session_context(agent_id: str) -> str:
    async with _http() as c:
        r = await c.get(f"/sessions/handoff/{agent_id}")
        r.raise_for_status()
        data = r.json()

    parts: list[str] = []
    h = data.get("last_handoff")
    if h:
        parts.append(f"## Last session ({h['created_at']})\n{h['summary']}")
        if h.get("in_progress"):
            parts.append("**In progress:** " + ", ".join(h["in_progress"]))
        if h.get("next_steps"):
            parts.append("**Next steps:**\n" + "\n".join(f"- {s}" for s in h["next_steps"]))
    else:
        parts.append("No previous session.")

    delta = data.get("memory_delta", [])
    if delta:
        parts.append(f"\n## Memory since last session ({len(delta)} entries)")
        for e in delta[:20]:
            parts.append(f"- [{e['id']}] {e['content'][:150]}")

    return "\n\n".join(parts)


@mcp.tool()
async def session_handoff(
    summary: str,
    next_steps: list[str] | None = None,
    in_progress: list[str] | None = None,
) -> str:
    async with _http() as c:
        r = await c.post("/sessions/handoff", json={
            "summary": summary,
            "next_steps": next_steps or [],
            "in_progress": in_progress or [],
        })
        r.raise_for_status()
        return r.json()["id"]
