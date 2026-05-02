from datetime import UTC, datetime, timedelta

import anthropic

from .client import OmarionClient
from .config import settings

_anthropic: anthropic.AsyncAnthropic | None = None


def _client() -> anthropic.AsyncAnthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _anthropic


def _utc_ago(hours: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )


async def run_synthesis(client: OmarionClient) -> None:
    entries = await client.get_delta(_utc_ago(24))
    entries = [e for e in entries if e["agent_id"] != settings.archivist_id]

    if len(entries) < 2:
        return

    formatted = "\n\n".join(
        f"[{e['id']}] ({e['agent_id']}, {e['type']}) {e['content']}"
        for e in entries
    )

    msg = await _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=(
            "You are the Omarion archivist. Synthesize agent memory entries "
            "and surface connections, patterns, and contradictions no individual agent can see."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Memory entries written in the last 24 hours:\n\n{formatted}\n\n"
                "Write a synthesis document (markdown). Identify connections between entries, "
                "surface insights, note contradictions. Cite entry IDs like [id] when relevant. "
                "Be specific and concise."
            ),
        }],
    )

    tags = list({e.get("project") for e in entries if e.get("project")} | {"synthesis"})
    await client.write_memory(
        content=msg.content[0].text,
        type="doc",
        tags=tags,
    )


async def decay_confidence(client: OmarionClient) -> None:
    pass
