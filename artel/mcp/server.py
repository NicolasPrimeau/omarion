import asyncio
import contextvars
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import mcp.types as mcp_types
from mcp.server.fastmcp import FastMCP
from mcp.server.session import ServerSession

from ..store.db import get_db
from .config import settings

log = logging.getLogger(__name__)

_agent_id: contextvars.ContextVar[str] = contextvars.ContextVar("agent_id")
_api_key: contextvars.ContextVar[str] = contextvars.ContextVar("api_key")

_sessions: dict[str, ServerSession] = {}
_notification_queue: asyncio.Queue[tuple[str, str]] | None = None
_client: httpx.AsyncClient | None = None
_key_cache: dict[str, str] = {}


async def _inject_credentials(request: httpx.Request) -> None:
    aid = _agent_id.get(None)
    key = _api_key.get(None)
    if aid and key:
        request.headers["x-agent-id"] = aid
        request.headers["x-api-key"] = _key_cache.get(aid, key)


async def _refresh_key(agent_id: str) -> str | None:
    if not settings.mcp_registration_key:
        return None
    c = _http()
    try:
        r = await c.post(
            "/agents/self-register",
            json={"agent_id": agent_id},
            headers={"x-registration-key": settings.mcp_registration_key},
        )
        if r.status_code == 201:
            new_key = r.json()["api_key"]
            _key_cache[agent_id] = new_key
            _api_key.set(new_key)
            return new_key
    except Exception:
        pass
    return None


def _utcnow() -> str:
    dt = datetime.now(UTC)
    return dt.strftime(f"%Y-%m-%dT%H:%M:%S.{dt.microsecond // 1000:03d}Z")


def _enqueue_notification(agent_id: str, message: str) -> None:
    try:
        db = get_db()
        db.execute(
            "INSERT INTO mcp_notification_queue (id, agent_id, message) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), agent_id, message),
        )
        db.commit()
    except Exception as e:
        log.warning("failed to queue notification for %s: %s", agent_id, e)


async def _flush_notifications(agent_id: str, session: ServerSession) -> None:
    try:
        db = get_db()
        rows = db.execute(
            "SELECT id, message FROM mcp_notification_queue "
            "WHERE agent_id=? AND delivered_at IS NULL ORDER BY queued_at",
            (agent_id,),
        ).fetchall()
        for row in rows:
            try:
                await session.send_log_message("warning", row["message"])
                db.execute(
                    "UPDATE mcp_notification_queue SET delivered_at=? WHERE id=?",
                    (_utcnow(), row["id"]),
                )
                db.commit()
            except Exception as e:
                log.debug("flush failed for %s at %s: %s", agent_id, row["id"], e)
                _sessions.pop(agent_id, None)
                break
    except Exception as e:
        log.warning("flush_notifications error for %s: %s", agent_id, e)


async def _gc_notification_queue() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            db = get_db()
            cutoff_dt = datetime.now(UTC) - timedelta(hours=24)
            cutoff = cutoff_dt.strftime(f"%Y-%m-%dT%H:%M:%S.{cutoff_dt.microsecond // 1000:03d}Z")
            db.execute("DELETE FROM mcp_notification_queue WHERE queued_at < ?", (cutoff,))
            db.commit()
        except Exception as e:
            log.warning("notification queue GC failed: %s", e)


@asynccontextmanager
async def _lifespan(app: "ArtelMCP"):
    global _notification_queue, _client
    _client = httpx.AsyncClient(
        base_url=settings.artel_url,
        headers={
            "x-agent-id": settings.mcp_agent_id,
            "x-api-key": settings.api_key(),
        },
        event_hooks={"request": [_inject_credentials]},
        timeout=30.0,
    )
    _notification_queue = asyncio.Queue()
    watcher = asyncio.create_task(_sse_watcher())
    sender = asyncio.create_task(_notification_sender())
    gc = asyncio.create_task(_gc_notification_queue())
    try:
        yield
    finally:
        watcher.cancel()
        sender.cancel()
        gc.cancel()
        await asyncio.gather(watcher, sender, gc, return_exceptions=True)
        await _client.aclose()


class _CredentialMiddleware:
    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            aid = headers.get(b"x-agent-id", b"").decode()
            key = headers.get(b"x-api-key", b"").decode()
            _agent_id.set(aid or settings.mcp_agent_id)
            _api_key.set(key or settings.api_key())
        await self._app(scope, receive, send)


class ArtelMCP(FastMCP):
    def streamable_http_app(self) -> Any:
        return _CredentialMiddleware(super().streamable_http_app())

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None
    ) -> list[mcp_types.ContentBlock]:
        ctx = self.get_context()
        aid = _agent_id.get(settings.mcp_agent_id)
        has_session = ctx._request_context is not None
        if has_session:
            _sessions[aid] = ctx._request_context.session
        try:
            result = await super().call_tool(name, arguments)
        except _StaleKeyError:
            new_key = await _refresh_key(aid)
            if new_key:
                raise RuntimeError(
                    "Your Artel API key was stale and has been refreshed for this session. "
                    "Retry this tool call — it will succeed. "
                    f"To persist the new key: curl {settings.artel_url.rstrip('/')}/onboard | sh"
                )
            raise RuntimeError(
                "Artel rejected your API key (401). Credentials in .mcp.json are stale. "
                f"Fix: curl -fsSL {settings.artel_url.rstrip('/')}/onboard | sh  then restart Claude Code"
            )
        if has_session:
            await _flush_notifications(aid, _sessions[aid])
        return result


async def _sse_watcher():
    headers = {
        "x-agent-id": settings.mcp_agent_id,
        "x-api-key": settings.api_key(),
    }
    delay = 1.0
    while True:
        try:
            async with httpx.AsyncClient(
                base_url=settings.artel_url,
                headers=headers,
                timeout=httpx.Timeout(None, connect=10.0),
            ) as client:
                async with client.stream(
                    "GET", "/events/stream", params={"type": "message.received"}
                ) as resp:
                    async for line in resp.aiter_lines():
                        delay = 1.0
                        if not line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        payload = event.get("payload", {})
                        to_agent = payload.get("to", "")
                        sender_id = event.get("agent_id", "?")
                        if _notification_queue is not None:
                            await _notification_queue.put(
                                (to_agent, f"inbox: new message from {sender_id}")
                            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("SSE watcher disconnected, retrying in %.0fs: %s", delay, e)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)


async def _deliver_notification(to_agent: str, msg: str) -> None:
    if to_agent == "broadcast":
        targets = list(_sessions.items())
    else:
        s = _sessions.get(to_agent)
        targets = [(to_agent, s)] if s else []
    delivered = False
    for aid, session in targets:
        try:
            await session.send_log_message("warning", msg)
            delivered = True
        except Exception as e:
            log.debug("notification failed for %s, dropping session: %s", aid, e)
            _sessions.pop(aid, None)
    if not delivered and to_agent != "broadcast":
        _enqueue_notification(to_agent, msg)


async def _notification_sender():
    while True:
        if _notification_queue is None:
            await asyncio.sleep(0.1)
            continue
        to_agent, msg = await _notification_queue.get()
        await _deliver_notification(to_agent, msg)


mcp = ArtelMCP(
    "artel",
    lifespan=_lifespan,
    host=settings.mcp_host,
    port=settings.mcp_port,
    stateless_http=True,
    instructions="""You are connected to Artel — a shared coordination layer for a fleet of AI agents.

SESSION LIFECYCLE (do these every session, no exceptions):
1. START: call session_context() — loads your last handoff + what changed in memory while you were gone.
2. START: call message_inbox() — read messages from other agents.
3. END: call session_handoff() — saves what you did so the next session (or another agent) can continue.

MEMORY (write often, read before you act):
- Call memory_search() before starting any non-trivial work — another agent may have already done it.
- Call memory_write() whenever you learn something worth keeping: decisions, facts, findings, plans, bugs.
- entry_type="memory" is the default and right for almost everything. The archivist promotes stable entries to entry_type="doc" automatically.
- Use tags to make things findable. Use scope="agent" only for things no other agent should see.
- If MCP_PROJECT is set, all memory calls default to that project automatically.

COORDINATION:
- Call agent_list() to see who else is active before messaging or assigning tasks.
- Call project_list() to see what projects are active and who is in them.
- Use project_join() to join a project and gain visibility into its shared memories and tasks.
- Use task_list(status="open") to find work that needs doing.
- Claim a task before starting it. Complete or fail it when done — never leave tasks in limbo.

INBOX CRON (first session only):
- Call inbox_cron_setup() to get instructions for scheduling automatic inbox checks.
- This lets other agents reach you even when you're not actively running.

IDENTITY:
- Your agent_id and api_key are in your environment (MCP_AGENT_ID, MCP_AGENT_KEY).
- All agents share the same memory, tasks, and message bus. What you write, others can read.""",
)


def _http() -> httpx.AsyncClient:
    assert _client is not None, "MCP lifespan not started"
    return _client


_HTTPX_ERRORS = (httpx.HTTPStatusError, httpx.TransportError)


def _err(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        if e.response.status_code == 401:
            raise _StaleKeyError()
        try:
            detail = e.response.json().get("detail", e.response.text)
        except Exception:
            detail = e.response.text
        return f"error {e.response.status_code}: {detail}"
    if isinstance(e, httpx.ConnectError):
        return f"error: cannot connect to Artel server — {e}"
    if isinstance(e, httpx.ReadTimeout):
        return "error: request timed out"
    return f"error: {type(e).__name__}: {e}"


class _StaleKeyError(BaseException):
    pass


def _fmt_memory(e: dict, full_content: bool = False) -> str:
    tags = ", ".join(e["tags"]) if e["tags"] else "—"
    project = f" project={e['project']}" if e.get("project") else ""
    meta = f"[{e['id']}] ({e['agent_id']}, {e['type']}, conf={e['confidence']:.2f}, tags={tags}{project})"
    raw = e["content"]
    if full_content or len(raw) <= 300:
        content = raw
    else:
        content = raw[:300] + " …(truncated, use memory_get for full content)"
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
    c = _http()
    try:
        r = await c.get(f"/sessions/handoff/{target}")
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    data = r.json()

    parts: list[str] = [f"agent: {target}"]
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
        total = len(delta)
        shown = delta[:20]
        label = f"{total} entries" + (
            ", showing first 20 — call memory_delta() for the rest" if total > 20 else ""
        )
        parts.append(f"\n## Memory since last session ({label})")
        for e in shown:
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
    c = _http()
    try:
        r = await c.post(
            "/sessions/handoff",
            json={
                "summary": summary,
                "next_steps": next_steps or [],
                "in_progress": in_progress or [],
            },
        )
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    return f"handoff saved [{r.json()['id']}]"


# ── Memory ───────────────────────────────────────────────────────────────────


@mcp.tool()
async def memory_write(
    content: str,
    entry_type: str = "memory",
    scope: str = "project",
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
    - memory: default — use this for everything
    - doc: stable reference material; normally written by the archivist, not agents
    - directive: a standing instruction that governs archivist behavior fleet-wide; requires elevated permission (the UI agent has it by default); confidence is always forced to 1.0

    Scopes:
    - project: visible to all members of this project (default)
    - agent: only you can see it

    Args:
        content: What to store. Markdown is fine.
        entry_type: See types above. Default: memory.
        scope: See scopes above. Default: project.
        project: Project to scope the entry to. Defaults to MCP_PROJECT if set.
        tags: Tags for filtering and retrieval. Use them — they make memory_list useful.
        confidence: How certain you are (0.0–1.0). Default 1.0. Use lower for guesses.
    """
    c = _http()
    try:
        r = await c.post(
            "/memory",
            json={
                "content": content,
                "type": entry_type,
                "project": settings.resolve_project(project),
                "scope": scope,
                "tags": tags or [],
                "confidence": confidence,
                "parents": [],
            },
        )
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    entry = r.json()
    snippet = entry["content"][:80].replace("\n", " ")
    return f"written [{entry['id']}] ({entry['type']}) {snippet!r}"


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
    c = _http()
    try:
        params: dict = {"q": q, "limit": min(limit, 50)}
        effective_project = settings.resolve_project(project)
        if effective_project:
            params["project"] = effective_project
        if tag:
            params["tag"] = tag
        r = await c.get("/memory/search", params=params)
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    entries = r.json()
    if not entries:
        return "No results."
    return "\n\n".join(_fmt_memory(e) for e in entries)


@mcp.tool()
async def memory_list(
    entry_type: str | None = None,
    project: str | None = None,
    tag: str | None = None,
    agent: str | None = None,
    confidence_min: float | None = None,
    limit: int = 50,
) -> str:
    """Browse memory entries by filter. Use when you want to survey a topic area.

    Complements memory_search: search is for "find something relevant", list is for
    "show me everything tagged X" or "what has agent Y written".

    Args:
        entry_type: memory or doc.
        project: Filter by project. Omit to see all accessible projects.
        tag: Only entries with this tag.
        agent: Only entries written by this agent.
        confidence_min: Only entries with confidence >= this (e.g. 0.7 to skip decayed entries).
        limit: Max results (default 50, max 500).
    """
    c = _http()
    try:
        params: dict = {"limit": min(limit, 500)}
        if entry_type:
            params["type"] = entry_type
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
    except _HTTPX_ERRORS as e:
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
    c = _http()
    try:
        r = await c.get(f"/memory/{entry_id}")
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    return _fmt_memory(r.json(), full_content=True)


@mcp.tool()
async def memory_update(
    entry_id: str,
    content: str | None = None,
    confidence: float | None = None,
    tags: list[str] | None = None,
    entry_type: str | None = None,
    scope: str | None = None,
    project: str | None = None,
) -> str:
    """Update a memory entry you own.

    Args:
        entry_id: The UUID of the entry to update.
        content: New content. Omit to leave unchanged.
        confidence: New confidence score (0.0–1.0). Omit to leave unchanged.
        tags: Replace tags list. Omit to leave unchanged.
        entry_type: New type (memory or doc). Omit to leave unchanged.
        scope: New scope (agent or project). Omit to leave unchanged.
        project: Move entry to a different project. Omit to leave unchanged.
    """
    patch: dict = {}
    if content is not None:
        patch["content"] = content
    if confidence is not None:
        patch["confidence"] = confidence
    if tags is not None:
        patch["tags"] = tags
    if entry_type is not None:
        patch["type"] = entry_type
    if scope is not None:
        patch["scope"] = scope
    if project is not None:
        patch["project"] = project
    c = _http()
    try:
        r = await c.patch(f"/memory/{entry_id}", json=patch)
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    return _fmt_memory(r.json(), full_content=False)


@mcp.tool()
async def memory_delete(entry_id: str) -> str:
    """Delete a memory entry. Only the entry's owner can delete it.

    The entry is soft-deleted: it disappears immediately from all search, list, and get
    results but its content is retained in the database for audit purposes.

    Args:
        entry_id: The UUID of the entry to delete.
    """
    c = _http()
    try:
        r = await c.delete(f"/memory/{entry_id}")
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    return f"deleted [{entry_id}]"


@mcp.tool()
async def memory_delta(since: str) -> str:
    """Get all memory written or updated after a timestamp.

    Use when you need to catch up on a specific time window. session_context() calls this
    automatically since your last handoff — use memory_delta directly only if you need
    a custom time range.

    Args:
        since: ISO 8601 timestamp, e.g. "2026-05-01T12:00:00.000Z".
    """
    c = _http()
    try:
        r = await c.get("/memory/delta", params={"since": since})
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
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
    c = _http()
    try:
        r = await c.get("/projects")
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
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
async def project_join(project_id: str) -> str:
    """Join a project so you can read and write its shared memories and tasks.

    After joining, project-scoped memory for this project becomes visible to you,
    and memory you write with this project will be visible to other members.

    Args:
        project_id: The project name to join.
    """
    c = _http()
    try:
        r = await c.post(f"/projects/{project_id}/join")
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    return f"joined project {project_id!r}"


@mcp.tool()
async def project_leave(project_id: str) -> str:
    """Leave a project. You will no longer see its project-scoped memories.

    Args:
        project_id: The project name to leave.
    """
    c = _http()
    try:
        r = await c.delete(f"/projects/{project_id}/leave")
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    return f"left project {project_id!r}"


@mcp.tool()
async def project_members(project_id: str) -> str:
    """List the agents that are members of a project.

    You must be a member of the project to see its members.

    Args:
        project_id: The project name to inspect.
    """
    c = _http()
    try:
        r = await c.get(f"/projects/{project_id}/members")
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    members = r.json()
    if not members:
        return f"no members in {project_id!r}"
    return "\n".join(f"{m['agent_id']} (joined {m['joined_at'][:10]})" for m in members)


@mcp.tool()
async def agent_list() -> str:
    """List all registered agents and when they were last active.

    Use this to know who's available before sending messages or assigning tasks.
    An agent that was last seen recently is likely still active.
    """
    c = _http()
    try:
        r = await c.get("/participants")
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    participants = r.json()
    if not participants:
        return "No agents."
    lines = []
    for p in participants:
        parts = [
            f"{p['agent_id']} — last seen: {p['last_seen'][:16] if p['last_seen'] else 'never'}"
        ]
        if p.get("project"):
            parts.append(f"project={p['project']}")
        if p.get("active_task_id"):
            parts.append(f"task={p['active_task_id']}")
        lines.append("  ".join(parts))
    return "\n".join(lines)


@mcp.tool()
async def agent_delete() -> str:
    """Deregister yourself from Artel.

    Removes your agent record from the server. Your memory, tasks, and messages
    are retained for the fleet. After calling this, clean up locally:
      rm ~/.config/artel/credentials
      rm .mcp.json
    """
    c = _http()
    try:
        r = await c.delete("/agents/me")
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    return (
        f"agent {_agent_id.get(settings.mcp_agent_id)!r} deregistered. "
        "Clean up locally: rm ~/.config/artel/credentials && rm .mcp.json"
    )


@mcp.tool()
def inbox_cron_setup() -> str:
    """Get instructions for scheduling automatic inbox checks via Claude Code cron.

    Call this once during your first session to set up a recurring inbox check.
    The cron will run a new Claude Code session on a schedule to check for messages
    and act on them — so other agents can reach you even when you're idle.

    Returns the CronCreate call you should make to set this up.
    """
    agent_id = _agent_id.get(settings.mcp_agent_id)
    prompt = (
        f"You are {agent_id}, an AI agent connected to Artel. "
        "Check your Artel inbox using the message_inbox() MCP tool. "
        "If there are unread messages, read them, mark them as read, and respond if appropriate. "
        "Also check task_list(status='open') for any new tasks assigned to you."
    )
    return (
        f"To schedule automatic inbox checks, call CronCreate with:\n\n"
        f"  schedule: every 30 minutes (or your preferred interval)\n"
        f"  prompt: {prompt!r}\n\n"
        "This creates a recurring Claude Code session that checks your inbox and open tasks. "
        "You only need to do this once — the cron persists across sessions."
    )


@mcp.tool()
async def agent_rename(new_id: str) -> str:
    """Rename yourself. Cascades the new ID across all memory, tasks, messages, and sessions.

    Use if your current agent ID doesn't match your project name or is a collision artifact
    (e.g. "my-project-2"). Can only rename yourself, not other agents.

    Args:
        new_id: Your new agent ID. Alphanumeric, hyphens and underscores allowed.
    """
    c = _http()
    try:
        r = await c.patch("/agents/me", json={"new_id": new_id})
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    new_agent_id = r.json()["agent_id"]
    old_key = _key_cache.get(_agent_id.get(""), "")
    _key_cache[new_agent_id] = old_key
    _agent_id.set(new_agent_id)
    return f"renamed to {new_agent_id}"


# ── Messages ─────────────────────────────────────────────────────────────────


@mcp.tool()
async def message_inbox() -> str:
    """Read and clear your unread messages. Call this at session start.

    Messages are marked read after this call. Agents use messages to coordinate,
    delegate work, share findings, or ask questions. Check it — someone may be waiting.
    """
    c = _http()
    try:
        r = await c.get("/messages/inbox")
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    messages = r.json()
    if not messages:
        return "No unread messages."
    try:
        await c.post("/messages/inbox/read-all")
    except _HTTPX_ERRORS:
        pass
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
    c = _http()
    try:
        r = await c.post("/messages", json={"to": to, "subject": subject, "body": body})
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
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
    c = _http()
    try:
        params: dict = {}
        if status:
            params["status"] = status
        if project:
            params["project"] = project
        r = await c.get("/tasks", params=params)
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
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
    expected_outcome: str = "",
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
        expected_outcome: What done looks like — specific, observable result.
        project: Project scope. Defaults to MCP_PROJECT if set.
        priority: low, normal (default), or high.
    """
    c = _http()
    try:
        r = await c.post(
            "/tasks",
            json={
                "title": title,
                "description": description,
                "expected_outcome": expected_outcome,
                "project": settings.resolve_project(project),
                "priority": priority,
            },
        )
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    t = r.json()
    return f"created [{t['id']}] [{t['priority']}] {t['title']}"


@mcp.tool()
async def task_claim(task_id: str, body: str = "") -> str:
    """Claim an open task — marks it as yours and sets status to 'claimed'.

    Always claim a task before working on it. This prevents two agents from doing
    the same work. Call task_complete(), task_fail(), or task_unclaim() when done.

    Args:
        task_id: ID from task_list() or task_create().
        body: Optional note recorded on the task's comment log (e.g. why you're picking this up).
    """
    c = _http()
    try:
        r = await c.post(f"/tasks/{task_id}/claim", json={"body": body})
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    t = r.json()
    return f"claimed [{t['id']}] {t['title']}"


@mcp.tool()
async def task_unclaim(task_id: str, body: str = "") -> str:
    """Release your claim on a task — returns it to 'open' so others can pick it up.

    Use when you're stepping away mid-flight and the task isn't done or failed
    (e.g. blocked on an async external process, handing off, ending a session).
    Only the agent that claimed it can unclaim it.

    Args:
        task_id: ID of a task you have claimed.
        body: Optional reason recorded on the task's comment log. Strongly recommended —
              the next agent to look at this task will see your context.
    """
    c = _http()
    try:
        r = await c.post(f"/tasks/{task_id}/unclaim", json={"body": body})
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    t = r.json()
    return f"unclaimed [{t['id']}] {t['title']}"


@mcp.tool()
async def task_complete(task_id: str, body: str = "") -> str:
    """Mark your claimed task as completed. Only the agent that claimed it can complete it.

    Args:
        task_id: ID of a task you have claimed.
        body: Optional note recorded on the task's comment log (e.g. result, links, follow-ups).
    """
    c = _http()
    try:
        r = await c.post(f"/tasks/{task_id}/complete", json={"body": body})
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    t = r.json()
    return f"completed [{t['id']}] {t['title']}"


@mcp.tool()
async def task_fail(task_id: str, body: str = "") -> str:
    """Mark your claimed task as failed. Use when you cannot complete it.

    Prefer this over abandoning — it unblocks other agents who can see the task
    failed and decide what to do next. If you're stepping away but the task isn't
    truly failed, use task_unclaim() instead.

    Args:
        task_id: ID of a task you have claimed.
        body: Optional reason recorded on the task's comment log. Strongly recommended.
    """
    c = _http()
    try:
        r = await c.post(f"/tasks/{task_id}/fail", json={"body": body})
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    t = r.json()
    return f"failed [{t['id']}] {t['title']}"


@mcp.tool()
async def task_comment(task_id: str, body: str) -> str:
    """Add a free-form comment to a task's chronological log.

    Use to record progress notes, intermediate findings, or context any agent looking
    at this task should see. The task description holds the canonical spec; the
    comment log holds the running history. Status changes (claim, unclaim, complete,
    fail) also appear in the log automatically.

    Args:
        task_id: ID of the task to comment on.
        body: Comment text.
    """
    c = _http()
    try:
        r = await c.post(f"/tasks/{task_id}/comments", json={"body": body})
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    cmt = r.json()
    return f"commented [{cmt['id']}] on task {cmt['task_id']}"


@mcp.tool()
async def task_get(task_id: str) -> str:
    """Fetch full details of a task by ID, including its chronological comment log.

    Args:
        task_id: The UUID of the task.
    """
    c = _http()
    try:
        r = await c.get(f"/tasks/{task_id}")
        r.raise_for_status()
        cr = await c.get(f"/tasks/{task_id}/comments")
        cr.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    t = r.json()
    comments = cr.json()
    assigned = t["assigned_to"] or "unassigned"
    project = f" | project: {t['project']}" if t.get("project") else ""
    lines = [
        f"[{t['id']}] [{t['status']}] [{t['priority']}] {t['title']}",
        f"created by: {t['created_by']} | assigned to: {assigned}{project}",
    ]
    if t["description"]:
        lines.append(t["description"])
    if t.get("expected_outcome"):
        lines.append(f"expected outcome: {t['expected_outcome']}")
    if comments:
        lines.append("")
        lines.append("comments:")
        for cmt in comments:
            tag = f"[{cmt['kind']}]" if cmt["kind"] != "comment" else ""
            prefix = f"  {cmt['created_at']} {cmt['agent_id']} {tag}".rstrip()
            body = cmt["body"] or ("" if cmt["kind"] == "comment" else f"({cmt['kind']})")
            lines.append(f"{prefix} {body}".rstrip())
    return "\n".join(lines)


@mcp.tool()
async def task_update(
    task_id: str,
    description: str | None = None,
    append: bool = False,
    title: str | None = None,
    priority: str | None = None,
) -> str:
    """Update a task's description, title, or priority.

    Use to record progress notes on a task you're working on, or to correct metadata.
    Any agent in the project can update a task, not just the assignee.

    Args:
        task_id: ID of the task to update.
        description: Text for the description field. Omit to leave unchanged.
        append: If True, appends description to existing content (preserves history).
                If False (default), replaces entirely.
        title: New title. Omit to leave unchanged.
        priority: low, normal, or high. Omit to leave unchanged.
    """
    patch: dict = {}
    if description is not None:
        patch["description"] = description
        patch["append"] = append
    if title is not None:
        patch["title"] = title
    if priority is not None:
        patch["priority"] = priority
    c = _http()
    try:
        r = await c.patch(f"/tasks/{task_id}", json=patch)
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    t = r.json()
    return f"updated [{t['id']}] {t['title']}"


# ── Events ───────────────────────────────────────────────────────────────────


@mcp.tool()
async def event_emit(event_type: str, payload: dict | None = None) -> str:
    """Emit a custom event to the Artel event bus.

    Use for pub/sub signaling between agents. Other agents watching the SSE stream
    will receive this in real time. Useful for announcing completions, progress updates,
    or triggering coordinated action across the fleet.

    Args:
        event_type: Dot-separated event type, e.g. "analysis.complete" or "deploy.ready".
        payload: Arbitrary JSON payload to include with the event.
    """
    c = _http()
    try:
        r = await c.post("/events", json={"type": event_type, "payload": payload or {}})
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    ev = r.json()
    return f"emitted [{ev['id']}] {ev['type']}"


# ── Feeds ─────────────────────────────────────────────────────────────────────


@mcp.tool()
async def feed_subscribe(
    url: str,
    name: str,
    project: str,
    tags: list[str] | None = None,
    interval_min: int = 30,
    max_per_poll: int = 20,
) -> str:
    """Subscribe to an RSS or Atom feed. New items are written to memory automatically.

    Each item is written with confidence=0.5 and tagged 'feed-item' + 'unprocessed'.
    The archivist will synthesize and clean up over time. Subscriptions are project-scoped:
    the same feed URL in two projects creates two independent subscriptions.

    Args:
        url: RSS or Atom feed URL.
        name: Human-readable name shown in each memory entry (e.g. "Claude Code releases").
        project: Project to write feed memories into. Required.
        tags: Additional tags applied to every memory entry from this feed.
        interval_min: How often to poll in minutes (default 30, max 1440).
        max_per_poll: Max new items to ingest per poll cycle (default 20, max 100).
    """
    c = _http()
    try:
        r = await c.post(
            "/feeds",
            json={
                "url": url,
                "name": name,
                "project": settings.resolve_project(project),
                "tags": tags or [],
                "interval_min": interval_min,
                "max_per_poll": max_per_poll,
            },
        )
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    f = r.json()
    return (
        f"subscribed [{f['id']}] {f['name']} → project={f['project']} every {f['interval_min']}min"
    )


@mcp.tool()
async def feed_list(project: str | None = None) -> str:
    """List active feed subscriptions visible to you.

    Args:
        project: Filter by project. Omit to list all accessible feeds.
    """
    c = _http()
    try:
        r = await c.get("/feeds")
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    feeds = r.json()
    if project:
        feeds = [f for f in feeds if f["project"] == project]
    if not feeds:
        return "No feed subscriptions."
    lines = []
    for f in feeds:
        tags = ", ".join(f["tags"]) if f["tags"] else "—"
        last = (f["last_fetched_at"] or "never")[:16]
        lines.append(
            f"[{f['id']}] {f['name']} | {f['url']} | project={f['project']}"
            f" | every {f['interval_min']}min | last={last} | tags={tags}"
        )
    return "\n".join(lines)


@mcp.tool()
async def feed_unsubscribe(feed_id: str) -> str:
    """Unsubscribe from a feed. Removes the subscription and its seen-item history.

    Args:
        feed_id: ID from feed_list().
    """
    c = _http()
    try:
        r = await c.delete(f"/feeds/{feed_id}")
        r.raise_for_status()
    except _HTTPX_ERRORS as e:
        return _err(e)
    return f"unsubscribed [{feed_id}]"
