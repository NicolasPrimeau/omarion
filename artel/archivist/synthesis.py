import asyncio
import json
import logging
import math
from datetime import UTC, datetime, timedelta

from .client import ArtelClient
from .config import settings
from .llm import complete, is_configured

log = logging.getLogger(__name__)

_KNOWN_OPS = {"merge", "promote", "prune", "tag", "adjust_confidence", "task", "split", "extract"}


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
                entry = entries_by_id[eid]
                current_conf = entry.get("confidence", 1.0)
                if current_conf <= settings.decay_floor:
                    await client.delete_memory(eid)
                    log.info("archivist pruned entry %s", eid)
                else:
                    existing_tags = entry.get("tags", [])
                    merged_tags = list(set(existing_tags) | {"archivist-flagged"})
                    await client.patch_memory(
                        eid, confidence=settings.decay_floor, tags=merged_tags
                    )
                    log.info("archivist flagged entry %s for decay", eid)

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

            elif op_name == "split":
                eid = op.get("entry")
                if eid not in valid_ids:
                    log.warning("split op references hallucinated ID: %s", eid)
                    continue
                parts = op.get("parts", [])
                if len(parts) < 2:
                    log.warning("split op requires at least 2 parts, got %d", len(parts))
                    continue
                if any(not (p.get("content") or "").strip() for p in parts):
                    log.warning("split op has part with empty content")
                    continue
                original = entries_by_id[eid]
                original_tags = original.get("tags", [])
                original_project = original.get("project")
                original_type = original.get("type", "memory")
                for part in parts:
                    part_tags = list(set(original_tags) | set(part.get("tags", [])))
                    await client.write_memory(
                        content=part["content"],
                        type=original_type,
                        tags=part_tags,
                        parents=[eid],
                        project=original_project,
                    )
                await client.delete_memory(eid)
                log.info("archivist split entry %s into %d parts", eid, len(parts))

            elif op_name == "extract":
                from_id = op.get("from")
                into_id = op.get("into")
                if from_id not in valid_ids or into_id not in valid_ids:
                    log.warning(
                        "extract op references hallucinated IDs: from=%s into=%s", from_id, into_id
                    )
                    continue
                if from_id == into_id:
                    log.warning("extract op from and into are the same ID: %s", from_id)
                    continue
                merged_content = op.get("merged_content", "")
                remaining_content = op.get("remaining_content", "")
                await client.patch_memory(into_id, content=merged_content)
                if remaining_content and remaining_content.strip():
                    await client.patch_memory(from_id, content=remaining_content)
                else:
                    await client.delete_memory(from_id)
                log.info("archivist extracted segment from %s into %s", from_id, into_id)

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
    related = await client.search_memory(query, limit=8)

    if not is_configured():
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
        return

    memory_block = ""
    if related:
        memory_block = "\n\n".join(
            f"[{r['id']}] {r['content'][:300].replace(chr(10), ' ')}" for r in related
        )

    try:
        text = await complete(
            system='You are the Artel archivist. A task just completed. Extract any generalizable facts that should be written or updated in project memory. Output a JSON object with keys: facts (list of strings, each a standalone memory entry to write), update_ids (list of memory entry IDs to update with new content, format: [{"id": "<id>", "content": "<new content>"}]). Be conservative — only extract facts that apply project-wide and will still be true in a week. If nothing meaningful, output {"facts": [], "update_ids": []}.',
            user=(
                f'Task: "{task["title"]}"\n'
                f"Description: {task.get('description') or 'none'}\n"
                f"Expected outcome: {task.get('expected_outcome') or 'none'}\n"
                f"Completed by: {agent_id}\n\n"
                + (f"Existing related memory:\n{memory_block}\n\n" if memory_block else "")
                + "What project-wide facts, if any, does this completion establish or update?"
            ),
            max_tokens=1024,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning("task completion LLM call failed for %s: %s", task_id, e)
        return

    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        end = len(lines) - 1
        while end > 0 and not lines[end].strip().startswith("```"):
            end -= 1
        stripped = "\n".join(lines[1:end]).strip()
    try:
        result = json.loads(stripped)
    except Exception:
        log.warning("task completion LLM returned unparseable JSON for %s", task_id)
        return

    valid_ids = {r["id"] for r in related}
    facts_written = 0
    updates_applied = 0
    for fact in result.get("facts", []):
        if not isinstance(fact, str) or not fact.strip():
            continue
        try:
            await client.write_memory(
                content=fact,
                type="memory",
                tags=["task-completion", "archivist-extracted"],
                project=task.get("project"),
            )
            facts_written += 1
            log.info("archivist extracted fact from task %s", task_id[:8])
        except Exception as e:
            log.warning("could not write extracted fact for task %s: %s", task_id, e)

    for update in result.get("update_ids", []):
        if not isinstance(update, dict):
            continue
        uid = update.get("id", "")
        new_content = update.get("content", "")
        if uid not in valid_ids or not new_content.strip():
            log.warning("task completion update references unknown or empty id: %s", uid)
            continue
        try:
            await client.patch_memory(uid, content=new_content)
            updates_applied += 1
            log.info("archivist updated memory %s from task completion %s", uid[:8], task_id[:8])
        except Exception as e:
            log.warning("could not update memory %s from task %s: %s", uid, task_id, e)

    await client.log(
        action="fact_extraction",
        message=f'task "{task["title"][:60]}" completed: {facts_written} fact(s) written, {updates_applied} memor{"y" if updates_applied == 1 else "ies"} updated',
        details={
            "task_id": task_id,
            "facts_written": facts_written,
            "updates_applied": updates_applied,
        },
    )


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
                    "Issue a JSON array of operations to perform on this memory. Available ops: merge, promote, prune, tag, adjust_confidence, task, split, extract.\n\n"
                    "Op schemas:\n"
                    '- {"op": "merge", "entries": ["<id>", "<id>", ...], "merged_content": "<synthesized text>"}\n'
                    '- {"op": "promote", "entry": "<id>"}\n'
                    '- {"op": "prune", "entry": "<id>"}\n'
                    '- {"op": "tag", "entry": "<id>", "add_tags": ["<tag>", ...]}\n'
                    '- {"op": "adjust_confidence", "entry": "<id>", "confidence": <0.0-1.0>}\n'
                    '- {"op": "task", "title": "<title>", "description": "<desc>", "priority": "low|normal|high", "project": "<project|null>"}\n'
                    '- {"op": "split", "entry": "<id>", "parts": [{"content": "...", "tags": [...]}, ...]}\n'
                    '- {"op": "extract", "from": "<id>", "into": "<id>", "extracted_content": "<segment moved>", "remaining_content": "<what stays in from, empty to delete>", "merged_content": "<into rewritten with extracted segment>"}\n\n'
                    "Rules:\n"
                    "- Merge entries that are redundant or say the same thing from different agents. Write merged_content that synthesizes both.\n"
                    "- Promote entries that are stable, high-signal, and likely to remain true.\n"
                    "- Prune entries that are superseded, duplicated, or low-signal. If confidence is already at floor, they will be deleted. Otherwise they are flagged for decay.\n"
                    "- Split entries that cover multiple unrelated topics into focused entries. Each part must be self-contained. Minimum 2 parts.\n"
                    "- Extract a segment from one entry and fold it into another when partial content belongs with a different entry. Set remaining_content to empty string to delete the source after extraction.\n"
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
    await client.log(
        action="synthesis",
        message=f"synthesis pass complete: {len(ops)} op(s) on {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}",
        details={
            "ops": len(ops),
            "entries": len(entries),
            "op_types": list({o.get("op") for o in ops}),
        },
    )


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

    decayed = 0
    for entry in entries:
        current = entry["confidence"]
        if current <= settings.decay_floor:
            continue
        new_conf = max(settings.decay_floor, current * settings.decay_rate)
        try:
            await client.patch_memory(entry["id"], confidence=new_conf)
            decayed += 1
        except Exception as e:
            log.warning("decay failed for %s: %s", entry["id"], e)
    if decayed:
        await client.log(
            action="decay",
            message=f"decayed confidence on {decayed} entr{'y' if decayed == 1 else 'ies'}",
            details={"decayed": decayed, "candidates": len(entries)},
        )


async def run_task_triage(client: ArtelClient) -> None:
    try:
        all_tasks = await client.list_tasks(status="open", limit=50)
    except Exception as e:
        log.warning("task triage could not fetch open tasks: %s", e)
        return

    unclaimed = [t for t in all_tasks if not t.get("assigned_to")]
    if not unclaimed:
        return

    triaged = 0
    for task in unclaimed:
        try:
            await _triage_task(task, client)
            triaged += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("task triage failed for %s: %s", task["id"], e)
    if triaged:
        await client.log(
            action="triage",
            message=f"triaged {triaged} open task{'s' if triaged != 1 else ''}",
            details={"triaged": triaged, "open_unclaimed": len(unclaimed)},
        )


async def _triage_task(task: dict, client: ArtelClient) -> None:
    query = f"{task['title']} {task.get('description') or ''}"
    related = await client.search_memory(query, limit=8, max_distance=0.5)
    if not related:
        return

    memory_block = "\n\n".join(
        f"[{r['id']}] conf={r.get('confidence', 1.0):.2f} tags={r.get('tags', [])}\n{r['content'][:300].replace(chr(10), ' ')}"
        for r in related
    )

    if not is_configured():
        if related:
            snippet = "; ".join(r["content"][:80].replace("\n", " ") for r in related[:3])
            await client.add_task_comment(
                task["id"],
                f"[archivist] Related memory entries found:\n{snippet}",
            )
        return

    try:
        text = await complete(
            system="You are the Artel archivist triaging an open task. Output a JSON object with keys: link_comment (string or null — a comment noting relevant memory entries by ID and how they relate, null if nothing useful), duplicate_of (string or null — task title hint if this looks like a duplicate of known work, null if not), already_done (bool — true only if memory strongly suggests this work is complete). Be conservative: only flag duplicates if very confident, only flag already_done if memory explicitly describes the outcome.",
            user=(
                f'Task: "{task["title"]}"\n'
                f"Description: {task.get('description') or 'none'}\n"
                f"Expected outcome: {task.get('expected_outcome') or 'none'}\n\n"
                f"Related memory:\n{memory_block}"
            ),
            max_tokens=512,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning("task triage LLM call failed for %s: %s", task["id"], e)
        return

    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        end = len(lines) - 1
        while end > 0 and not lines[end].strip().startswith("```"):
            end -= 1
        stripped = "\n".join(lines[1:end]).strip()
    try:
        result = json.loads(stripped)
    except Exception:
        log.warning("task triage LLM returned unparseable JSON for %s", task["id"])
        return

    link_comment = result.get("link_comment")
    duplicate_of = result.get("duplicate_of")
    already_done = result.get("already_done", False)

    if link_comment and isinstance(link_comment, str) and link_comment.strip():
        try:
            await client.add_task_comment(task["id"], f"[archivist] {link_comment.strip()}")
            log.info("archivist linked memory to task %s", task["id"][:8])
        except Exception as e:
            log.warning("could not add link comment to task %s: %s", task["id"], e)

    if duplicate_of and isinstance(duplicate_of, str) and duplicate_of.strip():
        try:
            await client.add_task_comment(
                task["id"],
                f"[archivist] This task may duplicate existing work: {duplicate_of.strip()}. Review before starting.",
            )
            log.info("archivist flagged possible duplicate for task %s", task["id"][:8])
        except Exception as e:
            log.warning("could not add duplicate comment to task %s: %s", task["id"], e)

    if already_done:
        try:
            await client.add_task_comment(
                task["id"],
                "[archivist] Project memory suggests this work may already be complete. Verify before claiming.",
            )
            log.info("archivist flagged possible completion for task %s", task["id"][:8])
        except Exception as e:
            log.warning("could not add already_done comment to task %s: %s", task["id"], e)


async def run_promotion(client: ArtelClient) -> None:
    cutoff = (datetime.now(UTC) - timedelta(days=settings.promotion_stability_days)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    memory_entries = await client.list_entries(
        type="memory",
        min_version=settings.promotion_memory_min_version,
        updated_before=cutoff,
    )
    promoted = 0
    for entry in memory_entries:
        if entry["agent_id"] == settings.archivist_id:
            continue
        if entry.get("type") == "directive":
            continue
        try:
            await client.patch_memory(entry["id"], type="doc")
            promoted += 1
        except Exception as e:
            log.warning("memory promotion failed for %s: %s", entry["id"], e)
    if promoted:
        await client.log(
            action="promotion",
            message=f"promoted {promoted} memor{'y' if promoted == 1 else 'ies'} to doc",
            details={"promoted": promoted},
        )
