import asyncio

from .config import settings
from .mdns import MDNSService


async def _run():
    svc = MDNSService(settings.port)
    await svc.start()
    print(f"mDNS: artel.local -> :{settings.port}", flush=True)
    await asyncio.Event().wait()


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
