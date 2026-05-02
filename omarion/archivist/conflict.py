import anthropic

from .client import OmarionClient
from .config import settings

_MAX_DISTANCE = 1.0 - settings.conflict_threshold

_anthropic: anthropic.AsyncAnthropic | None = None


def _client() -> anthropic.AsyncAnthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _anthropic


async def check_and_merge(entry_id: str, client: OmarionClient) -> None:
    entry = await client.get_memory(entry_id)
    similar = await client.search_memory(entry["content"], limit=6, max_distance=_MAX_DISTANCE)

    conflicts = [
        s for s in similar
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
    msg = await _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": (
                f"Two agents wrote conflicting memory entries. "
                f"Produce one canonical merged entry.\n\n"
                f"Entry A (agent: {a['agent_id']}):\n{a['content']}\n\n"
                f"Entry B (agent: {b['agent_id']}):\n{b['content']}\n\n"
                "Write the merged entry. Resolve contradictions. Be concise. "
                "Return only the merged content, no preamble."
            ),
        }],
    )
    return msg.content[0].text
