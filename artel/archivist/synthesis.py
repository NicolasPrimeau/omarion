import asyncio
import json
import logging
import math
from datetime import UTC, datetime, timedelta

from .client import ArtelClient
from .config import settings
from .llm import complete, is_configured

log = logging.getLogger(__name__)

_KNOWN_OPS = {"merge", "promote", "prune", "tag", "adjust_confidence", "task"}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def _build_directive_preamble(directives: list[dict]) -> str:
    if not directives:
        return ""
    lines = ["--- STANDING DIRECTIVES ---"]
    for i, d in enumerate(directives, 1):
        scope_label = (
            "agent-private"
            if d.get("scope") == "agent"
            else f"project: {d.get('project') or 'global'}"
        )
        lines.append(f"[{i}] ({scope_label}) {d['content']}")
    lines.append("--- END DIRECTIVES ---")
    return "\n".join(lines)


async def _check_directive_conflicts(directives: list[dict], client: ArtelClient) -> str | None:
    if len(directives) < 2:
        return None
    threshold = settings.directive_conflict_threshold
    from artel.store.embeddings import embed

    embeddings = []
    for d in directives:
        try:
            embeddings.append(embed(d["content"]))
        except Exception:
            embeddings.append([0.0] * 384)
    for i in range(len(directives)):
        for j in range(i + 1, len(directives)):
            sim = _cosine_similarity(embeddings[i], embeddings[j])
            if sim >= threshold:
                warning = (
                    f"WARNING: Directives [{i + 1}] and [{j + 1}] may conflict "
                    f"(similarity={sim:.2f}). Review and reconcile."
                )
                try:
                    from artel.server.config import settings as server_settings

                    await client.send_message(
                        to=server_settings.ui_agent_id,
                        subject="Directive conflict detected",
                        body=(
                            f"Two standing directives have high similarity ({sim:.2f}) and may conflict:\n\n"
                            f"[{i + 1}] {directives[i]['content']}\n\n"
                            f"[{j + 1}] {directives[j]['content']}"
                        ),
                    )
                except Exception as e:
                    log.warning("could not send directive conflict message: %s", e)
                return warning
    return None


def _utc_ago(hours: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_operations(text: str) -> list[dict]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        end = len(lines) - 1
        while end > 0 and not lines[end].strip().startswith("```"):
            end -= 1
        stripped = "\n".join(lines[1:end]).strip()
    try:
        parsed = json.loads(stripped)
    except Exception as e:
        log.warning("could not parse synthesis operations JSON: %s", e)
        return []
    if not isinstance(parsed, list):
        log.warning("synthesis operations is not a JSON array")
        return []
    ops = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        op_name = item.get("op")
        if op_name not in _KNOWN_OPS:
            log.warning("skipping unknown synthesis op: %s", op_name)
            continue
        ops.append(item)
    return ops


async def _execute_operations(ops: list[dict], client: ArtelClient, entries: list[dict]) -> None:
    valid_ids = {e["id"] for e in entries}
    entries_by_id = {e["id"]: e for e in entries}

    for op in ops:
        op_name = op.get("op")
        try:
            if op_name == "merge":
                ids = op.get("entries", [])
                if len(ids) < 2:
                    log.warning("merge op requires at least 2 entries, got %d", len(ids))
                    continue
                if any(eid not in valid_ids for eid in ids):
                    log.warning("merge op references hallucinated IDs: %s", ids)
                    continue
                merged_content = op.get("merged_content", "")
                if not merged_content:
                    log.warning("merge op missing merged_content")
                    continue
                source_entries = [entries_by_id[eid] for eid in ids]
                primary = max(source_entries, key=lambda e: e.get("confidence", 1.0))
                entry_type = primary.get("type", "memory")
                merged_tags = list({tag for e in source_entries for tag in e.get("tags", [])})
                projects = {e.get("project") for e in source_entries}
                merged_project = projects.pop() if len(projects) == 1 else None
                await client.write_memory(
                    content=merged_content,
                    type=entry_type,
                    tags=merged_tags,
                    parents=ids,
                    project=merged_project,
                )
                for eid in ids:
                    await client.delete_memory(eid)
                log.info("archivist merged entries %s", ids)

            elif op_name == "promote":
                eid = op.get("entry")
                if eid not in valid_ids:
                    log.warning("promote op references hallucinated ID: %s", eid)
                    continue
                await client.patch_memory(eid, type="doc")
                log.info("archivist promoted entry %s", eid)

            elif op_name == "prune":
                eid = op.get("entry")
                if eid not in valid_ids:
                    log.warning("prune op references hallucinated ID: %s", eid)
                    continue
                await client.delete_memory(eid)
                log.info("archivist pruned entry %s", eid)

            elif op_name == "tag":
                eid = op.get("entry")
                if eid not in valid_ids:
                    log.warning("tag op references hallucinated ID: %s", eid)
                    continue
                add_tags = op.get("add_tags", [])
                current_entry = await client.get_memory(eid)
                existing_tags = current_entry.get("tags", [])
                merged_tags = list(set(existing_tags) | set(add_tags))
                await client.patch_memory(eid, tags=merged_tags)
                log.info("archivist tagged entry %s with %s", eid, add_tags)

            elif op_name == "adjust_confidence":
                eid = op.get("entry")
                if eid not in valid_ids:
                    log.warning("adjust_confidence op references hallucinated ID: %s", eid)
                    continue
                confidence = max(0.0, min(1.0, float(op.get("confidence", 1.0))))
                await client.patch_memory(eid, confidence=confidence)
                log.info("archivist adjusted confidence of %s to %s", eid, confidence)

            elif op_name == "task":
                title = op.get("title", "")
                if not title:
                    log.warning("task op missing title")
                    continue
                priority = op.get("priority", "normal")
                if priority not in ("low", "normal", "high"):
                    priority = "normal"
                await client.create_task(
                    title=title,
                    description=op.get("description"),
                    priority=priority,
                    project=op.get("project"),
                )
                log.info("archivist created task: %s", title[:60])

        except Exception as e:
            log.warning("synthesis op %s failed: %s", op_name, e)


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

    directives: list[dict] = []
    try:
        directives = await client.get_directives()
    except Exception as e:
        log.warning("could not load directives for synthesis: %s", e)

    conflict_warning: str | None = None
    if directives:
        try:
            conflict_warning = await _check_directive_conflicts(directives, client)
        except Exception as e:
            log.warning("directive conflict check failed: %s", e)

    entries = await client.get_delta(_utc_ago(24))
    entries = [
        e
        for e in entries
        if e["agent_id"] != settings.archivist_id and e.get("type") != "directive"
    ]

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
        f"[{e['id']}] agent={e['agent_id']} type={e['type']} conf={e.get('confidence', 1.0)} tags={e.get('tags', [])}\n{e['content']}"
        for e in entries
    )

    task_block = ""
    if recently_completed:
        task_lines = [
            f'- "{t["title"]}" completed by {t["assigned_to"] or t["created_by"]}'
            + (f" — outcome: {t['expected_outcome']}" if t.get("expected_outcome") else "")
            for t in recently_completed
        ]
        task_block = "\n\nCompleted tasks (last 24h):\n" + "\n".join(task_lines)

    preamble = _build_directive_preamble(directives)
    if conflict_warning:
        preamble = conflict_warning + "\n\n" + preamble if preamble else conflict_warning

    system_prompt = "You are the Artel archivist — an invisible curator of project memory. Your only job is to keep the memory store clean, non-redundant, and high-signal by issuing precise operations. You do not write reports. You act."
    if preamble:
        system_prompt = preamble + "\n\n" + system_prompt

    text = None
    for attempt in range(3):
        try:
            text = await complete(
                system=system_prompt,
                user=(
                    f"Memory entries written or updated in the last 24h:\n\n{memory_block}"
                    f"{task_block}\n\n"
                    "Directives are in the system prompt. Follow them above all else.\n\n"
                    "Issue a JSON array of operations to perform on this memory. Available ops: merge, promote, prune, tag, adjust_confidence, task.\n\n"
                    "Rules:\n"
                    "- Merge entries that are redundant or say the same thing from different agents. Write merged_content that synthesizes both.\n"
                    "- Promote entries that are stable, high-signal, and likely to remain true.\n"
                    "- Prune entries that are superseded, low-signal, or contradicted by higher-confidence entries.\n"
                    "- Use tag/adjust_confidence to surface connections or correct signal strength.\n"
                    "- Create tasks ONLY for work requiring an external agent — never for memory operations.\n"
                    "- When in doubt about an operation, omit it. Conservatism is correct.\n"
                    "- Output ONLY the JSON array. No prose, no explanation, no markdown fences."
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

    ops = _parse_operations(text)
    await _execute_operations(ops, client, entries)


async def decay_confidence(client: ArtelClient) -> None:
    cutoff = (datetime.now(UTC) - timedelta(days=settings.decay_window_days)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    entries = await client.list_entries(updated_before=cutoff)
    entries = [
        e
        for e in entries
        if e["agent_id"] != settings.archivist_id and e.get("type") != "directive"
    ]

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
        if entry.get("type") == "directive":
            continue
        try:
            await client.patch_memory(entry["id"], type="doc")
        except Exception as e:
            log.warning("memory promotion failed for %s: %s", entry["id"], e)
