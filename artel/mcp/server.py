import httpx
from mcp.server.fastmcp import FastMCP

from .config import settings

mcp = FastMCP("artel", host=settings.mcp_host, port=settings.mcp_port)

_HEADERS = {
    "x-agent-id": settings.mcp_agent_id,
    "x-api-key": settings.mcp_agent_key,
}


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=settings.artel_url, headers=_HEADERS, timeout=30.0)


def _err(e: httpx.HTTPStatusError) -> str:
    try:
        detail = e.response.json().get("detail", e.response.text)
    except Exception:
        detail = e.response.text
    return f"error {e.response.status_code}: {detail}"


@mcp.tool()
async def memory_write(
    content: str,
    type: str = "memory",
    scope: str = "shared",
    project: str | None = None,
    tags: list[str] | None = None,
    confidence: float = 1.0,
) -> str:
    """Write an entry to shared memory.

    Args:
        content: The knowledge to store, in markdown.
        type: Entry type — memory (default), doc, scratch, reference, or task.
        scope: Visibility — shared (default, all agents) or private (only you).
        project: Optional project name to scope the entry.
        tags: Optional list of tags for retrieval.
        confidence: Confidence score 0.0–1.0 (default 1.0).
    """
    async with _http() as c:
        try:
            r = await c.post("/memory", json={
                "content": content,
                "type": type,
                "project": project,
                "scope": scope,
                "tags": tags or [],
                "confidence": confidence,
            })
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        entry = r.json()
    return f"written [{entry['id']}]: {entry['content'][:120]}"


@mcp.tool()
async def memory_get(entry_id: str) -> str:
    """Retrieve a single memory entry by ID.

    Args:
        entry_id: The UUID of the entry to retrieve.
    """
    async with _http() as c:
        try:
            r = await c.get(f"/memory/{entry_id}")
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        e = r.json()
    tags = ", ".join(e["tags"]) if e["tags"] else "none"
    return (
        f"[{e['id']}] ({e['agent_id']}, {e['type']}, confidence={e['confidence']:.2f}, tags={tags})\n"
        f"{e['content']}"
    )


@mcp.tool()
async def memory_search(q: str, project: str | None = None, limit: int = 10) -> str:
    """Semantic search across shared memory.

    Args:
        q: Natural-language query.
        project: Optional project filter.
        limit: Max results to return (default 10, max 50).
    """
    async with _http() as c:
        try:
            params: dict = {"q": q, "limit": min(limit, 50)}
            if project:
                params["project"] = project
            r = await c.get("/memory/search", params=params)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        entries = r.json()
    if not entries:
        return "No results."
    return "\n".join(
        f"[{e['id']}] ({e['agent_id']}, {e['type']}) {e['content'][:500]}"
        for e in entries
    )


@mcp.tool()
async def memory_delta(since: str) -> str:
    """Get all memory entries updated since a given timestamp.

    Use this at session start to see what changed while you were gone.

    Args:
        since: ISO 8601 timestamp, e.g. "2026-05-01T12:00:00.000Z".
    """
    async with _http() as c:
        try:
            r = await c.get("/memory/delta", params={"since": since})
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        entries = r.json()
    if not entries:
        return "No changes."
    return "\n".join(
        f"[{e['id']}] ({e['agent_id']}, {e['updated_at']}) {e['content'][:500]}"
        for e in entries
    )


@mcp.tool()
async def task_get(task_id: str) -> str:
    """Retrieve a single task by ID.

    Args:
        task_id: The UUID of the task to retrieve.
    """
    async with _http() as c:
        try:
            r = await c.get(f"/tasks/{task_id}")
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        t = r.json()
    assigned = t["assigned_to"] or "unassigned"
    lines = [f"[{t['id']}] [{t['status']}] [{t['priority']}] {t['title']}",
             f"created by: {t['created_by']} | assigned to: {assigned}"]
    if t["description"]:
        lines.append(t["description"])
    return "\n".join(lines)


@mcp.tool()
async def task_create(
    title: str,
    description: str = "",
    project: str | None = None,
    priority: str = "normal",
) -> str:
    """Create a new task visible to all agents.

    Args:
        title: Short summary of the work.
        description: Detailed description (optional).
        project: Optional project scope.
        priority: low, normal (default), or high.
    """
    async with _http() as c:
        try:
            r = await c.post("/tasks", json={
                "title": title,
                "description": description,
                "project": project,
                "priority": priority,
            })
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        t = r.json()
    return f"created [{t['id']}] [{t['priority']}] {t['title']}"


@mcp.tool()
async def task_list(status: str | None = None, project: str | None = None) -> str:
    """List tasks, optionally filtered by status and/or project.

    Args:
        status: Filter by status — open, claimed, completed, or failed.
        project: Filter by project name.
    """
    async with _http() as c:
        try:
            params: dict = {}
            if status:
                params["status"] = status
            if project:
                params["project"] = project
            r = await c.get("/tasks", params=params)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        tasks = r.json()
    if not tasks:
        return "No tasks."
    return "\n".join(
        f"[{t['id']}] [{t['status']}] [{t['priority']}] {t['title']}"
        for t in tasks
    )


@mcp.tool()
async def task_claim(task_id: str) -> str:
    """Claim an open task. Assigns it to you and moves it to 'claimed'.

    Args:
        task_id: The ID of the task to claim.
    """
    async with _http() as c:
        try:
            r = await c.post(f"/tasks/{task_id}/claim")
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        t = r.json()
    return f"claimed [{t['id']}] {t['title']}"


@mcp.tool()
async def task_complete(task_id: str) -> str:
    """Mark a task as completed.

    Args:
        task_id: The ID of the task to complete.
    """
    async with _http() as c:
        try:
            r = await c.post(f"/tasks/{task_id}/complete")
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        t = r.json()
    return f"completed [{t['id']}] {t['title']}"


@mcp.tool()
async def task_fail(task_id: str) -> str:
    """Mark a task as failed.

    Args:
        task_id: The ID of the task to fail.
    """
    async with _http() as c:
        try:
            r = await c.post(f"/tasks/{task_id}/fail")
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        t = r.json()
    return f"failed [{t['id']}] {t['title']}"


@mcp.tool()
async def send_message(to: str, body: str, subject: str = "") -> str:
    """Send a message to another agent's inbox.

    Args:
        to: Recipient agent_id, or "broadcast" to reach all agents.
        body: Message content.
        subject: Optional subject line.
    """
    async with _http() as c:
        try:
            r = await c.post("/messages", json={"to": to, "subject": subject, "body": body})
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        m = r.json()
    return f"sent to {m['to_agent']} [{m['id']}]"


@mcp.tool()
async def read_inbox() -> str:
    """Read and clear your unread inbox. Marks all returned messages as read."""
    async with _http() as c:
        try:
            r = await c.get("/messages/inbox")
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        messages = r.json()
        for m in messages:
            await c.post(f"/messages/{m['id']}/read")
    if not messages:
        return "No unread messages."
    lines = []
    for m in messages:
        header = f"[{m['id']}] from {m['from_agent']} · {m['created_at'][:16]}"
        if m["subject"]:
            header += f" · {m['subject']}"
        lines.append(f"{header}\n{m['body']}")
    return "\n\n".join(lines)


@mcp.tool()
async def list_participants() -> str:
    """List all registered agents and when they were last active."""
    async with _http() as c:
        try:
            r = await c.get("/participants")
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        participants = r.json()
    if not participants:
        return "No participants."
    return "\n".join(
        f"{p['agent_id']} — last seen: {p['last_seen'] or 'never'}"
        for p in participants
    )


@mcp.tool()
async def session_context(agent_id: str | None = None) -> str:
    """Load the last session handoff and memory changes since then.

    Call this at the start of every session to warm-start your context.

    Args:
        agent_id: Agent whose context to load. Defaults to your own agent ID.
    """
    target = agent_id or settings.mcp_agent_id
    async with _http() as c:
        try:
            r = await c.get(f"/sessions/handoff/{target}")
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
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
    """Save a session handoff so the next session can pick up where you left off.

    Call this at the end of every session.

    Args:
        summary: What you accomplished this session.
        next_steps: List of things to do in the next session.
        in_progress: List of task IDs currently in progress.
    """
    async with _http() as c:
        try:
            r = await c.post("/sessions/handoff", json={
                "summary": summary,
                "next_steps": next_steps or [],
                "in_progress": in_progress or [],
            })
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        return f"handoff saved [{r.json()['id']}]"
