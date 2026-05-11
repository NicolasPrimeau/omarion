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
        await client.write_memory(
            content=content,
            type="memory",
            tags=["task-completion"],
            project=task.get("project"),
        )
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
            project=task.get("project"),
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


async def run_synthesis(client: ArtelClient) -> None:
    if not is_configured():
        return

    entries = await client.get_delta(_utc_ago(24))
    entries = [e for e in entries if e["agent_id"] != settings.archivist_id]

    if len(entries) < 2:
        return

    recently_completed = []
    try:
        all_tasks = await client.list_tasks(status="completed", limit=20)
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        recently_completed = [
            t
            for t in all_tasks
            if datetime.fromisoformat(t["updated_at"].replace("Z", "+00:00")) > cutoff
        ]
    except Exception as e:
        log.warning("could not fetch recent tasks for synthesis: %s", e)

    memory_block = "\n\n".join(
        f"[{e['id'][:8]}] agent={e['agent_id']} type={e['type']}\n{e['content']}" for e in entries
    )

    task_block = ""
    if recently_completed:
        task_lines = [
            f'- "{t["title"]}" completed by {t["assigned_to"] or t["created_by"]}'
            + (f" — outcome: {t['expected_outcome']}" if t.get("expected_outcome") else "")
            for t in recently_completed
        ]
        task_block = "\n\nCompleted tasks (last 24h):\n" + "\n".join(task_lines)

    text = None
    for attempt in range(3):
        try:
            text = await complete(
                system=(
                    "You are the Artel archivist. Your role is to surface what no individual agent "
                    "can see by synthesizing knowledge across the entire fleet."
                ),
                user=(
                    f"Agent memory activity (last 24h):\n\n{memory_block}"
                    f"{task_block}\n\n"
                    "Write a synthesis document with these sections. Omit any section with nothing relevant.\n\n"
                    "### Connections\n"
                    "Meaningful relationships between entries from different agents. Cite entry IDs like [id].\n\n"
                    "### Contradictions\n"
                    'Conflicting information between agents. Format: "Agent X states [Y]; Agent Z states [W]."\n\n'
                    "### Patterns\n"
                    "Recurring themes, repeated issues, or trends across entries and tasks.\n\n"
                    "### Gaps\n"
                    "What appears unknown or underinvestigated. What questions remain unanswered?\n\n"
                    "### Recommended Actions\n"
                    "Specific tasks or investigations that should happen. One per line, starting with `- `."
                ),
                max_tokens=2048,
            )
            break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if attempt == 2:
                log.error("synthesis LLM call failed after 3 attempts: %s", e)
                return
            await asyncio.sleep(2.0**attempt)

    if not text:
        return

    tags = list({e.get("project") for e in entries if e.get("project")} | {"synthesis"})
    await client.write_memory(content=text, type="doc", tags=tags)

    await _act_on_synthesis(text, entries, client)


async def _act_on_synthesis(text: str, entries: list[dict], client: ArtelClient) -> None:
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

    project = next((e.get("project") for e in entries if e.get("project")), None)
    for line in actions_section.strip().splitlines():
        action = line.strip()
        if not action:
            continue
        try:
            await client.create_task(
                title=action[:120],
                description="Identified by archivist synthesis. Review synthesis doc in shared memory.",
                priority="medium",
                project=project,
            )
            log.info("archivist created task from synthesis: %s", action[:60])
        except Exception as e:
            log.warning("could not create synthesis action task: %s", e)


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
