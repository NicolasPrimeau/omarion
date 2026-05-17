import ipaddress
import socket
import threading
from typing import Any

from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf
from zeroconf.asyncio import AsyncZeroconf

_SERVICE_TYPE = "_artel._tcp.local."

_discovered: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"


def _make_info(port: int, instance_id: str, public_url: str) -> ServiceInfo:
    props = {
        "id": instance_id,
        "url": public_url or f"http://{_local_ip()}:{port}",
    }
    return ServiceInfo(
        _SERVICE_TYPE,
        f"{instance_id}.{_SERVICE_TYPE}",
        server=f"artel-{instance_id[:8]}.local.",
        addresses=[socket.inet_aton(_local_ip())],
        port=port,
        properties={k: v.encode() for k, v in props.items()},
    )


def is_private_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def get_discovered() -> list[dict[str, Any]]:
    with _lock:
        return list(_discovered.values())


def remove_discovered(instance_id: str) -> None:
    with _lock:
        _discovered.pop(instance_id, None)


class _Listener(ServiceListener):
    def __init__(self, own_id: str):
        self._own_id = own_id

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if not info:
            return
        props = {k.decode(): v.decode() for k, v in info.properties.items()}
        peer_id = props.get("id", "")
        if peer_id == self._own_id or not peer_id:
            return
        url = props.get("url", "")
        if not url:
            addr = socket.inet_ntoa(info.addresses[0]) if info.addresses else ""
            url = f"http://{addr}:{info.port}"
        with _lock:
            _discovered[peer_id] = {"instance_id": peer_id, "url": url, "name": name}

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        with _lock:
            gone = [k for k, v in _discovered.items() if v.get("name") == name]
            for k in gone:
                _discovered.pop(k, None)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zc, type_, name)


class MDNSService:
    def __init__(self, port: int, instance_id: str = "", public_url: str = ""):
        self._port = port
        self._instance_id = instance_id
        self._public_url = public_url
        self._info: ServiceInfo | None = None
        self._zc: AsyncZeroconf | None = None
        self._browser: ServiceBrowser | None = None

    async def start(self):
        self._info = _make_info(self._port, self._instance_id, self._public_url)
        self._zc = AsyncZeroconf()
        await self._zc.async_register_service(self._info, allow_name_change=True)
        self._browser = ServiceBrowser(
            self._zc.zeroconf, _SERVICE_TYPE, _Listener(self._instance_id)
        )

    async def stop(self):
        if self._browser:
            self._browser.cancel()
        if self._zc and self._info:
            await self._zc.async_unregister_service(self._info)
            await self._zc.async_close()
