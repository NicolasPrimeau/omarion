import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import feedparser
import httpx

from ..store.db import get_db
from ..store.embeddings import embed
from .broadcast import broadcast
from .models import EventEntry, new_id

log = logging.getLogger(__name__)

_POLL_INTERVAL = 60


def _utcnow() -> str:
    dt = datetime.now(UTC)
    return dt.strftime(f"%Y-%m-%dT%H:%M:%S.{dt.microsecond // 1000:03d}Z")


def _item_guid(entry: feedparser.FeedParserDict) -> str:
    return entry.get("id") or entry.get("link") or entry.get("title", "")


def _item_content(feed_name: str, entry: feedparser.FeedParserDict) -> str:
    title = entry.get("title", "(no title)")
    summary = entry.get("summary", entry.get("description", ""))
    link = entry.get("link", "")
    published = entry.get("published", "")
    parts = [f"## [{feed_name}] {title}"]
    if published:
        parts.append(f"Published: {published}")
    if summary:
        parts.append(f"\n{summary[:1000]}")
    if link:
        parts.append(f"\nSource: {link}")
    return "\n".join(parts)


def _write_memory(agent_id: str, project: str, content: str, tags: list[str]) -> None:
    db = get_db()
    entry_id = new_id()
    event_id = new_id()
    vec = embed(content)
    with db:
        db.execute(
            """INSERT INTO memory (id, type, agent_id, project, scope, content,
               confidence, parents, tags) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                entry_id,
                "memory",
                agent_id,
                project,
                "project",
                content,
                0.5,
                "[]",
                json.dumps(tags),
            ),
        )
        db.execute(
            "INSERT INTO memory_vec (id, embedding) VALUES (?, ?)",
            (entry_id, json.dumps(vec)),
        )
        db.execute(
            "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
            (event_id, "memory.written", agent_id, json.dumps({"memory_id": entry_id})),
        )
    broadcast(
        EventEntry(
            id=event_id,
            type="memory.written",
            agent_id=agent_id,
            payload={"memory_id": entry_id},
            created_at=_utcnow(),
        )
    )


def _parse_json_feed(resp_text: str, feed_name: str) -> list[tuple[str, str]]:
    try:
        data = json.loads(resp_text)
    except Exception:
        return []
    if not isinstance(data.get("items"), list):
        return []
    results = []
    for item in data["items"]:
        guid = item.get("id") or item.get("url", "")
        if not guid:
            continue
        title = item.get("title", "(no title)")
        body = item.get("content_text") or item.get("content_html") or item.get("summary", "")
        published = item.get("date_published", "")
        link = item.get("url", "")
        parts = [f"## [{feed_name}] {title}"]
        if published:
            parts.append(f"Published: {published}")
        if body:
            parts.append(f"\n{body[:1000]}")
        if link:
            parts.append(f"\nSource: {link}")
        results.append((guid, "\n".join(parts)))
    return results


async def _poll_feed(feed: dict) -> None:
    feed_id = feed["id"]
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(feed["url"])
            resp.raise_for_status()
    except Exception as e:
        log.warning("feed %s (%s) fetch failed: %s", feed["name"], feed["url"], e)
        lid = new_id()
        db = get_db()
        with db:
            db.execute(
                "INSERT INTO archivist_logs (id, level, source, action, message, details) VALUES (?,?,?,?,?,?)",
                (
                    lid,
                    "warning",
                    "poller",
                    "feed_poll",
                    f'feed "{feed["name"]}" fetch failed: {e}',
                    json.dumps({"feed_id": feed_id, "feed_name": feed["name"], "url": feed["url"]}),
                ),
            )
            db.execute(
                "DELETE FROM archivist_logs WHERE id IN (SELECT id FROM archivist_logs ORDER BY created_at DESC LIMIT -1 OFFSET 10000)"
            )
        return

    content_type = resp.headers.get("content-type", "")
    is_json_feed = "feed+json" in content_type or (
        "json" in content_type and '"version"' in resp.text and "jsonfeed.org" in resp.text
    )

    db = get_db()
    seen = {
        r["item_guid"]
        for r in db.execute(
            "SELECT item_guid FROM feed_items_seen WHERE feed_id=?", (feed_id,)
        ).fetchall()
    }

    tags = json.loads(feed["tags"]) + ["feed-item", "unprocessed"]
    count = 0
    new_guids = []

    if is_json_feed:
        entries = _parse_json_feed(resp.text, feed["name"])
        for guid, content in entries:
            if count >= feed["max_per_poll"]:
                break
            if not guid or guid in seen:
                continue
            _write_memory(feed["agent_id"], feed["project"], content, tags)
            new_guids.append(guid)
            count += 1
    else:
        parsed = feedparser.parse(resp.text)
        for entry in parsed.entries:
            if count >= feed["max_per_poll"]:
                break
            guid = _item_guid(entry)
            if not guid or guid in seen:
                continue
            content = _item_content(feed["name"], entry)
            _write_memory(feed["agent_id"], feed["project"], content, tags)
            new_guids.append(guid)
            count += 1

    if new_guids:
        now = _utcnow()
        with db:
            db.executemany(
                "INSERT OR IGNORE INTO feed_items_seen (feed_id, item_guid, seen_at) VALUES (?,?,?)",
                [(feed_id, g, now) for g in new_guids],
            )

    with db:
        db.execute(
            "UPDATE feed_subscriptions SET last_fetched_at=? WHERE id=?",
            (_utcnow(), feed_id),
        )

    if count:
        log.info(
            "feed %s: ingested %d new items into project %s", feed["name"], count, feed["project"]
        )
        lid = new_id()
        with db:
            db.execute(
                "INSERT INTO archivist_logs (id, level, source, action, message, details) VALUES (?,?,?,?,?,?)",
                (
                    lid,
                    "info",
                    "poller",
                    "feed_poll",
                    f'feed "{feed["name"]}": ingested {count} new item{"s" if count != 1 else ""} into project {feed["project"]}',
                    json.dumps(
                        {
                            "feed_id": feed_id,
                            "feed_name": feed["name"],
                            "project": feed["project"],
                            "count": count,
                        }
                    ),
                ),
            )
            db.execute(
                "DELETE FROM archivist_logs WHERE id IN (SELECT id FROM archivist_logs ORDER BY created_at DESC LIMIT -1 OFFSET 10000)"
            )


async def run_poller() -> None:
    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        db = get_db()
        now = datetime.now(UTC)
        rows = db.execute("SELECT * FROM feed_subscriptions").fetchall()
        for row in rows:
            feed = dict(row)
            if feed["last_fetched_at"] is None:
                due = True
            else:
                try:
                    last = datetime.fromisoformat(feed["last_fetched_at"].replace("Z", "+00:00"))
                    due = now - last >= timedelta(minutes=feed["interval_min"])
                except ValueError:
                    due = True
            if due:
                try:
                    await _poll_feed(feed)
                except Exception as e:
                    log.error("poll_feed %s failed: %s", feed["name"], e)
