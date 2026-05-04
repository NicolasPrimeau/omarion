from .client import ArtelClient
from .config import settings
from .llm import complete, is_configured

_MAX_DISTANCE = 1.0 - settings.conflict_threshold


async def check_and_merge(entry_id: str, client: ArtelClient) -> None:
    if not is_configured():
        return

    entry = await client.get_memory(entry_id)
    similar = await client.search_memory(entry["content"], limit=6, max_distance=_MAX_DISTANCE)

    conflicts = [
        s
        for s in similar
        if s["id"] != entry_id
        and s["agent_id"] != entry["agent_id"]
        and s["agent_id"] != settings.archivist_id
        and not s["parents"]
    ]

    if not conflicts:
        return

    other = conflicts[0]
    merged_content = await _merge(entry, other)
    merged_tags = list(set(entry["tags"] + other["tags"]))
    merged_project = entry["project"] if entry["project"] == other["project"] else None

    await client.write_memory(
        content=merged_content,
        type=entry["type"],
        tags=merged_tags,
        parents=[entry["id"], other["id"]],
        project=merged_project,
    )
    await client.delete_memory(entry["id"])
    await client.delete_memory(other["id"])


async def _merge(a: dict, b: dict) -> str:
    return await complete(
        system="You are the Artel archivist. Merge conflicting memory entries into one canonical entry.",
        user=(
            f"Two agents wrote conflicting memory entries. "
            f"Produce one canonical merged entry.\n\n"
            f"Entry A (agent: {a['agent_id']}):\n{a['content']}\n\n"
            f"Entry B (agent: {b['agent_id']}):\n{b['content']}\n\n"
            "Write the merged entry. Resolve contradictions. Be concise. "
            "Return only the merged content, no preamble."
        ),
        max_tokens=1024,
    )
