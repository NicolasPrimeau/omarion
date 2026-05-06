import asyncio
import logging
from datetime import UTC, datetime, timedelta

from .client import ArtelClient
from .config import settings
from .llm import complete, is_configured

log = logging.getLogger(__name__)


def _utc_ago(hours: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


async def run_synthesis(client: ArtelClient) -> None:
    if not is_configured():
        return

    entries = await client.get_delta(_utc_ago(24))
    entries = [e for e in entries if e["agent_id"] != settings.archivist_id]

    if len(entries) < 2:
        return

    formatted = "\n\n".join(
        f"[{e['id']}] ({e['agent_id']}, {e['type']}) {e['content']}" for e in entries
    )

    text = None
    for attempt in range(3):
        try:
            text = await complete(
                system=(
                    "You are the Artel archivist. Synthesize agent memory entries "
                    "and surface connections, patterns, and contradictions no individual agent can see."
                ),
                user=(
                    f"Memory entries written in the last 24 hours:\n\n{formatted}\n\n"
                    "Write a synthesis document (markdown). Identify connections between entries, "
                    "surface insights, note contradictions. Cite entry IDs like [id] when relevant. "
                    "Be specific and concise."
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

    if text:
        tags = list({e.get("project") for e in entries if e.get("project")} | {"synthesis"})
        await client.write_memory(content=text, type="doc", tags=tags)


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
    scratch_cutoff = (
        datetime.now(UTC) - timedelta(hours=settings.promotion_scratch_age_hours)
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    scratch_entries = await client.list_entries(type="scratch", created_before=scratch_cutoff)
    for entry in scratch_entries:
        if entry["agent_id"] == settings.archivist_id:
            continue
        if entry["confidence"] >= 0.5:
            try:
                await client.patch_memory(entry["id"], type="memory")
            except Exception as e:
                log.warning("scratch promotion failed for %s: %s", entry["id"], e)

    memory_entries = await client.list_entries(
        type="memory", min_version=settings.promotion_memory_min_version
    )
    for entry in memory_entries:
        if entry["agent_id"] == settings.archivist_id:
            continue
        try:
            await client.patch_memory(entry["id"], type="doc")
        except Exception as e:
            log.warning("memory promotion failed for %s: %s", entry["id"], e)
