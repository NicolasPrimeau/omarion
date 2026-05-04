import asyncio
import contextvars
import json
from contextlib import asynccontextmanager
from typing import Any

import httpx
import mcp.types as mcp_types
from mcp.server.fastmcp import FastMCP
from mcp.server.session import ServerSession

from .config import settings

_agent_id: contextvars.ContextVar[str] = contextvars.ContextVar("agent_id")
_api_key: contextvars.ContextVar[str] = contextvars.ContextVar("api_key")

_active_session: ServerSession | None = None
_session_ready: asyncio.Event | None = None
_notification_queue: asyncio.Queue[str] | None = None


@asynccontextmanager
async def _lifespan(app: "ArtelMCP"):
    global _session_ready, _notification_queue
    _session_ready = asyncio.Event()
    _notification_queue = asyncio.Queue()
    watcher = asyncio.create_task(_sse_watcher())
    sender = asyncio.create_task(_notification_sender())
    try:
        yield
    finally:
        watcher.cancel()
        sender.cancel()
        await asyncio.gather(watcher, sender, return_exceptions=True)


class ArtelMCP(FastMCP):
    async def call_tool(self, name: str, arguments: dict[str, Any] | None) -> list[mcp_types.ContentBlock]:
        global _active_session
        if _active_session is None:
            ctx = self.get_context()
            if ctx._request_context is not None:
                _active_session = ctx._request_context.session
                if _session_ready is not None:
                    _session_ready.set()
        return await super().call_tool(name, arguments)


async def _sse_watcher():
    headers = {
        "x-agent-id": settings.mcp_agent_id,
        "x-api-key": settings.mcp_agent_key,
    }
    while True:
        try:
            async with httpx.AsyncClient(
                base_url=settings.artel_url,
                headers=headers,
                timeout=httpx.Timeout(None, connect=10.0),
            ) as client:
                async with client.stream("GET", "/events/stream", params={"type": "message.received"}) as resp:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        payload = event.get("payload", {})
                        to_agent = payload.get("to", "")
                        if to_agent not in (settings.mcp_agent_id, "broadcast"):
                            continue
                        sender_id = event.get("agent_id", "?")
                        if _notification_queue is not None:
                            await _notification_queue.put(f"inbox: new message from {sender_id}")
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)


async def _notification_sender():
    while True:
        if _notification_queue is None or _session_ready is None:
            await asyncio.sleep(0.1)
            continue
        msg = await _notification_queue.get()
        await _session_ready.wait()
        if _active_session is not None:
            try:
                await _active_session.send_log_message("warning", msg)
            except Exception:
                pass


mcp = ArtelMCP(
    "artel",
    lifespan=_lifespan,
    host=settings.mcp_host,
    port=settings.mcp_port,
    instructions="""You are connected to Artel — a shared coordination layer for a fleet of AI agents.

SESSION LIFECYCLE (do these every session, no exceptions):
1. START: call session_context() — loads your last handoff + what changed in memory while you were gone.
2. START: call message_inbox() — read messages from other agents.
3. END: call session_handoff() — saves what you did so the next session (or another agent) can continue.

MEMORY (write often, read before you act):
- Call memory_search() before starting any non-trivial work — another agent may have already done it.
- Call memory_write() whenever you learn something worth keeping: decisions, facts, findings, plans, bugs.
- Use type="scratch" for working notes, type="doc" for stable reference, type="memory" for facts and decisions.
- Use tags to make things findable. Use scope="private" only for things no other agent should see.
- If MCP_PROJECT is set, all memory calls default to that project automatically.

COORDINATION:
- Call agent_list() to see who else is active before messaging or assigning tasks.
- Call project_list() to see what projects are active and who is in them.
- Use task_list(status="open") to find work that needs doing.
- Claim a task before starting it. Complete or fail it when done — never leave tasks in limbo.

IDENTITY:
- Your agent_id and api_key are in your environment (MCP_AGENT_ID, MCP_AGENT_KEY).
- All agents share the same memory, tasks, and message bus. What you write, others can read.""",
)


def _http() -> httpx.AsyncClient:
    headers = {
        "x-agent-id": _agent_id.get(settings.mcp_agent_id),
        "x-api-key": _api_key.get(settings.mcp_agent_key),
    }
    return httpx.AsyncClient(base_url=settings.artel_url, headers=headers, timeout=30.0)


def _err(e: httpx.HTTPStatusError) -> str:
    try:
        detail = e.response.json().get("detail", e.response.text)
    except Exception:
        detail = e.response.text
    return f"error {e.response.status_code}: {detail}"


def _fmt_memory(e: dict, full_content: bool = False) -> str:
    tags = ", ".join(e["tags"]) if e["tags"] else "—"
    project = f" project={e['project']}" if e.get("project") else ""
    meta = f"[{e['id']}] ({e['agent_id']}, {e['type']}, conf={e['confidence']:.2f}, tags={tags}{project})"
    content = e["content"] if full_content else e["content"][:300]
    return f"{meta}\n{content}"


# ── Session ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def session_context(agent_id: str | None = None) -> str:
    """CALL THIS FIRST at the start of every session, before doing any work.

    Returns your last session handoff (what you were doing, what's next) and all memory
    entries written or updated since that session. This is how you avoid repeating work
    and pick up where you left off across context resets or machine switches.

    Args:
        agent_id: Whose context to load. Omit to load your own.
    """
    target = agent_id or _agent_id.get(settings.mcp_agent_id)
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
        parts.append(f"## Last session ({h['created_at'][:16]})\n{h['summary']}")
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
            parts.append(_fmt_memory(e))

    return "\n\n".join(parts)


@mcp.tool()
async def session_handoff(
    summary: str,
    next_steps: list[str] | None = None,
    in_progress: list[str] | None = None,
) -> str:
    """CALL THIS LAST before your session ends — saves state for your next session.

    Stores what you did, what's in progress, and what to do next. The next time you (or
    any agent loading your context) calls session_context(), this is what they'll get.
    Write a thorough summary: decisions made, blockers hit, context that would be lost otherwise.

    Args:
        summary: What you accomplished this session. Be specific — this is your only record.
        next_steps: What to do in the next session, in order of priority.
        in_progress: Task IDs that are currently claimed and not yet completed.
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


# ── Memory ───────────────────────────────────────────────────────────────────

@mcp.tool()
async def memory_write(
    content: str,
    type: str = "memory",
    scope: str = "shared",
    project: str | None = None,
    tags: list[str] | None = None,
    confidence: float = 1.0,
) -> str:
    """Write something to shared memory. Use this often.

    Write whenever you learn, decide, or discover something worth keeping:
    - Facts about the codebase, infrastructure, or domain
    - Decisions made and why
    - Bugs found, workarounds, gotchas
    - Plans, designs, open questions
    - Anything another agent (or future you) would want to know

    Types:
    - memory: default — facts, decisions, findings (persistent)
    - doc: stable reference material (architecture, runbooks)
    - scratch: working notes — disposable, will be promoted or decayed by archivist
    - reference: pointers to external resources (URLs, file paths, credentials)

    Scopes:
    - shared: visible to all agents in this project (default)
    - global: visible to all agents, bypasses project restrictions
    - private: only you can see it

    Args:
        content: What to store. Markdown is fine.
        type: See types above. Default: memory.
        scope: See scopes above. Default: shared.
        project: Project to scope the entry to. Defaults to MCP_PROJECT if set.
        tags: Tags for filtering and retrieval. Use them — they make memory_list useful.
        confidence: How certain you are (0.0–1.0). Default 1.0. Use lower for guesses.
    """
    async with _http() as c:
        try:
            r = await c.post("/memory", json={
                "content": content,
                "type": type,
                "project": project or settings.mcp_project or None,
                "scope": scope,
                "tags": tags or [],
                "confidence": confidence,
                "parents": [],
            })
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        entry = r.json()
    return f"written [{entry['id']}]"


@mcp.tool()
async def memory_search(
    q: str,
    project: str | None = None,
    tag: str | None = None,
    limit: int = 10,
) -> str:
    """Search shared memory by meaning. Call this before starting work.

    Uses semantic (embedding) search — finds entries by meaning, not exact keywords.
    Always search before writing: another agent may have already captured what you need.
    Also useful for: finding prior decisions, understanding what's been explored, avoiding duplication.

    Args:
        q: What you're looking for, in natural language.
        project: Restrict to a project. Defaults to MCP_PROJECT if set.
        tag: Restrict to entries with this tag.
        limit: How many results (default 10, max 50).
    """
    async with _http() as c:
        try:
            params: dict = {"q": q, "limit": min(limit, 50)}
            effective_project = project or settings.mcp_project or None
            if effective_project:
                params["project"] = effective_project
            if tag:
                params["tag"] = tag
            r = await c.get("/memory/search", params=params)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        entries = r.json()
    if not entries:
        return "No results."
    return "\n\n".join(_fmt_memory(e) for e in entries)


@mcp.tool()
async def memory_list(
    type: str | None = None,
    project: str | None = None,
    tag: str | None = None,
    agent: str | None = None,
    confidence_min: float | None = None,
    limit: int = 50,
) -> str:
    """Browse memory entries by filter. Use when you want to survey a topic area.

    Complements memory_search: search is for "find something relevant", list is for
    "show me everything tagged X" or "what has agent Y written" or "all scratch notes".

    Args:
        type: memory, doc, scratch, or reference.
        project: Filter by project. Omit to see all accessible projects.
        tag: Only entries with this tag.
        agent: Only entries written by this agent.
        confidence_min: Only entries with confidence >= this (e.g. 0.7 to skip decayed entries).
        limit: Max results (default 50, max 500).
    """
    async with _http() as c:
        try:
            params: dict = {"limit": min(limit, 500)}
            if type:
                params["type"] = type
            if project:
                params["project"] = project
            if tag:
                params["tag"] = tag
            if agent:
                params["agent"] = agent
            if confidence_min is not None:
                params["confidence_min"] = confidence_min
            r = await c.get("/memory", params=params)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        entries = r.json()
    if not entries:
        return "No entries."
    return "\n\n".join(_fmt_memory(e) for e in entries)


@mcp.tool()
async def memory_get(entry_id: str) -> str:
    """Fetch a single memory entry by ID. Use when you have an ID and need the full content.

    Args:
        entry_id: The UUID of the entry.
    """
    async with _http() as c:
        try:
            r = await c.get(f"/memory/{entry_id}")
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        e = r.json()
    return _fmt_memory(e, full_content=True)


@mcp.tool()
async def memory_delta(since: str) -> str:
    """Get all memory written or updated after a timestamp.

    Use when you need to catch up on a specific time window. session_context() calls this
    automatically since your last handoff — use memory_delta directly only if you need
    a custom time range.

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
    return "\n\n".join(_fmt_memory(e) for e in entries)


# ── Projects & Agents ────────────────────────────────────────────────────────

@mcp.tool()
async def project_list() -> str:
    """List all projects with their members, memory count, and last activity.

    Use this to understand what projects are active, who's working on what,
    and how much shared context each project has. Your default project is
    MCP_PROJECT (if set) — memory you write goes there automatically.
    """
    async with _http() as c:
        try:
            r = await c.get("/projects")
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        projects = r.json()
    if not projects:
        return "No projects yet."
    lines = []
    for p in projects:
        marker = " ◀ yours" if p["name"] == settings.mcp_project else ""
        lines.append(
            f"{p['name']}{marker} — {p['memory_count']} memories, {p['task_count']} tasks"
            f" | agents: {', '.join(p['agents']) or 'none'}"
            f" | last: {(p['last_activity'] or 'never')[:16]}"
        )
    return "\n".join(lines)


@mcp.tool()
async def agent_list() -> str:
    """List all registered agents and when they were last active.

    Use this to know who's available before sending messages or assigning tasks.
    An agent that was last seen recently is likely still active.
    """
    async with _http() as c:
        try:
            r = await c.get("/participants")
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        participants = r.json()
    if not participants:
        return "No agents."
    return "\n".join(
        f"{p['agent_id']} — last seen: {p['last_seen'][:16] if p['last_seen'] else 'never'}"
        for p in participants
    )


@mcp.tool()
async def agent_rename(new_id: str) -> str:
    """Rename yourself. Cascades the new ID across all memory, tasks, messages, and sessions.

    Use if your current agent ID doesn't match your project name or is a collision artifact
    (e.g. "my-project-2"). Can only rename yourself, not other agents.

    Args:
        new_id: Your new agent ID. Alphanumeric, hyphens and underscores allowed.
    """
    async with _http() as c:
        try:
            r = await c.patch("/agents/me", json={"new_id": new_id})
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        a = r.json()
    return f"renamed to {a['agent_id']}"


# ── Messages ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def message_inbox() -> str:
    """Read and clear your unread messages. Call this at session start.

    Messages are marked read after this call. Agents use messages to coordinate,
    delegate work, share findings, or ask questions. Check it — someone may be waiting.
    """
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
async def message_send(to: str, body: str, subject: str = "") -> str:
    """Send a message to another agent's inbox.

    Use for async coordination: delegating work, sharing a finding, asking a question,
    or notifying another agent that something is ready. The recipient will see it when
    they call message_inbox().

    Args:
        to: The agent_id to send to, or "broadcast" to reach all agents.
        body: Message body.
        subject: Optional subject line (helps the recipient triage).
    """
    async with _http() as c:
        try:
            r = await c.post("/messages", json={"to": to, "subject": subject, "body": body})
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        m = r.json()
    return f"sent to {m['to_agent']} [{m['id']}]"


# ── Tasks ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def task_list(status: str | None = None, project: str | None = None) -> str:
    """List tasks. Call with status="open" to find work that needs doing.

    Tasks are the coordination primitive for multi-agent work: one agent creates a task,
    another claims and completes it. Check for open tasks before creating new ones.

    Args:
        status: open, claimed, completed, or failed. Omit for all.
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
        + (f" (assigned: {t['assigned_to']})" if t.get("assigned_to") else "")
        for t in tasks
    )


@mcp.tool()
async def task_create(
    title: str,
    description: str = "",
    project: str | None = None,
    priority: str = "normal",
) -> str:
    """Create a task for yourself or another agent to pick up.

    Use when there's a discrete unit of work that should be tracked, may be done by
    a different agent, or needs to survive across sessions. Check task_list() for
    duplicates before creating.

    Args:
        title: Short imperative description, e.g. "Fix auth token expiry bug".
        description: Context, acceptance criteria, or relevant links.
        project: Project scope. Defaults to MCP_PROJECT if set.
        priority: low, normal (default), or high.
    """
    async with _http() as c:
        try:
            r = await c.post("/tasks", json={
                "title": title,
                "description": description,
                "project": project or settings.mcp_project or None,
                "priority": priority,
            })
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        t = r.json()
    return f"created [{t['id']}] [{t['priority']}] {t['title']}"


@mcp.tool()
async def task_claim(task_id: str) -> str:
    """Claim an open task — marks it as yours and sets status to 'claimed'.

    Always claim a task before working on it. This prevents two agents from doing
    the same work. Call task_complete() or task_fail() when done.

    Args:
        task_id: ID from task_list() or task_create().
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
    """Mark your claimed task as completed. Only the agent that claimed it can complete it.

    Args:
        task_id: ID of a task you have claimed.
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
    """Mark your claimed task as failed. Use when you cannot complete it.

    Prefer this over abandoning — it unblocks other agents who can see the task
    failed and decide what to do next.

    Args:
        task_id: ID of a task you have claimed.
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
async def task_get(task_id: str) -> str:
    """Fetch full details of a task by ID.

    Args:
        task_id: The UUID of the task.
    """
    async with _http() as c:
        try:
            r = await c.get(f"/tasks/{task_id}")
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return _err(e)
        t = r.json()
    assigned = t["assigned_to"] or "unassigned"
    project = f" | project: {t['project']}" if t.get("project") else ""
    lines = [
        f"[{t['id']}] [{t['status']}] [{t['priority']}] {t['title']}",
        f"created by: {t['created_by']} | assigned to: {assigned}{project}",
    ]
    if t["description"]:
        lines.append(t["description"])
    return "\n".join(lines)
