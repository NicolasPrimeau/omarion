import asyncio
import logging
from datetime import UTC, datetime, timedelta

from .client import ArtelClient
from .config import settings
from .llm import complete, is_configured

log = logging.getLogger(__name__)


def _utc_ago(hours: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


async def on_task_completed(task_id: str, agent_id: str, client: ArtelClient) -> None:
    try:
        task = await client.get_task(task_id)
    except Exception as e:
        log.warning("could not fetch completed task %s: %s", task_id, e)
        return

    query = f"{task['title']} {task.get('description') or ''}"
    related = await client.search_memory(query, limit=5)
    if not related:
        return

    snippet_lines = [
        f"- [{r['id'][:8]}] {r['content'][:120].replace(chr(10), ' ')}" for r in related[:3]
    ]
    content = (
        f'Task completed: "{task["title"]}" (by {agent_id}).\n'
        f"Expected outcome: {task.get('expected_outcome') or 'not specified'}\n\n"
        f"Related knowledge at completion:\n" + "\n".join(snippet_lines)
    )
    try:
        await client.write_memory(content=content, type="memory", tags=["task-completion"])
    except Exception as e:
        log.warning("could not write task completion observation for %s: %s", task_id, e)


async def on_task_failed(task_id: str, agent_id: str, client: ArtelClient) -> None:
    try:
        task = await client.get_task(task_id)
    except Exception as e:
        log.warning("could not fetch failed task %s: %s", task_id, e)
        return

    content = f'Task failed: "{task["title"]}" (attempted by {agent_id}).'
    if task.get("description"):
        content += f"\nDescription: {task['description']}"

    try:
        await client.write_memory(
            content=content,
            type="memory",
            tags=["task-failure"],
            confidence=0.8,
        )
    except Exception as e:
        log.warning("could not write task failure observation for %s: %s", task_id, e)
        return

    similar = await client.search_memory(f"task failed {task['title']}", limit=5)
    failures = [
        e
        for e in similar
        if "task-failure" in e.get("tags", []) and e["agent_id"] == settings.archivist_id
    ]
    if len(failures) >= 2 and is_configured():
        try:
            await client.create_task(
                title=f"Investigate recurring failure: {task['title'][:60]}",
                description=(
                    f"This task has failed {len(failures) + 1} times. "
                    f"Latest attempt by {agent_id}. Review failure pattern and resolve blockers."
                ),
                priority="high",
                project=task.get("project"),
            )
        except Exception as e:
            log.warning("could not create investigation task for repeated failure: %s", e)


# ── Synthesis helpers ────────────────────────────────────────────────────────


async def _find_existing_doc(
    client: ArtelClient,
    scope: str,
    for_agent: str | None = None,
    project: str | None = None,
) -> dict | None:
    try:
        if scope == "agent" and for_agent:
            candidates = await client.list_entries(scope="agent", agent=for_agent, type="doc")
        elif scope == "project" and project:
            candidates = await client.list_entries(scope="project", project=project, type="doc")
        else:
            candidates = await client.list_entries(scope="project", type="doc")
            candidates = [e for e in candidates if not e.get("project")]
        candidates = sorted(
            [e for e in candidates if "synthesis" in (e.get("tags") or [])],
            key=lambda e: e["updated_at"],
            reverse=True,
        )
        return candidates[0] if candidates else None
    except Exception:
        return None


async def _llm_synthesis(system: str, user: str) -> str | None:
    for attempt in range(3):
        try:
            text = await complete(system=system, user=user, max_tokens=2048)
            return text
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if attempt == 2:
                log.error("synthesis LLM call failed after 3 attempts: %s", e)
                return None
            await asyncio.sleep(2.0**attempt)
    return None


async def _save_synthesis(
    client: ArtelClient,
    text: str,
    tags: list[str],
    scope: str,
    project: str | None,
    for_agent: str | None,
    existing_doc: dict | None,
) -> None:
    if existing_doc:
        try:
            await client.patch_memory(existing_doc["id"], content=text, tags=tags)
            return
        except Exception as e:
            log.warning("could not update synthesis doc, creating new: %s", e)
    await client.write_memory(
        content=text,
        type="doc",
        tags=tags,
        scope=scope,
        project=project,
        for_agent=for_agent,
    )


async def _act_on_synthesis(text: str, project: str | None, client: ArtelClient) -> None:
    actions_section = ""
    in_actions = False
    for line in text.splitlines():
        if line.strip().startswith("### Recommended Actions"):
            in_actions = True
            continue
        if in_actions:
            if line.startswith("### "):
                break
            if line.strip().startswith("- "):
                actions_section += line.strip()[2:].strip() + "\n"

    if not actions_section.strip():
        return

    active_titles: set[str] = set()
    try:
        for status in ("open", "claimed"):
            for t in await client.list_tasks(status=status, limit=100):
                active_titles.add(t["title"].lower()[:80])
    except Exception as e:
        log.warning("could not load active tasks for dedup: %s", e)

    for line in actions_section.strip().splitlines():
        action = line.strip()
        if not action:
            continue
        if action.lower()[:80] in active_titles:
            log.info("skipping duplicate synthesis task: %s", action[:60])
            continue
        try:
            await client.create_task(
                title=action[:120],
                description="Identified by archivist synthesis. Review synthesis doc in shared memory.",
                priority="normal",
                project=project,
            )
            log.info("archivist created task from synthesis: %s", action[:60])
            active_titles.add(action.lower()[:80])
        except Exception as e:
            log.warning("could not create synthesis action task: %s", e)


# ── Three synthesis passes ───────────────────────────────────────────────────


async def _run_agent_synthesis(target_agent: str, client: ArtelClient) -> None:
    existing_doc = await _find_existing_doc(client, scope="agent", for_agent=target_agent)
    since = existing_doc["updated_at"] if existing_doc else _utc_ago(48)

    entries = await client.get_delta(since, scope="agent", agent=target_agent)
    entries = [e for e in entries if e["agent_id"] == target_agent]

    if len(entries) < 2:
        return

    memory_block = "\n\n".join(
        f"[{e['id'][:8]}] type={e['type']}\n{e['content'][:600]}" for e in entries[:40]
    )

    text = await _llm_synthesis(
        system=(
            f"You are the Artel archivist reviewing the private memory store of agent '{target_agent}'. "
            "Your role is to improve the quality and coherence of this agent's knowledge."
        ),
        user=(
            f"Recent entries from agent '{target_agent}'s private memory:\n\n{memory_block}\n\n"
            "Write a synthesis document with these sections. Omit any section with nothing relevant.\n\n"
            "### Patterns\n"
            "Recurring themes or facts in this agent's knowledge.\n\n"
            "### Redundancies\n"
            "Entries that overlap or repeat the same fact — cite IDs like [id].\n\n"
            "### Gaps\n"
            "What this agent appears not to know or hasn't recorded.\n\n"
            "### Recommended Actions\n"
            "Specific tasks or investigations for this agent. One per line, starting with `- `."
        ),
    )
    if not text:
        return

    tags = ["synthesis", f"agent:{target_agent}"]
    await _save_synthesis(
        client,
        text,
        tags,
        scope="agent",
        project=None,
        for_agent=target_agent,
        existing_doc=existing_doc,
    )
    await _act_on_synthesis(text, project=None, client=client)
    log.info("agent synthesis complete for %s", target_agent)


async def _run_project_synthesis(project: str, client: ArtelClient) -> None:
    existing_doc = await _find_existing_doc(client, scope="project", project=project)
    since = existing_doc["updated_at"] if existing_doc else _utc_ago(24)

    entries = await client.get_delta(since, scope="project", project=project)
    entries = [e for e in entries if e["agent_id"] != settings.archivist_id]

    if len(entries) < 2:
        return

    recently_completed = []
    try:
        all_tasks = await client.list_tasks(status="completed", limit=20)
        cutoff = datetime.fromisoformat(since.replace("Z", "+00:00"))
        recently_completed = [
            t
            for t in all_tasks
            if t.get("project") == project
            and datetime.fromisoformat(t["updated_at"].replace("Z", "+00:00")) > cutoff
        ]
    except Exception as e:
        log.warning("could not fetch recent tasks for project synthesis %s: %s", project, e)

    memory_block = "\n\n".join(
        f"[{e['id'][:8]}] agent={e['agent_id']} type={e['type']}\n{e['content'][:600]}"
        for e in entries[:40]
    )

    task_block = ""
    if recently_completed:
        task_lines = [
            f'- "{t["title"]}" completed by {t["assigned_to"] or t["created_by"]}'
            + (f" — outcome: {t['expected_outcome']}" if t.get("expected_outcome") else "")
            for t in recently_completed
        ]
        task_block = "\n\nCompleted tasks since last synthesis:\n" + "\n".join(task_lines)

    text = await _llm_synthesis(
        system=(
            f"You are the Artel archivist synthesizing knowledge for project '{project}'. "
            "Your role is to surface cross-agent insights within this project."
        ),
        user=(
            f"New memory activity in project '{project}' since last synthesis:\n\n{memory_block}"
            f"{task_block}\n\n"
            "Write a synthesis document with these sections. Omit any section with nothing relevant.\n\n"
            "### Connections\n"
            "Meaningful relationships between entries from different agents. Cite entry IDs like [id].\n\n"
            "### Contradictions\n"
            'Conflicting information between agents. Format: "Agent X states [Y]; Agent Z states [W]."\n\n'
            "### Patterns\n"
            "Recurring themes, repeated issues, or trends across entries and tasks.\n\n"
            "### Gaps\n"
            "What appears unknown or underinvestigated within this project.\n\n"
            "### Recommended Actions\n"
            "Specific tasks or investigations that should happen. One per line, starting with `- `."
        ),
    )
    if not text:
        return

    tags = ["synthesis", project]
    await _save_synthesis(
        client,
        text,
        tags,
        scope="project",
        project=project,
        for_agent=None,
        existing_doc=existing_doc,
    )
    await _act_on_synthesis(text, project=project, client=client)
    log.info("project synthesis complete for %s", project)


async def _run_global_synthesis(client: ArtelClient) -> None:
    existing_doc = await _find_existing_doc(client, scope="project")
    since = existing_doc["updated_at"] if existing_doc else _utc_ago(24)

    raw_entries = await client.get_delta(since)
    entries = [
        e for e in raw_entries if e["agent_id"] != settings.archivist_id and not e.get("project")
    ]

    project_synth_docs = await client.list_entries(scope="project", type="doc")
    project_synth_docs = [
        e
        for e in project_synth_docs
        if e["agent_id"] == settings.archivist_id
        and "synthesis" in (e.get("tags") or [])
        and e.get("project")
    ]

    if len(entries) < 2 and not project_synth_docs:
        return

    memory_block = "\n\n".join(
        f"[{e['id'][:8]}] agent={e['agent_id']} type={e['type']}\n{e['content'][:600]}"
        for e in entries[:30]
    )

    synth_block = ""
    if project_synth_docs:
        synth_lines = [
            f"[{d['id'][:8]}] project={d['project']}\n{d['content'][:400]}"
            for d in project_synth_docs[:10]
        ]
        synth_block = "\n\nProject synthesis summaries:\n\n" + "\n\n".join(synth_lines)

    text = await _llm_synthesis(
        system=(
            "You are the Artel archivist synthesizing knowledge across the entire fleet. "
            "Your role is to surface what no individual project can see."
        ),
        user=(
            f"Global (unscoped) memory activity since last synthesis:\n\n{memory_block}"
            f"{synth_block}\n\n"
            "Write a synthesis document with these sections. Omit any section with nothing relevant.\n\n"
            "### Connections\n"
            "Meaningful relationships between entries or projects. Cite entry IDs like [id].\n\n"
            "### Contradictions\n"
            "Conflicting information across projects or agents.\n\n"
            "### Patterns\n"
            "Fleet-wide recurring themes, issues, or trends.\n\n"
            "### Gaps\n"
            "What appears unknown or underinvestigated across the fleet.\n\n"
            "### Recommended Actions\n"
            "Specific tasks or investigations that should happen. One per line, starting with `- `."
        ),
    )
    if not text:
        return

    tags = ["synthesis", "global"]
    await _save_synthesis(
        client,
        text,
        tags,
        scope="project",
        project=None,
        for_agent=None,
        existing_doc=existing_doc,
    )
    await _act_on_synthesis(text, project=None, client=client)
    log.info("global synthesis complete")


async def run_synthesis(client: ArtelClient) -> None:
    if not is_configured():
        return

    recent = await client.get_delta(_utc_ago(24))
    recent = [e for e in recent if e["agent_id"] != settings.archivist_id]

    active_agents = list({e["agent_id"] for e in recent if e.get("scope") == "agent"})
    active_projects = list({e["project"] for e in recent if e.get("project")})

    for target_agent in active_agents:
        try:
            await _run_agent_synthesis(target_agent, client)
        except Exception as e:
            log.warning("agent synthesis failed for %s: %s", target_agent, e)

    for project in active_projects:
        try:
            await _run_project_synthesis(project, client)
        except Exception as e:
            log.warning("project synthesis failed for %s: %s", project, e)

    try:
        await _run_global_synthesis(client)
    except Exception as e:
        log.warning("global synthesis failed: %s", e)


# ── Memory maintenance ───────────────────────────────────────────────────────


async def decay_confidence(client: ArtelClient) -> None:
    cutoff = (datetime.now(UTC) - timedelta(days=settings.decay_window_days)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    entries = await client.list_entries(updated_before=cutoff)
    entries = [e for e in entries if e["agent_id"] != settings.archivist_id]

    for entry in entries:
        current = entry["confidence"]
        if current <= settings.decay_floor:
            continue
        new_conf = max(settings.decay_floor, current * settings.decay_rate)
        try:
            await client.patch_memory(entry["id"], confidence=new_conf)
        except Exception as e:
            log.warning("decay failed for %s: %s", entry["id"], e)


async def run_promotion(client: ArtelClient) -> None:
    cutoff = (datetime.now(UTC) - timedelta(days=settings.promotion_stability_days)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    memory_entries = await client.list_entries(
        type="memory",
        min_version=settings.promotion_memory_min_version,
        updated_before=cutoff,
    )
    for entry in memory_entries:
        if entry["agent_id"] == settings.archivist_id:
            continue
        try:
            await client.patch_memory(entry["id"], type="doc")
        except Exception as e:
            log.warning("memory promotion failed for %s: %s", entry["id"], e)
