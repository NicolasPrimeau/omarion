import asyncio
import logging

from .client import ArtelClient
from .config import settings
from .conflict import check_and_merge
from .synthesis import decay_confidence, run_promotion, run_synthesis

log = logging.getLogger(__name__)


async def _event_watcher(client: ArtelClient) -> None:
    while True:
        try:
            async for event in client.stream_events("memory.written"):
                entry_id = event.get("payload", {}).get("memory_id")
                if entry_id:
                    try:
                        await check_and_merge(entry_id, client)
                    except Exception as e:
                        log.error("conflict check failed %s: %s", entry_id, e)
        except Exception as e:
            log.error("event stream disconnected: %s", e)
            await asyncio.sleep(10)


async def _scheduler(client: ArtelClient) -> None:
    while True:
        try:
            await run_synthesis(client)
            await decay_confidence(client)
            await run_promotion(client)
        except Exception as e:
            log.error("synthesis pass failed: %s", e)
        await asyncio.sleep(settings.synthesis_interval)


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = ArtelClient()
    try:
        await asyncio.gather(
            _event_watcher(client),
            _scheduler(client),
        )
    finally:
        await client.aclose()
