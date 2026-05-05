import socket

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf


def _local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"


def _make_info(port: int) -> ServiceInfo:
    return ServiceInfo(
        "_artel._tcp.local.",
        "artel._artel._tcp.local.",
        server="artel.local.",
        addresses=[socket.inet_aton(_local_ip())],
        port=port,
        properties={},
    )


class MDNSService:
    def __init__(self, port: int):
        self._info = _make_info(port)
        self._zc: AsyncZeroconf | None = None

    async def start(self):
        self._zc = AsyncZeroconf()
        await self._zc.async_register_service(self._info, allow_name_change=True)

    async def stop(self):
        if self._zc:
            await self._zc.async_unregister_service(self._info)
            await self._zc.async_close()
